from __future__ import annotations

import logging
from dataclasses import replace
from typing import Optional, Protocol

from src.memory.llm.errors import ProviderError
from src.memory.core.diagnostics import log_boundary_failure
from src.memory.task_state.context import TaskContextBuilder
from src.memory.task_state.models import (
    ActiveWorkingContext,
    EntryKind,
    EntryStatus,
    EventProcessStatus,
    Priority,
    ProposalAction,
    ProposalBasis,
    SourceKind,
    TaskAnalysis,
    TaskEvent,
    TaskIntent,
    TaskStateProposal,
    TaskStateProposalBatch,
    TaskStateUpdateReceipt,
    TaskUpdateTrace,
    TaskStateScope,
)
from src.memory.task_state.repository import TaskStateRepository


logger = logging.getLogger(__name__)


class ProposalValidationError(ValueError):
    pass


class TaskStateInterpreterError(RuntimeError):
    pass


class TaskStateInterpreter(Protocol):
    identity: str
    version: str

    def interpret(
        self, event: TaskEvent, context: Optional[ActiveWorkingContext]
    ) -> TaskStateProposalBatch: ...


class TaskStateProposalValidator:
    """Pure deterministic safety gate before any durable mutation."""

    def validate(self, event: TaskEvent, batch: TaskStateProposalBatch, repository: TaskStateRepository) -> None:
        if batch.task_id != event.task_id or batch.event_id != event.event_id:
            raise ProposalValidationError("proposal batch task/event identity mismatch")
        if len({proposal.proposal_id for proposal in batch.proposals}) != len(batch.proposals):
            raise ProposalValidationError("proposal IDs must be unique")
        if batch.analysis is not None and batch.analysis.query_intent is TaskIntent.EXPLICIT_MEMORY_WRITE:
            if not batch.analysis.memory_write_requested:
                raise ProposalValidationError(
                    "EXPLICIT_MEMORY_WRITE requires explicit memory_write_requested"
                )
            if batch.proposals:
                raise ProposalValidationError("EXPLICIT_MEMORY_WRITE cannot mutate Task 3 state")
            if not batch.analysis.memory_content:
                raise ProposalValidationError("EXPLICIT_MEMORY_WRITE requires grounded memory_content")
        for proposal in batch.proposals:
            self._validate_proposal(event, proposal, repository)

    def _validate_proposal(self, event: TaskEvent, proposal: TaskStateProposal, repository: TaskStateRepository) -> None:
        if proposal.task_id != event.task_id or proposal.source_event_id != event.event_id:
            raise ProposalValidationError(f"proposal identity mismatch: {proposal.proposal_id}")
        if any(span not in event.content for span in proposal.support_spans):
            raise ProposalValidationError(f"support span is not in source event: {proposal.proposal_id}")
        for ref in proposal.evidence_refs:
            if not ref.bundle_id or not ref.memory_id or not ref.revision_id:
                raise ProposalValidationError(f"invalid evidence reference: {proposal.proposal_id}")
        if proposal.basis is ProposalBasis.INFERRED and proposal.priority is Priority.CRITICAL:
            raise ProposalValidationError("inferred proposal cannot become CRITICAL")
        if event.source_kind is not SourceKind.USER and proposal.entry_kind is EntryKind.CONSTRAINT:
            raise ProposalValidationError("non-user event cannot create a user constraint")
        if proposal.action is ProposalAction.ADD:
            if proposal.entry_kind is None or not proposal.content or not proposal.content.strip():
                raise ProposalValidationError("ADD requires entry_kind and content")
            if proposal.entry_kind is EntryKind.PROGRESS:
                if event.source_kind is SourceKind.TOOL and event.structured_payload.get("success") is not True:
                    raise ProposalValidationError("tool failure cannot create observed progress")
                if event.source_kind is SourceKind.AGENT and proposal.basis is not ProposalBasis.OBSERVED:
                    raise ProposalValidationError("agent intent cannot become observed progress")
        elif proposal.action in {ProposalAction.RESOLVE, ProposalAction.REMOVE, ProposalAction.SET_PRIORITY, ProposalAction.SUPERSEDE}:
            if not proposal.target_entry_id:
                raise ProposalValidationError(f"{proposal.action.value} requires target entry")
            try:
                target = repository.get_entry(proposal.target_entry_id)
            except KeyError as exc:
                raise ProposalValidationError(f"target entry does not exist: {proposal.target_entry_id}") from exc
            if target.task_id != event.task_id:
                raise ProposalValidationError("target entry belongs to another task")
            if proposal.action is ProposalAction.RESOLVE and target.status is not EntryStatus.ACTIVE:
                raise ProposalValidationError("only ACTIVE entry can be resolved")
            if proposal.action is ProposalAction.SUPERSEDE:
                if target.status is not EntryStatus.ACTIVE or not proposal.replacement_content:
                    raise ProposalValidationError("SUPERSEDE requires ACTIVE target and replacement content")
            if proposal.action is ProposalAction.SET_PRIORITY and proposal.priority is None:
                raise ProposalValidationError("SET_PRIORITY requires priority")
            if proposal.action in {ProposalAction.REMOVE, ProposalAction.SET_PRIORITY} and target.status is not EntryStatus.ACTIVE:
                raise ProposalValidationError(f"{proposal.action.value} requires ACTIVE target")
            if proposal.action is ProposalAction.REMOVE and not proposal.support_spans and not proposal.warnings:
                raise ProposalValidationError("REMOVE requires a source reason")
        elif proposal.action is ProposalAction.LINK_RELATION:
            relation = proposal.relation
            if relation is None or relation.task_id != event.task_id:
                raise ProposalValidationError("LINK_RELATION requires same-task relation")
            if relation.from_entry_id == relation.to_entry_id:
                raise ProposalValidationError("self relation is forbidden")
            for entry_id in (relation.from_entry_id, relation.to_entry_id):
                try:
                    relation_entry = repository.get_entry(entry_id)
                except KeyError as exc:
                    raise ProposalValidationError(f"relation endpoint does not exist: {entry_id}") from exc
                if relation_entry.task_id != event.task_id:
                    raise ProposalValidationError("relation endpoint belongs to another task")
        else:
            raise ProposalValidationError(f"unsupported action: {proposal.action}")

class TaskStateUpdater:
    """Interpreter proposes outside DB transactions; repository applies atomically."""

    def __init__(
        self,
        repository: TaskStateRepository,
        interpreter: TaskStateInterpreter,
        *,
        context_builder: Optional[TaskContextBuilder] = None,
        validator: Optional[TaskStateProposalValidator] = None,
    ) -> None:
        self.repository = repository
        self.interpreter = interpreter
        self.context_builder = context_builder or TaskContextBuilder(repository)
        self.validator = validator or TaskStateProposalValidator()
        self.last_analysis: Optional[TaskAnalysis] = None
        self.last_batch: Optional[TaskStateProposalBatch] = None
        self.last_trace: Optional[TaskUpdateTrace] = None

    def propose(self, event: TaskEvent) -> TaskStateProposalBatch:
        self.repository.register_event(event)
        context = self.context_builder.build(event.task_id, current_turn=event.content)
        try:
            batch = self.interpreter.interpret(event, context)
            self.last_analysis = batch.analysis
            self.last_batch = batch
        except ProviderError as exc:
            safe_info = exc.to_safe_projection()
            self.repository.mark_event_failed(event.event_id, f"ProviderError[{exc.__class__.__name__}]: {safe_info['safe_message']}")
            self.last_trace = TaskUpdateTrace(
                event.task_id,
                event.event_id,
                EventProcessStatus.FAILED,
                warnings=(safe_info["safe_message"],),
            )
            log_boundary_failure(
                logger,
                "task_state_interpreter_provider_failed",
                exc,
                task_id=event.task_id,
                event_id=event.event_id,
            )
            raise
        except Exception as exc:
            metadata = log_boundary_failure(
                logger,
                "task_state_interpreter_failed",
                exc,
                task_id=event.task_id,
                event_id=event.event_id,
            )
            safe_text = f"Task state interpreter failed ({metadata['error_type']})."
            self.repository.mark_event_failed(event.event_id, safe_text)
            self.last_trace = TaskUpdateTrace(
                event.task_id,
                event.event_id,
                EventProcessStatus.FAILED,
                warnings=(safe_text,),
            )
            raise TaskStateInterpreterError(safe_text) from exc
        try:
            self.validator.validate(event, batch, self.repository)
        except ProposalValidationError as exc:
            receipt = TaskStateUpdateReceipt(
                task_id=event.task_id,
                event_id=event.event_id,
                status=EventProcessStatus.REJECTED,
                batch_id=f"batch:{event.event_id}",
                proposal_ids=tuple(item.proposal_id for item in batch.proposals),
                warnings=(str(exc),),
                analysis=batch.analysis,
            )
            self.repository.mark_event_rejected(event.event_id, receipt)
            self.last_trace = TaskUpdateTrace(
                event.task_id,
                event.event_id,
                EventProcessStatus.REJECTED,
                analysis=batch.analysis,
                proposals=batch.proposals,
                receipt=receipt,
                warnings=receipt.warnings,
            )
            raise
        return batch

    def apply(self, event: TaskEvent, batch: TaskStateProposalBatch) -> TaskStateUpdateReceipt:
        # SESSION proposals are request-local conversational context.  The
        # transcript remains their durable source for the current session; a
        # Task 3 ledger entry would incorrectly expose them to another session.
        task_scoped_batch = replace(
            batch,
            proposals=tuple(
                proposal for proposal in batch.proposals
                if proposal.scope is TaskStateScope.TASK
            ),
            confirmation_required=(
                batch.confirmation_required
                and (
                    not batch.proposals
                    or any(proposal.scope is TaskStateScope.TASK for proposal in batch.proposals)
                )
            ),
        )
        self.validator.validate(event, task_scoped_batch, self.repository)
        if task_scoped_batch.confirmation_required:
            receipt = TaskStateUpdateReceipt(
                task_id=event.task_id,
                event_id=event.event_id,
                status=EventProcessStatus.PENDING_CONFIRMATION,
                batch_id=f"batch:{event.event_id}",
                proposal_ids=tuple(item.proposal_id for item in task_scoped_batch.proposals),
                warnings=tuple(task_scoped_batch.warnings),
                analysis=task_scoped_batch.analysis,
            )
            self.repository.mark_event_pending_confirmation(event.event_id, receipt)
            self.last_batch = task_scoped_batch
            self.last_trace = TaskUpdateTrace(
                event.task_id,
                event.event_id,
                receipt.status,
                analysis=task_scoped_batch.analysis,
                proposals=task_scoped_batch.proposals,
                receipt=receipt,
                confirmation_required=True,
                warnings=receipt.warnings,
            )
            return receipt
        receipt = self.repository.apply_proposal_batch(event, task_scoped_batch)
        self.last_batch = task_scoped_batch
        self.last_trace = TaskUpdateTrace(
            event.task_id,
            event.event_id,
            receipt.status,
            analysis=task_scoped_batch.analysis,
            proposals=task_scoped_batch.proposals,
            receipt=receipt,
            warnings=receipt.warnings,
        )
        return receipt

    def process_event(self, event: TaskEvent) -> TaskStateUpdateReceipt:
        status = self.repository.register_event(event)
        if status in {
            EventProcessStatus.APPLIED,
            EventProcessStatus.PENDING_CONFIRMATION,
            EventProcessStatus.REJECTED,
        }:
            receipt = self.repository.get_event_receipt(event.event_id)
            if receipt is None:
                raise RuntimeError("processed event has no receipt")
            self.last_analysis = receipt.analysis
            self.last_trace = TaskUpdateTrace(
                event.task_id,
                event.event_id,
                receipt.status,
                analysis=receipt.analysis,
                receipt=receipt,
                confirmation_required=receipt.status is EventProcessStatus.PENDING_CONFIRMATION,
                warnings=receipt.warnings,
            )
            return receipt
        batch = self.propose(event)
        return self.apply(event, batch)
