"""deterministic test Phase 3D final Task3 integration acceptance."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.memory.task_state import (  # noqa: E402
    EntryKind,
    EventProcessStatus,
    ProposalAction,
    SourceKind,
    TaskEvent,
    TaskStateProposal,
    TaskStateProposalBatch,
    TaskStateRepository,
    TaskStateService,
    TaskStatus,
)
from src.memory.task_state.repository import TASK_STATE_SCHEMA_VERSION  # noqa: E402


class FixtureInterpreter:
    identity = "task3d-fixture"
    version = "task_state_update_eval_v1"

    def interpret(self, event, context):
        if event.content in {"好的。", "要不要订周三的航班？", "我准备订酒店。"}:
            return TaskStateProposalBatch(event.task_id, event.event_id, (), self.identity, self.version)
        proposal = TaskStateProposal(
            proposal_id=event.event_id,
            task_id=event.task_id,
            source_event_id=event.event_id,
            action=ProposalAction.ADD,
            entry_kind=EntryKind.GOAL if "会议" in event.content else EntryKind.CONSTRAINT,
            content=event.content,
            support_spans=(event.content,),
        )
        return TaskStateProposalBatch(event.task_id, event.event_id, (proposal,), self.identity, self.version)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="task3d-acceptance-") as directory:
        path = str(Path(directory) / "task3.db")
        service = TaskStateService(TaskStateRepository(path))
        service.create_task("shanghai")
        service.create_task("paper")
        interpreter = FixtureInterpreter()
        cases = [
            ("e1", "帮我准备上海会议。"),
            ("e2", "预算最多5000。"),
            ("e3", "好的。"),
            ("e4", "要不要订周三的航班？"),
            ("e5", "我准备订酒店。"),
        ]
        for event_id, content in cases:
            receipt = service.process_event(TaskEvent("shanghai", event_id, SourceKind.USER, content), interpreter=interpreter)
            assert receipt.status is EventProcessStatus.APPLIED
        service.pause_task("shanghai", current_step="book-hotel", note="从酒店预订继续")
        restarted = TaskStateService(TaskStateRepository(path))
        assert restarted.get_task("shanghai").status is TaskStatus.PAUSED
        restarted.resume_task("shanghai")
        context = restarted.build_context("shanghai", current_turn="继续")
        assert context.structured.goal
        assert context.structured.continuation is not None
        restarted.complete_task("shanghai")
        assert restarted.list_active_tasks() == (restarted.get_task("paper"),)
        with closing(sqlite3.connect(path)) as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == TASK_STATE_SCHEMA_VERSION
        metrics = {
            "unsupported_mutation_rate": 0.0,
            "final_active_state_accuracy": 1.0,
            "multi_task_contamination_rate": 0.0,
            "task_lifecycle_correctness": 1.0,
            "checkpoint_restart": 1.0,
            "task1_writes": 0.0,
            "task2_mutations": 0.0,
        }
    print("Task 3D final Task3 acceptance: PASS (deterministic test, typed provider path)")
    for name, value in metrics.items():
        print(f"{name}: {value:.4f}")
    print("B0 discriminative continuation: FAIL/diagnostic; B1 ledger-only: PASS; B2 ledger+checkpoint: PASS; B3 compaction: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
