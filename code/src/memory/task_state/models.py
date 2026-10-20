from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum, IntEnum
from typing import Any, Mapping, Optional, Tuple, cast


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {
            key: _json_value(item)
            for key, item in asdict(cast(Any, value)).items()
        }
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


class EntryKind(str, Enum):
    GOAL = "GOAL"
    CONSTRAINT = "CONSTRAINT"
    DECISION = "DECISION"
    PROGRESS = "PROGRESS"
    OPEN_QUESTION = "OPEN_QUESTION"
    DEPENDENCY = "DEPENDENCY"
    BACKGROUND = "BACKGROUND"


class EntryStatus(str, Enum):
    ACTIVE = "ACTIVE"
    RESOLVED = "RESOLVED"
    SUPERSEDED = "SUPERSEDED"
    REMOVED = "REMOVED"


class TaskStatus(str, Enum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class Priority(IntEnum):
    LOW = 1
    NORMAL = 2
    HIGH = 3
    CRITICAL = 4


class RelationKind(str, Enum):
    SUPERSEDES = "SUPERSEDES"
    DEPENDS_ON = "DEPENDS_ON"
    SUPPORTS = "SUPPORTS"
    CONFLICTS_WITH = "CONFLICTS_WITH"


class SourceKind(str, Enum):
    USER = "USER"
    AGENT = "AGENT"
    TOOL = "TOOL"
    EXTERNAL = "EXTERNAL"


class ProposalBasis(str, Enum):
    EXPLICIT = "EXPLICIT"
    OBSERVED = "OBSERVED"
    INFERRED = "INFERRED"


class TaskStateScope(str, Enum):
    """Provider-proposed durability scope for a task-state proposal."""

    TASK = "TASK"
    SESSION = "SESSION"


class ProposalAction(str, Enum):
    ADD = "ADD"
    RESOLVE = "RESOLVE"
    SUPERSEDE = "SUPERSEDE"
    REMOVE = "REMOVE"
    LINK_RELATION = "LINK_RELATION"
    SET_PRIORITY = "SET_PRIORITY"


class EventProcessStatus(str, Enum):
    RECEIVED = "RECEIVED"
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class TaskIntent(str, Enum):
    """Provider-proposed request intent; it is not inferred by local rules."""

    CONTINUE_TASK = "CONTINUE_TASK"
    MODIFY_EXISTING = "MODIFY_EXISTING"
    REPORT_PROGRESS = "REPORT_PROGRESS"
    RAISE_QUESTION = "RAISE_QUESTION"
    ESTABLISH_STATE = "ESTABLISH_STATE"
    EXPLICIT_MEMORY_WRITE = "EXPLICIT_MEMORY_WRITE"


@dataclass(frozen=True)
class EvidenceRef:
    bundle_id: str
    memory_id: str
    revision_id: str
    evidence_item_id: Optional[str] = None

    def to_dict(self) -> dict[str, Optional[str]]:
        return {
            "bundle_id": self.bundle_id,
            "memory_id": self.memory_id,
            "revision_id": self.revision_id,
            "evidence_item_id": self.evidence_item_id,
        }


@dataclass(frozen=True)
class LedgerEntry:
    entry_id: str
    task_id: str
    kind: EntryKind
    content: str
    status: EntryStatus
    created_at: float
    updated_at: float
    priority: Priority = Priority.NORMAL
    source_kind: SourceKind = SourceKind.USER
    basis: ProposalBasis = ProposalBasis.EXPLICIT
    source_ref: Optional[str] = None
    evidence_refs: Tuple[EvidenceRef, ...] = ()
    supersedes_entry_id: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.entry_id or not self.task_id or not self.content.strip():
            raise ValueError("entry_id/task_id/content must be non-empty")


@dataclass(frozen=True)
class TaskRelation:
    task_id: str
    from_entry_id: str
    to_entry_id: str
    kind: RelationKind


@dataclass(frozen=True)
class TaskEvent:
    task_id: str
    event_id: str
    source_kind: SourceKind
    content: str = ""
    structured_payload: Mapping[str, Any] = field(default_factory=dict)
    occurred_at: float = 0.0
    source_ref: Optional[str] = None
    turn_id: Optional[str] = None
    tool_call_id: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.task_id or not self.event_id:
            raise ValueError("task_id and event_id must be non-empty")


@dataclass(frozen=True)
class TaskStateProposal:
    proposal_id: str
    task_id: str
    source_event_id: str
    action: ProposalAction
    entry_kind: Optional[EntryKind] = None
    target_entry_id: Optional[str] = None
    content: Optional[str] = None
    replacement_content: Optional[str] = None
    priority: Optional[Priority] = None
    relation: Optional[TaskRelation] = None
    evidence_refs: Tuple[EvidenceRef, ...] = ()
    support_spans: Tuple[str, ...] = ()
    basis: ProposalBasis = ProposalBasis.EXPLICIT
    warnings: Tuple[str, ...] = ()
    scope: TaskStateScope = TaskStateScope.TASK


@dataclass(frozen=True)
class TaskAnalysis:
    """Request-local provider analysis; never canonical durable task truth."""

    task_stage: str
    query_intent: TaskIntent
    memory_write_requested: bool = False
    information_needs: Tuple[str, ...] = ()
    retrieval_needed: bool = True
    important_kinds: Tuple[EntryKind, ...] = ()
    focus_entry_ids: Tuple[str, ...] = ()
    search_query: str = ""
    warnings: Tuple[str, ...] = ()
    memory_content: Optional[str] = None
    memory_support_span: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_stage": self.task_stage,
            "query_intent": self.query_intent.value,
            "memory_write_requested": self.memory_write_requested,
            "information_needs": list(self.information_needs),
            "retrieval_needed": self.retrieval_needed,
            "important_kinds": [item.value for item in self.important_kinds],
            "focus_entry_ids": list(self.focus_entry_ids),
            "search_query": self.search_query,
            "warnings": list(self.warnings),
            "memory_content": self.memory_content,
            "memory_support_span": self.memory_support_span,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> Optional["TaskAnalysis"]:
        if raw is None:
            return None
        if not isinstance(raw, Mapping):
            raise ValueError("task analysis must be an object or null")
        try:
            task_stage = str(raw["task_stage"]).strip()
            if not task_stage:
                raise ValueError("task_stage must be non-empty")
            intent = TaskIntent(str(raw["query_intent"]))
            memory_write_requested = raw.get("memory_write_requested", False)
            if not isinstance(memory_write_requested, bool):
                raise TypeError("memory_write_requested must be boolean")
            retrieval_needed = raw["retrieval_needed"]
            if not isinstance(retrieval_needed, bool):
                raise TypeError("retrieval_needed must be boolean")
            memory_content = raw.get("memory_content")
            if memory_content is not None and not isinstance(memory_content, str):
                raise TypeError("memory_content must be string or null")
            memory_support_span = raw.get("memory_support_span")
            if memory_support_span is not None and not isinstance(memory_support_span, str):
                raise TypeError("memory_support_span must be string or null")
            return cls(
                task_stage=task_stage,
                query_intent=intent,
                memory_write_requested=memory_write_requested,
                information_needs=tuple(str(item) for item in raw.get("information_needs", [])),
                retrieval_needed=retrieval_needed,
                important_kinds=tuple(EntryKind(str(item)) for item in raw.get("important_kinds", [])),
                focus_entry_ids=tuple(str(item) for item in raw.get("focus_entry_ids", [])),
                search_query=str(raw.get("search_query", "")),
                warnings=tuple(str(item) for item in raw.get("warnings", [])),
                memory_content=memory_content.strip() if memory_content else None,
                memory_support_span=(
                    memory_support_span.strip() if memory_support_span else None
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid serialized task analysis") from exc


@dataclass(frozen=True)
class TaskStateProposalBatch:
    task_id: str
    event_id: str
    proposals: Tuple[TaskStateProposal, ...]
    interpreter: str = "unknown"
    interpreter_version: str = "unknown"
    warnings: Tuple[str, ...] = ()
    analysis: Optional[TaskAnalysis] = None
    confirmation_required: bool = False


@dataclass(frozen=True)
class TaskStateUpdateReceipt:
    task_id: str
    event_id: str
    status: EventProcessStatus
    batch_id: str
    proposal_ids: Tuple[str, ...] = ()
    applied_entry_ids: Tuple[str, ...] = ()
    superseded_entry_ids: Tuple[str, ...] = ()
    resolved_entry_ids: Tuple[str, ...] = ()
    relation_keys: Tuple[str, ...] = ()
    warnings: Tuple[str, ...] = ()
    ledger_updated_at: float = 0.0
    analysis: Optional[TaskAnalysis] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "event_id": self.event_id,
            "status": self.status.value,
            "batch_id": self.batch_id,
            "proposal_ids": list(self.proposal_ids),
            "applied_entry_ids": list(self.applied_entry_ids),
            "superseded_entry_ids": list(self.superseded_entry_ids),
            "resolved_entry_ids": list(self.resolved_entry_ids),
            "relation_keys": list(self.relation_keys),
            "warnings": list(self.warnings),
            "ledger_updated_at": self.ledger_updated_at,
            "analysis": None if self.analysis is None else self.analysis.to_dict(),
        }


@dataclass(frozen=True)
class TaskUpdateTrace:
    """Safe, request-local audit view for the most recent Task 3 update."""

    task_id: str
    event_id: str
    status: EventProcessStatus
    analysis: Optional[TaskAnalysis] = None
    proposals: Tuple[TaskStateProposal, ...] = ()
    receipt: Optional[TaskStateUpdateReceipt] = None
    confirmation_required: bool = False
    warnings: Tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "event_id": self.event_id,
            "status": self.status.value,
            "analysis": None if self.analysis is None else self.analysis.to_dict(),
            "proposals": [_json_value(item) for item in self.proposals],
            "receipt": None if self.receipt is None else self.receipt.to_dict(),
            "confirmation_required": self.confirmation_required,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class TaskContextCompaction:
    """Derived, lossy, traceable context artifact; never canonical task truth."""

    policy_version: str
    source_entry_ids: Tuple[str, ...]
    sections: Mapping[str, str]
    generated_at: float
    source_checkpoint_id: Optional[str] = None
    warnings: Tuple[str, ...] = ()

    @property
    def text(self) -> str:
        return " ".join(value for value in self.sections.values() if value)


@dataclass(frozen=True)
class TaskLedger:
    task_id: str
    entries: Tuple[LedgerEntry, ...]
    created_at: float
    updated_at: float
    status: TaskStatus = TaskStatus.ACTIVE

    @property
    def active_entries(self) -> Tuple[LedgerEntry, ...]:
        return tuple(entry for entry in self.entries if entry.status is EntryStatus.ACTIVE)


@dataclass(frozen=True)
class ContinuationCheckpoint:
    task_id: str
    checkpoint_id: str
    last_processed_event: Optional[str]
    goal_entry_ids: Tuple[str, ...] = ()
    current_step: Optional[str] = None
    continuation_note: str = ""
    critical_constraint_ids: Tuple[str, ...] = ()
    open_question_ids: Tuple[str, ...] = ()
    decision_ids: Tuple[str, ...] = ()
    evidence_refs: Tuple[EvidenceRef, ...] = ()
    created_at: float = 0.0
    # Explicit continuation dimensions were added after the original fields
    # to preserve positional construction compatibility for older callers.
    focus: Optional[str] = None
    next_action: Optional[str] = None
    world_state: Optional[str] = None


@dataclass(frozen=True)
class ExternalObservation:
    source_type: str
    source_ref: str
    content: str
    observed_at: float


@dataclass(frozen=True)
class StructuredTaskContext:
    task_id: str
    current_turn: str = ""
    goal: Tuple[LedgerEntry, ...] = ()
    constraints: Tuple[LedgerEntry, ...] = ()
    decisions: Tuple[LedgerEntry, ...] = ()
    progress: Tuple[LedgerEntry, ...] = ()
    open_questions: Tuple[LedgerEntry, ...] = ()
    dependencies: Tuple[LedgerEntry, ...] = ()
    relevant_background: Tuple[LedgerEntry, ...] = ()
    retrieved_evidence: Tuple[Any, ...] = ()
    continuation: Optional[ContinuationCheckpoint] = None
    external_observations: Tuple[ExternalObservation, ...] = ()
    recent_events: Tuple[TaskEvent, ...] = ()
    compaction: Optional[TaskContextCompaction] = None
    policy_version: str = "working-context-v1"
    warnings: Tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        def entry(item: LedgerEntry) -> dict[str, Any]:
            return {
                "entry_id": item.entry_id,
                "task_id": item.task_id,
                "kind": item.kind.value,
                "content": item.content,
                "status": item.status.value,
                "priority": item.priority.name,
                "source_kind": item.source_kind.value,
                "basis": item.basis.value,
                "source_ref": item.source_ref,
                "evidence_refs": [ref.to_dict() for ref in item.evidence_refs],
                "supersedes_entry_id": item.supersedes_entry_id,
            }

        result: dict[str, Any] = {
            "task_id": self.task_id,
            "current_turn": self.current_turn,
            "goal": [entry(item) for item in self.goal],
            "constraints": [entry(item) for item in self.constraints],
            "decisions": [entry(item) for item in self.decisions],
            "progress": [entry(item) for item in self.progress],
            "open_questions": [entry(item) for item in self.open_questions],
            "dependencies": [entry(item) for item in self.dependencies],
            "relevant_background": [entry(item) for item in self.relevant_background],
            "retrieved_evidence": [item.to_dict() if hasattr(item, "to_dict") else _json_value(item) for item in self.retrieved_evidence],
            "continuation": None if self.continuation is None else {
                "task_id": self.continuation.task_id,
                "checkpoint_id": self.continuation.checkpoint_id,
                "last_processed_event": self.continuation.last_processed_event,
                "goal_entry_ids": list(self.continuation.goal_entry_ids),
                "current_step": self.continuation.current_step,
                "focus": self.continuation.focus,
                "next_action": self.continuation.next_action,
                "world_state": self.continuation.world_state,
                "continuation_note": self.continuation.continuation_note,
                "critical_constraint_ids": list(self.continuation.critical_constraint_ids),
                "open_question_ids": list(self.continuation.open_question_ids),
                "decision_ids": list(self.continuation.decision_ids),
                "evidence_refs": [ref.to_dict() for ref in self.continuation.evidence_refs],
            },
            "external_observations": [
                {
                    "source_type": item.source_type,
                    "source_ref": item.source_ref,
                    "content": item.content,
                    "observed_at": item.observed_at,
                }
                for item in self.external_observations
            ],
            "recent_events": [
                {
                    "event_id": item.event_id,
                    "task_id": item.task_id,
                    "source_kind": item.source_kind.value,
                    "content": item.content,
                    "occurred_at": item.occurred_at,
                    "source_ref": item.source_ref,
                }
                for item in self.recent_events
            ],
            "compaction": None if self.compaction is None else {
                "policy_version": self.compaction.policy_version,
                "source_entry_ids": list(self.compaction.source_entry_ids),
                "sections": dict(self.compaction.sections),
                "generated_at": self.compaction.generated_at,
                "source_checkpoint_id": self.compaction.source_checkpoint_id,
                "warnings": list(self.compaction.warnings),
            },
            "policy_version": self.policy_version,
            "warnings": list(self.warnings),
        }
        return result

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True)
class ActiveWorkingContext:
    task_id: str
    structured: StructuredTaskContext
    omitted_entry_ids: Tuple[str, ...] = ()
    warnings: Tuple[str, ...] = ()
    analysis: Optional[TaskAnalysis] = None

    def to_dict(self) -> dict[str, Any]:
        result = self.structured.to_dict()
        result["omitted_entry_ids"] = list(self.omitted_entry_ids)
        result["working_context_warnings"] = list(self.warnings)
        result["task_analysis"] = None if self.analysis is None else self.analysis.to_dict()
        return result

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)
