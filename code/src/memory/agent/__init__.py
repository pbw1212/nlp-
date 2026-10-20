"""Main Agent package (Phase 4B)."""

from __future__ import annotations

from src.memory.agent.journal import AgentExecutionJournal
from src.memory.agent.loop import AgentLoopConfig, AgentLoopController
from src.memory.agent.main_agent import MainAgent
from src.memory.agent.models import (
    ActionConfirmation,
    AgentAction,
    AgentActionKind,
    AgentErrorInfo,
    AgentRequest,
    AgentResult,
    AgentResultStatus,
    ConfirmationDecision,
    PendingAction,
    ToolObservation,
    TurnConversationItem,
    derive_stable_identities,
)
from src.memory.agent.prompt_composer import PromptComposer
from src.memory.agent.reasoner import AgentReasoner, InvalidAgentActionError, OpenAICompatibleAgentReasoner
from src.memory.agent.tooling import (
    IdempotencySupport,
    ToolEffect,
    ToolExecutionRequest,
    ToolExecutor,
    ToolRegistry,
    ToolSpec,
    validate_tool_arguments,
)

__all__ = [
    "ActionConfirmation",
    "AgentAction",
    "AgentActionKind",
    "AgentErrorInfo",
    "AgentExecutionJournal",
    "AgentLoopConfig",
    "AgentLoopController",
    "AgentReasoner",
    "AgentRequest",
    "AgentResult",
    "AgentResultStatus",
    "ConfirmationDecision",
    "IdempotencySupport",
    "InvalidAgentActionError",
    "MainAgent",
    "OpenAICompatibleAgentReasoner",
    "PendingAction",
    "PromptComposer",
    "ToolEffect",
    "ToolExecutionRequest",
    "ToolExecutor",
    "ToolObservation",
    "ToolRegistry",
    "ToolSpec",
    "TurnConversationItem",
    "derive_stable_identities",
    "validate_tool_arguments",
]
