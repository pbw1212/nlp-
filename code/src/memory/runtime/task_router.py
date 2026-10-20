"""Task routing contracts and Provider-backed task binding decisions."""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional, Protocol, Sequence

from src.memory.llm.llm_client import LLMClientBase
from src.memory.runtime.errors import (
    TaskLifecycleError,
    TaskNotFoundError,
    TaskSelectionRequiredError,
)
from src.memory.task_state.models import TaskLedger, TaskStatus
from src.memory.task_state.service import TaskStateService


# Technical runtime sentinel. It is never inserted into Task 3 and is not a
# user-visible task. It lets the existing journal/tool identity contracts
# represent a turn that intentionally has no Task binding.
UNBOUND_TASK_ID = "__unbound__"


class TaskRouter(Protocol):
    """Protocol for task resolution."""

    def resolve_task(self, turn_id: str, requested_task_id: Optional[str] = None) -> str: ...


class TaskBindingAction(str, Enum):
    """Provider-proposed binding state for the current user turn."""

    EXISTING = "EXISTING"
    NEW = "NEW"
    UNBOUND = "UNBOUND"


@dataclass(frozen=True)
class TaskBindingDecision:
    """Typed, request-local proposal; it does not mutate the Task store."""

    action: TaskBindingAction
    task_id: Optional[str] = None
    title: Optional[str] = None

    def __post_init__(self) -> None:
        if self.action is TaskBindingAction.EXISTING and not self.task_id:
            raise ValueError("EXISTING task binding requires task_id")
        if self.action is not TaskBindingAction.EXISTING and self.task_id is not None:
            raise ValueError("only EXISTING task binding may carry task_id")
        if self.action is TaskBindingAction.NEW and not (self.title and self.title.strip()):
            raise ValueError("NEW task binding requires a non-empty title")
        if self.action is not TaskBindingAction.NEW and self.title is not None:
            raise ValueError("only NEW task binding may carry title")


class TaskBindingResolver(Protocol):
    """Resolves the task affinity of one turn without changing durable state."""

    def resolve(
        self,
        *,
        turn_id: str,
        user_input: str,
        current_task_id: Optional[str],
        tasks: Sequence[TaskLedger],
    ) -> TaskBindingDecision: ...


TASK_BINDING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": [item.value for item in TaskBindingAction]},
        "task_id": {"type": ["string", "null"]},
        "title": {"type": ["string", "null"]},
    },
    "required": ["action", "task_id", "title"],
    "additionalProperties": False,
}


TASK_BINDING_SYSTEM_PROMPT = """You decide the task binding for exactly one current user turn.
Return only the supplied JSON schema. This is a semantic proposal: you never mutate storage,
invent task IDs, or treat a task as a global user profile.

Choose EXISTING only when the current request clearly continues one of the supplied ACTIVE
tasks, and copy that task's exact task_id. Choose NEW when the request establishes a distinct
ongoing task that should have its own durable Task 3 working set; provide a concise title based
only on the current request. Choose UNBOUND for casual conversation, a one-off information
request, or when no task should receive durable Task 3 state. A new conversation session starts
with no binding, but each turn may continue, change, or release that binding based on the current
request. Do not keep a binding merely because it was used on the previous turn.

The task binding decision is independent of long-term personal memory. A user fact may require
long-term memory evaluation even when this decision is UNBOUND, and a task-related turn may also
contain durable personal information.
"""


class ProviderTaskBindingResolver:
    """Provider-backed task resolver with deterministic identity/lifecycle checks outside it."""

    identity = "openai-compatible-task-binding-resolver"
    version = "task_binding_decision_v1"

    def __init__(self, client: LLMClientBase, *, provider: str = "openai-compatible", model: str = "configured") -> None:
        self._client = client
        self.identity = f"{provider}:{model}:task-binding"

    def resolve(
        self,
        *,
        turn_id: str,
        user_input: str,
        current_task_id: Optional[str],
        tasks: Sequence[TaskLedger],
    ) -> TaskBindingDecision:
        candidates = []
        for task in tasks:
            active_entries = [
                {
                    "kind": entry.kind.value,
                    "content": entry.content[:600],
                }
                for entry in task.active_entries[:12]
            ]
            candidates.append(
                {
                    "task_id": task.task_id,
                    "status": task.status.value,
                    "entries": active_entries,
                }
            )
        payload = {
            "turn_id": turn_id,
            "current_task_id": current_task_id,
            "active_tasks": candidates,
            "current_user_request": user_input,
        }
        raw = self._client.generate_json(
            TASK_BINDING_SYSTEM_PROMPT,
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            response_schema=TASK_BINDING_SCHEMA,
            schema_name=self.version,
        )
        if not isinstance(raw, dict):
            raise ValueError("task binding response must be an object")
        try:
            action = TaskBindingAction(str(raw["action"]))
            task_id = raw.get("task_id")
            title = raw.get("title")
            if task_id is not None and not isinstance(task_id, str):
                raise TypeError("task_id must be string or null")
            if title is not None and not isinstance(title, str):
                raise TypeError("title must be string or null")
            return TaskBindingDecision(
                action=action,
                task_id=task_id.strip() if isinstance(task_id, str) else None,
                title=title.strip() if isinstance(title, str) else None,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid task binding response") from exc


class BaselineTaskRouter:
    """Deterministic, non-LLM baseline task router.

    Rules:
    1. Requested task_id provided -> validate existence & ACTIVE status.
       Non-ACTIVE (PAUSED, COMPLETED, CANCELLED) -> raise TaskLifecycleError.
    2. No task_id provided & exactly 1 ACTIVE task -> select it.
    3. No task_id provided & 0 ACTIVE tasks -> deterministically derive task_id
       from turn_id and create it (retry-safe, idempotent).
    4. No task_id provided & >1 ACTIVE tasks -> raise TaskSelectionRequiredError.
    """

    def __init__(self, task_service: TaskStateService) -> None:
        self.task_service = task_service

    def resolve_task(self, turn_id: str, requested_task_id: Optional[str] = None) -> str:
        if requested_task_id:
            try:
                ledger = self.task_service.get_task(requested_task_id)
            except KeyError as exc:
                raise TaskNotFoundError(requested_task_id) from exc

            if ledger.status is not TaskStatus.ACTIVE:
                raise TaskLifecycleError(requested_task_id, ledger.status.value)
            return requested_task_id

        active_tasks = self.task_service.list_active_tasks()
        if len(active_tasks) == 1:
            return active_tasks[0].task_id

        if len(active_tasks) == 0:
            # Deterministic, retry-safe task_id derived from turn_id
            derived_hash = hashlib.sha256(turn_id.encode("utf-8")).hexdigest()[:12]
            derived_task_id = f"task_{derived_hash}"
            # create_task in repository is idempotent if task already exists
            self.task_service.create_task(derived_task_id)
            return derived_task_id

        # > 1 ACTIVE tasks -> refuse to guess
        active_ids = tuple(t.task_id for t in active_tasks)
        raise TaskSelectionRequiredError(active_ids)
