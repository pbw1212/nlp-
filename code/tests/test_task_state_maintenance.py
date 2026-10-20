from __future__ import annotations

from src.memory.task_state import (
    CheckpointRefreshPolicy,
    ContinuationCheckpoint,
    EntryKind,
    EntryStatus,
    ExternalObservation,
    Priority,
    SourceKind,
    TaskContextBudget,
    TaskContextBuilder,
    TaskContextMaintenanceService,
    TaskEvent,
    TaskStateUpdateReceipt,
    EventProcessStatus,
    TaskStateRepository,
)


def _repo(tmp_path):
    repository = TaskStateRepository(str(tmp_path / "maintenance.db"))
    repository.create_task("task")
    return repository


def test_critical_constraint_survives_pressure_and_background_evicted_first(tmp_path):
    repository = _repo(tmp_path)
    goal = repository.add_entry("task", EntryKind.GOAL, "准备上海会议", priority=Priority.HIGH)
    critical = repository.add_entry("task", EntryKind.CONSTRAINT, "预算不得超过4000", priority=Priority.CRITICAL)
    for index in range(8):
        repository.add_entry("task", EntryKind.BACKGROUND, f"旧背景 {index}", priority=Priority.LOW)
    context = TaskContextBuilder(repository, TaskContextBudget(max_entries=2, max_chars=100)).build("task")
    ids = {entry.entry_id for section in (context.structured.goal, context.structured.constraints) for entry in section}
    assert goal.entry_id in ids
    assert critical.entry_id in ids
    assert context.omitted_entry_ids
    assert context.structured.compaction is not None


def test_superseded_and_resolved_entries_do_not_leak_into_active_context(tmp_path):
    repository = _repo(tmp_path)
    old = repository.add_entry("task", EntryKind.DECISION, "周二下午航班")
    repository.supersede_entry(old.entry_id, "周三上午航班")
    question = repository.add_entry("task", EntryKind.OPEN_QUESTION, "酒店是否已预订")
    repository.resolve_entry(question.entry_id)
    context = TaskContextBuilder(repository).build("task")
    assert [entry.content for entry in context.structured.decisions] == ["周三上午航班"]
    assert context.structured.open_questions == ()
    assert "周二下午航班" not in context.to_json()


def test_checkpoint_refresh_and_stale_reference_repair(tmp_path):
    repository = _repo(tmp_path)
    old = repository.add_entry("task", EntryKind.DECISION, "周二下午航班")
    checkpoint = ContinuationCheckpoint("task", "old", None, decision_ids=(old.entry_id,))
    repository.write_checkpoint(checkpoint)
    new = repository.supersede_entry(old.entry_id, "周三上午航班")
    event = TaskEvent("task", "e1", SourceKind.USER, "改成周三上午")
    repository.register_event(event)
    receipt = TaskStateUpdateReceipt("task", "e1", EventProcessStatus.APPLIED, "batch:e1", superseded_entry_ids=(old.entry_id,))
    service = TaskContextMaintenanceService(repository)
    result = service.refresh_after_update(event, receipt, current_step="confirm-flight")
    assert result.checkpoint is not None
    assert old.entry_id not in result.checkpoint.decision_ids
    assert new.entry_id in result.checkpoint.decision_ids


def test_zero_update_does_not_refresh_checkpoint(tmp_path):
    repository = _repo(tmp_path)
    event = TaskEvent("task", "e1", SourceKind.USER, "好的")
    repository.register_event(event)
    receipt = TaskStateUpdateReceipt("task", "e1", EventProcessStatus.APPLIED, "batch:e1")
    assert TaskContextMaintenanceService(repository).refresh_after_update(event, receipt).checkpoint is None
    assert repository.get_latest_checkpoint("task") is None


def test_recent_events_and_fresh_external_observation_are_bounded_and_deterministic(tmp_path):
    repository = _repo(tmp_path)
    for index in range(5):
        event = TaskEvent("task", f"e{index}", SourceKind.TOOL, f"state {index}", occurred_at=float(index + 1))
        repository.register_event(event)
    observations = (
        ExternalObservation("calendar", "meeting", "old", 1),
        ExternalObservation("calendar", "meeting", "new", 2),
    )
    context = TaskContextBuilder(repository, TaskContextBudget(max_recent_events=2)).build(
        "task", external_observations=observations
    )
    assert [event.event_id for event in context.structured.recent_events] == ["e3", "e4"]
    assert context.structured.external_observations[0].content == "new"


def test_checkpoint_rebuild_after_restart_is_reference_based(tmp_path):
    repository = _repo(tmp_path)
    goal = repository.add_entry("task", EntryKind.GOAL, "准备会议", priority=Priority.HIGH)
    constraint = repository.add_entry("task", EntryKind.CONSTRAINT, "预算4000", priority=Priority.CRITICAL)
    service = TaskContextMaintenanceService(repository, policy=CheckpointRefreshPolicy())
    checkpoint = service.rebuild_checkpoint("task", last_processed_event="e-final", current_step="book-hotel")
    restarted = TaskStateRepository(repository.db_path)
    restored = restarted.get_latest_checkpoint("task")
    assert restored is not None
    assert restored.goal_entry_ids == (goal.entry_id,)
    assert restored.critical_constraint_ids == (constraint.entry_id,)
    assert restored.current_step == "book-hotel"
    assert "预算4000" not in restored.continuation_note


def test_checkpoint_persists_focus_next_action_and_world_state(tmp_path):
    repository = TaskStateRepository(str(tmp_path / "checkpoint-fields.db"))
    repository.create_task("task")
    checkpoint = TaskContextMaintenanceService(repository).rebuild_checkpoint(
        "task",
        focus="确认航班",
        next_action="联系航空公司",
        world_state="机票尚未出票",
    )

    reopened = TaskStateRepository(str(tmp_path / "checkpoint-fields.db"))
    restored = reopened.get_latest_checkpoint("task")

    assert restored == checkpoint
    assert restored.focus == "确认航班"
    assert restored.next_action == "联系航空公司"
    assert restored.world_state == "机票尚未出票"


def test_checkpoint_failure_does_not_rollback_ledger(tmp_path, monkeypatch):
    repository = _repo(tmp_path)
    entry = repository.add_entry("task", EntryKind.GOAL, "准备会议")
    event = TaskEvent("task", "e1", SourceKind.USER, "继续")
    repository.register_event(event)
    receipt = TaskStateUpdateReceipt("task", "e1", EventProcessStatus.APPLIED, "batch:e1", applied_entry_ids=(entry.entry_id,))
    service = TaskContextMaintenanceService(repository)

    def fail(*args, **kwargs):
        raise RuntimeError("checkpoint unavailable")

    monkeypatch.setattr(service, "rebuild_checkpoint", fail)
    result = service.refresh_after_update(event, receipt)
    assert result.checkpoint is None
    assert result.warnings == ("checkpoint_refresh_failed:RuntimeError",)
    assert repository.get_entry(entry.entry_id).status is EntryStatus.ACTIVE
