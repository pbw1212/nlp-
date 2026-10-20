from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class MemoryType(str, Enum):
    CORE_PROFILE = "CORE_PROFILE"
    PREFERENCE_RULES = "PREFERENCE_RULES"
    EPISODIC_GRAPH = "EPISODIC_GRAPH"


class Granularity(str, Enum):
    FINE_EPISODE = "FINE_EPISODE"
    SEMANTIC_INSIGHT = "SEMANTIC_INSIGHT"
    HIGH_ABSTRACT = "HIGH_ABSTRACT"


class SemanticValidity(str, Enum):
    CURRENT = "CURRENT"
    SUPERSEDED = "SUPERSEDED"
    CONTRADICTED = "CONTRADICTED"
    EXPIRED = "EXPIRED"


class RetentionDisposition(str, Enum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"
    RECYCLED = "RECYCLED"


class ActiveStatus(str, Enum):
    """Phase 1A compatibility facade；canonical retention 使用 RetentionDisposition。"""

    ACTIVE = "ACTIVE"
    IN_RECYCLE_BIN = "IN_RECYCLE_BIN"
    DEPRECATED = "DEPRECATED"
    CONTRADICTED = "CONTRADICTED"


class ChangeKind(str, Enum):
    ADD = "ADD"
    UPDATE = "UPDATE"
    CORRECT = "CORRECT"
    RISE = "RISE"
    LEGACY_IMPORT = "LEGACY_IMPORT"


class ProvenanceAuthority(str, Enum):
    EXPLICIT_USER = "EXPLICIT_USER"
    INFERRED = "INFERRED"
    SYSTEM_OR_TOOL = "SYSTEM_OR_TOOL"
    UNKNOWN = "UNKNOWN"


class IngestionState(str, Enum):
    PENDING = "PENDING"
    APPLIED = "APPLIED"


class LifecycleOperation(str, Enum):
    ADD = "ADD"
    UPDATE = "UPDATE"
    CORRECT = "CORRECT"
    REMOVE = "REMOVE"
    NOOP = "NOOP"


class RelationType(str, Enum):
    DERIVED_TO = "DERIVED_TO"
    CORRECTED_FROM = "CORRECTED_FROM"
    QUALIFIES = "QUALIFIES"
    DEPENDS_ON = "DEPENDS_ON"


@dataclass(frozen=True)
class GraphRelation:
    """现阶段继续作为 memory identity-level relation。"""

    target_id: str
    relation_type: RelationType
    weight: float = 1.0


def _validated_string_list(value: Any, field_name: str) -> Tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"{field_name} 必须是字符串数组")
    return tuple(v for v in value if v)


@dataclass(frozen=True)
class SemanticMetadata:
    persons: Tuple[str, ...] = ()
    entities: Tuple[str, ...] = ()
    locations: Tuple[str, ...] = ()
    topics: Tuple[str, ...] = ()
    keywords: Tuple[str, ...] = ()
    scopes: Tuple[str, ...] = ()
    conditions: Tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "SemanticMetadata":
        if data is None:
            return cls()
        if not isinstance(data, dict):
            raise ValueError("semantic metadata 必须是 JSON object")
        allowed = {"persons", "entities", "locations", "topics", "keywords", "scopes", "conditions"}
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"semantic metadata 包含未知字段: {sorted(unknown)}")
        return cls(**{name: _validated_string_list(data.get(name), name) for name in allowed})

    def to_dict(self) -> Dict[str, List[str]]:
        return {
            "persons": list(self.persons),
            "entities": list(self.entities),
            "locations": list(self.locations),
            "topics": list(self.topics),
            "keywords": list(self.keywords),
            "scopes": list(self.scopes),
            "conditions": list(self.conditions),
        }


@dataclass(frozen=True)
class MemoryRevision:
    """Semantic payload immutable；仅 semantic_validity/valid_until 可受控迁移。"""

    revision_id: str
    memory_id: str
    revision_no: int
    content: str
    semantic_validity: SemanticValidity
    created_at: float
    event_time: Optional[float]
    valid_from: float
    valid_until: Optional[float]
    confidence: float
    change_kind: ChangeKind
    supersedes_revision_id: Optional[str] = None
    semantic_metadata: SemanticMetadata = field(default_factory=SemanticMetadata)

    def validate(self) -> None:
        if not self.revision_id or not self.memory_id:
            raise ValueError("revision_id 和 memory_id 不能为空")
        if self.revision_no <= 0:
            raise ValueError("revision_no 必须大于 0")
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("revision content 不能为空")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence 必须位于 [0, 1]")


@dataclass(frozen=True)
class ProvenanceRef:
    provenance_id: str
    source_event_id: str
    session_id: Optional[str]
    message_id: Optional[str]
    source_type: str
    observed_at: float
    authority: ProvenanceAuthority
    content_hash: str
    controlled_excerpt: str


@dataclass(frozen=True)
class IngestionEvent:
    event_id: str
    session_id: Optional[str]
    message_id: Optional[str]
    payload_hash: str
    raw_text: Optional[str]
    assistant_text: Optional[str]
    controlled_excerpt: str
    state: IngestionState
    created_at: float
    applied_at: Optional[float] = None
    result_metadata: Optional[Dict[str, Any]] = None
    consolidation_run_id: Optional[str] = None


@dataclass(frozen=True)
class LifecycleProposal:
    operation: LifecycleOperation
    content: Optional[str] = None
    target_memory_id: Optional[str] = None
    memory_type: MemoryType = MemoryType.PREFERENCE_RULES
    granularity: Granularity = Granularity.SEMANTIC_INSIGHT
    confidence: float = 0.8
    importance_score: Optional[float] = None
    semantic_metadata: SemanticMetadata = field(default_factory=SemanticMetadata)
    provenance_event_ids: Tuple[str, ...] = ()

    def validate_shape(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("proposal confidence 必须位于 [0, 1]")
        if self.importance_score is not None and not 0.0 <= self.importance_score <= 1.0:
            raise ValueError("proposal importance_score 必须位于 [0, 1]")
        if self.operation in {LifecycleOperation.ADD, LifecycleOperation.UPDATE, LifecycleOperation.CORRECT}:
            if not isinstance(self.content, str) or not self.content.strip():
                raise ValueError(f"{self.operation.value} 需要非空 content")
        if self.operation in {LifecycleOperation.UPDATE, LifecycleOperation.CORRECT} and not self.target_memory_id:
            raise ValueError(f"{self.operation.value} 需要 target_memory_id")
        if self.operation is LifecycleOperation.REMOVE and not self.target_memory_id:
            raise ValueError("REMOVE 需要 target_memory_id")


@dataclass
class MemoryItem:
    """Canonical stable identity；current_revision 只是读取/兼容投影。"""

    memory_id: str = field(default_factory=lambda: f"mem_{uuid.uuid4().hex[:12]}")
    memory_type: MemoryType = MemoryType.PREFERENCE_RULES
    granularity: Granularity = Granularity.SEMANTIC_INSIGHT
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    current_revision_id: Optional[str] = None
    importance_score: float = 0.5
    access_count: int = 1
    last_accessed: float = field(default_factory=time.time)
    decay_strength: float = 1.0
    retention_disposition: RetentionDisposition = RetentionDisposition.ACTIVE
    is_pinned: bool = False
    enable_time_decay: bool = False
    graph_relations: List[GraphRelation] = field(default_factory=list)
    current_revision: Optional[MemoryRevision] = field(default=None, repr=False, compare=False)
    # DEPRECATED COMPATIBILITY PROJECTION：仅供 Phase 1A call compatibility；
    # repository/lifecycle/read boundary 禁止把它们作为 canonical source。
    content: str = ""
    status: ActiveStatus = ActiveStatus.ACTIVE
    metadata: "MemoryMetadata" = field(default_factory=lambda: MemoryMetadata())

    def __post_init__(self) -> None:
        if self.retention_disposition is RetentionDisposition.ACTIVE and self.status is not ActiveStatus.ACTIVE:
            self.retention_disposition = {
                ActiveStatus.IN_RECYCLE_BIN: RetentionDisposition.RECYCLED,
                ActiveStatus.DEPRECATED: RetentionDisposition.ARCHIVED,
                ActiveStatus.CONTRADICTED: RetentionDisposition.RECYCLED,
            }.get(self.status, RetentionDisposition.ACTIVE)
        self.importance_score = self.metadata.importance_score if self.importance_score == 0.5 else self.importance_score
        self.access_count = self.metadata.access_count if self.access_count == 1 else self.access_count
        self.last_accessed = self.metadata.last_accessed
        self.decay_strength = self.metadata.decay_strength
        self.is_pinned = self.metadata.is_pinned
        # A freshly constructed compatibility item may explicitly request the
        # legacy switch before a revision is attached. Persisted items carry
        # the canonical value in metadata/current-revision hydration.
        if self.current_revision is not None or self.metadata.enable_time_decay:
            self.enable_time_decay = self.metadata.enable_time_decay

    def attach_current_revision(self, revision: Optional[MemoryRevision]) -> None:
        self.current_revision = revision
        self.current_revision_id = revision.revision_id if revision else None
        self.content = revision.content if revision else ""
        self.status = {
            RetentionDisposition.ACTIVE: ActiveStatus.ACTIVE,
            RetentionDisposition.RECYCLED: ActiveStatus.IN_RECYCLE_BIN,
            RetentionDisposition.ARCHIVED: ActiveStatus.DEPRECATED,
        }[self.retention_disposition]
        self.metadata = MemoryMetadata(
            timestamp=self.created_at,
            last_accessed=self.last_accessed,
            importance_score=self.importance_score,
            confidence=revision.confidence if revision else 0.0,
            access_count=self.access_count,
            decay_strength=self.decay_strength,
            valid_scope=list(revision.semantic_metadata.scopes) if revision else [],
            is_pinned=self.is_pinned,
            enable_time_decay=self.enable_time_decay,
        )


@dataclass
class MemoryMetadata:
    """Phase 1A input/read compatibility DTO；不再是 canonical persisted object。"""

    source_event_id: str = ""
    timestamp: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    importance_score: float = 0.5
    confidence: float = 0.8
    access_count: int = 1
    decay_strength: float = 1.0
    valid_scope: List[str] = field(default_factory=list)
    is_explicit: bool = False
    is_pinned: bool = False
    enable_time_decay: bool = False


@dataclass(frozen=True)
class MemorySnapshot:
    memory_id: str
    memory_type: MemoryType
    granularity: Granularity
    retention_disposition: RetentionDisposition
    revision: MemoryRevision
    provenance: Tuple[ProvenanceRef, ...]
    importance_score: float
    access_count: int
    last_accessed: float
    decay_strength: float
    is_pinned: bool


def payload_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def bounded_excerpt(text: str, max_chars: int) -> str:
    normalized = " ".join(text.split())
    return normalized[:max_chars]


def new_revision_id() -> str:
    return f"rev_{uuid.uuid4().hex}"


def new_memory_id() -> str:
    return f"mem_{uuid.uuid4().hex[:12]}"


def new_provenance_id() -> str:
    return f"prov_{uuid.uuid4().hex}"


def new_event_id() -> str:
    return f"evt_{uuid.uuid4().hex}"
