"""deterministic test Phase 3B validated task-state update acceptance."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.memory.task_state import (  # noqa: E402
    EntryKind,
    EventProcessStatus,
    ProposalAction,
    ProposalBasis,
    Priority,
    SourceKind,
    TaskEvent,
    TaskStateProposal,
    TaskStateProposalBatch,
    TaskStateRepository,
    TaskStateUpdater,
)


class FixtureInterpreter:
    identity = "task3b-fixture-interpreter"
    version = "task_state_update_eval_v1"

    def interpret(self, event, context):
        if event.content == "好的。":
            return TaskStateProposalBatch(event.task_id, event.event_id, (), self.identity, self.version)
        if event.content == "预算最多5000。":
            proposal = TaskStateProposal(
                "budget", event.task_id, event.event_id, ProposalAction.ADD,
                entry_kind=EntryKind.CONSTRAINT, content=event.content,
                support_spans=(event.content,),
            )
            return TaskStateProposalBatch(event.task_id, event.event_id, (proposal,), self.identity, self.version)
        proposal = TaskStateProposal(
            "goal", event.task_id, event.event_id, ProposalAction.ADD,
            entry_kind=EntryKind.GOAL, content=event.content,
            priority=Priority.NORMAL, basis=ProposalBasis.EXPLICIT,
            support_spans=(event.content,),
        )
        return TaskStateProposalBatch(event.task_id, event.event_id, (proposal,), self.identity, self.version)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="task3b-acceptance-") as directory:
        repository = TaskStateRepository(str(Path(directory) / "task-state.db"))
        repository.create_task("fixture-task")
        updater = TaskStateUpdater(repository, FixtureInterpreter())
        zero = TaskEvent("fixture-task", "zero", SourceKind.USER, "好的。")
        receipt = updater.process_event(zero)
        assert receipt.status is EventProcessStatus.APPLIED and not receipt.applied_entry_ids
        event = TaskEvent("fixture-task", "goal", SourceKind.USER, "准备上海会议")
        applied = updater.process_event(event)
        assert applied.applied_entry_ids
        assert updater.process_event(event) == applied
        assert len(repository.get_history("fixture-task")) == 1
    print("Task 3B validated task-state update acceptance: PASS (deterministic test, typed proposals, atomic/idempotent)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

