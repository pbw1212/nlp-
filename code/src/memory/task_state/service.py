from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from src.memory.retrieval.evidence import EvidenceBundle
from src.memory.task_state.context import TaskContextBuilder
from src.memory.task_state.maintenance import MaintenanceResult, TaskContextMaintenanceService
from src.memory.task_state.models import (
    ActiveWorkingContext,
    ExternalObservation,
    TaskEvent,
    TaskLedger,
    TaskStateProposalBatch,
    TaskStateUpdateReceipt,
    TaskStatus,
    EventProcessStatus,
    TaskAnalysis,
    TaskUpdateTrace,
    ProposalAction,
    ProposalBasis,
    SourceKind,
    TaskStateProposal,
)
from src.memory.task_state.repository import TaskStateRepository
from src.memory.task_state.updater import TaskStateInterpreter, TaskStateUpdater


class TaskLifecycleError(RuntimeError):
    def __init__(self, task_id: str = "", status: str = "", message: str = "") -> None:
        self.task_id = task_id
        self.status = status
        msg = message or (f"Task '{task_id}' is in non-ACTIVE status '{status}'; operation rejected." if task_id else "Task lifecycle error")
        super().__init__(msg)


@dataclass(frozen=True)
class TaskServiceResult:
    receipt: TaskStateUpdateReceipt
    maintenance: MaintenanceResult
    analysis: Optional[TaskAnalysis] = None
    trace: Optional[TaskUpdateTrace] = None


class TaskStateService:
    """Thin public Task3 facade; domain rules remain in repository/updater/policy."""

    def __init__(self, repository: TaskStateRepository, *, context_builder: Optional[TaskContextBuilder] = None) -> None:
        self.repository = repository
        self.context_builder = context_builder or TaskContextBuilder(repository)
        self.maintenance = TaskContextMaintenanceService(repository, context_builder=self.context_builder)
        self.last_maintenance = MaintenanceResult(None)
        self.last_trace: Optional[TaskUpdateTrace] = None

    def create_task(self, task_id: str) -> TaskLedger:
        return self.repository.create_task(task_id)

    def process_event_result(self, event: TaskEvent, *, interpreter: TaskStateInterpreter) -> TaskServiceResult:
        self._require_active(event.task_id)
        updater = TaskStateUpdater(self.repository, interpreter, context_builder=self.context_builder)
        try:
            receipt = updater.process_event(event)
        finally:
            self.last_trace = updater.last_trace
        m_result = self.maintenance.refresh_after_update(event, receipt)
        self.last_maintenance = m_result
        return TaskServiceResult(
            receipt=receipt,
            maintenance=m_result,
            analysis=updater.last_analysis,
            trace=updater.last_trace,
        )

    def process_event(self, event: TaskEvent, *, interpreter: TaskStateInterpreter) -> TaskStateUpdateReceipt:
        return self.process_event_result(event, interpreter=interpreter).receipt

    def apply_proposals_result(self, event: TaskEvent, batch: TaskStateProposalBatch) -> TaskServiceResult:
        self._require_active(event.task_id)
        self.repository.register_event(event)
        updater = TaskStateUpdater(self.repository, _TypedBatchInterpreter(batch), context_builder=self.context_builder)
        receipt = updater.apply(event, batch)
        self.last_trace = updater.last_trace
        m_result = self.maintenance.refresh_after_update(event, receipt)
        self.last_maintenance = m_result
        return TaskServiceResult(
            receipt=receipt,
            maintenance=m_result,
            analysis=batch.analysis,
            trace=updater.last_trace,
        )

    def apply_proposals(self, event: TaskEvent, batch: TaskStateProposalBatch) -> TaskStateUpdateReceipt:
        return self.apply_proposals_result(event, batch).receipt

    def build_context(
        self,
        task_id: str,
        *,
        current_turn: str = "",
        evidence_bundle: Optional[EvidenceBundle] = None,
        external_observations: Sequence[ExternalObservation] = (),
    ) -> ActiveWorkingContext:
        return self.context_builder.build(
            task_id,
            current_turn=current_turn,
            evidence_bundle=evidence_bundle,
            external_observations=external_observations,
        )

    def pause_task(
        self,
        task_id: str,
        *,
        current_step: Optional[str] = None,
        focus: Optional[str] = None,
        next_action: Optional[str] = None,
        world_state: Optional[str] = None,
        note: Optional[str] = None,
    ) -> TaskLedger:
        self._require_status(task_id, TaskStatus.ACTIVE)
        self.maintenance.rebuild_checkpoint(
            task_id,
            current_step=current_step,
            focus=focus,
            next_action=next_action,
            world_state=world_state,
            continuation_note=note,
        )
        return self.repository.set_task_status(task_id, TaskStatus.PAUSED)

    def resume_task(self, task_id: str) -> TaskLedger:
        self._require_status(task_id, TaskStatus.PAUSED)
        ledger = self.repository.set_task_status(task_id, TaskStatus.ACTIVE)
        self.maintenance.rebuild_checkpoint(task_id)
        return ledger

    def complete_task(self, task_id: str) -> TaskLedger:
        self._require_not_terminal(task_id)
        return self.repository.set_task_status(task_id, TaskStatus.COMPLETED)

    def cancel_task(self, task_id: str) -> TaskLedger:
        self._require_not_terminal(task_id)
        return self.repository.set_task_status(task_id, TaskStatus.CANCELLED)

    def get_task(self, task_id: str) -> TaskLedger:
        return self.repository.get_ledger(task_id)

    def list_active_tasks(self) -> tuple[TaskLedger, ...]:
        return self.repository.list_active_tasks()

    def get_last_trace(self) -> Optional[TaskUpdateTrace]:
        """Return the latest safe in-process Task 3 audit record."""
        return self.last_trace

    def confirm_pending_supersede(
        self,
        event: TaskEvent,
        *,
        target_entry_id: str,
        replacement_content: str,
    ) -> TaskServiceResult:
        """Apply an explicit user-confirmed replacement for a pending event."""
        trace = self.last_trace
        pending = trace is not None and trace.confirmation_required and trace.event_id == event.event_id
        if not pending:
            receipt = self.repository.get_event_receipt(event.event_id)
            pending = receipt is not None and receipt.status is EventProcessStatus.PENDING_CONFIRMATION
        if not pending:
            raise TaskLifecycleError(
                task_id=event.task_id,
                message="no pending SUPERSEDE confirmation exists for this event",
            )
        if event.source_kind is not SourceKind.USER:
            raise TaskLifecycleError(
                task_id=event.task_id,
                message="SUPERSEDE confirmation must come from the user",
            )
        if not target_entry_id or not replacement_content.strip():
            raise ValueError("target_entry_id and replacement_content are required")
        proposal = TaskStateProposal(
            proposal_id=f"confirm:{event.event_id}",
            task_id=event.task_id,
            source_event_id=event.event_id,
            action=ProposalAction.SUPERSEDE,
            target_entry_id=target_entry_id,
            replacement_content=replacement_content.strip(),
            basis=ProposalBasis.EXPLICIT,
            warnings=("user_confirmed_supersede",),
        )
        batch = TaskStateProposalBatch(
            task_id=event.task_id,
            event_id=event.event_id,
            proposals=(proposal,),
            interpreter="explicit-user-confirmation",
            interpreter_version="typed-v1",
            warnings=("user_confirmed_supersede",),
        )
        return self.apply_proposals_result(event, batch)

    def _require_active(self, task_id: str) -> None:
        self._require_status(task_id, TaskStatus.ACTIVE)

    def _require_status(self, task_id: str, expected: TaskStatus) -> None:
        actual = self.repository.get_task_status(task_id)
        if actual is not expected:
            raise TaskLifecycleError(
                task_id=task_id,
                status=actual.value,
                message=f"task {task_id} is {actual.value}, expected {expected.value}",
            )

    def _require_not_terminal(self, task_id: str) -> None:
        status = self.repository.get_task_status(task_id)
        if status in {TaskStatus.COMPLETED, TaskStatus.CANCELLED}:
            raise TaskLifecycleError(
                task_id=task_id,
                status=status.value,
                message=f"task {task_id} is already terminal: {status.value}",
            )


class _TypedBatchInterpreter:
    identity = "typed-proposal-batch"
    version = "typed-v1"

    def __init__(self, batch: TaskStateProposalBatch) -> None:
        self.batch = batch

    def interpret(self, event, context):
        return self.batch
