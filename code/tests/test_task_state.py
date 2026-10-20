from __future__ import annotations

from dataclasses import replace

from src.memory.retrieval.applicability_models import ApplicabilityDecision, ApplicabilityStatus
from src.memory.retrieval.evidence import (
    DistilledEvidence,
    EvidenceBundle,
    EvidenceSufficiency,
    EvidenceSufficiencyStatus,
    ConflictStatus,
)
from src.memory.task_state import (
    ContinuationCheckpoint,
    EntryKind,
    EntryStatus,
    EvidenceRef,
    Priority,
    RelationKind,
    StructuredTaskContext,
    TaskContextBudget,
    TaskContextBuilder,
    TaskRelation,
    TaskStateRepository,
)


def _bundle() -> EvidenceBundle:
    item = DistilledEvidence(
        memory_id="mem-1",
        revision_id="rev-1",
        text="偏好安静酒店",
        original_rank=1,
        original_score=1.0,
        score_breakdown={"content_bm25": 1.0, "metadata_overlap": 0.0},
        provenance_refs=("provenance:p1",),
        applicability_status=ApplicabilityStatus.MATCH,
        applicability_decision=ApplicabilityDecision.RETAIN,
        applicability_reasons=("EXACT_MATCH",),
        conflict_status=ConflictStatus.NO_CONFLICT,
        conflict_key=None,
        conflict_losing_memory_ids=(),
        source_trace=("recall_rank:1",),
    )
    return EvidenceBundle(
        schema_version="evidence_bundle_v1",
        request_id="bundle-1",
        query="会议住宿",
        selected=(item,),
        sufficiency=EvidenceSufficiency(EvidenceSufficiencyStatus.SUFFICIENT),
    )


def test_task_ledger_lifecycle_idempotency_relations_and_restart(tmp_path):
    path = tmp_path / "task-state.db"
    repository = TaskStateRepository(str(path))
    repository.create_task("task-1")
    goal = repository.add_entry("task-1", EntryKind.GOAL, "准备上海会议", priority=Priority.CRITICAL, operation_id="op-goal")
    assert repository.add_entry("task-1", EntryKind.GOAL, "不同内容不会被静默合并", operation_id="op-goal") == goal
    constraint = repository.add_entry("task-1", EntryKind.CONSTRAINT, "预算不超过5000", priority=Priority.HIGH)
    question = repository.add_entry("task-1", EntryKind.OPEN_QUESTION, "酒店尚未确定")
    decision = repository.add_entry("task-1", EntryKind.DECISION, "先确认航班")
    repository.resolve_entry(question.entry_id, operation_id="resolve-question")
    replacement = repository.supersede_entry(decision.entry_id, "已订周二下午航班", operation_id="supersede-decision")
    repository.add_relation(TaskRelation("task-1", replacement.entry_id, constraint.entry_id, RelationKind.DEPENDS_ON))

    reopened = TaskStateRepository(str(path))
    history = reopened.get_history("task-1")
    statuses = {entry.entry_id: entry.status for entry in history}
    assert statuses[question.entry_id] is EntryStatus.RESOLVED
    assert statuses[decision.entry_id] is EntryStatus.SUPERSEDED
    assert statuses[replacement.entry_id] is EntryStatus.ACTIVE
    assert len(reopened.list_active_entries("task-1")) == 3


def test_checkpoint_is_compact_reference_based_and_restartable(tmp_path):
    path = tmp_path / "checkpoint.db"
    repository = TaskStateRepository(str(path))
    repository.create_task("task-2")
    goal = repository.add_entry("task-2", EntryKind.GOAL, "准备会议")
    checkpoint = ContinuationCheckpoint(
        task_id="task-2",
        checkpoint_id="cp-1",
        last_processed_event="turn-4",
        goal_entry_ids=(goal.entry_id,),
        current_step="book-hotel",
        continuation_note="酒店仍待预订",
        evidence_refs=(EvidenceRef("bundle-1", "mem-1", "rev-1"),),
    )
    repository.write_checkpoint(checkpoint)
    reopened = TaskStateRepository(str(path))
    loaded = reopened.get_latest_checkpoint("task-2")
    assert loaded is not None
    assert loaded.goal_entry_ids == (goal.entry_id,)
    assert loaded.evidence_refs[0].memory_id == "mem-1"
    assert "content" not in loaded.__dict__


def test_active_context_is_bounded_and_preserves_critical_relevant_state(tmp_path):
    repository = TaskStateRepository(str(tmp_path / "context.db"))
    repository.create_task("task-3")
    goal = repository.add_entry("task-3", EntryKind.GOAL, "准备上海会议", priority=Priority.CRITICAL)
    repository.add_entry("task-3", EntryKind.CONSTRAINT, "预算最多5000", priority=Priority.CRITICAL)
    repository.add_entry("task-3", EntryKind.OPEN_QUESTION, "酒店还没订", priority=Priority.HIGH)
    for index in range(10):
        repository.add_entry("task-3", EntryKind.BACKGROUND, "低优先背景 " + ("x" * 100), priority=Priority.LOW)
    checkpoint = ContinuationCheckpoint("task-3", "cp-3", "turn-5", goal_entry_ids=(goal.entry_id,))
    repository.write_checkpoint(checkpoint)
    context = TaskContextBuilder(
        repository,
        TaskContextBudget(max_entries=4, max_chars=100, max_chars_per_entry=40),
    ).build("task-3", current_turn="继续准备", evidence_bundle=_bundle())
    assert context.structured.goal[0].entry_id == goal.entry_id
    assert context.structured.constraints
    assert context.structured.open_questions
    assert context.omitted_entry_ids
    assert len(context.to_json()) < 5000
    assert context.structured.retrieved_evidence


def test_structured_context_serialization_is_deterministic_and_task1_boundary_is_clean(tmp_path):
    repository = TaskStateRepository(str(tmp_path / "serialization.db"))
    repository.create_task("task-4")
    repository.add_entry("task-4", EntryKind.PROGRESS, "已完成准备")
    builder = TaskContextBuilder(repository)
    first = builder.build("task-4", current_turn="继续")
    second = builder.build("task-4", current_turn="继续")
    assert first.to_json() == second.to_json()
    with repository._connect() as connection:
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "memory_items" not in names
    assert "task_entries" in names
