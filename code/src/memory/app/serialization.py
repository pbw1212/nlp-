"""Client-neutral result serialization shared by JSONL hosts."""
from __future__ import annotations
import json
from typing import Any, Dict, Optional
from src.memory.agent.models import AgentResult, AgentResultStatus, PendingAction

def _user_response(result: AgentResult) -> str:
    """Return the user-facing result without the developer receipt."""
    if result.final_response:
        return result.final_response
    if result.status is AgentResultStatus.AWAITING_CONFIRMATION and result.pending_action:
        action = result.pending_action
        return (
            "Agent 请求执行具有副作用的操作，需要你的确认。\n"
            f"工具：{action.tool_name}\n"
            f"参数：{json.dumps(action.validated_arguments, ensure_ascii=False, sort_keys=True)}\n"
            "输入 /confirm 确认，或 /reject 拒绝。"
        )
    if result.error_info is not None:
        return result.error_info.safe_message
    return {
        AgentResultStatus.STEP_LIMIT_REACHED: "本次 Agent 未能在允许的步骤数内完成任务。",
        AgentResultStatus.CANCELLED: "本轮操作已取消。",
        AgentResultStatus.TASK_SELECTION_REQUIRED: "当前请求需要先确定关联任务。",
        AgentResultStatus.EXECUTION_STATUS_UNKNOWN: "工具执行状态暂时无法安全确认。",
    }.get(result.status, "Agent 已完成这一步。")

def _serialize_result(result: AgentResult, *, display_text: Optional[str] = None) -> Dict[str, Any]:
    metadata = result.trace_metadata or {}
    developer_details = {
        "summary": "Worked for " + (
            f"{metadata['turn_elapsed_seconds']:.3f}s"
            if isinstance(metadata.get("turn_elapsed_seconds"), (int, float))
            else "unknown time"
        ),
        "elapsed_seconds": metadata.get("turn_elapsed_seconds"),
        "status": result.status.value,
        "step_count": result.step_count,
        "runtime_stage_traces": metadata.get("runtime_stage_traces", ()),
        "tool_details": metadata.get("tool_details", ()),
        "provider_usage_delta": metadata.get("provider_usage_delta", {}),
        "provider_reasoning_content": metadata.get("provider_reasoning_content", ""),
        "warnings": list(result.warnings),
    }
    if result.error_info is not None:
        developer_details["error"] = {
            "stage": result.error_info.stage,
            "category": result.error_info.category,
            "code": result.error_info.code,
            "safe_message": result.error_info.safe_message,
            "retryable": result.error_info.retryable,
            "details": result.error_info.details,
        }
    payload: Dict[str, Any] = {
        "root_turn_id": result.root_turn_id,
        "request_turn_id": result.request_turn_id,
        "task_id": result.task_id,
        "agent_run_id": result.agent_run_id,
        "status": result.status.value,
        "final_response": result.final_response,
        "executed_tool_call_ids": list(result.executed_tool_call_ids),
        "step_count": result.step_count,
        "warnings": list(result.warnings),
        "trace_metadata": result.trace_metadata,
        "user_response": _user_response(result),
        # Presentation is structured so browser clients can place the
        # collapsible developer receipt above the answer without parsing the
        # CLI's human-readable text.
        "developer_details": developer_details,
    }
    if display_text is not None:
        payload["display_text"] = display_text
    if result.pending_action is not None:
        payload["pending_action"] = _serialize_pending_action(result.pending_action)
    if result.error_info is not None:
        payload["error_info"] = {
            "stage": result.error_info.stage,
            "category": result.error_info.category,
            "code": result.error_info.code,
            "safe_message": result.error_info.safe_message,
            "retryable": result.error_info.retryable,
            "details": result.error_info.details,
        }
    return payload

def _serialize_pending_action(action: PendingAction) -> Dict[str, Any]:
    return {
        "pending_action_id": action.pending_action_id,
        "task_id": action.task_id,
        "tool_name": action.tool_name,
        "validated_arguments": action.validated_arguments,
        "effect": action.effect,
    }
