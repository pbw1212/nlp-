from __future__ import annotations

import pytest

from src.memory.task_state import (
    EntryKind,
    EntryStatus,
    EventProcessStatus,
    ProposalAction,
    ProposalBasis,
    Priority,
    SourceKind,
    TaskEvent,
    TaskStateInterpreterError,
    TaskStateProposal,
    TaskStateProposalBatch,
    TaskStateScope,
    TaskStateUpdater,
    TaskStateProposalValidator,
    TaskStateRepository,
    TaskRelation,
    RelationKind,
)


class FakeInterpreter:
    identity = "fake-task-interpreter"
    version = "fixture-v1"

    def __init__(self, outputs):
        self.outputs = outputs
        self.calls = []

    def interpret(self, event, context):
        self.calls.append((event.event_id, context))
        output = self.outputs[event.event_id]
        if isinstance(output, Exception):
            raise output
        return output


def _batch(event, *proposals):
    return TaskStateProposalBatch("task", event.event_id, tuple(proposals), "fake", "v1")


def _add(event, proposal_id, kind, content, *, priority=Priority.NORMAL, span=None, basis=ProposalBasis.EXPLICIT):
    return TaskStateProposal(
        proposal_id, "task", event.event_id, ProposalAction.ADD,
        entry_kind=kind, content=content, priority=priority,
        support_spans=() if span is None else (span,), basis=basis,
    )


def test_add_zero_proposal_and_event_idempotency(tmp_path):
    repository = TaskStateRepository(str(tmp_path / "updates.db"))
    repository.create_task("task")
    event = TaskEvent("task", "e1", SourceKind.USER, "好的")
    interpreter = FakeInterpreter({"e1": _batch(event)})
    updater = TaskStateUpdater(repository, interpreter)
    receipt = updater.process_event(event)
    replay = updater.process_event(event)
    assert receipt.status is EventProcessStatus.APPLIED
    assert receipt.applied_entry_ids == ()
    assert replay == receipt
    assert len(interpreter.calls) == 1
    assert repository.list_active_entries("task") == ()


def test_session_scoped_proposal_is_not_promoted_to_task_ledger(tmp_path):
    repository = TaskStateRepository(str(tmp_path / "session-scope.db"))
    repository.create_task("task")
    event = TaskEvent("task", "session-fact", SourceKind.USER, "这个会话临时代号是北极星")
    proposal = TaskStateProposal(
        "temporary", "task", event.event_id, ProposalAction.ADD,
        entry_kind=EntryKind.BACKGROUND,
        content="用户在本会话里的临时代号是北极星",
        support_spans=(event.content,),
        scope=TaskStateScope.SESSION,
    )
    receipt = TaskStateUpdater(repository, FakeInterpreter({event.event_id: _batch(event, proposal)})).process_event(event)

    assert receipt.status is EventProcessStatus.APPLIED
    assert receipt.applied_entry_ids == ()
    assert repository.list_active_entries("task") == ()


def test_task_scoped_proposal_remains_durable(tmp_path):
    repository = TaskStateRepository(str(tmp_path / "task-scope.db"))
    repository.create_task("task")
    event = TaskEvent("task", "durable", SourceKind.USER, "项目截止日期是周五")
    proposal = _add(event, "deadline", EntryKind.CONSTRAINT, "项目截止日期是周五", span=event.content)

    receipt = TaskStateUpdater(repository, FakeInterpreter({event.event_id: _batch(event, proposal)})).process_event(event)

    assert receipt.status is EventProcessStatus.APPLIED
    assert len(receipt.applied_entry_ids) == 1


def test_correction_supersedes_old_truth_and_preserves_history(tmp_path):
    repository = TaskStateRepository(str(tmp_path / "correction.db"))
    repository.create_task("task")
    first = TaskEvent("task", "e1", SourceKind.USER, "预算最多5000")
    first_interpreter = FakeInterpreter({"e1": _batch(first, _add(first, "p1", EntryKind.CONSTRAINT, "预算最多5000", span="预算最多5000"))})
    old = TaskStateUpdater(repository, first_interpreter).process_event(first).applied_entry_ids[0]
    second = TaskEvent("task", "e2", SourceKind.USER, "预算不是5000，改成4000")
    proposal = TaskStateProposal(
        "p2", "task", "e2", ProposalAction.SUPERSEDE,
        target_entry_id=old, replacement_content="预算最多4000", support_spans=("预算不是5000，改成4000",),
    )
    receipt = TaskStateUpdater(repository, FakeInterpreter({"e2": _batch(second, proposal)})).process_event(second)
    assert receipt.superseded_entry_ids == (old,)
    history = repository.get_history("task")
    assert any(entry.entry_id == old and entry.status is EntryStatus.SUPERSEDED for entry in history)
    assert any(entry.content == "预算最多4000" and entry.status is EntryStatus.ACTIVE for entry in history)


def test_source_authority_and_provider_owned_semantics(tmp_path):
    repository = TaskStateRepository(str(tmp_path / "authority.db"))
    repository.create_task("task")
    question = TaskEvent("task", "q", SourceKind.USER, "要不要订周三的航班？")
    question_proposal = _add(question, "q1", EntryKind.DECISION, "周三航班", span=question.content)
    question_receipt = TaskStateUpdater(repository, FakeInterpreter({"q": _batch(question, question_proposal)})).process_event(question)
    assert question_receipt.status is EventProcessStatus.APPLIED
    assert len(question_receipt.applied_entry_ids) == 1
    assert repository.list_active_entries("task")[0].content == "周三航班"

    calculator = TaskEvent("task", "calc", SourceKind.USER, "请计算 2 + 2")
    calculator_proposal = _add(calculator, "calc1", EntryKind.PROGRESS, "4", span=calculator.content)
    calculator_receipt = TaskStateUpdater(
        repository, FakeInterpreter({"calc": _batch(calculator, calculator_proposal)})
    ).process_event(calculator)
    assert calculator_receipt.status is EventProcessStatus.APPLIED
    assert len(calculator_receipt.applied_entry_ids) == 1
    assert repository.get_entry(calculator_receipt.applied_entry_ids[0]).content == "4"

    agent = TaskEvent("task", "a", SourceKind.AGENT, "建议住会场附近")
    agent_proposal = _add(agent, "a1", EntryKind.CONSTRAINT, "必须住会场附近", span=agent.content)
    with pytest.raises(Exception):
        TaskStateUpdater(repository, FakeInterpreter({"a": _batch(agent, agent_proposal)})).process_event(agent)

    tool_fail = TaskEvent("task", "t1", SourceKind.TOOL, "酒店预订失败", {"success": False})
    progress = _add(tool_fail, "t1p", EntryKind.PROGRESS, "酒店已订好", span=tool_fail.content, basis=ProposalBasis.OBSERVED)
    with pytest.raises(Exception):
        TaskStateUpdater(repository, FakeInterpreter({"t1": _batch(tool_fail, progress)})).process_event(tool_fail)
    assert len(repository.list_active_entries("task")) == 2


def test_provider_failure_zero_ledger_mutation_and_retry(tmp_path):
    repository = TaskStateRepository(str(tmp_path / "failure.db"))
    repository.create_task("task")
    event = TaskEvent("task", "e1", SourceKind.USER, "预算最多5000")
    interpreter = FakeInterpreter({"e1": RuntimeError("provider unavailable")})
    updater = TaskStateUpdater(repository, interpreter)
    with pytest.raises(TaskStateInterpreterError):
        updater.process_event(event)
    assert repository.list_active_entries("task") == ()
    assert repository.get_event_status("e1") is EventProcessStatus.FAILED
    interpreter.outputs["e1"] = _batch(event, _add(event, "p1", EntryKind.CONSTRAINT, "预算最多5000", span=event.content))
    assert updater.process_event(event).status is EventProcessStatus.APPLIED


def test_atomic_batch_rejects_all_when_one_proposal_invalid(tmp_path):
    repository = TaskStateRepository(str(tmp_path / "atomic.db"))
    repository.create_task("task")
    event = TaskEvent("task", "e1", SourceKind.USER, "预算最多5000")
    good = _add(event, "good", EntryKind.CONSTRAINT, "预算最多5000", span=event.content)
    bad = TaskStateProposal("bad", "task", "e1", ProposalAction.RESOLVE, target_entry_id="missing")
    with pytest.raises(Exception):
        TaskStateUpdater(repository, FakeInterpreter({"e1": _batch(event, good, bad)})).process_event(event)
    assert repository.list_active_entries("task") == ()


def test_resolution_priority_relation_and_restart_replay(tmp_path):
    db = str(tmp_path / "lifecycle.db")
    repository = TaskStateRepository(db)
    repository.create_task("task")
    event = TaskEvent("task", "e1", SourceKind.USER, "酒店还没订")
    open_q = _add(event, "q", EntryKind.OPEN_QUESTION, "酒店是否已预订？", span=event.content)
    goal = _add(event, "g", EntryKind.GOAL, "准备上海会议", span=event.content)
    receipt = TaskStateUpdater(repository, FakeInterpreter({"e1": _batch(event, open_q, goal)})).process_event(event)
    q_id, g_id = receipt.applied_entry_ids
    resolve = TaskEvent("task", "e2", SourceKind.USER, "酒店已经订好了")
    r = TaskStateProposal("r", "task", "e2", ProposalAction.RESOLVE, target_entry_id=q_id, support_spans=(resolve.content,))
    p = TaskStateProposal("p", "task", "e2", ProposalAction.SET_PRIORITY, target_entry_id=g_id, priority=Priority.HIGH, support_spans=(resolve.content,))
    TaskStateUpdater(repository, FakeInterpreter({"e2": _batch(resolve, r, p)})).process_event(resolve)
    assert repository.get_entry(q_id).status is EntryStatus.RESOLVED
    assert repository.get_entry(g_id).priority is Priority.HIGH
    # Replay after closing/reopening must not call the provider or duplicate entries.
    before = repository.get_history("task")
    repository = TaskStateRepository(db)
    replay = TaskStateUpdater(repository, FakeInterpreter({})).process_event(resolve)
    assert replay.status is EventProcessStatus.APPLIED
    assert repository.get_history("task") == before


def test_tool_success_can_record_observed_completion_and_agent_cannot_claim_user_constraint(tmp_path):
    repository = TaskStateRepository(str(tmp_path / "sources.db"))
    repository.create_task("task")
    tool = TaskEvent("task", "tool-ok", SourceKind.TOOL, "booking succeeded", {"success": True})
    proposal = _add(tool, "progress", EntryKind.PROGRESS, "酒店已订好了", span=tool.content, basis=ProposalBasis.OBSERVED)
    receipt = TaskStateUpdater(repository, FakeInterpreter({"tool-ok": _batch(tool, proposal)})).process_event(tool)
    entry = repository.get_entry(receipt.applied_entry_ids[0])
    assert entry.source_kind is SourceKind.TOOL
    assert entry.basis is ProposalBasis.OBSERVED


def test_relation_proposal_is_task_local_and_rejects_self_relation(tmp_path):
    repository = TaskStateRepository(str(tmp_path / "relations.db"))
    repository.create_task("task")
    event = TaskEvent("task", "e1", SourceKind.USER, "依赖")
    a = _add(event, "a", EntryKind.GOAL, "准备会议", span=event.content)
    b = _add(event, "b", EntryKind.DEPENDENCY, "先订票", span=event.content)
    receipt = TaskStateUpdater(repository, FakeInterpreter({"e1": _batch(event, a, b)})).process_event(event)
    relation = TaskRelation("task", receipt.applied_entry_ids[1], receipt.applied_entry_ids[0], RelationKind.DEPENDS_ON)
    event2 = TaskEvent("task", "e2", SourceKind.USER, "先订票")
    link = TaskStateProposal("link", "task", "e2", ProposalAction.LINK_RELATION, relation=relation, support_spans=(event2.content,))
    assert TaskStateUpdater(repository, FakeInterpreter({"e2": _batch(event2, link)})).process_event(event2).relation_keys
    bad = TaskRelation("task", receipt.applied_entry_ids[0], receipt.applied_entry_ids[0], RelationKind.DEPENDS_ON)
    event3 = TaskEvent("task", "e3", SourceKind.USER, "错误关系")
    invalid = TaskStateProposal("bad", "task", "e3", ProposalAction.LINK_RELATION, relation=bad, support_spans=(event3.content,))
    with pytest.raises(Exception):
        TaskStateUpdater(repository, FakeInterpreter({"e3": _batch(event3, invalid)})).process_event(event3)
