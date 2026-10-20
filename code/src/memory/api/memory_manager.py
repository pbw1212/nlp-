from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Any, Dict, List, Optional, Sequence

from src.memory.core.config import MemoryConfig
from src.memory.core.errors import InvalidLifecycleTransitionError, MemoryNotFoundError
from src.memory.core.schema import (
    ActiveStatus,
    IngestionState,
    LifecycleOperation,
    LifecycleProposal,
    MemoryItem,
    MemoryRevision,
    MemorySnapshot,
    MemoryType,
    Granularity,
    ProvenanceAuthority,
)
from src.memory.lifecycle.consolidation import ConsolidationEngine
from src.memory.lifecycle.crud_engine import CRUDEngine
from src.memory.lifecycle.nemori_distiller import NemoriDistiller
from src.memory.llm.llm_client import LLMClientBase, build_llm_client
from src.memory.router.memory_router import MemoryRouter
from src.memory.storage.sqlite_manager import SQLiteMemoryManager
from src.memory.storage.vector_store import InMemoryVectorStore


class LongTermMemoryManager:
    """Task 1 canonical lifecycle API；Task 2/3 不在此模块实现。"""

    def __init__(
        self,
        db_path: str = "memory_store.db",
        llm_client: Optional[LLMClientBase] = None,
        config: Optional[MemoryConfig] = None,
    ):
        self.config = config or MemoryConfig(sqlite_db_path=db_path)
        self.llm_client = llm_client or build_llm_client(self.config)
        self.sqlite_mgr = SQLiteMemoryManager(
            self.config.sqlite_db_path,
            provenance_excerpt_max_chars=self.config.provenance_excerpt_max_chars,
        )
        self.vector_store = InMemoryVectorStore()
        self.router = MemoryRouter()
        for snapshot in self.sqlite_mgr.list_current():
            self.vector_store.add_or_update(snapshot.memory_id, snapshot.revision.content)
        self.crud = CRUDEngine(self.sqlite_mgr, self.vector_store, self.config)
        self.distiller = NemoriDistiller(self.llm_client, self.config)
        self.consolidation = ConsolidationEngine(
            self.sqlite_mgr, self.crud, self.llm_client, self.config
        )

    def _refresh_derived_index(self) -> None:
        """Rebuild the compatibility index from current-safe SQLite snapshots."""
        self.vector_store.vectors.clear()
        for snapshot in self.sqlite_mgr.list_current():
            self.vector_store.add_or_update(snapshot.memory_id, snapshot.revision.content)

    @property
    def pending_dialogue_buffer(self) -> List[Dict[str, str]]:
        """Compatibility projection；durable source of truth 是 ingestion_events。"""
        turns: List[Dict[str, str]] = []
        for event in self.sqlite_mgr.list_pending_events():
            if event.raw_text:
                turns.append({"role": "user", "content": event.raw_text})
            if event.assistant_text:
                turns.append({"role": "assistant", "content": event.assistant_text})
        return turns

    def process_user_interaction(
        self,
        user_input: str,
        is_explicit: bool = False,
        *,
        source_event_id: Optional[str] = None,
        session_id: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        # Semantic routing must be decided by the Provider-facing caller. The
        # storage manager accepts only the typed explicit flag and never
        # infers memory intent from local keyword rules.
        if is_explicit:
            memory_id, replay, changed = self._process_explicit_with_result(
                user_input,
                source_event_id=source_event_id,
                session_id=session_id,
                message_id=message_id,
            )
            snapshot = self.sqlite_mgr.get_memory(memory_id)
            return {
                "route": "FAST_PATH",
                "action": "IDEMPOTENT_REPLAY" if replay else "ADD_FAST_PATH",
                "memory_id": memory_id,
                "updated": changed,
                "retention": snapshot.retention_disposition.value if snapshot else None,
                "idempotent_replay": replay,
            }
        result = self.record_foreground_interaction(
            user_input,
            "",
            source_event_id=source_event_id,
            session_id=session_id,
            message_id=message_id,
        )
        action = "UPDATED" if result.get("updated") else "BUFFERED_FOR_UPDATE"
        if result["idempotent_replay"]:
            action = "IDEMPOTENT_REPLAY"
        return {
            "route": "SLOW_PATH",
            "action": action,
            "memory_id": None,
            "updated": result.get("updated", False),
            "idempotent_replay": result["idempotent_replay"],
            "event_id": result["event_id"],
        }

    def _process_explicit_with_result(
        self,
        text: str,
        *,
        source_event_id: Optional[str],
        session_id: Optional[str],
        message_id: Optional[str],
        authority: ProvenanceAuthority = ProvenanceAuthority.EXPLICIT_USER,
    ) -> tuple[str, bool, bool]:
        event, replay = self.sqlite_mgr.register_ingestion_event(
            text, event_id=source_event_id, session_id=session_id, message_id=message_id
        )
        if replay and event.state is IngestionState.APPLIED:
            result = event.result_metadata or {}
            memory_id = result.get("memory_id")
            if not isinstance(memory_id, str):
                # Backward compatibility for NOOP events written before the
                # matched identity was included in result metadata.
                if result.get("operation") == LifecycleOperation.NOOP.value:
                    return "noop", True, False
                raise InvalidLifecycleTransitionError("APPLIED explicit event 缺少 memory_id result")
            return memory_id, True, False
        current_snapshots = self.sqlite_mgr.list_current()
        formatted_memories = [
            {"memory_id": snap.memory_id, "content": snap.revision.content}
            for snap in current_snapshots
        ]

        # 1. Canonical Provider/Proposer Work outside DB transaction
        proposal = self.distiller.propose_explicit_lifecycle(
            formatted_memories, text, provenance_event_id=event.event_id
        )

        # 2. Validate proposal shape & target identity
        self._validate_proposals([proposal])

        # 3. Single Atomic Apply inside DB transaction with EXPLICIT_USER authority
        if proposal.operation is LifecycleOperation.NOOP:
            matched_id = proposal.target_memory_id or "noop"
            self.sqlite_mgr.apply_noop(
                [event.event_id],
                result_metadata={"memory_id": matched_id},
            )
            return matched_id, False, False

        results = self.sqlite_mgr.apply_proposals_atomic(
            [proposal],
            [event.event_id],
            consolidation_run_id=f"explicit_{event.event_id}",
            authority=authority,
            enable_time_decay=self.config.enable_forgetting,
        )

        if len(results) != 1 or not results[0][0]:
            raise InvalidLifecycleTransitionError(
                "explicit lifecycle apply returned no durable memory identity"
            )
        self._refresh_derived_index()
        return results[0][0], False, True

    def process_explicit_instruction(
        self,
        instruction_text: str,
        *,
        source_event_id: Optional[str] = None,
        session_id: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> str:
        return self._process_explicit_with_result(
            instruction_text,
            source_event_id=source_event_id,
            session_id=session_id,
            message_id=message_id,
        )[0]

    def record_foreground_interaction(
        self,
        user_input: str,
        assistant_reply: str,
        *,
        source_event_id: Optional[str] = None,
        session_id: Optional[str] = None,
        message_id: Optional[str] = None,
        defer_consolidation: bool = False,
    ) -> Dict[str, Any]:
        """Persist one foreground event in the retryable ingestion journal.

        This low-level compatibility API deliberately does not decide when to
        run semantic memory processing.  Product turns use
        ``update_long_term_memory_for_turn`` through the Agent tool; callers
        that use this journal API can explicitly invoke the dream pipeline.
        ``defer_consolidation`` is retained for source compatibility and now
        documents the same explicit-pipeline boundary rather than a trigger.
        """
        event, replay = self.sqlite_mgr.register_ingestion_event(
            user_input,
            assistant_text=assistant_reply,
            event_id=source_event_id,
            session_id=session_id,
            message_id=message_id,
        )
        if replay:
            return {
                "buffered_turns": len(self.sqlite_mgr.list_pending_events()),
                "updated": event.state is IngestionState.APPLIED,
                "idempotent_replay": True,
                "event_id": event.event_id,
            }
        return {
            "buffered_turns": len(self.sqlite_mgr.list_pending_events()),
            "updated": False,
            "idempotent_replay": False,
            "event_id": event.event_id,
            "pipeline_deferred": True,
        }

    def update_long_term_memory_for_turn(
        self,
        user_input: str,
        *,
        source_event_id: str,
        session_id: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Immediately evaluate the exact current user turn for durable memory.

        This is the application-owned implementation behind the Agent tool.
        It never accepts a model-authored memory summary, so the existing
        Provider/NEMORI lifecycle proposal remains the only semantic decision
        point and the original user event remains the provenance source.
        """
        event, replay = self.sqlite_mgr.register_ingestion_event(
            user_input,
            event_id=source_event_id,
            session_id=session_id,
            message_id=message_id,
        )
        if replay and event.state is IngestionState.APPLIED:
            metadata = event.result_metadata or {}
            lifecycle_changes = (
                len(metadata.get("results", []))
                if isinstance(metadata.get("results"), list)
                else 0
            )
            return {
                "event_id": event.event_id,
                "processed": True,
                "updated": lifecycle_changes > 0,
                "idempotent_replay": True,
                "lifecycle_changes": lifecycle_changes,
                "result_metadata": metadata,
            }

        result = self.run_autonomous_dream_pipeline()
        return {
            "event_id": event.event_id,
            "processed": True,
            "updated": bool(result.get("lifecycle_changes", 0)),
            "idempotent_replay": False,
            "pending_events": len(self.sqlite_mgr.list_pending_events()),
            **result,
        }

    @staticmethod
    def _consolidation_run_id(event_ids: Sequence[str]) -> str:
        digest = hashlib.sha256("\x00".join(event_ids).encode("utf-8")).hexdigest()
        return f"run_{digest}"

    def run_autonomous_dream_pipeline(self, *, failure_hook: Optional[Any] = None) -> Dict[str, Any]:
        events = self.sqlite_mgr.list_pending_events()
        if not events:
            return {"episodes_processed": 0, "new_memories_created": 0, "consolidation_run_id": None}
        event_ids = [event.event_id for event in events]
        run_id = self._consolidation_run_id(event_ids)
        dialogue: List[Dict[str, str]] = []
        dialogue_event_ids: List[str] = []
        for event in events:
            if event.raw_text:
                dialogue.append({"role": "user", "content": event.raw_text})
                dialogue_event_ids.append(event.event_id)
            # Long-term user memory is grounded in what the user said. The
            # assistant response remains durable in the Conversation
            # transcript, but must not become a source of user facts merely
            # because it acknowledged, suggested, or summarized something.

        # Provider work and complete validation happen outside the write transaction.
        episodes = self.distiller.partition_dialogue(dialogue)
        durable_episodes = [
            episode for episode in episodes if episode.get("durability") == "DURABLE"
        ]
        proposals: List[LifecycleProposal] = []
        current_snapshots = self.sqlite_mgr.list_current()
        current_memories = []
        for snapshot in current_snapshots:
            if snapshot.revision is None:
                continue
            authorities = sorted({ref.authority.value for ref in snapshot.provenance})
            current_memories.append({
                "memory_id": snapshot.memory_id,
                "content": snapshot.revision.content,
                "memory_type": snapshot.memory_type.value,
                "confidence": snapshot.revision.confidence,
                "is_pinned": snapshot.is_pinned,
                "source_authorities": authorities,
            })
        # Keep a provider-facing working set while the batch is being
        # proposed. Persistence is still one transaction, but later episodes
        # must see earlier UPDATE/CORRECT proposals instead of the stale
        # pre-batch snapshot.
        working_memories = [dict(memory) for memory in current_memories]
        for episode in durable_episodes:
            narrative = episode.get("narrative", "")
            if not isinstance(narrative, str) or not narrative.strip():
                continue
            proposal = self.distiller.propose_inferred_lifecycle(
                working_memories,
                narrative,
                provenance_event_id=dialogue_event_ids[episode["message_indices"][0] - 1],
            )
            # Only episode source events support this proposal. All batch
            # events are acknowledged atomically, including transient ones.
            proposal = replace(
                proposal,
                provenance_event_ids=tuple(dict.fromkeys(
                    dialogue_event_ids[index - 1] for index in episode["message_indices"]
                )),
            )
            if proposal.operation is LifecycleOperation.REMOVE and proposal.target_memory_id:
                target_memory = next(
                    (
                        memory for memory in working_memories
                        if memory.get("memory_id") == proposal.target_memory_id
                    ),
                    None,
                )
                if target_memory is not None and (
                    target_memory.get("is_pinned") is True
                    or target_memory.get("memory_type") not in {
                        MemoryType.CORE_PROFILE.value,
                        MemoryType.PREFERENCE_RULES.value,
                    }
                ):
                    proposal = replace(
                        proposal,
                        operation=LifecycleOperation.NOOP,
                        content=None,
                        importance_score=None,
                    )
            proposals.append(proposal)
            if proposal.target_memory_id and proposal.operation in {
                LifecycleOperation.UPDATE,
                LifecycleOperation.CORRECT,
            }:
                for memory in working_memories:
                    if memory.get("memory_id") == proposal.target_memory_id:
                        memory["content"] = proposal.content or memory.get("content", "")
                        break
            elif proposal.target_memory_id and proposal.operation is LifecycleOperation.REMOVE:
                working_memories = [
                    memory for memory in working_memories
                    if memory.get("memory_id") != proposal.target_memory_id
                ]
        if not proposals:
            proposals = [LifecycleProposal(operation=LifecycleOperation.NOOP)]
        self._validate_proposals(proposals)
        results = self.sqlite_mgr.apply_proposals_atomic(
            proposals,
            event_ids,
            consolidation_run_id=run_id,
            failure_hook=failure_hook,
            enable_time_decay=self.config.enable_forgetting,
        )
        self._refresh_derived_index()
        return {
            "episodes_processed": len(durable_episodes),
            "new_memories_created": sum(
                1 for proposal in proposals if proposal.operation is LifecycleOperation.ADD
            ),
            "memories_removed": sum(
                1 for proposal in proposals if proposal.operation is LifecycleOperation.REMOVE
            ),
            "lifecycle_changes": len(results),
            "consolidation_run_id": run_id,
        }

    def _validate_proposals(self, proposals: Sequence[LifecycleProposal]) -> None:
        target_operations: Dict[str, List[LifecycleOperation]] = {}
        for proposal in proposals:
            proposal.validate_shape()
            if proposal.operation in {
                LifecycleOperation.UPDATE,
                LifecycleOperation.CORRECT,
                LifecycleOperation.REMOVE,
            }:
                if not self.sqlite_mgr.get_memory(proposal.target_memory_id or ""):
                    raise MemoryNotFoundError(proposal.target_memory_id or "")
            if proposal.operation is LifecycleOperation.NOOP and proposal.target_memory_id:
                if not self.sqlite_mgr.get_memory(proposal.target_memory_id):
                    raise MemoryNotFoundError(proposal.target_memory_id)
            for event_id in proposal.provenance_event_ids:
                if self.sqlite_mgr.get_ingestion_event(event_id) is None:
                    raise InvalidLifecycleTransitionError(f"proposal provenance event 不存在: {event_id}")
            if proposal.target_memory_id:
                target_operations.setdefault(proposal.target_memory_id, []).append(proposal.operation)

        mutating = {
            LifecycleOperation.UPDATE,
            LifecycleOperation.CORRECT,
            LifecycleOperation.REMOVE,
        }
        for target_id, operations in target_operations.items():
            if len(operations) > 1 and any(operation in mutating for operation in operations):
                raise InvalidLifecycleTransitionError(
                    f"同一自动记忆批次对 memory_id={target_id} 提出了多个互相竞争的生命周期操作"
                )

    def add_memory_item(self, item: MemoryItem) -> str:
        """DEPRECATED Phase 1A facade adapter；新代码应提交 typed lifecycle input。"""
        event_id = item.metadata.source_event_id or None
        event, replay = self.sqlite_mgr.register_ingestion_event(item.content, event_id=event_id)
        if replay and event.state is IngestionState.APPLIED:
            memory_id = (event.result_metadata or {}).get("memory_id")
            if not isinstance(memory_id, str) or not memory_id.strip():
                raise InvalidLifecycleTransitionError(
                    "APPLIED add-memory event 缺少 memory_id result"
                )
            return memory_id
        return self.crud.add_memory(item, event_ids=[event.event_id])

    def update_memory_item(
        self, memory_id: str, supplement_content: str, *, source_event_id: Optional[str] = None
    ) -> Optional[MemoryItem]:
        event, replay = self.sqlite_mgr.register_ingestion_event(
            supplement_content, event_id=source_event_id
        )
        if replay and event.state is IngestionState.APPLIED:
            return self.sqlite_mgr.get_memory(memory_id)
        return self.crud.update_memory(
            memory_id,
            supplement_content,
            event_ids=[event.event_id],
            authority=ProvenanceAuthority.EXPLICIT_USER,
        )

    def correct_memory_item(
        self, memory_id: str, corrected_content: str, *, source_event_id: Optional[str] = None
    ) -> MemoryItem:
        event, replay = self.sqlite_mgr.register_ingestion_event(
            corrected_content, event_id=source_event_id
        )
        if replay and event.state is IngestionState.APPLIED:
            existing = self.sqlite_mgr.get_memory(memory_id)
            if not existing:
                raise MemoryNotFoundError(memory_id)
            return existing
        return self.crud.correct_memory(
            memory_id, corrected_content, event_ids=[event.event_id],
            authority=ProvenanceAuthority.EXPLICIT_USER,
        )

    def rise_abstract_memories(
        self, fine_ids: List[str], abstract_content: str, memory_type: MemoryType
    ) -> MemoryItem:
        return self.crud.rise_abstract_memory(fine_ids, abstract_content, memory_type)

    def merge_abstract_memories(
        self, fine_ids: List[str], abstract_content: str, memory_type: MemoryType
    ) -> MemoryItem:
        """Compatibility：仍表示 RISE 新 identity；cross-identity merge deferred。"""
        return self.rise_abstract_memories(fine_ids, abstract_content, memory_type)

    def soft_delete_memory(self, memory_id: str, reason: str = "USER_DELETE") -> bool:
        return self.crud.soft_delete_to_recycle_bin(memory_id, reason)

    def archive_memory(self, memory_id: str) -> bool:
        return self.crud.archive_memory(memory_id)

    def restore_memory_from_recycle_bin(self, memory_id: str) -> Optional[MemoryItem]:
        return self.crud.restore_from_recycle_bin(memory_id)

    def get_active_memories(self) -> List[MemoryItem]:
        return self.sqlite_mgr.list_memories(ActiveStatus.ACTIVE)

    def get_recycle_bin_memories(self) -> List[MemoryItem]:
        return self.sqlite_mgr.list_memories(ActiveStatus.IN_RECYCLE_BIN)

    def get_current(self, memory_id: str) -> Optional[MemorySnapshot]:
        return self.sqlite_mgr.get_current(memory_id)

    def list_current(self) -> List[MemorySnapshot]:
        return self.sqlite_mgr.list_current()

    def get_history(self, memory_id: str) -> List[MemoryRevision]:
        return self.sqlite_mgr.get_history(memory_id)

    def get_revision_provenance(self, revision_id: str):
        """Return provenance for one revision through the canonical read boundary."""
        return self.sqlite_mgr.get_revision_provenance(revision_id)

    def pin_memory(self, memory_id: str, is_pinned: bool = True) -> None:
        item = self.sqlite_mgr.get_memory(memory_id)
        if item:
            item.is_pinned = is_pinned
            self.sqlite_mgr.save_operational_state(item)

    def set_time_decay_switch(self, memory_id: str, enable_time_decay: bool = True) -> None:
        item = self.sqlite_mgr.get_memory(memory_id)
        if item:
            item.enable_time_decay = enable_time_decay
            self.sqlite_mgr.save_operational_state(item)
