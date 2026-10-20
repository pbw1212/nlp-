"""deterministic test Phase 3A durable task-state acceptance."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.memory.task_state import (  # noqa: E402
    ContinuationCheckpoint,
    EntryKind,
    EntryStatus,
    Priority,
    TaskContextBuilder,
    TaskStateRepository,
)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="task3-acceptance-") as directory:
        path = str(Path(directory) / "task-state.db")
        repository = TaskStateRepository(path)
        repository.create_task("meeting-task")
        goal = repository.add_entry("meeting-task", EntryKind.GOAL, "准备上海会议", priority=Priority.CRITICAL)
        repository.add_entry("meeting-task", EntryKind.CONSTRAINT, "预算最多5000", priority=Priority.HIGH)
        question = repository.add_entry("meeting-task", EntryKind.OPEN_QUESTION, "酒店还没订", priority=Priority.HIGH)
        repository.add_entry("meeting-task", EntryKind.DECISION, "已订周二下午航班")
        repository.resolve_entry(question.entry_id)
        checkpoint = ContinuationCheckpoint(
            "meeting-task", "checkpoint-1", "turn-5", goal_entry_ids=(goal.entry_id,),
            current_step="book-hotel", continuation_note="酒店仍待预订",
        )
        repository.write_checkpoint(checkpoint)

        restarted = TaskStateRepository(path)
        context = TaskContextBuilder(restarted).build("meeting-task", current_turn="继续处理")
        assert context.structured.goal
        assert context.structured.constraints
        assert context.structured.decisions
        assert context.structured.continuation is not None
        assert context.structured.open_questions == ()
        assert all(entry.status is EntryStatus.ACTIVE for entry in restarted.list_active_entries("meeting-task"))
        assert context.to_json()
    print("Task 3A durable task-state acceptance: PASS (deterministic test, restart recovery)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

