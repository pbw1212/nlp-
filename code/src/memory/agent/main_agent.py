"""Top-level MainAgent facade orchestrating Runtime, Reasoner, and Tool Loop."""

from __future__ import annotations

from dataclasses import replace
import logging
from typing import Any, Callable, Optional, Protocol

from src.memory.agent.journal import AgentExecutionJournal
from src.memory.agent.loop import AgentLoopConfig, AgentLoopController
from src.memory.agent.models import (
    AgentErrorInfo,
    AgentRequest,
    AgentResult,
    AgentResultStatus,
    derive_stable_identities,
)
from src.memory.agent.reasoner import AgentReasoner
from src.memory.agent.tooling import ToolRegistry
from src.memory.runtime.agent_runtime import AgentRuntime
from src.memory.task_state.models import TaskIntent
from src.memory.core.diagnostics import log_boundary_failure
from src.memory.llm.errors import ProviderError


logger = logging.getLogger(__name__)


class PreparedTurnRunner(Protocol):
    """Optional orchestration after the existing user-turn ingestion boundary."""
    def replay(self, request: AgentRequest) -> Optional[AgentResult]: ...
    def run_prepared(self, context: Any, request: AgentRequest) -> AgentResult: ...


class MainAgent:
    """Public facade for Main Agent reasoning and execution.

    Callers invoke MainAgent.run(request) which orchestrates AgentRuntime.process_user_turn(),
    Task 3/Task 2 contexts, model reasoning, confirmation policies, and tool execution.
    """

    def __init__(
        self,
        runtime: AgentRuntime,
        journal: AgentExecutionJournal,
        registry: ToolRegistry,
        reasoner: AgentReasoner,
        config: Optional[AgentLoopConfig] = None,
        memory_writer: Optional[Callable[[str, str], Any]] = None,
        prepared_runner: Optional[PreparedTurnRunner] = None,
    ) -> None:
        self.runtime = runtime
        self.journal = journal
        self.registry = registry
        self.reasoner = reasoner
        self.memory_writer = memory_writer
        self.prepared_runner = prepared_runner
        self.loop_controller = AgentLoopController(
            runtime=runtime,
            journal=journal,
            registry=registry,
            reasoner=reasoner,
            config=config,
        )
        # Single-process CLI observability projection. The durable execution
        # journal remains authoritative for tool execution; this pointer only
        # exposes the request-local retrieval bundle to the app trace writer.
        self.last_turn_context = None

    def run(self, request: AgentRequest) -> AgentResult:
        if self.prepared_runner is not None and request.confirmation is None:
            cached = self.prepared_runner.replay(request)
            if cached is not None:
                return cached
        turn_req = request.turn_request
        request_turn_id = turn_req.turn_id
        orig_root_turn_id = request_turn_id
        confirmed_run_id: Optional[str] = None

        # 1. Resolve Task ID and Confirmation if ActionConfirmation is present
        if request.confirmation is not None:
            pending = self.journal.get_pending_action(request.confirmation.pending_action_id)
            if pending is None:
                err_info = AgentErrorInfo(
                    stage="CONFIRMATION",
                    category="VALIDATION_ERROR",
                    code="PENDING_ACTION_NOT_FOUND",
                    safe_message=f"Pending action '{request.confirmation.pending_action_id}' was not found in journal.",
                )
                return AgentResult(
                    root_turn_id=request_turn_id,
                    request_turn_id=request_turn_id,
                    task_id=turn_req.task_id or "",
                    agent_run_id="",
                    status=AgentResultStatus.FAILED,
                    error_info=err_info,
                )

            pending_status = self.journal.get_pending_action_status(
                pending.pending_action_id
            )
            decision = request.confirmation.decision
            confirmable = decision.value == "CONFIRM" and pending_status in {
                "PENDING",
                "CONFIRMED",
            }
            rejectable = decision.value == "REJECT" and pending_status == "PENDING"
            if not (confirmable or rejectable):
                err_info = AgentErrorInfo(
                    stage="CONFIRMATION",
                    category="VALIDATION_ERROR",
                    code="PENDING_ACTION_NOT_ACTIONABLE",
                    safe_message=(
                        f"Pending action '{pending.pending_action_id}' is already "
                        f"{pending_status or 'MISSING'} and cannot accept this decision."
                    ),
                )
                return AgentResult(
                    root_turn_id=request_turn_id,
                    request_turn_id=request_turn_id,
                    task_id=pending.task_id,
                    agent_run_id=pending.agent_run_id,
                    status=AgentResultStatus.FAILED,
                    error_info=err_info,
                )

            # Verify run exists and matches task_id & identity
            run_row = self.journal.get_run(pending.agent_run_id)
            if run_row is None or run_row["task_id"] != pending.task_id:
                err_info = AgentErrorInfo(
                    stage="CONFIRMATION",
                    category="INTEGRITY_ERROR",
                    code="JOURNAL_INTEGRITY_ERROR",
                    safe_message=f"Pending action '{pending.pending_action_id}' references corrupted or missing agent run '{pending.agent_run_id}'.",
                )
                return AgentResult(
                    root_turn_id=request_turn_id,
                    request_turn_id=request_turn_id,
                    task_id=pending.task_id,
                    agent_run_id=pending.agent_run_id,
                    status=AgentResultStatus.FAILED,
                    error_info=err_info,
                )

            orig_root_turn_id = run_row["root_turn_id"]
            confirmed_run_id = pending.agent_run_id

            calc_ids = derive_stable_identities(run_row["root_turn_id"], run_row["task_id"])
            if calc_ids["agent_run_id"] != pending.agent_run_id:
                err_info = AgentErrorInfo(
                    stage="CONFIRMATION",
                    category="INTEGRITY_ERROR",
                    code="JOURNAL_INTEGRITY_ERROR",
                    safe_message=f"Pending action run ID '{pending.agent_run_id}' does not match calculated run identity.",
                )
                return AgentResult(
                    root_turn_id=run_row["root_turn_id"],
                    request_turn_id=request_turn_id,
                    task_id=pending.task_id,
                    agent_run_id=pending.agent_run_id,
                    status=AgentResultStatus.FAILED,
                    error_info=err_info,
                )

            # If user omitted task_id in TurnRequest, resolve it from durable PendingAction
            if not turn_req.task_id:
                turn_req = replace(turn_req, task_id=pending.task_id)
            elif turn_req.task_id != pending.task_id:
                err_info = AgentErrorInfo(
                    stage="CONFIRMATION",
                    category="VALIDATION_ERROR",
                    code="CONFIRMATION_TASK_MISMATCH",
                    safe_message=f"Provided task_id '{turn_req.task_id}' does not match pending action task_id '{pending.task_id}'.",
                )
                return AgentResult(
                    root_turn_id=run_row["root_turn_id"],
                    request_turn_id=request_turn_id,
                    task_id=turn_req.task_id,
                    agent_run_id=pending.agent_run_id,
                    status=AgentResultStatus.FAILED,
                    error_info=err_info,
                )

        # 2. Trigger System Orchestration via AgentRuntime. A confirmation is
        # a trusted control-flow event, not a new semantic user turn; avoid
        # asking Task 3's Provider to interpret it as durable task state.
        if request.confirmation is not None and not turn_req.skip_task_state_update:
            turn_req = replace(turn_req, skip_task_state_update=True)
        turn_res = self.runtime.process_user_turn(turn_req)

        # A confirmation turn is part of a sensitive action lifecycle.  Do
        # not execute the pending side effect if the pre-confirmation Task 3
        # interpretation itself was not valid, even though ordinary turns may
        # continue with a warning when that auxiliary update fails.
        if request.confirmation is not None and turn_res.context is not None:
            task_update_warning = any(
                trace.stage.value == "TASK_UPDATE"
                and trace.status.value == "WARNING"
                for trace in turn_res.stage_traces
            )
            if task_update_warning:
                return AgentResult(
                    root_turn_id=orig_root_turn_id,
                    request_turn_id=request_turn_id,
                    task_id=turn_res.task_id or turn_req.task_id or "",
                    agent_run_id=confirmed_run_id or "",
                    status=AgentResultStatus.FAILED,
                    warnings=turn_res.warnings,
                    error_info=AgentErrorInfo(
                        stage="TASK_UPDATE",
                        category="TASK_STATE_ERROR",
                        code="CONFIRMATION_TASK_UPDATE_UNAVAILABLE",
                        safe_message=(
                            "确认操作前任务状态未能安全更新，待确认操作尚未执行；请稍后重试。"
                        ),
                    ),
                )

        # 3. Handle Runtime Errors / Task Ambiguity without invoking Reasoner
        if not turn_res.is_success or turn_res.context is None:
            err_info = (
                AgentErrorInfo.from_turn_error_info(turn_res.error_info)
                if turn_res.error_info
                else AgentErrorInfo(
                    stage="RUNTIME",
                    category="UNKNOWN",
                    code="RUNTIME_EXECUTION_FAILED",
                    safe_message="Runtime execution failed without error_info",
                )
            )

            status = (
                AgentResultStatus.TASK_SELECTION_REQUIRED
                if err_info.code == "TASK_SELECTION_REQUIRED"
                else AgentResultStatus.FAILED
            )

            identities = derive_stable_identities(request_turn_id, turn_res.task_id or "")
            eff_run_id = confirmed_run_id if confirmed_run_id else identities["agent_run_id"]
            return AgentResult(
                root_turn_id=orig_root_turn_id,
                request_turn_id=request_turn_id,
                task_id=turn_res.task_id or "",
                agent_run_id=eff_run_id,
                status=status,
                warnings=turn_res.warnings,
                error_info=err_info,
            )

        # 4. Delegate to Loop Controller for Reasoning and Tool Dispatch
        turn_context = turn_res.context
        self.last_turn_context = turn_context
        analysis = turn_context.task_analysis
        if analysis is not None and analysis.query_intent is TaskIntent.EXPLICIT_MEMORY_WRITE:
            return self._run_explicit_memory_write(
                request_turn_id=request_turn_id,
                task_id=turn_context.task_id,
                analysis=analysis,
                warnings=turn_context.warnings,
            )
        if self.prepared_runner is not None and request.confirmation is None:
            return self.prepared_runner.run_prepared(turn_context, request)
        result = self.loop_controller.run_loop(
            turn_context=turn_context,
            user_input=turn_req.user_input,
            recent_history=request.recent_history,
            confirmation=request.confirmation,
            conversation_context=request.conversation_context,
            context_overflow_handler=request.context_overflow_handler,
        )
        if self.loop_controller.last_tool_stage_traces:
            result = replace(
                result,
                trace_metadata={
                    **result.trace_metadata,
                    "tool_stage_traces": tuple(
                        self.loop_controller.last_tool_stage_traces
                    ),
                },
            )
            if request.confirmation is not None and any(
                trace.stage.value == "TASK_UPDATE"
                and trace.status.value == "WARNING"
                for trace in self.loop_controller.last_tool_stage_traces
            ):
                result = replace(
                    result,
                    status=AgentResultStatus.FAILED,
                    final_response="",
                    error_info=AgentErrorInfo(
                        stage="TASK_UPDATE",
                        category="TASK_STATE_ERROR",
                        code="CONFIRMATION_TOOL_STATE_UNAVAILABLE",
                        safe_message=(
                            "副作用操作可能已经执行，但其结果未能安全写入任务状态；"
                            "请检查工具执行记录后再继续。"
                        ),
                    ),
                )
        return result

    def _run_explicit_memory_write(
        self,
        *,
        request_turn_id: str,
        task_id: str,
        analysis: Any,
        warnings: tuple[str, ...],
    ) -> AgentResult:
        """Commit a provider-routed explicit memory request through the app boundary."""
        identities = derive_stable_identities(request_turn_id, task_id)
        content = analysis.memory_content
        if self.memory_writer is None or not isinstance(content, str) or not content.strip():
            return AgentResult(
                root_turn_id=request_turn_id,
                request_turn_id=request_turn_id,
                task_id=task_id,
                agent_run_id=identities["agent_run_id"],
                status=AgentResultStatus.FAILED,
                warnings=warnings,
                error_info=AgentErrorInfo(
                    stage="MEMORY",
                    category="CONFIGURATION_ERROR",
                    code="MEMORY_WRITE_ROUTE_UNAVAILABLE",
                    safe_message="无法执行显式长期记忆写入。",
                ),
            )
        try:
            result = self.memory_writer(content.strip(), request_turn_id)
            memory_id = result.get("memory_id") if isinstance(result, dict) else result
            if not isinstance(memory_id, str) or not memory_id.strip():
                raise RuntimeError("memory writer returned no durable memory identity")
        except ProviderError as exc:
            return AgentResult(
                root_turn_id=request_turn_id,
                request_turn_id=request_turn_id,
                task_id=task_id,
                agent_run_id=identities["agent_run_id"],
                status=AgentResultStatus.FAILED,
                warnings=warnings,
                error_info=AgentErrorInfo.from_provider_error(exc),
            )
        except Exception as exc:
            metadata = log_boundary_failure(
                logger,
                "explicit_memory_write_failed",
                exc,
                turn_id=request_turn_id,
                task_id=task_id,
            )
            return AgentResult(
                root_turn_id=request_turn_id,
                request_turn_id=request_turn_id,
                task_id=task_id,
                agent_run_id=identities["agent_run_id"],
                status=AgentResultStatus.FAILED,
                warnings=warnings,
                error_info=AgentErrorInfo(
                    stage="MEMORY",
                    category="MEMORY_ERROR",
                    code="MEMORY_WRITE_FAILED",
                    safe_message="长期记忆写入失败。",
                    details={"error_type": metadata["error_type"]},
                ),
            )
        return AgentResult(
            root_turn_id=request_turn_id,
            request_turn_id=request_turn_id,
            task_id=task_id,
            agent_run_id=identities["agent_run_id"],
            status=AgentResultStatus.SUCCESS,
            final_response=(
                "本次请求已处理，未新增或修改长期记忆。"
                if memory_id == "noop" or (isinstance(result, dict) and result.get("updated") is False)
                else "已从有效长期记忆中移除，可在回收站恢复。"
                if isinstance(result, dict) and result.get("retention") == "RECYCLED"
                else "已记录到长期记忆。"
            ),
            warnings=warnings,
            trace_metadata={
                "explicit_memory_write": True,
                "memory_id": memory_id,
            },
        )
