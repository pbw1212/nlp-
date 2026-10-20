"""Domain DTOs, Actions, Identities, and Result Models for Main Agent."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Optional, Tuple

from src.memory.runtime.models import AgentTurnContext, TurnErrorInfo, TurnRequest
from src.memory.agent.tooling import ToolSpec


class AgentActionKind(str, Enum):
    FINAL_RESPONSE = "FINAL_RESPONSE"
    TOOL_CALL = "TOOL_CALL"


@dataclass(frozen=True)
class AgentAction:
    """Structured action decided by AgentReasoner (Model output).

    Note: Models can ONLY select FINAL_RESPONSE or TOOL_CALL.
    Models are NEVER permitted to decide confirmation policies.
    """

    action_kind: AgentActionKind
    content: str = ""
    tool_name: Optional[str] = None
    tool_arguments: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.action_kind == AgentActionKind.FINAL_RESPONSE:
            if not self.content or not self.content.strip():
                raise ValueError("FINAL_RESPONSE content must be non-empty string.")
            if self.tool_name is not None or self.tool_arguments:
                raise ValueError("FINAL_RESPONSE cannot carry tool fields.")
        elif self.action_kind == AgentActionKind.TOOL_CALL:
            if not self.tool_name or not self.tool_name.strip():
                raise ValueError("TOOL_CALL tool_name must be non-empty string.")
            if self.content:
                raise ValueError("TOOL_CALL cannot carry final response content.")
            if not isinstance(self.tool_arguments, dict):
                raise ValueError("TOOL_CALL arguments must be a dict/object.")


class ConfirmationDecision(str, Enum):
    CONFIRM = "CONFIRM"
    REJECT = "REJECT"


@dataclass(frozen=True)
class ActionConfirmation:
    """Explicit token binding user's confirmation decision to a pending action."""

    pending_action_id: str
    decision: ConfirmationDecision

    def __post_init__(self) -> None:
        if not self.pending_action_id:
            raise ValueError("pending_action_id cannot be empty")


@dataclass(frozen=True)
class TurnConversationItem:
    """Bounded conversation history turn representation for model context continuity."""

    role: str  # "user" | "assistant" | "system"
    content: str
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class AgentRequest:
    """Top-level request to MainAgent facade."""

    turn_request: TurnRequest
    confirmation: Optional[ActionConfirmation] = None
    recent_history: Tuple[TurnConversationItem, ...] = ()
    conversation_context: Optional[Any] = None
    context_overflow_handler: Optional[Callable[[], Any]] = None


@dataclass(frozen=True)
class ToolObservation:
    """Derived run-local observation fed into downstream Reasoner prompt steps."""

    tool_call_id: str
    tool_name: str
    status: str
    text: str
    truncated: bool = False
    original_size: int = 0
    included_size: int = 0


@dataclass(frozen=True)
class ReasonerInput:
    """Internal input model passed to AgentReasoner.reason()."""

    turn_context: AgentTurnContext
    user_input: str
    tool_specs: Tuple[ToolSpec, ...] = ()
    tool_observations: Tuple[ToolObservation, ...] = ()
    recent_history: Tuple[TurnConversationItem, ...] = ()
    conversation_context: Optional[Any] = None


@dataclass(frozen=True)
class PendingAction:
    """Durable pending action requiring explicit user confirmation before execution."""

    pending_action_id: str
    agent_run_id: str
    task_id: str
    agent_step_id: str
    tool_call_id: str
    tool_name: str
    validated_arguments: Dict[str, Any]
    arguments_hash: str
    idempotency_key: str
    effect: str  # "SIDE_EFFECT"
    created_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        identities = (
            self.pending_action_id,
            self.agent_run_id,
            self.task_id,
            self.agent_step_id,
            self.tool_call_id,
            self.tool_name,
            self.arguments_hash,
            self.idempotency_key,
        )
        if any(not isinstance(value, str) or not value.strip() for value in identities):
            raise ValueError("pending action execution identities must be non-empty strings")
        if not isinstance(self.validated_arguments, dict):
            raise ValueError("pending action arguments must be a dict/object")
        if self.effect != "SIDE_EFFECT":
            raise ValueError("pending actions are only valid for SIDE_EFFECT tools")


@dataclass(frozen=True)
class AgentErrorInfo:
    """Sanitized, structured error details for MainAgent failures."""

    stage: str
    category: str
    code: str
    safe_message: str
    retryable: bool = False
    details: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_turn_error_info(cls, err: TurnErrorInfo) -> AgentErrorInfo:
        return cls(
            stage=err.stage.value if hasattr(err.stage, "value") else str(err.stage),
            category=err.category,
            code=err.code,
            safe_message=err.safe_message,
            retryable=err.retryable,
            details=err.details or {},
        )

    @classmethod
    def from_provider_error(cls, err: Any) -> AgentErrorInfo:
        proj = err.to_safe_projection() if hasattr(err, "to_safe_projection") else {}
        details = {
            "provider": proj.get("provider"),
            "operation": proj.get("operation"),
            "status_code": proj.get("status_code"),
        }
        for key in (
            "provider_error_type",
            "provider_error_code",
            "request_id",
            "retry_after",
        ):
            if proj.get(key) is not None:
                details[key] = proj[key]
        return cls(
            stage="REASONING",
            category=proj.get("category", "PROVIDER_ERROR"),
            code=proj.get("code", "PROVIDER_FAILED"),
            safe_message=proj.get("safe_message", "Provider request failed."),
            retryable=proj.get("retryable", False),
            details=proj.get("details") or details,
        )


class AgentResultStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    STEP_LIMIT_REACHED = "STEP_LIMIT_REACHED"
    TASK_SELECTION_REQUIRED = "TASK_SELECTION_REQUIRED"
    EXECUTION_STATUS_UNKNOWN = "EXECUTION_STATUS_UNKNOWN"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class AgentResult:
    """Public execution result returned by MainAgent.run()."""

    root_turn_id: str
    task_id: str
    agent_run_id: str
    status: AgentResultStatus
    request_turn_id: str = ""
    final_response: Optional[str] = None
    executed_tool_call_ids: Tuple[str, ...] = ()
    pending_action: Optional[PendingAction] = None
    step_count: int = 0
    warnings: Tuple[str, ...] = ()
    error_info: Optional[AgentErrorInfo] = None
    trace_metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        return self.status == AgentResultStatus.SUCCESS and self.final_response is not None


def canonical_json_bytes(data: Dict[str, Any]) -> bytes:
    """Produces stable UTF-8 JSON bytes with sorted keys."""
    return json.dumps(data or {}, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def derive_stable_identities(
    root_turn_id: str,
    task_id: str,
    step_index: int = 0,
    tool_name: str = "",
    tool_arguments: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """Derives deterministic, restart-safe, cross-process stable identity strings via SHA-256.

    Python built-in hash() is strictly FORBIDDEN to ensure cross-process and cross-restart stability.
    """
    run_seed = f"{root_turn_id}:{task_id}".encode("utf-8")
    agent_run_id = f"run_{hashlib.sha256(run_seed).hexdigest()[:12]}"

    step_seed = f"{agent_run_id}:{step_index}".encode("utf-8")
    agent_step_id = f"step_{hashlib.sha256(step_seed).hexdigest()[:12]}"

    args_bytes = canonical_json_bytes(tool_arguments or {})
    arguments_hash = hashlib.sha256(args_bytes).hexdigest()[:12]

    if tool_name:
        call_seed = f"{agent_step_id}:{tool_name}:{arguments_hash}".encode("utf-8")
        tool_call_id = f"call_{hashlib.sha256(call_seed).hexdigest()[:12]}"

        pending_seed = f"pending:{tool_call_id}".encode("utf-8")
        pending_action_id = f"pending_{hashlib.sha256(pending_seed).hexdigest()[:12]}"

        idemp_seed = f"{agent_run_id}:{agent_step_id}:{tool_name}:{arguments_hash}".encode("utf-8")
        idempotency_key = f"idemp_{hashlib.sha256(idemp_seed).hexdigest()[:16]}"
    else:
        tool_call_id = ""
        pending_action_id = ""
        idempotency_key = ""

    return {
        "agent_run_id": agent_run_id,
        "agent_step_id": agent_step_id,
        "arguments_hash": arguments_hash,
        "tool_call_id": tool_call_id,
        "pending_action_id": pending_action_id,
        "idempotency_key": idempotency_key,
    }
