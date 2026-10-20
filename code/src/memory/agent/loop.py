"""Bounded Agent Loop Controller executing FSM, confirmation policies, and tool dispatch."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from src.memory.agent.journal import AgentExecutionJournal, JournalConfigurationMismatchError
from src.memory.agent.models import (
    ActionConfirmation,
    AgentActionKind,
    AgentErrorInfo,
    AgentResult,
    AgentResultStatus,
    ConfirmationDecision,
    PendingAction,
    ReasonerInput,
    ToolObservation,
    TurnConversationItem,
    derive_stable_identities,
)
from src.memory.agent.reasoner import AgentReasoner, InvalidAgentActionError
from src.memory.agent.tooling import IdempotencySupport, ToolEffect, ToolExecutionRequest, ToolRegistry
from src.memory.core.diagnostics import log_boundary_failure
from src.memory.llm.errors import ContextWindowOverflowError, ProviderError
from src.memory.runtime.agent_runtime import AgentRuntime
from src.memory.runtime.models import (
    AgentTurnContext,
    ToolCallResult,
    ToolCallStatus,
    TurnStage,
    TurnStageStatus,
)
from src.memory.runtime.task_router import UNBOUND_TASK_ID
from src.memory.task_state.models import TaskIntent


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentLoopConfig:
    max_steps: int = 6
    max_observation_chars: int = 4000

    def __post_init__(self) -> None:
        if self.max_steps <= 0:
            raise ValueError("max_steps must be greater than zero")
        if self.max_observation_chars <= 0:
            raise ValueError("max_observation_chars must be greater than zero")


@dataclass(frozen=True)
class ToolExecutionIdentity:
    agent_run_id: str
    agent_step_id: str
    tool_call_id: str
    arguments_hash: str
    idempotency_key: str


@dataclass(frozen=True)
class ReconciliationResult:
    ok: bool
    updated_context: Optional[AgentTurnContext] = None
    failure_result: Optional[AgentResult] = None
    observation: Optional[ToolObservation] = None


class AgentLoopController:
    """Manages the bounded reason-action-execution loop for MainAgent."""

    def __init__(
        self,
        runtime: AgentRuntime,
        journal: AgentExecutionJournal,
        registry: ToolRegistry,
        reasoner: AgentReasoner,
        config: Optional[AgentLoopConfig] = None,
    ) -> None:
        self.runtime = runtime
        self.journal = journal
        self.registry = registry
        self.reasoner = reasoner
        self.config = config or AgentLoopConfig()
        self.last_tool_stage_traces: List[TurnStageTrace] = []

    @staticmethod
    def _truthful_final_response(content: str, turn_context: AgentTurnContext) -> str:
        """Prevent optimistic model wording from hiding a rejected Task 3 write."""
        task_trace = next(
            (trace for trace in turn_context.stage_traces if trace.stage is TurnStage.TASK_UPDATE),
            None,
        )
        if task_trace is None or task_trace.status is not TurnStageStatus.WARNING:
            return content
        analysis = getattr(turn_context, "task_analysis", None)
        if analysis is not None and analysis.query_intent in {
            TaskIntent.CONTINUE_TASK,
            TaskIntent.RAISE_QUESTION,
        }:
            # A rejected no-op/task proposal is not a user-visible failure when
            # the Provider classified the request as ordinary conversation.
            return content
        return (
            f"{content}\n\n"
            "[系统说明] 本轮回答已生成，但任务状态未写入；如需记录这项修改，"
            "请确认目标和约束后重新说明。"
        )

    def run_loop(
        self,
        turn_context: AgentTurnContext,
        user_input: str,
        recent_history: Tuple[TurnConversationItem, ...] = (),
        confirmation: Optional[ActionConfirmation] = None,
        conversation_context: Optional[Any] = None,
        context_overflow_handler: Optional[Any] = None,
    ) -> AgentResult:
        request_turn_id = turn_context.turn_id
        task_id = turn_context.task_id
        self.last_tool_stage_traces = []

        # 1. Check Confirmation Decision (if provided)
        pending_tool_to_execute: Optional[Tuple[PendingAction, ToolExecutionIdentity]] = None
        if confirmation is not None:
            pending = self.journal.get_pending_action(confirmation.pending_action_id)
            if pending is None or pending.task_id != task_id:
                err_info = AgentErrorInfo(
                    stage="CONFIRMATION",
                    category="VALIDATION_ERROR",
                    code="CONFIRMATION_TASK_MISMATCH",
                    safe_message=f"Pending action '{confirmation.pending_action_id}' does not match task '{task_id}'.",
                )
                identities = derive_stable_identities(request_turn_id, task_id)
                self.journal.register_run(identities["agent_run_id"], request_turn_id, task_id)
                self.journal.update_run_status(identities["agent_run_id"], "FAILED")
                return AgentResult(
                    root_turn_id=request_turn_id,
                    request_turn_id=request_turn_id,
                    task_id=task_id,
                    agent_run_id=identities["agent_run_id"],
                    status=AgentResultStatus.FAILED,
                    error_info=err_info,
                    warnings=turn_context.warnings,
                )

            run_row = self.journal.get_run(pending.agent_run_id)
            if run_row is None:
                err_info = AgentErrorInfo(
                    stage="CONFIRMATION",
                    category="INTEGRITY_ERROR",
                    code="JOURNAL_INTEGRITY_ERROR",
                    safe_message=f"Agent run '{pending.agent_run_id}' not found in journal.",
                )
                return AgentResult(
                    root_turn_id=request_turn_id,
                    request_turn_id=request_turn_id,
                    task_id=task_id,
                    agent_run_id=pending.agent_run_id,
                    status=AgentResultStatus.FAILED,
                    error_info=err_info,
                )

            root_turn_id = run_row["root_turn_id"]
            agent_run_id = pending.agent_run_id

            if confirmation.decision == ConfirmationDecision.REJECT:
                pending_status = self.journal.get_pending_action_status(pending.pending_action_id)
                if pending_status != "PENDING":
                    return AgentResult(
                        root_turn_id=root_turn_id,
                        request_turn_id=request_turn_id,
                        task_id=task_id,
                        agent_run_id=agent_run_id,
                        status=AgentResultStatus.FAILED,
                        error_info=AgentErrorInfo(
                            stage="CONFIRMATION",
                            category="VALIDATION_ERROR",
                            code="PENDING_ACTION_NOT_REJECTABLE",
                            safe_message=f"Pending action '{pending.pending_action_id}' is already {pending_status}.",
                        ),
                    )
                self.journal.update_pending_action_status(pending.pending_action_id, "REJECTED")
                self.journal.update_run_status(agent_run_id, "CANCELLED")
                return AgentResult(
                    root_turn_id=root_turn_id,
                    request_turn_id=request_turn_id,
                    task_id=task_id,
                    agent_run_id=agent_run_id,
                    status=AgentResultStatus.CANCELLED,
                    final_response="Action cancelled per user rejection.",
                    pending_action=pending,
                    step_count=0,
                    warnings=turn_context.warnings,
                )
            elif confirmation.decision == ConfirmationDecision.CONFIRM:
                pending_status = self.journal.get_pending_action_status(pending.pending_action_id)
                if pending_status not in {"PENDING", "CONFIRMED"}:
                    return AgentResult(
                        root_turn_id=root_turn_id,
                        request_turn_id=request_turn_id,
                        task_id=task_id,
                        agent_run_id=agent_run_id,
                        status=AgentResultStatus.FAILED,
                        error_info=AgentErrorInfo(
                            stage="CONFIRMATION",
                            category="VALIDATION_ERROR",
                            code="PENDING_ACTION_NOT_CONFIRMABLE",
                            safe_message=f"Pending action '{pending.pending_action_id}' is already {pending_status}.",
                        ),
                    )
                if pending_status == "PENDING":
                    self.journal.update_pending_action_status(pending.pending_action_id, "CONFIRMED")
                restored_id = ToolExecutionIdentity(
                    agent_run_id=pending.agent_run_id,
                    agent_step_id=pending.agent_step_id,
                    tool_call_id=pending.tool_call_id,
                    arguments_hash=pending.arguments_hash,
                    idempotency_key=pending.idempotency_key,
                )
                pending_tool_to_execute = (pending, restored_id)
        else:
            root_turn_id = request_turn_id
            identities = derive_stable_identities(root_turn_id, task_id)
            agent_run_id = identities["agent_run_id"]
            self.journal.register_run(agent_run_id, root_turn_id, task_id)

        executed_tool_ids: List[str] = []
        tool_observations: List[ToolObservation] = []
        successful_read_only_results: Dict[str, ToolObservation] = {}
        context_overflow_retried = False

        # Task-scoped pre-reasoner deterministic reconciliation: process all task-unreconciled terminal receipts
        unreconciled = self.journal.get_unreconciled_receipts_for_task(task_id)
        for u_rec in unreconciled:
            u_call_id = u_rec["tool_call_id"]
            rec_run_id = u_rec["agent_run_id"]
            u_run = self.journal.get_run(rec_run_id)
            rec_root_turn = u_run["root_turn_id"] if u_run else root_turn_id

            rec_res = self._reconcile_tool_receipt(
                turn_context=turn_context,
                agent_run_id=rec_run_id,
                task_id=task_id,
                tool_call_id=u_call_id,
                root_turn_id=rec_root_turn,
                request_turn_id=request_turn_id,
            )
            if not rec_res.ok:
                if rec_res.failure_result is not None:
                    self.journal.update_run_status(agent_run_id, "FAILED")
                    return rec_res.failure_result
                break

            if rec_res.updated_context is not None:
                turn_context = rec_res.updated_context

            # Only append raw ToolObservation into prompt observations if receipt belongs to current run
            if rec_run_id == agent_run_id and rec_res.observation is not None:
                tool_observations.append(rec_res.observation)
                executed_tool_ids.append(u_call_id)

        # 2. Main Execution Loop
        for step_index in range(self.config.max_steps):
            # If we have a confirmed pending tool action from confirmation step, execute it immediately
            if pending_tool_to_execute is not None:
                pending, restored_id = pending_tool_to_execute
                pending_tool_to_execute = None  # Clear flag

                exec_result, obs, updated_ctx = self._execute_tool_dispatch(
                    turn_context=turn_context,
                    agent_run_id=agent_run_id,
                    step_index=step_index,
                    tool_name=pending.tool_name,
                    tool_arguments=pending.validated_arguments,
                    restored_identity=restored_id,
                    root_turn_id=root_turn_id,
                )
                turn_context = updated_ctx

                receipt = self.journal.get_receipt(pending.tool_call_id)
                if receipt is not None and receipt["status"] in {"SUCCEEDED", "FAILED"}:
                    self.journal.update_pending_action_status(
                        pending.pending_action_id, "EXECUTED"
                    )

                if exec_result.error_info is not None:
                    self.journal.update_run_status(agent_run_id, exec_result.status.value)
                    return exec_result

                if obs is not None:
                    executed_tool_ids.append(obs.tool_call_id)
                    tool_observations.append(obs)
                continue

            # Assemble Reasoner Input with refreshed turn_context
            tool_specs = self.registry.list_specs()
            reasoner_input = ReasonerInput(
                turn_context=turn_context,
                user_input=user_input,
                tool_specs=tool_specs,
                tool_observations=tuple(tool_observations),
                recent_history=recent_history,
                conversation_context=conversation_context,
            )

            # Invoke Model Reasoning
            try:
                action = self.reasoner.reason(reasoner_input)
            except ProviderError as p_err:
                if (
                    isinstance(p_err, ContextWindowOverflowError)
                    and not context_overflow_retried
                    and callable(context_overflow_handler)
                ):
                    try:
                        rebuilt_context = context_overflow_handler()
                    except Exception as compact_exc:
                        log_boundary_failure(
                            logger,
                            "agent_context_overflow_compaction_failed",
                            compact_exc,
                            agent_run_id=agent_run_id,
                            request_turn_id=request_turn_id,
                            task_id=task_id,
                            step_index=step_index,
                        )
                    else:
                        if rebuilt_context is not None:
                            conversation_context = rebuilt_context
                            context_overflow_retried = True
                            continue
                self.journal.update_run_status(agent_run_id, "FAILED")
                log_boundary_failure(
                    logger,
                    "agent_reasoner_provider_failed",
                    p_err,
                    agent_run_id=agent_run_id,
                    request_turn_id=request_turn_id,
                    task_id=task_id,
                    step_index=step_index,
                )
                err_info = AgentErrorInfo.from_provider_error(p_err)
                return AgentResult(
                    root_turn_id=root_turn_id,
                    request_turn_id=request_turn_id,
                    task_id=task_id,
                    agent_run_id=agent_run_id,
                    status=AgentResultStatus.FAILED,
                    executed_tool_call_ids=tuple(executed_tool_ids),
                    step_count=step_index,
                    error_info=err_info,
                    warnings=turn_context.warnings,
                )
            except InvalidAgentActionError as inv_err:
                self.journal.update_run_status(agent_run_id, "FAILED")
                metadata = log_boundary_failure(
                    logger,
                    "invalid_model_action",
                    inv_err,
                    agent_run_id=agent_run_id,
                    request_turn_id=request_turn_id,
                    task_id=task_id,
                    step_index=step_index,
                )
                err_info = AgentErrorInfo(
                    stage="REASONING",
                    category="INVALID_ACTION",
                    code="INVALID_MODEL_ACTION",
                    safe_message="Model returned an invalid structured action after bounded retries.",
                    details={"error_type": metadata["error_type"]},
                )
                return AgentResult(
                    root_turn_id=root_turn_id,
                    request_turn_id=request_turn_id,
                    task_id=task_id,
                    agent_run_id=agent_run_id,
                    status=AgentResultStatus.FAILED,
                    executed_tool_call_ids=tuple(executed_tool_ids),
                    step_count=step_index,
                    error_info=err_info,
                    warnings=turn_context.warnings,
                )
            except Exception as exc:
                self.journal.update_run_status(agent_run_id, "FAILED")
                metadata = log_boundary_failure(
                    logger,
                    "agent_reasoning_failed",
                    exc,
                    agent_run_id=agent_run_id,
                    request_turn_id=request_turn_id,
                    task_id=task_id,
                    step_index=step_index,
                )
                err_info = AgentErrorInfo(
                    stage="REASONING",
                    category="UNEXPECTED_ERROR",
                    code="MODEL_REASONING_FAILED",
                    safe_message="Model reasoning failed due to an unexpected internal error.",
                    details={"error_type": metadata["error_type"]},
                )
                return AgentResult(
                    root_turn_id=root_turn_id,
                    request_turn_id=request_turn_id,
                    task_id=task_id,
                    agent_run_id=agent_run_id,
                    status=AgentResultStatus.FAILED,
                    executed_tool_call_ids=tuple(executed_tool_ids),
                    step_count=step_index,
                    error_info=err_info,
                    warnings=turn_context.warnings,
                )

            # Process Reasoner Action Choice
            if action.action_kind == AgentActionKind.FINAL_RESPONSE:
                self.journal.update_run_status(agent_run_id, "COMPLETED")
                return AgentResult(
                    root_turn_id=root_turn_id,
                    request_turn_id=request_turn_id,
                    task_id=task_id,
                    agent_run_id=agent_run_id,
                    status=AgentResultStatus.SUCCESS,
                    final_response=self._truthful_final_response(action.content, turn_context),
                    executed_tool_call_ids=tuple(executed_tool_ids),
                    step_count=step_index + 1,
                    warnings=turn_context.warnings,
                )

            elif action.action_kind == AgentActionKind.TOOL_CALL:
                # AgentAction validates TOOL_CALL names at construction; the
                # fallback keeps that invariant explicit for static analysis.
                tool_name = action.tool_name or ""
                tool_args = action.tool_arguments

                # Lookup Tool Spec
                spec = self.registry.get_spec(tool_name)
                if spec is None:
                    self.journal.update_run_status(agent_run_id, "FAILED")
                    err_info = AgentErrorInfo(
                        stage="TOOL_DISPATCH",
                        category="UNKNOWN_TOOL",
                        code="UNKNOWN_TOOL",
                        safe_message=f"Model requested unknown tool '{tool_name}'.",
                    )
                    return AgentResult(
                        root_turn_id=root_turn_id,
                        request_turn_id=request_turn_id,
                        task_id=task_id,
                        agent_run_id=agent_run_id,
                        status=AgentResultStatus.FAILED,
                        executed_tool_call_ids=tuple(executed_tool_ids),
                        step_count=step_index,
                        error_info=err_info,
                        warnings=turn_context.warnings,
                    )

                # Validate Arguments against jsonschema
                val_ok, val_err = self.registry.validate_arguments(tool_name, tool_args)
                if not val_ok:
                    self.journal.update_run_status(agent_run_id, "FAILED")
                    err_info = AgentErrorInfo(
                        stage="TOOL_DISPATCH",
                        category="INVALID_ARGUMENTS",
                        code="INVALID_TOOL_ARGUMENTS",
                        safe_message=f"Arguments for tool '{tool_name}' failed schema validation: {val_err}",
                    )
                    return AgentResult(
                        root_turn_id=root_turn_id,
                        request_turn_id=request_turn_id,
                        task_id=task_id,
                        agent_run_id=agent_run_id,
                        status=AgentResultStatus.FAILED,
                        executed_tool_call_ids=tuple(executed_tool_ids),
                        step_count=step_index,
                        error_info=err_info,
                        warnings=turn_context.warnings,
                    )

                # Check Effect Policy for Confirmation
                if spec.effect == ToolEffect.SIDE_EFFECT:
                    # Side-effect requires explicit user confirmation
                    identities = derive_stable_identities(
                        root_turn_id, task_id, step_index=step_index, tool_name=tool_name, tool_arguments=tool_args
                    )
                    pending_id = f"pend_{identities['tool_call_id']}"

                    pending_action = PendingAction(
                        pending_action_id=pending_id,
                        agent_run_id=agent_run_id,
                        task_id=task_id,
                        agent_step_id=identities["agent_step_id"],
                        tool_call_id=identities["tool_call_id"],
                        tool_name=tool_name,
                        validated_arguments=tool_args,
                        arguments_hash=identities["arguments_hash"],
                        idempotency_key=identities["idempotency_key"],
                        effect=spec.effect.value,
                    )
                    self.journal.add_pending_action(pending_action)
                    self.journal.update_run_status(agent_run_id, "AWAITING_CONFIRMATION")

                    return AgentResult(
                        root_turn_id=root_turn_id,
                        request_turn_id=request_turn_id,
                        task_id=task_id,
                        agent_run_id=agent_run_id,
                        status=AgentResultStatus.AWAITING_CONFIRMATION,
                        pending_action=pending_action,
                        executed_tool_call_ids=tuple(executed_tool_ids),
                        step_count=step_index + 1,
                        warnings=turn_context.warnings,
                    )

                # Non-confirmed (read-only or internal) tool execution dispatch
                call_signature = json.dumps(
                    [tool_name, tool_args],
                    sort_keys=True,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                prior_observation = (
                    successful_read_only_results.get(call_signature)
                    if spec.effect == ToolEffect.READ_ONLY
                    else None
                )
                if prior_observation is not None:
                    self.journal.update_run_status(agent_run_id, "COMPLETED")
                    return AgentResult(
                        root_turn_id=root_turn_id,
                        request_turn_id=request_turn_id,
                        task_id=task_id,
                        agent_run_id=agent_run_id,
                        status=AgentResultStatus.SUCCESS,
                        final_response=prior_observation.text,
                        executed_tool_call_ids=tuple(executed_tool_ids),
                        step_count=step_index + 1,
                        warnings=turn_context.warnings + (
                            "Suppressed a duplicate read-only tool call and reused its successful result.",
                        ),
                        trace_metadata={"duplicate_read_only_tool_call_suppressed": True},
                    )

                exec_result, obs, updated_ctx = self._execute_tool_dispatch(
                    turn_context=turn_context,
                    agent_run_id=agent_run_id,
                    step_index=step_index,
                    tool_name=tool_name,
                    tool_arguments=tool_args,
                    root_turn_id=root_turn_id,
                )
                turn_context = updated_ctx

                if exec_result.error_info is not None:
                    self.journal.update_run_status(agent_run_id, exec_result.status.value)
                    return exec_result

                if obs is not None:
                    executed_tool_ids.append(obs.tool_call_id)
                    tool_observations.append(obs)
                    if obs.status == "SUCCESS" and spec.effect == ToolEffect.READ_ONLY:
                        successful_read_only_results[call_signature] = obs
                    if obs.status == "SUCCESS":
                        if spec.returns_user_facing_result:
                            self.journal.update_run_status(agent_run_id, "COMPLETED")
                            return AgentResult(
                                root_turn_id=root_turn_id,
                                request_turn_id=request_turn_id,
                                task_id=task_id,
                                agent_run_id=agent_run_id,
                                status=AgentResultStatus.SUCCESS,
                                final_response=obs.text,
                                executed_tool_call_ids=tuple(executed_tool_ids),
                                step_count=step_index + 1,
                                warnings=turn_context.warnings,
                                trace_metadata={"direct_tool_result": True},
                            )

        # Loop steps exhausted
        self.journal.update_run_status(agent_run_id, "STEP_LIMIT_REACHED")
        err_info = AgentErrorInfo(
            stage="LOOP",
            category="STEP_LIMIT",
            code="STEP_LIMIT_REACHED",
            safe_message=f"Agent exceeded maximum step limit ({self.config.max_steps}).",
        )
        return AgentResult(
            root_turn_id=root_turn_id,
            request_turn_id=request_turn_id,
            task_id=task_id,
            agent_run_id=agent_run_id,
            status=AgentResultStatus.STEP_LIMIT_REACHED,
            executed_tool_call_ids=tuple(executed_tool_ids),
            step_count=self.config.max_steps,
            error_info=err_info,
            warnings=turn_context.warnings,
        )

    def _reconcile_tool_receipt(
        self,
        turn_context: AgentTurnContext,
        agent_run_id: str,
        task_id: str,
        tool_call_id: str,
        root_turn_id: Optional[str] = None,
        request_turn_id: Optional[str] = None,
    ) -> ReconciliationResult:
        req_turn = request_turn_id or turn_context.turn_id
        run_row = self.journal.get_run(agent_run_id)
        orig_root = root_turn_id or (run_row["root_turn_id"] if run_row else req_turn)

        rec_row = self.journal.get_receipt(tool_call_id)
        if rec_row is None:
            err_info = AgentErrorInfo(
                stage="RUNTIME_RECONCILIATION",
                category="INTEGRITY_ERROR",
                code="RECEIPT_NOT_FOUND",
                safe_message=f"Receipt '{tool_call_id}' not found in journal.",
            )
            return ReconciliationResult(
                ok=False,
                failure_result=AgentResult(
                    root_turn_id=orig_root,
                    request_turn_id=req_turn,
                    task_id=task_id,
                    agent_run_id=agent_run_id,
                    status=AgentResultStatus.FAILED,
                    error_info=err_info,
                ),
            )

        status_str = rec_row["status"]
        if status_str not in ("SUCCEEDED", "FAILED"):
            err_info = AgentErrorInfo(
                stage="RUNTIME_RECONCILIATION",
                category="INTEGRITY_ERROR",
                code="NON_TERMINAL_RECEIPT",
                safe_message=f"Receipt '{tool_call_id}' status '{status_str}' is non-terminal and cannot be reconciled.",
            )
            return ReconciliationResult(
                ok=False,
                failure_result=AgentResult(
                    root_turn_id=orig_root,
                    request_turn_id=req_turn,
                    task_id=task_id,
                    agent_run_id=agent_run_id,
                    status=AgentResultStatus.FAILED,
                    error_info=err_info,
                ),
            )

        tool_name = rec_row["tool_name"]
        try:
            args_json = rec_row["arguments_json"]
            tool_arguments = json.loads(args_json)
            if not isinstance(tool_arguments, dict):
                raise ValueError("arguments_json must encode an object")
            result_data = json.loads(rec_row["result_json"])
            if not isinstance(result_data, dict):
                raise ValueError("result_json must encode an object")
            if result_data.get("tool_call_id") != tool_call_id:
                raise ValueError("receipt payload tool_call_id mismatch")
            if result_data.get("status") != status_str:
                raise ValueError("receipt payload status mismatch")
            result_content = result_data.get("result_content", "")
            error_message = result_data.get("error_message", "")
            if not isinstance(result_content, str) or not isinstance(error_message, str):
                raise ValueError("receipt result/error fields must be strings")
            if status_str == "SUCCEEDED" and error_message:
                raise ValueError("successful receipt cannot contain error_message")
            if status_str == "FAILED" and (result_content or not error_message.strip()):
                raise ValueError("failed receipt must contain only error_message")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            metadata = log_boundary_failure(
                logger,
                "tool_receipt_corrupt",
                exc,
                agent_run_id=agent_run_id,
                task_id=task_id,
                tool_call_id=tool_call_id,
            )
            err_info = AgentErrorInfo(
                stage="RUNTIME_RECONCILIATION",
                category="INTEGRITY_ERROR",
                code="JOURNAL_RECEIPT_CORRUPT",
                safe_message=f"Receipt '{tool_call_id}' contains invalid persisted data and was not reconciled.",
                details={"error_type": metadata["error_type"]},
            )
            return ReconciliationResult(
                ok=False,
                failure_result=AgentResult(
                    root_turn_id=orig_root,
                    request_turn_id=req_turn,
                    task_id=task_id,
                    agent_run_id=agent_run_id,
                    status=AgentResultStatus.FAILED,
                    error_info=err_info,
                ),
            )

        if task_id == UNBOUND_TASK_ID:
            self.journal.mark_receipt_reconciled(tool_call_id)
            obs_status = "SUCCESS" if status_str == "SUCCEEDED" else "FAILED"
            obs_text = result_content if obs_status == "SUCCESS" else error_message
            return ReconciliationResult(
                ok=True,
                updated_context=turn_context,
                observation=self._create_tool_observation(tool_call_id, tool_name, obs_status, obs_text),
            )

        replayed_status = ToolCallStatus.SUCCESS if status_str == "SUCCEEDED" else ToolCallStatus.FAILED
        tool_res = ToolCallResult(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments=tool_arguments,
            status=replayed_status,
            result_content=result_content,
            error_message=error_message,
        )

        turn_res = self.runtime.process_tool_result(tool_res, task_id=task_id)
        self.last_tool_stage_traces.extend(turn_res.stage_traces)

        if not turn_res.is_success or turn_res.context is None:
            err_info = (
                AgentErrorInfo.from_turn_error_info(turn_res.error_info)
                if turn_res.error_info
                else AgentErrorInfo(
                    stage="RUNTIME_RECONCILIATION",
                    category="DOMAIN_ERROR",
                    code="RUNTIME_RECONCILIATION_FAILED",
                    safe_message=f"Runtime ingestion failed for tool '{tool_name}' ({tool_call_id}).",
                )
            )
            res = AgentResult(
                root_turn_id=orig_root,
                request_turn_id=req_turn,
                task_id=task_id,
                agent_run_id=agent_run_id,
                status=AgentResultStatus.FAILED,
                warnings=turn_res.warnings,
                error_info=err_info,
            )
            return ReconciliationResult(ok=False, failure_result=res)

        self.journal.mark_receipt_reconciled(tool_call_id)

        obs_status = "SUCCESS" if status_str == "SUCCEEDED" else "FAILED"
        obs_text = result_content if obs_status == "SUCCESS" else error_message
        obs = self._create_tool_observation(tool_call_id, tool_name, obs_status, obs_text)
        return ReconciliationResult(
            ok=True,
            updated_context=turn_res.context,
            observation=obs,
        )

    def _execute_tool_dispatch(
        self,
        turn_context: AgentTurnContext,
        agent_run_id: str,
        step_index: int,
        tool_name: str,
        tool_arguments: Dict[str, Any],
        restored_identity: Optional[ToolExecutionIdentity] = None,
        root_turn_id: Optional[str] = None,
    ) -> Tuple[AgentResult, Optional[ToolObservation], AgentTurnContext]:
        task_id = turn_context.task_id
        run_row = self.journal.get_run(agent_run_id)
        effective_root_turn_id = root_turn_id or (run_row["root_turn_id"] if run_row else turn_context.turn_id)

        if restored_identity is not None:
            agent_step_id = restored_identity.agent_step_id
            tool_call_id = restored_identity.tool_call_id
            arguments_hash = restored_identity.arguments_hash
            idempotency_key = restored_identity.idempotency_key
        else:
            identities = derive_stable_identities(
                effective_root_turn_id, task_id, step_index=step_index, tool_name=tool_name, tool_arguments=tool_arguments
            )
            agent_step_id = identities["agent_step_id"]
            tool_call_id = identities["tool_call_id"]
            arguments_hash = identities["arguments_hash"]
            idempotency_key = identities["idempotency_key"]

        spec = self.registry.get_spec(tool_name)
        executor = self.registry.get_executor(tool_name)
        if not spec or not executor:
            err_info = AgentErrorInfo(
                stage="TOOL_EXECUTION",
                category="MISSING_EXECUTOR",
                code="UNKNOWN_TOOL",
                safe_message=f"No executor registered for tool '{tool_name}'.",
            )
            dummy_res = AgentResult(
                root_turn_id=effective_root_turn_id,
                request_turn_id=turn_context.turn_id,
                task_id=task_id,
                agent_run_id=agent_run_id,
                status=AgentResultStatus.FAILED,
                error_info=err_info,
            )
            dummy_obs = ToolObservation(tool_call_id=tool_call_id, tool_name=tool_name, status="FAILED", text=err_info.safe_message)
            return dummy_res, dummy_obs, turn_context

        # Atomic Journal Claim
        args_json = json.dumps(tool_arguments, sort_keys=True, ensure_ascii=False)
        try:
            claimed, status_str, receipt_data = self.journal.claim_execution(
                agent_run_id=agent_run_id,
                agent_step_id=agent_step_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                arguments_hash=arguments_hash,
                idempotency_key=idempotency_key,
                arguments_json=args_json,
                tool_effect=spec.effect.value,
                idempotency_support=spec.idempotency_support.value,
            )
        except ValueError as exc:
            metadata = log_boundary_failure(
                logger,
                "tool_execution_claim_rejected",
                exc,
                agent_run_id=agent_run_id,
                task_id=task_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
            )
            if isinstance(exc, JournalConfigurationMismatchError):
                err_info = AgentErrorInfo(
                    stage="TOOL_DISPATCH",
                    category="CONFIGURATION_MISMATCH",
                    code="CONFIGURATION_MISMATCH",
                    safe_message="Tool registry configuration conflicts with the durable execution-policy snapshot.",
                    details={"error_type": metadata["error_type"]},
                )
            else:
                err_info = AgentErrorInfo(
                    stage="TOOL_DISPATCH",
                    category="INTEGRITY_ERROR",
                    code="JOURNAL_EXECUTION_CLAIM_REJECTED",
                    safe_message="Durable tool execution claim failed an integrity check.",
                    details={"error_type": metadata["error_type"]},
                )
            dummy_res = AgentResult(
                root_turn_id=effective_root_turn_id,
                request_turn_id=turn_context.turn_id,
                task_id=task_id,
                agent_run_id=agent_run_id,
                status=AgentResultStatus.FAILED,
                error_info=err_info,
            )
            dummy_obs = ToolObservation(tool_call_id=tool_call_id, tool_name=tool_name, status="FAILED", text=err_info.safe_message)
            return dummy_res, dummy_obs, turn_context

        if not claimed:
            if status_str in ("SUCCEEDED", "FAILED"):
                rec_res = self._reconcile_tool_receipt(
                    turn_context, agent_run_id, task_id, tool_call_id, root_turn_id=effective_root_turn_id, request_turn_id=turn_context.turn_id
                )
                if not rec_res.ok:
                    fail_res = rec_res.failure_result
                    return (
                        fail_res if fail_res is not None else AgentResult(
                            root_turn_id=effective_root_turn_id, request_turn_id=turn_context.turn_id, task_id=task_id, agent_run_id=agent_run_id, status=AgentResultStatus.FAILED
                        ),
                        ToolObservation(tool_call_id=tool_call_id, tool_name=tool_name, status="FAILED", text=fail_res.error_info.safe_message if fail_res and fail_res.error_info else "Runtime reconciliation failed"),
                        turn_context,
                    )
                updated_ctx = rec_res.updated_context or turn_context
                obs = rec_res.observation
                dummy_res = AgentResult(
                    root_turn_id=effective_root_turn_id,
                    request_turn_id=turn_context.turn_id,
                    task_id=task_id,
                    agent_run_id=agent_run_id,
                    status=AgentResultStatus.SUCCESS if obs and obs.status == "SUCCESS" else AgentResultStatus.FAILED,
                )
                return dummy_res, obs, updated_ctx
            elif status_str == "DISPATCHING":
                stored_effect = receipt_data.get("tool_effect") if receipt_data else spec.effect.value
                stored_idemp = receipt_data.get("idempotency_support") if receipt_data else spec.idempotency_support.value
                if spec.effect.value != stored_effect or spec.idempotency_support.value != stored_idemp:
                    err_info = AgentErrorInfo(
                        stage="TOOL_DISPATCH",
                        category="CONFIGURATION_MISMATCH",
                        code="CONFIGURATION_MISMATCH",
                        safe_message="Tool registry configuration conflicts with durable claim execution policy snapshot.",
                    )
                    dummy_res = AgentResult(
                        root_turn_id=effective_root_turn_id,
                        request_turn_id=turn_context.turn_id,
                        task_id=task_id,
                        agent_run_id=agent_run_id,
                        status=AgentResultStatus.FAILED,
                        error_info=err_info,
                    )
                    dummy_obs = ToolObservation(tool_call_id=tool_call_id, tool_name=tool_name, status="FAILED", text=err_info.safe_message)
                    return dummy_res, dummy_obs, turn_context

                if stored_idemp == IdempotencySupport.NONE.value:
                    err_info = AgentErrorInfo(
                        stage="TOOL_DISPATCH",
                        category="UNCERTAIN_OUTCOME",
                        code="EXECUTION_STATUS_UNKNOWN",
                        safe_message=f"Tool '{tool_name}' crashed after dispatch without receipt; automatic retry forbidden.",
                    )
                    dummy_res = AgentResult(
                        root_turn_id=effective_root_turn_id,
                        request_turn_id=turn_context.turn_id,
                        task_id=task_id,
                        agent_run_id=agent_run_id,
                        status=AgentResultStatus.EXECUTION_STATUS_UNKNOWN,
                        error_info=err_info,
                    )
                    dummy_obs = ToolObservation(tool_call_id=tool_call_id, tool_name=tool_name, status="UNKNOWN", text=err_info.safe_message)
                    return dummy_res, dummy_obs, turn_context

        # Physical Tool Execution
        req = ToolExecutionRequest(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments=tool_arguments,
            agent_run_id=agent_run_id,
            agent_step_id=agent_step_id,
            idempotency_key=idempotency_key,
        )

        try:
            exec_res = executor.execute(req)
            is_success = exec_res.status == ToolCallStatus.SUCCESS
            result_content = exec_res.result_content or ""
            err_msg = exec_res.error_message or ""
        except Exception as exc:
            is_success = False
            result_content = ""
            metadata = log_boundary_failure(
                logger,
                "tool_executor_raised",
                exc,
                agent_run_id=agent_run_id,
                task_id=task_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
            )
            err_msg = f"Tool execution failed ({metadata['error_type']})."

        st_val = "SUCCEEDED" if is_success else "FAILED"
        self.journal.complete_execution(
            tool_call_id=tool_call_id,
            status=st_val,
            result_content=result_content,
            error_message=err_msg,
        )

        # Reconcile receipt with AgentRuntime / Task 3
        rec_res = self._reconcile_tool_receipt(
            turn_context, agent_run_id, task_id, tool_call_id, root_turn_id=effective_root_turn_id, request_turn_id=turn_context.turn_id
        )
        if not rec_res.ok:
            fail_res = rec_res.failure_result
            return (
                fail_res if fail_res is not None else AgentResult(
                    root_turn_id=effective_root_turn_id, request_turn_id=turn_context.turn_id, task_id=task_id, agent_run_id=agent_run_id, status=AgentResultStatus.FAILED
                ),
                ToolObservation(tool_call_id=tool_call_id, tool_name=tool_name, status="FAILED", text=fail_res.error_info.safe_message if fail_res and fail_res.error_info else "Runtime reconciliation failed"),
                turn_context,
            )

        updated_ctx = rec_res.updated_context or turn_context
        obs = rec_res.observation
        dummy_res = AgentResult(
            root_turn_id=effective_root_turn_id,
            request_turn_id=turn_context.turn_id,
            task_id=task_id,
            agent_run_id=agent_run_id,
            status=AgentResultStatus.SUCCESS if is_success else AgentResultStatus.FAILED,
        )
        return dummy_res, obs, updated_ctx

    def _create_tool_observation(
        self, tool_call_id: str, tool_name: str, status: str, text: str
    ) -> ToolObservation:
        orig_size = len(text)
        if orig_size > self.config.max_observation_chars:
            bounded_text = text[: self.config.max_observation_chars] + "... [TRUNCATED]"
            truncated = True
            included_size = len(bounded_text)
        else:
            bounded_text = text
            truncated = False
            included_size = orig_size

        return ToolObservation(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            status=status,
            text=bounded_text,
            truncated=truncated,
            original_size=orig_size,
            included_size=included_size,
        )
