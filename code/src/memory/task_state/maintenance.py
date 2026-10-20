from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional, Sequence

from src.memory.task_state.context import TaskContextBuilder
from src.memory.task_state.models import (
    ContinuationCheckpoint,
    EntryKind,
    EntryStatus,
    EvidenceRef,
    Priority,
    TaskEvent,
    TaskStateUpdateReceipt,
)
from src.memory.task_state.repository import TaskStateRepository
from src.memory.core.diagnostics import log_boundary_failure


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CheckpointRefreshPolicy:
    version: str = "checkpoint-refresh-v1"
    meaningful_event_threshold: int = 3
    max_goal_refs: int = 3
    max_constraint_refs: int = 8
    max_question_refs: int = 8
    max_decision_refs: int = 8

    def __post_init__(self) -> None:
        if self.meaningful_event_threshold <= 0:
            raise ValueError("meaningful_event_threshold must be positive")


@dataclass(frozen=True)
class MaintenanceResult:
    checkpoint: Optional[ContinuationCheckpoint]
    warnings: tuple[str, ...] = ()


class TaskContextMaintenanceService:
    """Refreshes derived continuation pointers after durable ledger updates."""

    def __init__(
        self,
        repository: TaskStateRepository,
        *,
        context_builder: Optional[TaskContextBuilder] = None,
        policy: Optional[CheckpointRefreshPolicy] = None,
    ) -> None:
        self.repository = repository
        self.context_builder = context_builder or TaskContextBuilder(repository)
        self.policy = policy or CheckpointRefreshPolicy()

    def should_refresh(
        self,
        event: TaskEvent,
        receipt: TaskStateUpdateReceipt,
        *,
        current_step: Optional[str] = None,
    ) -> bool:
        if receipt.status.value != "APPLIED":
            return False
        if receipt.applied_entry_ids or receipt.superseded_entry_ids or receipt.resolved_entry_ids or receipt.relation_keys:
            return True
        payload = event.structured_payload
        if payload.get("checkpoint_requested") or payload.get("pause") or payload.get("suspend"):
            return True
        previous = self.repository.get_latest_checkpoint(event.task_id)
        if previous is None:
            return False
        if current_step is not None and current_step != previous.current_step:
            return True
        meaningful_events = sum(
            1 for item in self.repository.get_recent_events(event.task_id, self.policy.meaningful_event_threshold)
            if item.event_id != event.event_id
        )
        return meaningful_events >= self.policy.meaningful_event_threshold

    def refresh_after_update(
        self,
        event: TaskEvent,
        receipt: TaskStateUpdateReceipt,
        *,
        current_step: Optional[str] = None,
        focus: Optional[str] = None,
        next_action: Optional[str] = None,
        world_state: Optional[str] = None,
        continuation_note: Optional[str] = None,
        evidence_refs: Sequence[EvidenceRef] = (),
    ) -> MaintenanceResult:
        if not self.should_refresh(event, receipt, current_step=current_step):
            return MaintenanceResult(None)
        try:
            checkpoint = self.rebuild_checkpoint(
                event.task_id,
                last_processed_event=event.event_id,
                current_step=current_step,
                focus=focus,
                next_action=next_action,
                world_state=world_state,
                continuation_note=continuation_note,
                evidence_refs=evidence_refs,
            )
            return MaintenanceResult(checkpoint)
        except Exception as exc:  # canonical ledger remains committed
            log_boundary_failure(
                logger,
                "checkpoint_refresh_failed",
                exc,
                task_id=event.task_id,
                event_id=event.event_id,
            )
            return MaintenanceResult(None, (f"checkpoint_refresh_failed:{type(exc).__name__}",))

    def rebuild_checkpoint(
        self,
        task_id: str,
        *,
        last_processed_event: Optional[str] = None,
        current_step: Optional[str] = None,
        focus: Optional[str] = None,
        next_action: Optional[str] = None,
        world_state: Optional[str] = None,
        continuation_note: Optional[str] = None,
        previous: Optional[ContinuationCheckpoint] = None,
        evidence_refs: Sequence[EvidenceRef] = (),
    ) -> ContinuationCheckpoint:
        ledger = self.repository.get_ledger(task_id)
        previous = previous or self.repository.get_latest_checkpoint(task_id)
        active = [entry for entry in ledger.entries if entry.status is EntryStatus.ACTIVE]
        goals = sorted((entry for entry in active if entry.kind is EntryKind.GOAL), key=self._entry_key)
        constraints = sorted(
            (entry for entry in active if entry.kind is EntryKind.CONSTRAINT and entry.priority >= Priority.HIGH),
            key=self._entry_key,
        )
        questions = sorted((entry for entry in active if entry.kind is EntryKind.OPEN_QUESTION), key=self._entry_key)
        decisions = sorted((entry for entry in active if entry.kind is EntryKind.DECISION), key=self._entry_key)
        refs = tuple(dict.fromkeys(ref for entry in active for ref in entry.evidence_refs))
        refs = tuple(dict.fromkeys((*refs, *evidence_refs)))
        fallback_focus = current_step
        if fallback_focus is None and goals:
            fallback_focus = goals[0].content
        fallback_next_action = next_action
        if fallback_next_action is None and questions:
            fallback_next_action = questions[0].content
        checkpoint = ContinuationCheckpoint(
            task_id=task_id,
            checkpoint_id=f"checkpoint:{task_id}:{last_processed_event or 'repair'}:{time.time_ns()}",
            last_processed_event=last_processed_event or (previous.last_processed_event if previous else None),
            goal_entry_ids=tuple(entry.entry_id for entry in goals[: self.policy.max_goal_refs]),
            current_step=current_step if current_step is not None else (previous.current_step if previous else None),
            focus=focus if focus is not None else (previous.focus if previous else fallback_focus),
            next_action=next_action if next_action is not None else (previous.next_action if previous else fallback_next_action),
            world_state=world_state if world_state is not None else (previous.world_state if previous else None),
            continuation_note=continuation_note if continuation_note is not None else (previous.continuation_note if previous else ""),
            critical_constraint_ids=tuple(entry.entry_id for entry in constraints[: self.policy.max_constraint_refs]),
            open_question_ids=tuple(entry.entry_id for entry in questions[: self.policy.max_question_refs]),
            decision_ids=tuple(entry.entry_id for entry in decisions[: self.policy.max_decision_refs]),
            evidence_refs=refs,
        )
        return self.repository.write_checkpoint(checkpoint)

    def build_context(self, task_id: str, **kwargs):
        return self.context_builder.build(task_id, **kwargs)

    def process_event(self, updater, event: TaskEvent, **kwargs):
        """Apply a Phase3B update, then refresh derived continuation state."""
        receipt = updater.process_event(event)
        maintenance = self.refresh_after_update(event, receipt, **kwargs)
        return receipt, maintenance

    @staticmethod
    def _entry_key(entry):
        return (-int(entry.priority), -entry.updated_at, entry.entry_id)
