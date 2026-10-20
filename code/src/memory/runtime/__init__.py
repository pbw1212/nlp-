"""Phase 4A AgentRuntime package."""

from src.memory.runtime.agent_runtime import AgentRuntime
from src.memory.runtime.builder import build_agent_runtime
from src.memory.runtime.config import AgentRuntimeConfig
from src.memory.runtime.events import AgentEvent, AgentEventStore, AgentEventStream
from src.memory.runtime.errors import (
    AgentRuntimeError,
    TaskLifecycleError,
    TaskNotFoundError,
    TaskSelectionRequiredError,
)
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
    ProviderTaskBindingResolver,
    TaskBindingAction,
    TaskBindingDecision,
    TaskBindingResolver,
    TaskRouter,
    UNBOUND_TASK_ID,
)

__all__ = [
    "AgentRuntime",
    "AgentRuntimeConfig",
    "AgentEvent",
    "AgentEventStore",
    "AgentEventStream",
    "AgentRuntimeError",
    "AgentTurnContext",
    "BaselineTaskRouter",
    "ProviderTaskBindingResolver",
    "TaskBindingAction",
    "TaskBindingDecision",
    "TaskBindingResolver",
    "TaskLifecycleError",
    "TaskNotFoundError",
    "TaskRouter",
    "TaskSelectionRequiredError",
    "ToolCallResult",
    "ToolCallStatus",
    "TurnErrorInfo",
    "TurnExecutionResult",
    "TurnRequest",
    "TurnStage",
    "TurnStageStatus",
    "TurnStageTrace",
    "UNBOUND_TASK_ID",
    "build_agent_runtime",
]
