"""Agent Reasoner protocol and OpenAI-compatible LLM implementation."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Protocol

from src.memory.agent.models import AgentAction, AgentActionKind, ReasonerInput
from src.memory.agent.prompt_composer import PromptComposer
from src.memory.llm.errors import ProviderError
from src.memory.llm.llm_client import LLMClientBase


class InvalidAgentActionError(Exception):
    """Raised when LLM output cannot be decoded into a valid AgentAction."""

    def __init__(self, message: str, raw_output: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.raw_output = raw_output


class AgentReasoner(Protocol):
    """Protocol for model reasoning component."""

    def reason(self, input_data: ReasonerInput) -> AgentAction: ...


AGENT_ACTION_RESPONSE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "action_kind": {
            "type": "string",
            "enum": ["FINAL_RESPONSE", "TOOL_CALL"],
        },
        # Nullable values preserve the existing optional-field semantics for
        # the two mutually exclusive action variants. DeepSeek receives this
        # schema as JSON Output and the local parser remains authoritative.
        "content": {"type": ["string", "null"]},
        "tool_name": {"type": ["string", "null"]},
        "tool_arguments": {
            # Strict provider schemas cannot express an object whose keys vary
            # with the selected tool: additionalProperties must be false, which
            # would otherwise make the only valid value an empty object.  Carry
            # the arguments as serialized JSON and validate the decoded object
            # against the trusted ToolSpec in the agent loop.
            "type": ["string", "null"],
        },
    },
    "required": ["action_kind", "content", "tool_name", "tool_arguments"],
    "additionalProperties": False,
}


class OpenAICompatibleAgentReasoner:
    """Reasoning engine leveraging LLMClientBase with structured JSON decoding."""

    def __init__(
        self,
        llm_client: LLMClientBase,
        prompt_composer: Optional[PromptComposer] = None,
        max_attempts: int = 2,
    ) -> None:
        if max_attempts <= 0:
            raise ValueError("max_attempts must be greater than zero")
        self.llm_client = llm_client
        self.prompt_composer = prompt_composer or PromptComposer()
        self.max_attempts = max_attempts

    def reason(self, input_data: ReasonerInput) -> AgentAction:
        system_prompt, user_prompt = self.prompt_composer.compose(input_data)

        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                reasoned_generate = getattr(self.llm_client, "generate_reasoned_json", None)
                if callable(reasoned_generate):
                    raw_dict = reasoned_generate(
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        response_schema=AGENT_ACTION_RESPONSE_SCHEMA,
                        schema_name="agent_action",
                    )
                else:
                    # Preserve compatibility with minimal injected clients
                    # used by integrations and tests. Production clients
                    # implement the explicit reasoning-aware operation above.
                    raw_dict = self.llm_client.generate_json(
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        response_schema=AGENT_ACTION_RESPONSE_SCHEMA,
                        schema_name="agent_action",
                    )
                return self._parse_and_validate_action(raw_dict)
            except ProviderError:
                # Provider errors (429/500/auth/timeout) propagate immediately for safe handling
                raise
            except (ValueError, TypeError, KeyError) as exc:
                last_error = exc

        raise InvalidAgentActionError(
            f"Failed to decode valid AgentAction from model after {self.max_attempts} attempts: {last_error}"
        ) from last_error

    @staticmethod
    def _parse_and_validate_action(raw: Dict[str, Any]) -> AgentAction:
        if not isinstance(raw, dict):
            raise ValueError(f"Model response must be a JSON object, got {type(raw).__name__}")

        kind_str = raw.get("action_kind")
        if kind_str not in ("FINAL_RESPONSE", "TOOL_CALL"):
            raise ValueError(f"Invalid action_kind '{kind_str}'. Must be FINAL_RESPONSE or TOOL_CALL.")

        action_kind = AgentActionKind(kind_str)

        if action_kind == AgentActionKind.FINAL_RESPONSE:
            if raw.get("tool_name") or raw.get("tool_arguments"):
                raise ValueError("FINAL_RESPONSE action cannot specify 'tool_name' or 'tool_arguments'")
            content = raw.get("content") or ""
            if not isinstance(content, str) or not content.strip():
                raise ValueError("FINAL_RESPONSE action requires non-empty string 'content'")
            return AgentAction(
                action_kind=action_kind,
                content=content.strip(),
            )
        else:  # TOOL_CALL
            if raw.get("content") and str(raw.get("content")).strip():
                raise ValueError("TOOL_CALL action cannot specify response 'content'")
            tool_name = raw.get("tool_name") or ""
            if not isinstance(tool_name, str) or not tool_name.strip():
                raise ValueError("TOOL_CALL action requires non-empty string 'tool_name'")

            args = raw.get("tool_arguments")
            if args is None:
                args = {}
            elif isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError as exc:
                    raise ValueError("TOOL_CALL 'tool_arguments' must contain valid JSON") from exc
            if not isinstance(args, dict):
                raise ValueError("TOOL_CALL 'tool_arguments' must decode to a JSON object")

            return AgentAction(
                action_kind=action_kind,
                tool_name=tool_name.strip(),
                tool_arguments=args,
            )
