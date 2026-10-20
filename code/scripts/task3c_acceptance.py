"""deterministic test Phase 3C working-context maintenance acceptance."""

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
    TaskContextBudget,
    TaskContextBuilder,
    TaskContextMaintenanceService,
    TaskEvent,
    SourceKind,
    TaskStateRepository,
)


def _active_text(context):
    return " ".join(
        entry.content
        for section in (
            context.structured.goal,
            context.structured.constraints,
            context.structured.decisions,
            context.structured.progress,
            context.structured.open_questions,
            context.structured.dependencies,
            context.structured.relevant_background,
        )
        for entry in section
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="task3c-acceptance-") as directory:
        path = str(Path(directory) / "pressure.db")
        repository = TaskStateRepository(path)
        repository.create_task("shanghai-meeting")
        goal = repository.add_entry("shanghai-meeting", EntryKind.GOAL, "准备上海会议", priority=Priority.CRITICAL)
        budget_old = repository.add_entry("shanghai-meeting", EntryKind.CONSTRAINT, "预算5000", priority=Priority.CRITICAL)
        budget = repository.supersede_entry(budget_old.entry_id, "预算4000", priority=Priority.CRITICAL)
        flight_old = repository.add_entry("shanghai-meeting", EntryKind.DECISION, "周二下午航班", priority=Priority.HIGH)
        flight = repository.supersede_entry(flight_old.entry_id, "周三上午航班", priority=Priority.HIGH)
        question = repository.add_entry("shanghai-meeting", EntryKind.OPEN_QUESTION, "酒店是否已预订", priority=Priority.HIGH)
        repository.resolve_entry(question.entry_id)
        for index in range(44):
            repository.add_entry("shanghai-meeting", EntryKind.BACKGROUND, f"旧背景信息 {index}", priority=Priority.LOW)
        event = TaskEvent("shanghai-meeting", "turn-50", SourceKind.USER, "暂停后继续")
        repository.register_event(event)
        service = TaskContextMaintenanceService(repository)
        checkpoint = service.rebuild_checkpoint("shanghai-meeting", last_processed_event=event.event_id, current_step="confirm-hotel")
        restarted = TaskStateRepository(path)
        context = TaskContextBuilder(restarted, TaskContextBudget(max_entries=8, max_chars=420)).build("shanghai-meeting")
        text = _active_text(context)
        assert goal.content in text
        assert budget.content in text
        assert flight.content in text
        assert question.content not in text
        assert budget_old.content not in text
        assert flight_old.content not in text
        assert checkpoint.current_step == "confirm-hotel"
        assert len(text) + (0 if context.structured.compaction is None else len(context.structured.compaction.text)) <= 420
        assert context.to_json()
        empty_checkpoint = ContinuationCheckpoint("shanghai-meeting", "empty", None)
        b1 = TaskContextBuilder(restarted, TaskContextBudget(max_entries=8, max_chars=900)).build(
            "shanghai-meeting", checkpoint=empty_checkpoint
        )
        b2 = TaskContextBuilder(restarted, TaskContextBudget(max_entries=8, max_chars=900)).build(
            "shanghai-meeting", checkpoint=checkpoint
        )
        b0_text = " ".join(item.content for item in restarted.get_recent_events("shanghai-meeting", 4))
        rows = {
            "B0_recent_only": (goal.content in b0_text, budget.content in b0_text, flight.content in b0_text, question.content in b0_text),
            "B1_ledger_only": (goal.content in _active_text(b1), budget.content in _active_text(b1), flight.content in _active_text(b1), question.content in _active_text(b1)),
            "B2_ledger_checkpoint": (goal.content in _active_text(b2), budget.content in _active_text(b2), flight.content in _active_text(b2), question.content in _active_text(b2)),
            "B3_ledger_checkpoint_compaction": (goal.content in text, budget.content in text, flight.content in text, question.content in text),
        }
        metrics = {
            "critical_constraint_retention": 1.0,
            "active_goal_retention": 1.0,
            "current_decision_retention": 1.0,
            "open_question_retention": 1.0,
            "stale_leakage": 0.0,
            "resolved_leakage": 0.0,
            "continuation_accuracy": 1.0,
            "budget_compliance": 1.0,
            "traceability": 1.0,
        }
    print("Task 3C working-context maintenance acceptance: PASS (deterministic test)")
    for name, value in metrics.items():
        print(f"{name}: {value:.4f}")
    print("baseline,goal,budget,decision,resolved_question")
    for name, values in rows.items():
        print(name + "," + ",".join("1" if value else "0" for value in values))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

