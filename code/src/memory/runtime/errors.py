"""Phase 4A AgentRuntime error taxonomy."""

from __future__ import annotations

from typing import Tuple, Optional


class AgentRuntimeError(RuntimeError):
    """Base class for all AgentRuntime errors."""

    pass


class TaskSelectionRequiredError(AgentRuntimeError):
    """Raised when multiple active tasks exist and no task_id was provided."""

    def __init__(self, active_task_ids: Tuple[str, ...], message: str = "") -> None:
        self.active_task_ids = active_task_ids
        msg = message or f"Multiple active tasks exist ({', '.join(active_task_ids)}); explicit task_id is required."
        super().__init__(msg)


class TaskNotFoundError(AgentRuntimeError):
    """Raised when the specified task_id does not exist."""

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        super().__init__(f"Task not found: {task_id}")


from src.memory.task_state.service import TaskLifecycleError as Task3LifecycleError


class TaskLifecycleError(AgentRuntimeError, Task3LifecycleError):
    """Raised when attempting an operation on a non-ACTIVE task (PAUSED, COMPLETED, CANCELLED)."""

    def __init__(self, task_id: str, status: str) -> None:
        super().__init__(task_id=task_id, status=status)
