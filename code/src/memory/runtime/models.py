"""Phase 4A AgentRuntime DTOs and value models."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Tuple

from src.memory.retrieval.evidence import EvidenceBundle
from src.memory.task_state.maintenance import MaintenanceResult
from src.memory.task_state.models import (
    ActiveWorkingContext,
    ExternalObservation,
    TaskAnalysis,
    StructuredTaskContext,
    TaskStateUpdateReceipt,
)


class TurnStage(str, Enum):
    TASK_BINDING = "TASK_BINDING"
    TASK_RESOLUTION = "TASK_RESOLUTION"
    TASK_UPDATE = "TASK_UPDATE"
    MAINTENANCE = "MAINTENANCE"
    RETRIEVAL = "RETRIEVAL"
    CONTEXT_BUILD = "CONTEXT_BUILD"


class TurnStageStatus(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    SUCCESS = "SUCCESS"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"
    WARNING = "WARNING"


class ToolCallStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


@dataclass(frozen=True)
class TurnStageTrace:
    """Telemetry and status for a single turn execution stage."""

    stage: TurnStage
    status: TurnStageStatus
    warnings: Tuple[str, ...] = ()
    duration_seconds: float = 0.0


@dataclass(frozen=True)
class TurnErrorInfo:
    """Sanitized, serializable error representation (no raw Python Exception objects)."""

    stage: TurnStage
    category: str
    code: str
    safe_message: str
    retryable: bool = False
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TurnRequest:
    """Incoming user turn request for runtime orchestration.

    Confirmation resumes are trusted control-flow turns rather than new user
    facts. They may opt out of semantic Task 3 interpretation while still
    rebuilding the current working context for the pending action.
    """

    turn_id: str
    user_input: str
    task_id: Optional[str] = None
    session_id: Optional[str] = None
    external_observations: Tuple[ExternalObservation, ...] = ()
    timestamp: float = field(default_factory=time.time)
    skip_task_state_update: bool = False

    def __post_init__(self) -> None:
        if not self.turn_id:
            raise ValueError("turn_id 不能为空")
        if not isinstance(self.user_input, str) or not self.user_input.strip():
            raise ValueError("user_input 不能为空")
        if not isinstance(self.skip_task_state_update, bool):
            raise ValueError("skip_task_state_update 必须是布尔值")


@dataclass(frozen=True)
class ToolCallResult:
    """Result of an executed tool call passed into runtime for Task3 ingestion."""

    tool_call_id: str
    tool_name: str
    arguments: Dict[str, Any]
    status: ToolCallStatus
    result_content: str = ""
    error_message: str = ""
    timestamp: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not self.tool_call_id:
            raise ValueError("tool_call_id 不能为空")
        if not self.tool_name:
            raise ValueError("tool_name 不能为空")
        if not isinstance(self.arguments, dict):
            raise ValueError("arguments 必须是 JSON object/dict")
        if not isinstance(self.result_content, str) or not isinstance(self.error_message, str):
            raise ValueError("tool result_content/error_message 必须是字符串")
        if self.status is ToolCallStatus.SUCCESS and self.error_message:
            raise ValueError("SUCCESS tool result 不能包含 error_message")
        if self.status is ToolCallStatus.FAILED:
            if self.result_content:
                raise ValueError("FAILED tool result 不能把错误伪装为 result_content")
            if not self.error_message.strip():
                raise ValueError("FAILED tool result 必须包含 error_message")


@dataclass(frozen=True)
class AgentTurnContext:
    """Derived, request-local, noncanonical runtime input package for downstream Main Agent."""

    turn_id: str
    task_id: str
    receipt: Optional[TaskStateUpdateReceipt]
    maintenance: Optional[MaintenanceResult]
    evidence_bundle: Optional[EvidenceBundle]
    working_context: ActiveWorkingContext
    structured_task_context: StructuredTaskContext
    task_analysis: Optional[TaskAnalysis] = None
    warnings: Tuple[str, ...] = ()
    stage_traces: Tuple[TurnStageTrace, ...] = ()


@dataclass(frozen=True)
class TurnExecutionResult:
    """Top-level runtime execution output for a turn or tool result ingestion."""

    turn_id: str
    task_id: Optional[str]
    context: Optional[AgentTurnContext]
    stage_traces: Tuple[TurnStageTrace, ...]
    error_info: Optional[TurnErrorInfo] = None
    warnings: Tuple[str, ...] = ()

    @property
    def is_success(self) -> bool:
        return self.error_info is None and self.context is not None
