import time
from typing import List, Optional, Sequence

from src.memory.core.config import MemoryConfig
from src.memory.core.errors import MemoryNotFoundError
from src.memory.core.schema import (
    ChangeKind,
    GraphRelation,
    Granularity,
    MemoryItem,
    MemoryType,
    ProvenanceAuthority,
    RelationType,
    RetentionDisposition,
    SemanticMetadata,
    new_memory_id,
)
from src.memory.storage.sqlite_manager import SQLiteMemoryManager
from src.memory.storage.vector_store import InMemoryVectorStore


class CRUDEngine:
    def __init__(self, sqlite_mgr: SQLiteMemoryManager, vector_store: InMemoryVectorStore, config: MemoryConfig):
        self.sqlite_mgr = sqlite_mgr
        self.vector_store = vector_store
        self.config = config

    def add_memory(
        self,
        item: MemoryItem,
        *,
        event_ids: Sequence[str] = (),
        authority: ProvenanceAuthority = ProvenanceAuthority.UNKNOWN,
        change_kind: ChangeKind = ChangeKind.ADD,
    ) -> str:
        """DEPRECATED Phase 1A MemoryItem facade adapter。"""
        self.sqlite_mgr.apply_add(item, event_ids=event_ids, authority=authority, change_kind=change_kind)
        if item.retention_disposition is RetentionDisposition.ACTIVE:
            self.vector_store.add_or_update(item.memory_id, item.content)
        return item.memory_id

    def add_canonical(
        self,
        *,
        content: str,
        memory_type: MemoryType,
        granularity: Granularity,
        memory_id: Optional[str] = None,
        importance_score: float = 0.5,
        confidence: float = 0.8,
        semantic_metadata: Optional[SemanticMetadata] = None,
        event_ids: Sequence[str] = (),
        authority: ProvenanceAuthority = ProvenanceAuthority.UNKNOWN,
        change_kind: ChangeKind = ChangeKind.ADD,
    ) -> str:
        memory_id = memory_id or new_memory_id()
        self.sqlite_mgr.create_memory(
            memory_id=memory_id,
            content=content,
            memory_type=memory_type,
            granularity=granularity,
            importance_score=importance_score,
            confidence=confidence,
            semantic_metadata=semantic_metadata,
            enable_time_decay=self.config.enable_forgetting,
            event_ids=event_ids,
            authority=authority,
            change_kind=change_kind,
        )
        self.vector_store.add_or_update(memory_id, content)
        return memory_id

    def update_memory(
        self,
        existing_id: str,
        new_supplement_content: str,
        *,
        event_ids: Sequence[str] = (),
        authority: ProvenanceAuthority = ProvenanceAuthority.UNKNOWN,
    ) -> Optional[MemoryItem]:
        item = self.sqlite_mgr.get_memory(existing_id)
        if not item or item.retention_disposition is not RetentionDisposition.ACTIVE:
            return None
        if item.current_revision is None:
            return None
        merged_content = new_supplement_content.strip()
        self.sqlite_mgr.apply_revision(
            existing_id, merged_content, change_kind=ChangeKind.UPDATE,
            event_ids=event_ids, authority=authority,
        )
        updated = self.sqlite_mgr.get_memory(existing_id)
        if updated:
            assert updated.current_revision is not None
            self.vector_store.add_or_update(existing_id, updated.current_revision.content)
        return updated

    def correct_memory(
        self,
        memory_id: str,
        new_correct_content: str,
        *,
        event_ids: Sequence[str] = (),
        authority: ProvenanceAuthority = ProvenanceAuthority.EXPLICIT_USER,
    ) -> MemoryItem:
        if not self.sqlite_mgr.get_memory(memory_id):
            raise MemoryNotFoundError(memory_id)
        self.sqlite_mgr.apply_revision(
            memory_id, new_correct_content, change_kind=ChangeKind.CORRECT,
            event_ids=event_ids, authority=authority,
        )
        corrected = self.sqlite_mgr.get_memory(memory_id)
        assert corrected is not None
        assert corrected.current_revision is not None
        if self.sqlite_mgr.get_current(memory_id) is not None:
            self.vector_store.add_or_update(memory_id, corrected.current_revision.content)
        else:
            self.vector_store.delete(memory_id)
        return corrected

    def rise_abstract_memory(
        self, fine_grained_ids: List[str], abstract_content: str, memory_type: MemoryType
    ) -> MemoryItem:
        abstract_id = self.add_canonical(
            content=abstract_content,
            memory_type=memory_type,
            granularity=Granularity.HIGH_ABSTRACT,
            importance_score=0.9,
            confidence=0.95,
            change_kind=ChangeKind.RISE,
        )
        for fine_id in fine_grained_ids:
            fine = self.sqlite_mgr.get_memory(fine_id)
            if fine:
                relation = GraphRelation(abstract_id, RelationType.DERIVED_TO, 1.0)
                if relation not in fine.graph_relations:
                    fine.graph_relations.append(relation)
                    self.sqlite_mgr.save_operational_state(fine)
        abstract = self.sqlite_mgr.get_memory(abstract_id)
        assert abstract is not None
        return abstract

    merge_abstract_memory = rise_abstract_memory

    def promote_memory(self, memory_id: str, boost_score: float = 0.1) -> None:
        item = self.sqlite_mgr.get_memory(memory_id)
        if item and item.retention_disposition is RetentionDisposition.ACTIVE:
            item.importance_score = min(1.0, item.importance_score + boost_score)
            item.access_count += 1
            item.last_accessed = time.time()
            self.sqlite_mgr.save_operational_state(item)

    def demote_memory(self, memory_id: str, penalty_score: float = 0.15) -> None:
        item = self.sqlite_mgr.get_memory(memory_id)
        if item and item.retention_disposition is RetentionDisposition.ACTIVE:
            item.importance_score = max(0.0, item.importance_score - penalty_score)
            item.decay_strength = max(0.0, item.decay_strength - penalty_score)
            self.sqlite_mgr.save_operational_state(item)

    def soft_delete_to_recycle_bin(self, memory_id: str, reason: str = "DECAY_OR_USER") -> bool:
        changed = self.sqlite_mgr.set_retention(memory_id, RetentionDisposition.RECYCLED)
        if changed:
            self.vector_store.delete(memory_id)
        return changed

    def archive_memory(self, memory_id: str) -> bool:
        changed = self.sqlite_mgr.set_retention(memory_id, RetentionDisposition.ARCHIVED)
        if changed:
            self.vector_store.delete(memory_id)
        return changed

    def restore_from_recycle_bin(self, memory_id: str) -> Optional[MemoryItem]:
        item = self.sqlite_mgr.get_memory(memory_id)
        if not item or item.retention_disposition is not RetentionDisposition.RECYCLED:
            return None
        self.sqlite_mgr.set_retention(memory_id, RetentionDisposition.ACTIVE)
        restored = self.sqlite_mgr.get_memory(memory_id)
        if restored:
            restored.decay_strength = self.config.s0_initial_strength
            restored.last_accessed = time.time()
            self.sqlite_mgr.save_operational_state(restored)
            if restored.current_revision:
                self.vector_store.add_or_update(memory_id, restored.current_revision.content)
        return restored
