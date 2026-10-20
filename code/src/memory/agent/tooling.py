"""Trusted Tool Registry, Tool Specs, Argument Validator, and Execution Contracts."""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional, Protocol, Tuple

from src.memory.runtime.models import ToolCallResult
from src.memory.core.diagnostics import log_boundary_failure


logger = logging.getLogger(__name__)


class ToolEffect(str, Enum):
    READ_ONLY = "READ_ONLY"
    INTERNAL = "INTERNAL"
    SIDE_EFFECT = "SIDE_EFFECT"


class IdempotencySupport(str, Enum):
    NONE = "NONE"
    SUPPORTED = "SUPPORTED"


import jsonschema
from jsonschema.exceptions import SchemaError, ValidationError


@dataclass(frozen=True)
class ToolSpec:
    """Trusted tool specification registered in Python application code.

    Policy Rule: ALL SIDE_EFFECT tools MANDATORILY require user confirmation in V1.
    INTERNAL tools are bounded application-owned state operations. They execute
    through the same durable journal, but are not external side effects and do
    not require a second confirmation after the user has authorized the Agent
    to maintain its own state.
    ToolEffect MUST be explicitly specified (no default fallback).
    """

    name: str
    description: str
    parameters_schema: Dict[str, Any]
    effect: ToolEffect
    idempotency_support: IdempotencySupport = IdempotencySupport.NONE
    returns_user_facing_result: bool = False

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("Tool name cannot be empty")
        if not self.description:
            raise ValueError("Tool description cannot be empty")
        if not isinstance(self.parameters_schema, dict):
            raise ValueError("Tool parameters_schema must be a dict/object")
        if not isinstance(self.effect, ToolEffect):
            raise ValueError("Tool effect must be an explicit ToolEffect")
        if not isinstance(self.idempotency_support, IdempotencySupport):
            raise ValueError("Tool idempotency_support must be an IdempotencySupport")
        if not isinstance(self.returns_user_facing_result, bool):
            raise ValueError("Tool returns_user_facing_result must be a bool")
        if self.returns_user_facing_result and self.effect != ToolEffect.READ_ONLY:
            raise ValueError("Only READ_ONLY tools may return results directly to users")

    def to_public_dict(self) -> Dict[str, Any]:
        """Return the stable, client-facing capability description.

        The registry remains the authority for executable tools.  A caller
        receives a defensive copy of the JSON schema, so a UI or an external
        protocol adapter cannot mutate the schema used for dispatch.
        """
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": deepcopy(self.parameters_schema),
            "effect": self.effect.value,
            "idempotencySupport": self.idempotency_support.value,
            "returnsUserFacingResult": self.returns_user_facing_result,
        }


@dataclass(frozen=True)
class ToolExecutionRequest:
    """Agent-level tool execution request containing run context and idempotency key."""

    tool_call_id: str
    tool_name: str
    arguments: Dict[str, Any]
    idempotency_key: str
    agent_run_id: str
    agent_step_id: str

    def __post_init__(self) -> None:
        identities = (
            self.tool_call_id,
            self.tool_name,
            self.idempotency_key,
            self.agent_run_id,
            self.agent_step_id,
        )
        if any(not isinstance(value, str) or not value.strip() for value in identities):
            raise ValueError("tool execution identities must be non-empty strings")
        if not isinstance(self.arguments, dict):
            raise ValueError("tool execution arguments must be a dict/object")


class ToolExecutor(Protocol):
    """Protocol for concrete tool execution adapters."""

    def execute(self, request: ToolExecutionRequest) -> ToolCallResult: ...


def validate_tool_arguments(schema: Dict[str, Any], arguments: Dict[str, Any]) -> Tuple[bool, str]:
    """Validates tool arguments against trusted ToolSpec parameters_schema using official jsonschema.

    Returns:
        (is_valid, error_message)
    """
    if not isinstance(arguments, dict):
        return False, f"Tool arguments must be a dict/object, got {type(arguments).__name__}"

    if not schema:
        return True, ""

    try:
        jsonschema.validate(instance=arguments, schema=schema)
        return True, ""
    except ValidationError as exc:
        return False, f"Invalid tool arguments: {exc.message}"
    except Exception as exc:
        metadata = log_boundary_failure(logger, "tool_schema_validation_failed", exc)
        return False, f"Schema validation failed ({metadata['error_type']})"


class ToolRegistry:
    """Trusted registry for tool definitions and executors."""

    def __init__(self) -> None:
        self._specs: Dict[str, ToolSpec] = {}
        self._executors: Dict[str, ToolExecutor] = {}

    def register(self, spec: ToolSpec, executor: ToolExecutor) -> None:
        if spec.name in self._specs:
            raise ValueError(f"Tool '{spec.name}' is already registered.")
        if spec.parameters_schema:
            try:
                jsonschema.Draft202012Validator.check_schema(spec.parameters_schema)
            except SchemaError as s_err:
                raise ValueError(f"Invalid parameters_schema for tool '{spec.name}': {s_err.message}") from s_err

        self._specs[spec.name] = spec
        self._executors[spec.name] = executor

    def get_spec(self, name: str) -> Optional[ToolSpec]:
        return self._specs.get(name)

    def get_executor(self, name: str) -> Optional[ToolExecutor]:
        return self._executors.get(name)

    def list_specs(self) -> Tuple[ToolSpec, ...]:
        return tuple(self._specs.values())

    def describe(self) -> Tuple[Dict[str, Any], ...]:
        """Return a deterministic public catalog for clients and audits."""
        return tuple(
            spec.to_public_dict()
            for spec in sorted(self._specs.values(), key=lambda item: item.name)
        )

    def validate_arguments(self, tool_name: str, arguments: Dict[str, Any]) -> Tuple[bool, str]:
        spec = self.get_spec(tool_name)
        if not spec:
            return False, f"Tool '{tool_name}' is not registered in ToolRegistry"
        return validate_tool_arguments(spec.parameters_schema, arguments)
