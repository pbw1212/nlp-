"""Phase 4A AgentRuntime main orchestration service."""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Optional, List

from src.memory.llm.errors import ProviderError
from src.memory.core.diagnostics import log_boundary_failure
from src.memory.retrieval.evidence import EvidenceBundle
from src.memory.retrieval.facade import MemoryRetrievalFacade
from src.memory.runtime.adapter import TaskRetrievalContextAdapter
from src.memory.runtime.config import AgentRuntimeConfig
from src.memory.runtime.errors import TaskSelectionRequiredError, TaskLifecycleError, TaskNotFoundError
from src.memory.runtime.models import (
    AgentTurnContext,
    ToolCallResult,
    ToolCallStatus,
    TurnErrorInfo,
    TurnExecutionResult,
    TurnRequest,
    TurnStage,
    TurnStageStatus,
    TurnStageTrace,
)
from src.memory.runtime.task_router import (
    BaselineTaskRouter,
    TaskBindingAction,
    TaskBindingDecision,
    TaskBindingResolver,
    TaskRouter,
    UNBOUND_TASK_ID,
)
from src.memory.task_state.models import (
    ActiveWorkingContext,
    EventProcessStatus,
    SourceKind,
    TaskEvent,
    EntryKind,
    ProposalBasis,
    StructuredTaskContext,
    TaskAnalysis,
    TaskStatus,
    TaskIntent,
    TaskStateProposalBatch,
)
from src.memory.task_state.service import TaskStateService, TaskServiceResult
from src.memory.task_state.updater import (
    ProposalValidationError,
    TaskStateInterpreter,
    TaskStateInterpreterError,
)


logger = logging.getLogger(__name__)


class _ControlFlowTaskStateInterpreter:
    """Typed no-op interpreter for trusted orchestration control events."""

    identity = "control-flow-noop"
    version = "v1"

    def interpret(self, event: TaskEvent, context: object) -> TaskStateProposalBatch:
        return TaskStateProposalBatch(
            task_id=event.task_id,
            event_id=event.event_id,
            proposals=(),
            interpreter=self.identity,
            interpreter_version=self.version,
        )


class AgentRuntime:
    """System-level orchestration runtime for Agent turns and tool result ingestion.

    Coordinates Task 3 state updates, Task 2 retrieval, and Task 3 context assembly
    without taking on Main Agent reasoning, prompt template generation, or direct Task 1 reads.
    """

    def __init__(
        self,
        task_service: TaskStateService,
        retrieval_facade: Optional[MemoryRetrievalFacade] = None,
        task_router: Optional[TaskRouter] = None,
        task_binding_resolver: Optional[TaskBindingResolver] = None,
        task_interpreter: Optional[TaskStateInterpreter] = None,
        config: Optional[AgentRuntimeConfig] = None,
        context_adapter: Optional[TaskRetrievalContextAdapter] = None,
    ) -> None:
        self.task_service = task_service
        self.retrieval_facade = retrieval_facade
        self.task_router = task_router or BaselineTaskRouter(task_service)
        self.task_binding_resolver = task_binding_resolver
        self.task_interpreter = task_interpreter
        self.config = config or AgentRuntimeConfig()
        self.context_adapter = context_adapter or TaskRetrievalContextAdapter()

    def _apply_task_binding(
        self,
        turn_id: str,
        decision: TaskBindingDecision,
    ) -> str:
        """Apply only lifecycle-safe effects of a Provider binding proposal."""
        if decision.action is TaskBindingAction.UNBOUND:
            return UNBOUND_TASK_ID

        if decision.action is TaskBindingAction.EXISTING:
            if not decision.task_id:
                raise TaskNotFoundError("")
            try:
                ledger = self.task_service.get_task(decision.task_id)
            except KeyError as exc:
                raise TaskNotFoundError(decision.task_id) from exc
            if ledger.status is not TaskStatus.ACTIVE:
                raise TaskLifecycleError(decision.task_id, ledger.status.value)
            return decision.task_id

        if decision.action is TaskBindingAction.NEW:
            if not decision.title:
                raise ValueError("NEW task binding requires a title")
            derived_hash = hashlib.sha256(turn_id.encode("utf-8")).hexdigest()[:12]
            task_id = f"task_{derived_hash}"
            self.task_service.create_task(task_id)
            self.task_service.repository.add_entry(
                task_id,
                EntryKind.GOAL,
                decision.title,
                source_kind=SourceKind.USER,
                basis=ProposalBasis.EXPLICIT,
                source_ref=f"turn:{turn_id}:task-binding",
                operation_id=f"task-binding:{turn_id}",
            )
            return task_id

        raise ValueError(f"unsupported task binding action: {decision.action!r}")

    @staticmethod
    def _build_unbound_context(
        request: TurnRequest,
        traces: List[TurnStageTrace],
        analysis: Optional[TaskAnalysis] = None,
        warnings: tuple[str, ...] = (),
        evidence_bundle: Optional[EvidenceBundle] = None,
    ) -> AgentTurnContext:
        context_warnings = ("This turn has no Task 3 binding.", *warnings)
        structured = StructuredTaskContext(
            task_id=UNBOUND_TASK_ID,
            current_turn=request.user_input,
            external_observations=request.external_observations,
            policy_version="unbound-turn-v1",
            warnings=context_warnings,
        )
        working = ActiveWorkingContext(
            task_id=UNBOUND_TASK_ID,
            structured=structured,
            warnings=context_warnings,
        )
        traces.append(
            TurnStageTrace(
                stage=TurnStage.CONTEXT_BUILD,
                status=TurnStageStatus.SUCCESS,
            )
        )
        return AgentTurnContext(
            turn_id=request.turn_id,
            task_id=UNBOUND_TASK_ID,
            receipt=None,
            maintenance=None,
            evidence_bundle=evidence_bundle,
            working_context=working,
            structured_task_context=structured,
            task_analysis=analysis,
            warnings=context_warnings,
            stage_traces=tuple(traces),
        )

    def _analyze_unbound_turn(
        self,
        request: TurnRequest,
        traces: List[TurnStageTrace],
    ) -> tuple[Optional[TaskAnalysis], tuple[str, ...]]:
        """Analyze memory intent without creating or mutating a Task 3 record."""
        if self.task_interpreter is None or request.skip_task_state_update:
            return None, ()

        started = time.time()
        event = TaskEvent(
            task_id=UNBOUND_TASK_ID,
            event_id=f"turn:{request.turn_id}:unbound-analysis",
            source_kind=SourceKind.USER,
            content=request.user_input,
            turn_id=request.turn_id,
        )
        try:
            batch = self.task_interpreter.interpret(event, context=None)
        except ProviderError:
            # Task 3 is optional for an unbound turn. Preserve the normal
            # Agent path if this auxiliary analysis is temporarily unavailable.
            warning = "unbound turn semantic analysis unavailable; no Task 3 state was written"
            traces.append(
                TurnStageTrace(
                    stage=TurnStage.TASK_UPDATE,
                    status=TurnStageStatus.WARNING,
                    warnings=(warning,),
                    duration_seconds=time.time() - started,
                )
            )
            return None, (warning,)
        except Exception as exc:
            log_boundary_failure(
                logger,
                "unbound_turn_semantic_analysis_failed",
                exc,
                turn_id=request.turn_id,
            )
            warning = (
                "unbound turn semantic analysis was invalid; no Task 3 state was written"
            )
            traces.append(
                TurnStageTrace(
                    stage=TurnStage.TASK_UPDATE,
                    status=TurnStageStatus.WARNING,
                    warnings=(warning,),
                    duration_seconds=time.time() - started,
                )
            )
            return None, (warning,)

        # The unbound route never applies proposals. In particular, an
        # explicit memory analysis is accepted only when the Provider also
        # obeyed the contract that Task 3 proposals are empty.
        if batch.proposals:
            warning = "Task 3 proposals were discarded because this turn is unbound"
            traces.append(
                TurnStageTrace(
                    stage=TurnStage.TASK_UPDATE,
                    status=TurnStageStatus.WARNING,
                    warnings=(warning,),
                    duration_seconds=time.time() - started,
                )
            )
            return None, (warning,)

        traces.append(
            TurnStageTrace(
                stage=TurnStage.TASK_UPDATE,
                status=TurnStageStatus.SKIPPED,
                warnings=("unbound turn; semantic analysis only",),
                duration_seconds=time.time() - started,
            )
        )
        return batch.analysis, tuple(batch.warnings)

    def process_user_turn(self, request: TurnRequest) -> TurnExecutionResult:
        """Executes a user turn through the orchestration lifecycle:

        resolve task -> Task3 update -> Task2 retrieval -> Task3 context build -> AgentTurnContext
        """
        traces: List[TurnStageTrace] = []
        warnings: List[str] = []
        task_id: Optional[str] = None

        # STAGE 1: PROVIDER-PROPOSED TASK BINDING + deterministic validation.
        # Task binding is evaluated for every turn. The legacy router remains
        # available to direct callers that intentionally do not inject the
        # Provider-backed resolver.
        t0 = time.time()
        try:
            if self.task_binding_resolver is not None:
                binding_started = time.time()
                decision = self.task_binding_resolver.resolve(
                    turn_id=request.turn_id,
                    user_input=request.user_input,
                    current_task_id=request.task_id,
                    tasks=self.task_service.list_active_tasks(),
                )
                traces.append(
                    TurnStageTrace(
                        stage=TurnStage.TASK_BINDING,
                        status=TurnStageStatus.SUCCESS,
                        duration_seconds=time.time() - binding_started,
                    )
                )
                task_id = self._apply_task_binding(
                    request.turn_id,
                    decision,
                )
            else:
                task_id = self.task_router.resolve_task(request.turn_id, requested_task_id=request.task_id)
            traces.append(TurnStageTrace(stage=TurnStage.TASK_RESOLUTION, status=TurnStageStatus.SUCCESS, duration_seconds=time.time() - t0))
        except ProviderError as exc:
            stage = TurnStage.TASK_BINDING if self.task_binding_resolver is not None else TurnStage.TASK_RESOLUTION
            err_info = self._sanitize_provider_error(stage, exc)
            traces.append(
                TurnStageTrace(
                    stage=stage,
                    status=TurnStageStatus.FAILED,
                    warnings=(err_info.safe_message,),
                    duration_seconds=time.time() - t0,
                )
            )
            return TurnExecutionResult(
                turn_id=request.turn_id,
                task_id=None,
                context=None,
                stage_traces=tuple(traces),
                error_info=err_info,
            )
        except TaskSelectionRequiredError as exc:
            if self.task_binding_resolver is not None:
                traces.append(TurnStageTrace(stage=TurnStage.TASK_BINDING, status=TurnStageStatus.FAILED, warnings=("Provider did not select a task binding.",), duration_seconds=time.time() - t0))
            traces.append(TurnStageTrace(stage=TurnStage.TASK_RESOLUTION, status=TurnStageStatus.FAILED, warnings=("Explicit task selection required.",), duration_seconds=time.time() - t0))
            err_info = TurnErrorInfo(
                stage=TurnStage.TASK_RESOLUTION,
                category="TASK_ROUTING",
                code="TASK_SELECTION_REQUIRED",
                safe_message=str(exc),
                retryable=False,
                details={"active_task_ids": list(exc.active_task_ids)},
            )
            return TurnExecutionResult(turn_id=request.turn_id, task_id=None, context=None, stage_traces=tuple(traces), error_info=err_info)
        except (TaskLifecycleError, TaskNotFoundError) as exc:
            if self.task_binding_resolver is not None:
                traces.append(TurnStageTrace(stage=TurnStage.TASK_BINDING, status=TurnStageStatus.FAILED, warnings=("Provider proposed an invalid task binding.",), duration_seconds=time.time() - t0))
            traces.append(TurnStageTrace(stage=TurnStage.TASK_RESOLUTION, status=TurnStageStatus.FAILED, warnings=("Invalid task status or ID.",), duration_seconds=time.time() - t0))
            err_info = TurnErrorInfo(
                stage=TurnStage.TASK_RESOLUTION,
                category="TASK_ROUTING",
                code="TASK_LIFECYCLE_ERROR",
                safe_message=str(exc),
                retryable=False,
            )
            return TurnExecutionResult(turn_id=request.turn_id, task_id=request.task_id, context=None, stage_traces=tuple(traces), error_info=err_info)
        except Exception as exc:
            details = log_boundary_failure(
                logger,
                "task_resolution_failed",
                exc,
                turn_id=request.turn_id,
                requested_task_id=request.task_id,
            )
            if self.task_binding_resolver is not None:
                traces.append(TurnStageTrace(stage=TurnStage.TASK_BINDING, status=TurnStageStatus.FAILED, warnings=("Task binding failed.",), duration_seconds=time.time() - t0))
            traces.append(TurnStageTrace(stage=TurnStage.TASK_RESOLUTION, status=TurnStageStatus.FAILED, warnings=("Failed to resolve task.",), duration_seconds=time.time() - t0))
            err_info = TurnErrorInfo(
                stage=TurnStage.TASK_RESOLUTION,
                category="RUNTIME_ERROR",
                code="RESOLUTION_FAILED",
                safe_message="Failed to resolve task.",
                retryable=False,
                details={"error_type": details["error_type"]},
            )
            return TurnExecutionResult(turn_id=request.turn_id, task_id=request.task_id, context=None, stage_traces=tuple(traces), error_info=err_info)

        if task_id == UNBOUND_TASK_ID:
            warning = "turn is not bound to a Task"
            analysis, analysis_warnings = self._analyze_unbound_turn(request, traces)
            warnings.extend(analysis_warnings)
            if not any(trace.stage is TurnStage.TASK_UPDATE for trace in traces):
                traces.append(
                    TurnStageTrace(
                        stage=TurnStage.TASK_UPDATE,
                        status=TurnStageStatus.SKIPPED,
                        warnings=(warning,),
                    )
                )
            for stage in (TurnStage.MAINTENANCE,):
                traces.append(
                    TurnStageTrace(
                        stage=stage,
                        status=TurnStageStatus.SKIPPED,
                        warnings=(warning,),
                    )
                )
            evidence_bundle = None
            explicit_memory = bool(
                analysis and analysis.memory_write_requested
                and analysis.query_intent is TaskIntent.EXPLICIT_MEMORY_WRITE
            )
            if self.config.enable_auto_retrieval and self.retrieval_facade and not explicit_memory:
                started = time.time()
                try:
                    evidence_bundle = self.retrieval_facade.retrieve_evidence(
                        request.user_input,
                        top_k=self.config.default_top_k,
                        query_id=request.turn_id,
                    )
                    traces.append(TurnStageTrace(
                        stage=TurnStage.RETRIEVAL, status=TurnStageStatus.SUCCESS,
                        duration_seconds=time.time() - started,
                    ))
                except Exception as exc:
                    details = log_boundary_failure(logger, "unbound_memory_retrieval_failed", exc,
                                                   turn_id=request.turn_id)
                    error = (
                        self._sanitize_provider_error(TurnStage.RETRIEVAL, exc)
                        if isinstance(exc, ProviderError)
                        else TurnErrorInfo(stage=TurnStage.RETRIEVAL, category="RETRIEVAL_ERROR",
                                           code="RETRIEVAL_FAILED", safe_message="Memory retrieval failed.",
                                           retryable=False, details={"error_type": details["error_type"]})
                    )
                    traces.append(TurnStageTrace(stage=TurnStage.RETRIEVAL,
                                                 status=TurnStageStatus.FAILED))
                    return TurnExecutionResult(turn_id=request.turn_id, task_id=task_id,
                                               context=None, stage_traces=tuple(traces), error_info=error)
            else:
                traces.append(TurnStageTrace(stage=TurnStage.RETRIEVAL, status=TurnStageStatus.SKIPPED))
            context = self._build_unbound_context(
                request,
                traces,
                analysis=analysis,
                warnings=analysis_warnings,
                evidence_bundle=evidence_bundle,
            )
            return TurnExecutionResult(
                turn_id=request.turn_id,
                task_id=UNBOUND_TASK_ID,
                context=context,
                stage_traces=tuple(traces),
                warnings=(warning,),
            )

        # STAGE 2: TASK 3 UPDATE
        t0 = time.time()
        event_id = f"turn:{request.turn_id}:user"
        event = TaskEvent(
            task_id=task_id,
            event_id=event_id,
            source_kind=SourceKind.USER,
            content=request.user_input,
            turn_id=request.turn_id,
        )

        service_result: Optional[TaskServiceResult] = None
        try:
            if self.task_interpreter is None:
                raise RuntimeError("task_interpreter was not injected into AgentRuntime.")
            interpreter = (
                _ControlFlowTaskStateInterpreter()
                if request.skip_task_state_update
                else self.task_interpreter
            )
            service_result = self.task_service.process_event_result(event, interpreter=interpreter)
            if request.skip_task_state_update:
                traces.append(
                    TurnStageTrace(
                        stage=TurnStage.TASK_UPDATE,
                        status=TurnStageStatus.SKIPPED,
                        warnings=("trusted control-flow turn",),
                        duration_seconds=time.time() - t0,
                    )
                )
            elif service_result and service_result.receipt and service_result.receipt.status in {
                EventProcessStatus.REJECTED,
                EventProcessStatus.PENDING_CONFIRMATION,
            }:
                if service_result.receipt.warnings:
                    warnings.extend(service_result.receipt.warnings)
                traces.append(TurnStageTrace(stage=TurnStage.TASK_UPDATE, status=TurnStageStatus.WARNING, warnings=service_result.receipt.warnings, duration_seconds=time.time() - t0))
            else:
                traces.append(TurnStageTrace(stage=TurnStage.TASK_UPDATE, status=TurnStageStatus.SUCCESS, duration_seconds=time.time() - t0))
        except ProviderError as exc:
            err_info = self._sanitize_provider_error(TurnStage.TASK_UPDATE, exc)
            traces.append(TurnStageTrace(stage=TurnStage.TASK_UPDATE, status=TurnStageStatus.FAILED, warnings=(err_info.safe_message,), duration_seconds=time.time() - t0))
            traces.append(TurnStageTrace(stage=TurnStage.MAINTENANCE, status=TurnStageStatus.SKIPPED))
            traces.append(TurnStageTrace(stage=TurnStage.RETRIEVAL, status=TurnStageStatus.SKIPPED))
            traces.append(TurnStageTrace(stage=TurnStage.CONTEXT_BUILD, status=TurnStageStatus.SKIPPED))
            return TurnExecutionResult(turn_id=request.turn_id, task_id=task_id, context=None, stage_traces=tuple(traces), error_info=err_info)
        except ProposalValidationError as exc:
            warn_text = str(exc)
            warnings.append(warn_text)
            traces.append(TurnStageTrace(stage=TurnStage.TASK_UPDATE, status=TurnStageStatus.WARNING, warnings=(warn_text,), duration_seconds=time.time() - t0))
        except TaskStateInterpreterError as exc:
            details = log_boundary_failure(
                logger,
                "task_state_interpreter_response_invalid",
                exc,
                turn_id=request.turn_id,
                task_id=task_id,
                event_id=event_id,
            )
            warning = (
                "Task state update was not applied because the Provider returned an "
                "invalid structured response; the current turn can continue, and "
                "the event remains retryable."
            )
            warnings.append(warning)
            traces.append(
                TurnStageTrace(
                    stage=TurnStage.TASK_UPDATE,
                    status=TurnStageStatus.WARNING,
                    warnings=(warning,),
                    duration_seconds=time.time() - t0,
                )
            )
        except Exception as exc:
            details = log_boundary_failure(
                logger,
                "task_state_update_failed",
                exc,
                turn_id=request.turn_id,
                task_id=task_id,
                event_id=event_id,
            )
            traces.append(TurnStageTrace(stage=TurnStage.TASK_UPDATE, status=TurnStageStatus.FAILED, warnings=("Task state update failed.",), duration_seconds=time.time() - t0))
            traces.append(TurnStageTrace(stage=TurnStage.MAINTENANCE, status=TurnStageStatus.SKIPPED))
            traces.append(TurnStageTrace(stage=TurnStage.RETRIEVAL, status=TurnStageStatus.SKIPPED))
            traces.append(TurnStageTrace(stage=TurnStage.CONTEXT_BUILD, status=TurnStageStatus.SKIPPED))
            err_info = TurnErrorInfo(
                stage=TurnStage.TASK_UPDATE,
                category="DOMAIN_ERROR",
                code="UPDATE_FAILED",
                safe_message="Task state update failed.",
                retryable=False,
                details={"error_type": details["error_type"]},
            )
            return TurnExecutionResult(turn_id=request.turn_id, task_id=task_id, context=None, stage_traces=tuple(traces), error_info=err_info)

        # STAGE 3: MAINTENANCE OBSERVABILITY
        t0 = time.time()
        m_warnings = service_result.maintenance.warnings if service_result and service_result.maintenance else ()
        if m_warnings:
            m_status = TurnStageStatus.WARNING
            warnings.extend(m_warnings)
        else:
            m_status = TurnStageStatus.SUCCESS
        traces.append(TurnStageTrace(stage=TurnStage.MAINTENANCE, status=m_status, warnings=m_warnings, duration_seconds=time.time() - t0))

        # STAGE 4: TASK 2 RETRIEVAL
        t0 = time.time()
        evidence_bundle: Optional[EvidenceBundle] = None
        explicit_memory_write = bool(
            service_result
            and service_result.analysis is not None
            and service_result.analysis.query_intent is TaskIntent.EXPLICIT_MEMORY_WRITE
        )
        if explicit_memory_write:
            traces.append(
                TurnStageTrace(
                    stage=TurnStage.RETRIEVAL,
                    status=TurnStageStatus.SKIPPED,
                    warnings=("explicit memory write route",),
                    duration_seconds=time.time() - t0,
                )
            )
        elif self.config.enable_auto_retrieval and self.retrieval_facade is not None:
            try:
                # Build temporary context view from updated task state to feed into adapter
                temp_context = self.task_service.build_context(task_id, current_turn=request.user_input)
                structured_temp = temp_context.structured
                query, recent_context = self.context_adapter.adapt(request.user_input, structured_temp, turn_id=request.turn_id)
                evidence_bundle = self.retrieval_facade.retrieve_evidence(
                    query,
                    top_k=self.config.default_top_k,
                    query_id=f"query:{request.turn_id}",
                    recent_context=recent_context,
                )
                traces.append(TurnStageTrace(stage=TurnStage.RETRIEVAL, status=TurnStageStatus.SUCCESS, duration_seconds=time.time() - t0))
            except ProviderError as exc:
                err_info = self._sanitize_provider_error(TurnStage.RETRIEVAL, exc)
                traces.append(TurnStageTrace(stage=TurnStage.RETRIEVAL, status=TurnStageStatus.FAILED, warnings=(err_info.safe_message,), duration_seconds=time.time() - t0))
                traces.append(TurnStageTrace(stage=TurnStage.CONTEXT_BUILD, status=TurnStageStatus.SKIPPED))
                return TurnExecutionResult(turn_id=request.turn_id, task_id=task_id, context=None, stage_traces=tuple(traces), error_info=err_info)
            except Exception as exc:
                details = log_boundary_failure(
                    logger,
                    "memory_retrieval_failed",
                    exc,
                    turn_id=request.turn_id,
                    task_id=task_id,
                )
                traces.append(TurnStageTrace(stage=TurnStage.RETRIEVAL, status=TurnStageStatus.FAILED, warnings=("Memory retrieval failed.",), duration_seconds=time.time() - t0))
                traces.append(TurnStageTrace(stage=TurnStage.CONTEXT_BUILD, status=TurnStageStatus.SKIPPED))
                err_info = TurnErrorInfo(
                    stage=TurnStage.RETRIEVAL,
                    category="RETRIEVAL_ERROR",
                    code="RETRIEVAL_FAILED",
                    safe_message="Memory retrieval failed.",
                    retryable=False,
                    details={"error_type": details["error_type"]},
                )
                return TurnExecutionResult(turn_id=request.turn_id, task_id=task_id, context=None, stage_traces=tuple(traces), error_info=err_info)
        else:
            traces.append(TurnStageTrace(stage=TurnStage.RETRIEVAL, status=TurnStageStatus.SKIPPED, duration_seconds=time.time() - t0))

        # STAGE 5: CONTEXT BUILD
        t0 = time.time()
        try:
            working_context = self.task_service.build_context(
                task_id,
                current_turn=request.user_input,
                evidence_bundle=evidence_bundle,
                external_observations=request.external_observations,
            )
            structured_context = working_context.structured
            traces.append(TurnStageTrace(stage=TurnStage.CONTEXT_BUILD, status=TurnStageStatus.SUCCESS, duration_seconds=time.time() - t0))
        except Exception as exc:
            details = log_boundary_failure(
                logger,
                "working_context_build_failed",
                exc,
                turn_id=request.turn_id,
                task_id=task_id,
            )
            traces.append(TurnStageTrace(stage=TurnStage.CONTEXT_BUILD, status=TurnStageStatus.FAILED, warnings=("Failed to build working context.",), duration_seconds=time.time() - t0))
            err_info = TurnErrorInfo(
                stage=TurnStage.CONTEXT_BUILD,
                category="CONTEXT_ERROR",
                code="BUILD_FAILED",
                safe_message="Failed to build working context.",
                retryable=False,
                details={"error_type": details["error_type"]},
            )
            return TurnExecutionResult(turn_id=request.turn_id, task_id=task_id, context=None, stage_traces=tuple(traces), error_info=err_info)

        agent_context = AgentTurnContext(
            turn_id=request.turn_id,
            task_id=task_id,
            receipt=service_result.receipt if service_result else None,
            maintenance=service_result.maintenance if service_result else None,
            evidence_bundle=evidence_bundle,
            working_context=working_context,
            structured_task_context=structured_context,
            task_analysis=service_result.analysis if service_result else None,
            warnings=tuple(warnings),
            stage_traces=tuple(traces),
        )

        return TurnExecutionResult(
            turn_id=request.turn_id,
            task_id=task_id,
            context=agent_context,
            stage_traces=tuple(traces),
            warnings=tuple(warnings),
        )

    def process_tool_result(self, result: ToolCallResult, task_id: str) -> TurnExecutionResult:
        """Ingests an executed tool call result into Task 3 state."""
        traces: List[TurnStageTrace] = []
        warnings: List[str] = []

        if task_id == UNBOUND_TASK_ID:
            # Execution and conversation layers may still retain this result,
            # but an unbound turn must never create or mutate a hidden Task 3
            # ledger merely because a tool was used.
            warning = "turn is not bound to a Task"
            for stage in (
                TurnStage.TASK_RESOLUTION,
                TurnStage.TASK_UPDATE,
                TurnStage.MAINTENANCE,
                TurnStage.CONTEXT_BUILD,
            ):
                traces.append(
                    TurnStageTrace(
                        stage=stage,
                        status=TurnStageStatus.SKIPPED,
                        warnings=(warning,),
                    )
                )
            return TurnExecutionResult(
                turn_id=f"tool:{result.tool_call_id}",
                task_id=UNBOUND_TASK_ID,
                context=None,
                stage_traces=tuple(traces),
                warnings=(warning,),
            )

        # Validate task
        t0 = time.time()
        try:
            self.task_router.resolve_task(f"tool:{result.tool_call_id}", requested_task_id=task_id)
            traces.append(TurnStageTrace(stage=TurnStage.TASK_RESOLUTION, status=TurnStageStatus.SUCCESS, duration_seconds=time.time() - t0))
        except (TaskLifecycleError, TaskNotFoundError):
            traces.append(TurnStageTrace(stage=TurnStage.TASK_RESOLUTION, status=TurnStageStatus.FAILED, warnings=("Invalid tool task target.",), duration_seconds=time.time() - t0))
            err_info = TurnErrorInfo(
                stage=TurnStage.TASK_RESOLUTION,
                category="TASK_ROUTING",
                code="TOOL_TASK_INVALID",
                safe_message="Invalid task target for tool result.",
                retryable=False,
            )
            return TurnExecutionResult(turn_id=f"tool:{result.tool_call_id}", task_id=task_id, context=None, stage_traces=tuple(traces), error_info=err_info)
        except Exception as exc:
            details = log_boundary_failure(
                logger,
                "tool_task_resolution_failed",
                exc,
                tool_call_id=result.tool_call_id,
                task_id=task_id,
            )
            traces.append(TurnStageTrace(stage=TurnStage.TASK_RESOLUTION, status=TurnStageStatus.FAILED, warnings=("Failed to resolve tool task target.",), duration_seconds=time.time() - t0))
            err_info = TurnErrorInfo(
                stage=TurnStage.TASK_RESOLUTION,
                category="RUNTIME_ERROR",
                code="TOOL_TASK_RESOLUTION_FAILED",
                safe_message="Failed to resolve task target for tool result.",
                retryable=False,
                details={"error_type": details["error_type"]},
            )
            return TurnExecutionResult(turn_id=f"tool:{result.tool_call_id}", task_id=task_id, context=None, stage_traces=tuple(traces), error_info=err_info)

        # Ingest tool event into Task 3
        t0 = time.time()
        event_id = f"tool:{result.tool_call_id}:result"
        content = f"Tool '{result.tool_name}' ({result.status.value}): {result.result_content or result.error_message}"
        event = TaskEvent(
            task_id=task_id,
            event_id=event_id,
            source_kind=SourceKind.TOOL,
            content=content,
            structured_payload={
                "success": result.status is ToolCallStatus.SUCCESS,
                "status": result.status.value,
                "tool_name": result.tool_name,
            },
            tool_call_id=result.tool_call_id,
        )

        service_result: Optional[TaskServiceResult] = None
        try:
            if self.task_interpreter is None:
                raise RuntimeError("task_interpreter was not injected into AgentRuntime.")
            service_result = self.task_service.process_event_result(event, interpreter=self.task_interpreter)
            traces.append(TurnStageTrace(stage=TurnStage.TASK_UPDATE, status=TurnStageStatus.SUCCESS, duration_seconds=time.time() - t0))
        except ProviderError as exc:
            err_info = self._sanitize_provider_error(TurnStage.TASK_UPDATE, exc)
            traces.append(TurnStageTrace(stage=TurnStage.TASK_UPDATE, status=TurnStageStatus.FAILED, warnings=(err_info.safe_message,), duration_seconds=time.time() - t0))
            traces.append(TurnStageTrace(stage=TurnStage.MAINTENANCE, status=TurnStageStatus.SKIPPED))
            traces.append(TurnStageTrace(stage=TurnStage.CONTEXT_BUILD, status=TurnStageStatus.SKIPPED))
            return TurnExecutionResult(turn_id=f"tool:{result.tool_call_id}", task_id=task_id, context=None, stage_traces=tuple(traces), error_info=err_info)
        except ProposalValidationError as exc:
            warn_text = str(exc)
            warnings.append(warn_text)
            traces.append(
                TurnStageTrace(
                    stage=TurnStage.TASK_UPDATE,
                    status=TurnStageStatus.WARNING,
                    warnings=(warn_text,),
                    duration_seconds=time.time() - t0,
                )
            )
        except TaskStateInterpreterError as exc:
            log_boundary_failure(
                logger,
                "tool_task_state_interpreter_response_invalid",
                exc,
                tool_call_id=result.tool_call_id,
                task_id=task_id,
                event_id=event_id,
            )
            warning = (
                "Tool result was retained, but its Task state update was not applied "
                "because the Provider returned an invalid structured response."
            )
            warnings.append(warning)
            traces.append(
                TurnStageTrace(
                    stage=TurnStage.TASK_UPDATE,
                    status=TurnStageStatus.WARNING,
                    warnings=(warning,),
                    duration_seconds=time.time() - t0,
                )
            )
        except Exception as exc:
            details = log_boundary_failure(
                logger,
                "tool_result_ingestion_failed",
                exc,
                tool_call_id=result.tool_call_id,
                task_id=task_id,
            )
            traces.append(TurnStageTrace(stage=TurnStage.TASK_UPDATE, status=TurnStageStatus.FAILED, warnings=("Tool ingestion failed.",), duration_seconds=time.time() - t0))
            err_info = TurnErrorInfo(
                stage=TurnStage.TASK_UPDATE,
                category="DOMAIN_ERROR",
                code="TOOL_INGESTION_FAILED",
                safe_message="Tool ingestion failed.",
                retryable=False,
                details={"error_type": details["error_type"]},
            )
            return TurnExecutionResult(turn_id=f"tool:{result.tool_call_id}", task_id=task_id, context=None, stage_traces=tuple(traces), error_info=err_info)

        # Maintenance
        t0 = time.time()
        m_warnings = service_result.maintenance.warnings if service_result and service_result.maintenance else ()
        traces.append(TurnStageTrace(stage=TurnStage.MAINTENANCE, status=TurnStageStatus.WARNING if m_warnings else TurnStageStatus.SUCCESS, warnings=m_warnings, duration_seconds=time.time() - t0))
        if m_warnings:
            warnings.extend(m_warnings)

        # Context build post tool result
        t0 = time.time()
        try:
            working_context = self.task_service.build_context(task_id)
            structured_context = working_context.structured
            traces.append(TurnStageTrace(stage=TurnStage.CONTEXT_BUILD, status=TurnStageStatus.SUCCESS, duration_seconds=time.time() - t0))
        except Exception as exc:
            details = log_boundary_failure(
                logger,
                "post_tool_context_build_failed",
                exc,
                tool_call_id=result.tool_call_id,
                task_id=task_id,
            )
            traces.append(TurnStageTrace(stage=TurnStage.CONTEXT_BUILD, status=TurnStageStatus.FAILED, warnings=("Failed to build working context.",), duration_seconds=time.time() - t0))
            err_info = TurnErrorInfo(
                stage=TurnStage.CONTEXT_BUILD,
                category="CONTEXT_ERROR",
                code="BUILD_FAILED",
                safe_message="Failed to build context after tool ingestion.",
                retryable=False,
                details={"error_type": details["error_type"]},
            )
            return TurnExecutionResult(turn_id=f"tool:{result.tool_call_id}", task_id=task_id, context=None, stage_traces=tuple(traces), error_info=err_info)

        agent_context = AgentTurnContext(
            turn_id=f"tool:{result.tool_call_id}",
            task_id=task_id,
            receipt=service_result.receipt if service_result else None,
            maintenance=service_result.maintenance if service_result else None,
            evidence_bundle=None,
            working_context=working_context,
            structured_task_context=structured_context,
            task_analysis=service_result.analysis if service_result else None,
            warnings=tuple(warnings),
            stage_traces=tuple(traces),
        )

        return TurnExecutionResult(
            turn_id=f"tool:{result.tool_call_id}",
            task_id=task_id,
            context=agent_context,
            stage_traces=tuple(traces),
            warnings=tuple(warnings),
        )

    @staticmethod
    def _sanitize_provider_error(stage: TurnStage, exc: ProviderError) -> TurnErrorInfo:
        """Sanitizes raw provider errors into safe TurnErrorInfo using structured safe projection."""
        proj = exc.to_safe_projection()
        log_boundary_failure(logger, "provider_operation_failed", exc, stage=stage.value)
        details = {
            "provider": proj["provider"],
            "operation": proj["operation"],
            "status_code": proj["status_code"],
        }
        for key in (
            "provider_error_type",
            "provider_error_code",
            "request_id",
            "retry_after",
        ):
            if proj.get(key) is not None:
                details[key] = proj[key]
        return TurnErrorInfo(
            stage=stage,
            category=proj["category"],
            code=proj["code"],
            safe_message=proj["safe_message"],
            retryable=proj["retryable"],
            details=details,
        )
