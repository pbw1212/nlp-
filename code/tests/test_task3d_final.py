from __future__ import annotations

import sqlite3

import pytest

from src.memory.task_state import (
    EntryKind,
    EntryStatus,
    EventProcessStatus,
    OpenAICompatibleTaskStateInterpreter,
    Priority,
    ProposalAction,
    SourceKind,
    TaskEvent,
    TaskStateProposal,
    TaskStateProposalBatch,
    TaskStateRepository,
    TaskStateService,
    TaskStatus,
    TaskLifecycleError,
    TaskIntent,
)
from src.memory.task_state.interpreter import SYSTEM_PROMPT


class FakeInterpreter:
    identity = "fake"
    version = "v1"

    def __init__(self, batches):
        self.batches = batches

    def interpret(self, event, context):
        return self.batches[event.event_id]


def test_task_state_prompt_routes_explicit_memory_corrections_to_memory_writer():
    assert "save, remember, correct, update, or remove" in SYSTEM_PROMPT
    assert "请纠正长期记忆" in SYSTEM_PROMPT
    assert "EXPLICIT_MEMORY_WRITE" in SYSTEM_PROMPT


def _add(event, pid, kind, content):
    return TaskStateProposal(pid, event.task_id, event.event_id, ProposalAction.ADD, entry_kind=kind, content=content, support_spans=(event.content,))


def test_task_lifecycle_pause_resume_complete_cancel_and_active_listing(tmp_path):
    service = TaskStateService(TaskStateRepository(str(tmp_path / "lifecycle.db")))
    service.create_task("a")
    service.create_task("b")
    event = TaskEvent("a", "e1", SourceKind.USER, "准备上海会议")
    batch = TaskStateProposalBatch("a", "e1", (_add(event, "goal", EntryKind.GOAL, "准备上海会议"),))
    service.process_event(event, interpreter=FakeInterpreter({"e1": batch}))
    paused = service.pause_task("a", current_step="book-hotel", note="从订酒店继续")
    assert paused.status is TaskStatus.PAUSED
    assert service.list_active_tasks() == (service.get_task("b"),)
    resumed = service.resume_task("a")
    assert resumed.status is TaskStatus.ACTIVE
    completed = service.complete_task("a")
    assert completed.status is TaskStatus.COMPLETED
    assert service.list_active_tasks() == (service.get_task("b"),)
    with pytest.raises(TaskLifecycleError):
        service.process_event(TaskEvent("a", "e2", SourceKind.USER, "继续"), interpreter=FakeInterpreter({}))
    cancelled = service.cancel_task("b")
    assert cancelled.status is TaskStatus.CANCELLED
    restarted = TaskStateService(TaskStateRepository(str(tmp_path / "lifecycle.db")))
    assert restarted.get_task("a").status is TaskStatus.COMPLETED
    assert restarted.list_active_tasks() == ()


def test_multiple_tasks_are_isolated(tmp_path):
    service = TaskStateService(TaskStateRepository(str(tmp_path / "isolation.db")))
    service.create_task("travel")
    service.create_task("paper")
    event_a = TaskEvent("travel", "a1", SourceKind.USER, "预算4000")
    batch_a = TaskStateProposalBatch("travel", "a1", (_add(event_a, "budget", EntryKind.CONSTRAINT, "预算4000"),))
    service.process_event(event_a, interpreter=FakeInterpreter({"a1": batch_a}))
    event_b = TaskEvent("paper", "b1", SourceKind.USER, "截止周五")
    batch_b = TaskStateProposalBatch("paper", "b1", (_add(event_b, "deadline", EntryKind.CONSTRAINT, "截止周五"),))
    service.process_event(event_b, interpreter=FakeInterpreter({"b1": batch_b}))
    assert "预算4000" in service.build_context("travel").to_json()
    assert "预算4000" not in service.build_context("paper").to_json()
    assert service.get_task("travel").status is TaskStatus.ACTIVE
    assert service.get_task("paper").status is TaskStatus.ACTIVE


def test_typed_facade_path_does_not_require_interpreter(tmp_path):
    service = TaskStateService(TaskStateRepository(str(tmp_path / "typed.db")))
    service.create_task("task")
    event = TaskEvent("task", "typed", SourceKind.USER, "准备会议")
    batch = TaskStateProposalBatch("task", "typed", (_add(event, "goal", EntryKind.GOAL, "准备会议"),))
    receipt = service.apply_proposals(event, batch)
    assert receipt.status is EventProcessStatus.APPLIED
    assert service.get_task("task").active_entries[0].content == "准备会议"


class FakeLLMClient:
    def __init__(self, output):
        self.output = output
        self.calls = []

    def generate_json(self, system_prompt, user_prompt, *, response_schema=None, schema_name="structured_output"):
        self.calls.append((system_prompt, user_prompt, response_schema, schema_name))
        return self.output


def test_real_interpreter_decodes_structured_proposals_without_mutating_repository():
    event = TaskEvent("task", "e1", SourceKind.USER, "预算最多5000")
    client = FakeLLMClient({
        "proposals": [{
            "proposal_id": "p1", "action": "ADD", "entry_kind": "CONSTRAINT",
            "target_entry_id": None, "content": "预算最多5000", "replacement_content": None,
            "priority": 3, "relation": None, "evidence_refs": [], "support_spans": ["预算最多5000"],
            "basis": "EXPLICIT", "warnings": [], "scope": "TASK",
        }],
        "warnings": [],
    })
    interpreter = OpenAICompatibleTaskStateInterpreter(client)
    batch = interpreter.interpret(event, None)
    assert batch.proposals[0].action is ProposalAction.ADD
    assert batch.proposals[0].entry_kind is EntryKind.CONSTRAINT
    assert client.calls[0][2]["additionalProperties"] is False
    assert client.calls[0][2]["properties"]["proposals"]["items"]["additionalProperties"] is False
    assert client.calls[0][2]["properties"]["proposals"]["items"]["properties"]["scope"]["enum"] == ["TASK", "SESSION"]


def test_task_analysis_is_provider_supplied_and_request_local():
    event = TaskEvent("task", "e-analysis", SourceKind.USER, "请继续处理这个任务")
    client = FakeLLMClient({
        "analysis": {
            "task_stage": "validation",
            "query_intent": "RAISE_QUESTION",
            "information_needs": ["当前验证结果"],
            "retrieval_needed": True,
            "important_kinds": ["OPEN_QUESTION"],
            "focus_entry_ids": ["entry-1"],
            "search_query": "当前验证结果",
            "warnings": [],
        },
        "proposals": [],
        "warnings": [],
    })
    batch = OpenAICompatibleTaskStateInterpreter(client).interpret(event, None)
    assert batch.analysis is not None
    assert batch.analysis.query_intent is TaskIntent.RAISE_QUESTION
    assert batch.analysis.focus_entry_ids == ("entry-1",)
    assert "analysis" in client.calls[0][2]["required"]


def test_memory_write_candidate_must_be_grounded_in_current_event():
    event = TaskEvent(
        "task",
        "e-stale-memory",
        SourceKind.USER,
        "如果我去杭州玩儿，只有我一个人，你给我推荐什么位置的酒店",
    )
    client = FakeLLMClient({
        "analysis": {
            "task_stage": "awaiting_memory_write",
            "query_intent": "EXPLICIT_MEMORY_WRITE",
            "memory_write_requested": True,
            "information_needs": [],
            "retrieval_needed": False,
            "important_kinds": [],
            "focus_entry_ids": [],
            "search_query": "",
            "warnings": [],
            "memory_content": "我solotrip喜欢住在市中心",
            "memory_support_span": "我solotrip喜欢住在市中心",
        },
        "proposals": [],
        "warnings": [],
    })

    batch = OpenAICompatibleTaskStateInterpreter(client).interpret(event, None)

    assert batch.analysis is None
    assert batch.proposals == ()
    assert any("not grounded" in warning for warning in batch.warnings)


def test_explicit_memory_intent_requires_provider_confirmation_of_storage_request():
    event = TaskEvent("task", "e-not-explicit", SourceKind.USER, "我偏好使用 SQLite 做个人项目")
    client = FakeLLMClient({
        "analysis": {
            "task_stage": "information_request",
            "query_intent": "EXPLICIT_MEMORY_WRITE",
            "memory_write_requested": False,
            "information_needs": [],
            "retrieval_needed": False,
            "important_kinds": [],
            "focus_entry_ids": [],
            "search_query": "",
            "warnings": [],
            "memory_content": "用户偏好使用 SQLite 做个人项目。",
            "memory_support_span": "我偏好使用 SQLite 做个人项目",
        },
        "proposals": [],
        "warnings": [],
    })

    batch = OpenAICompatibleTaskStateInterpreter(client).interpret(event, None)

    assert batch.analysis is None
    assert batch.proposals == ()
    assert batch.warnings


def test_ambiguous_supersede_is_durable_pending_and_confirmable_after_restart(tmp_path):
    path = str(tmp_path / "pending-supersede.db")
    repository = TaskStateRepository(path)
    service = TaskStateService(repository)
    service.create_task("task")
    old = repository.add_entry("task", EntryKind.CONSTRAINT, "预算最多5000")
    event = TaskEvent("task", "e-pending", SourceKind.USER, "把预算改一下")
    client = FakeLLMClient({
        "analysis": {
            "task_stage": "planning",
            "query_intent": "MODIFY_EXISTING",
            "information_needs": [],
            "retrieval_needed": False,
            "important_kinds": ["CONSTRAINT"],
            "focus_entry_ids": [],
            "search_query": "",
            "warnings": [],
        },
        "proposals": [{
            "proposal_id": "p1", "action": "SUPERSEDE", "entry_kind": "CONSTRAINT",
            "target_entry_id": None, "content": None, "replacement_content": None,
            "priority": None, "relation": None, "evidence_refs": [],
            "support_spans": [], "basis": "EXPLICIT", "warnings": [],
        }],
        "warnings": [],
    })
    result = service.process_event_result(event, interpreter=OpenAICompatibleTaskStateInterpreter(client))
    assert result.receipt.status is EventProcessStatus.PENDING_CONFIRMATION
    assert service.get_task("task").active_entries == (old,)

    restarted = TaskStateService(TaskStateRepository(path))
    confirmed = restarted.confirm_pending_supersede(
        event,
        target_entry_id=old.entry_id,
        replacement_content="预算最多4000",
    )
    assert confirmed.receipt.status is EventProcessStatus.APPLIED
    history = restarted.repository.get_history("task")
    assert any(item.entry_id == old.entry_id and item.status is EntryStatus.SUPERSEDED for item in history)
    assert any(item.content == "预算最多4000" and item.status is EntryStatus.ACTIVE for item in history)


def test_schema_v1_migrates_to_current_and_future_version_refuses(tmp_path):
    path = str(tmp_path / "migration.db")
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE task_ledgers(task_id TEXT PRIMARY KEY, created_at REAL NOT NULL, updated_at REAL NOT NULL);
        CREATE TABLE task_entries(entry_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, kind TEXT NOT NULL, content TEXT NOT NULL, status TEXT NOT NULL, priority INTEGER NOT NULL, source_ref TEXT, evidence_refs_json TEXT NOT NULL, supersedes_entry_id TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL);
        CREATE TABLE task_relations(task_id TEXT, from_entry_id TEXT, to_entry_id TEXT, relation_kind TEXT, PRIMARY KEY(task_id,from_entry_id,to_entry_id,relation_kind));
        CREATE TABLE task_checkpoints(checkpoint_id TEXT PRIMARY KEY, task_id TEXT, last_processed_event TEXT, goal_entry_ids_json TEXT NOT NULL, current_step TEXT, continuation_note TEXT NOT NULL, critical_constraint_ids_json TEXT NOT NULL, open_question_ids_json TEXT NOT NULL, decision_ids_json TEXT NOT NULL, evidence_refs_json TEXT NOT NULL, created_at REAL NOT NULL);
        CREATE TABLE task_operations(operation_id TEXT PRIMARY KEY, task_id TEXT, operation_kind TEXT, entry_id TEXT, created_at REAL NOT NULL);
        PRAGMA user_version=1;
    """)
    connection.commit(); connection.close()
    repository = TaskStateRepository(path)
    repository.create_task("task")
    assert repository.get_task_status("task") is TaskStatus.ACTIVE
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=999")
    with pytest.raises(ValueError, match="unsupported task-state schema"):
        TaskStateRepository(path)
