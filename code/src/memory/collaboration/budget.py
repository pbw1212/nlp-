"""A serial provider adapter; reservations survive process death and replay."""

from __future__ import annotations

import math
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from threading import RLock
from typing import Any

from src.memory.collaboration.models import canonical
from src.memory.collaboration.repository import (
    CoordinationConflict,
    CoordinationRepository,
)
from src.memory.llm.errors import ProviderError
from src.memory.llm.llm_client import LLMClientBase


class CollaborationBudgetError(ProviderError):
    def __init__(self) -> None:
        super().__init__(
            "collaboration budget exhausted",
            provider="coordinator",
            operation="model_budget",
        )

    def to_safe_projection(self) -> dict[str, Any]:
        return {
            **super().to_safe_projection(),
            "code": "COLLABORATION_BUDGET_EXHAUSTED",
            "safe_message": "协作调用预算或时限已用尽。",
            "retryable": False,
        }


@dataclass(frozen=True)
class CollaborationLimits:
    max_model_calls: int = 24
    max_token_units: int = 200000
    deadline_seconds: float = 180
    max_completion_tokens: int = 4096

    def __post_init__(self) -> None:
        integer_limits = (
            self.max_model_calls,
            self.max_token_units,
            self.max_completion_tokens,
        )
        if (
            any(type(value) is not int or value <= 0 for value in integer_limits)
            or isinstance(self.deadline_seconds, bool)
            or not isinstance(self.deadline_seconds, (int, float))
            or not math.isfinite(self.deadline_seconds)
            or self.deadline_seconds <= 0
        ):
            raise ValueError("collaboration limits must be positive")


class BudgetedLLMClient(LLMClientBase):
    """All runtime and role calls use one reservation ledger, never per-role caps.

    P1 is serial. A lock protects a borrowed client's mutable usage fields.
    Real providers must expose a completion ceiling and disable hidden retries.
    Injected offline clients may omit transport settings and usage.
    """

    def __init__(
        self,
        client: LLMClientBase,
        repository: CoordinationRepository,
        limits: CollaborationLimits,
    ) -> None:
        self.client, self.repository, self.limits = client, repository, limits
        if hasattr(client, "max_retries") and client.max_retries != 1:
            raise ValueError("multi_fixed requires provider max_retries=1")
        if hasattr(client, "max_completion_tokens") and (
            client.max_completion_tokens is None
            or client.max_completion_tokens > limits.max_completion_tokens
        ):
            raise ValueError(
                "provider completion ceiling exceeds collaboration reservation"
            )
        self._active: ContextVar[str | None] = ContextVar(
            "collaboration_budget", default=None
        )
        self._role: ContextVar[str] = ContextVar(
            "collaboration_role", default="Runtime"
        )
        self._lock = RLock()

    @property
    def active_run_id(self) -> str | None:
        return self._active.get()

    @contextmanager
    def turn(self, session_id: str, turn_id: str, user_input: str) -> Iterator[str]:
        run_id = self.repository.begin(
            session_id, turn_id, user_input, asdict(self.limits)
        )
        token = self._active.set(run_id)
        try:
            yield run_id
        finally:
            self._active.reset(token)

    @contextmanager
    def role(self, name: str) -> Iterator[None]:
        token = self._role.set(name)
        try:
            yield
        finally:
            self._role.reset(token)

    @property
    def last_usage(self) -> dict[str, int]:
        return dict(self.client.last_usage)

    @property
    def usage(self) -> dict[str, int]:
        return dict(getattr(self.client, "usage", {}))

    @property
    def last_reasoning_content(self) -> str:
        return self.client.last_reasoning_content

    def clear_last_reasoning_content(self) -> None:
        self.client.clear_last_reasoning_content()

    def set_progress_callback(self, callback: Any) -> None:
        setter = getattr(self.client, "set_progress_callback", None)
        if callable(setter):
            setter(callback)

    def _call(self, method: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        with self._lock:
            run_id = self.active_run_id
            if run_id is None:
                # Explicit legacy commands remain outside multi_fixed chat turns.
                return getattr(self.client, method)(*args, **kwargs)
            units = (
                len(canonical([args, kwargs]).encode("utf-8"))
                + 256
                + self.limits.max_completion_tokens
            )
            try:
                call_id = self.repository.reserve_call(
                    run_id, self._role.get(), kwargs.get("schema_name", method), units
                )
            except CoordinationConflict as exc:
                raise CollaborationBudgetError() from exc
            started = time.monotonic()
            old_timeout = getattr(self.client, "request_timeout_seconds", None)
            if old_timeout is not None:
                elapsed = time.time() - self.repository.get_run(run_id)["started"]
                self.client.request_timeout_seconds = min(
                    old_timeout, max(0.001, self.limits.deadline_seconds - elapsed)
                )
            status = "FAILED"
            usage: dict[str, int] = {}
            try:
                result = getattr(self.client, method)(*args, **kwargs)
                usage = {
                    k: v
                    for k, v in dict(self.client.last_usage).items()
                    if isinstance(v, int) and not isinstance(v, bool) and v >= 0
                }
                if (
                    time.time() - self.repository.get_run(run_id)["started"]
                    > self.limits.deadline_seconds
                ):
                    raise CollaborationBudgetError()
                status = "SUCCEEDED"
                return result
            finally:
                if old_timeout is not None:
                    self.client.request_timeout_seconds = old_timeout
                self.repository.settle_call(
                    call_id, usage, time.monotonic() - started, status
                )

    def generate_json(
        self, system_prompt: str, user_prompt: str, **kwargs: Any
    ) -> dict[str, Any]:
        return self._call("generate_json", system_prompt, user_prompt, **kwargs)

    def generate_reasoned_json(
        self, system_prompt: str, user_prompt: str, **kwargs: Any
    ) -> dict[str, Any]:
        return self._call(
            "generate_reasoned_json", system_prompt, user_prompt, **kwargs
        )

    def generate_chat_json(
        self, messages: list[dict[str, str]], enable_web_search: bool = False
    ) -> dict[str, Any]:
        return self._call(
            "generate_chat_json", messages, enable_web_search=enable_web_search
        )
