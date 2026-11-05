"""Task-level automatic completion, sealed provenance and durable job recovery."""

import io
import json
import sqlite3

import pytest
from scripts.personal_agent_app_server import JsonlAppServer
from src.memory.agent.models import AgentResultStatus
from src.memory.app.console import PersonalAgentApp
from src.memory.collaboration.repository import CoordinationConflict
from src.memory.core.config import MemoryConfig
from src.memory.task_state.models import TaskStatus
from test_experience_lifecycle import ExperienceProvider


class LifecycleProvider(ExperienceProvider):
    def __init__(self):
        super().__init__()
        self.task_decision = "COMPLETE"
        self.review_fault = ""

    def generate_json(self, system_prompt, user_prompt, **kwargs):
        if kwargs.get("schema_name") == "collaboration_taskreview_v1":
            self.names.append(kwargs["schema_name"])
            data = json.loads(user_prompt)
            support = [item["artifact_id"] for item in data["source"]["artifacts"]]
            assessments = [
                {
                    "entry_id": row["entry_id"],
                    "status": (
                        "SATISFIED" if self.task_decision == "COMPLETE" else "UNCERTAIN"
                    ),
                    "artifact_ids": (
                        ["forged"] if self.review_fault == "forged" else support
                    ),
                    "reason": "Whole task criteria checked against verified artifacts",
                }
                for row in data["required_entries"]
            ]
            if self.review_fault == "omitted":
                assessments = []
            return {
                "schema_version": 1,
                "decision": self.task_decision,
                "assessments": assessments,
                "remaining_requirements": (
                    []
                    if self.task_decision == "COMPLETE"
                    else ["Confirm remaining task scope"]
                ),
                "question": (
                    None
                    if self.task_decision == "COMPLETE"
                    else "还需要处理哪些输入文件？"
                ),
                "reason": "All tracked requirements checked",
            }
        return super().generate_json(system_prompt, user_prompt, **kwargs)


def loop_app(tmp_path, monkeypatch, provider=None):
    monkeypatch.chdir(tmp_path)
    for name, value in (("left", "alpha=10"), ("right", "beta=20")):
        p = tmp_path / (name + ".txt")
        if not p.exists():
            p.write_text(value, encoding="utf-8")
    return PersonalAgentApp(
        data_dir=str(tmp_path / "state"),
        config=MemoryConfig(),
        llm_client=provider or LifecycleProvider(),
        execution_mode="multi_fixed",
    )


def test_whole_task_auto_completion_learning_restart_and_next_task_reuse(
    tmp_path, monkeypatch
):
    provider = LifecycleProvider()
    app = loop_app(tmp_path, monkeypatch, provider)
    first = app.process_chat_turn("Read both independent files and combine values")
    assert first.status is AgentResultStatus.SUCCESS, first.error_info
    assert first.final_response == "alpha=10 | beta=20"
    assert (
        app.runtime.task_service.get_task(first.task_id).status is TaskStatus.COMPLETED
    )
    seal = app.runtime.task_service.repository.get_completion_receipt(first.task_id)
    assert seal["source_run_id"] == first.agent_run_id
    lesson = app.list_experiences()[0]
    assert lesson["state"] == "ACTIVE"
    source = app.collaboration.experiences.source(first.agent_run_id)
    assert (
        source["user_request"]["content"]
        == "Read both independent files and combine values"
    )
    assert source["task_completion"]["completion_id"] == seal["completion_id"]
    assert first.trace_metadata["task_lifecycle"]["tasks"][0]["state"] == "COMPLETED"
    assert (
        first.trace_metadata["task_lifecycle"]["tasks"][0]["detail"]["experience_state"]
        == "ACTIVE"
    )
    calls = len(provider.names)
    app.drain_task_lifecycle()
    assert len(provider.names) == calls
    session = app.active_session_id
    app.close()
    reopened = loop_app(tmp_path, monkeypatch, provider)
    try:
        assert reopened.resume_session(session)
        assert len(provider.names) == calls
        second = reopened.process_chat_turn(
            "Read both files again and combine both current values"
        )
        assert second.status is AgentResultStatus.SUCCESS, second.error_info
        assert second.task_id != first.task_id
        assert lesson["experience_id"] in {
            ref["experience_id"] for ref in second.trace_metadata["experience_inputs"]
        }
        assert (
            reopened.runtime.task_service.get_task(second.task_id).status
            is TaskStatus.COMPLETED
        )
        assert len(reopened.list_experiences()) == 2
        graph = next(
            g
            for g in reopened.coordination_repository.list_task_graphs(first.task_id)
            if g["run_id"] == first.agent_run_id
        )
        reopened.coordination_repository.invalidate_artifact(
            first.agent_run_id,
            graph["nodes"][0]["attempts"][0]["artifact_id"],
            "sealed-source-revoked",
        )
        assert reopened.list_experiences()[-1]["state"] == "STALE"
        replay = reopened._process_chat_turn_with_id(
            "Read both files again and combine both current values",
            second.request_turn_id,
            resume=True,
        )
        assert (
            replay.status is AgentResultStatus.FAILED and replay.final_response is None
        )
    finally:
        reopened.close()


def test_waiting_task_is_not_closed_and_source_change_repairs_then_completes(
    tmp_path, monkeypatch
):
    provider = LifecycleProvider()
    provider.task_decision = "WAITING_USER"
    app = loop_app(tmp_path, monkeypatch, provider)
    first = app.process_chat_turn("Read both files")
    assert app.runtime.task_service.get_task(first.task_id).status is TaskStatus.ACTIVE
    state = app.get_task_lifecycle_status(first.task_id)["tasks"][0]
    assert state["state"] == "WAITING_USER" and state["detail"]["question"]
    assert state["detail"]["question"] in first.final_response
    assert app.list_experiences() == []
    (tmp_path / "left.txt").write_text("alpha=99", encoding="utf-8")
    session = app.active_session_id
    app.close()
    provider.task_decision = "COMPLETE"
    reopened = loop_app(tmp_path, monkeypatch, provider)
    try:
        assert reopened.resume_session(session)
        assert (
            reopened.runtime.task_service.get_task(first.task_id).status
            is TaskStatus.COMPLETED
        )
        seal = reopened.runtime.task_service.repository.get_completion_receipt(
            first.task_id
        )
        assert seal["source_run_id"] != first.agent_run_id
        assert reopened.list_experiences()[0]["state"] == "ACTIVE"
        latest = json.loads(
            reopened.coordination_repository.get_run(seal["source_run_id"])[
                "result_json"
            ]
        )
        assert latest["final_response"] == "alpha=99 | beta=20"
    finally:
        reopened.close()


@pytest.mark.parametrize("fault", ["forged", "omitted"])
def test_bad_completion_coverage_never_seals_or_learns(tmp_path, monkeypatch, fault):
    provider = LifecycleProvider()
    provider.review_fault = fault
    app = loop_app(tmp_path, monkeypatch, provider)
    try:
        result = app.process_chat_turn("Read both files")
        assert result.status is AgentResultStatus.SUCCESS
        assert (
            app.runtime.task_service.get_task(result.task_id).status
            is TaskStatus.ACTIVE
        )
        assert (
            app.runtime.task_service.repository.get_completion_receipt(result.task_id)
            is None
        )
        assert app.get_task_lifecycle_status()["tasks"][0]["state"] == "FAILED"
        assert app.list_experiences() == []
        calls = len(provider.names)
        app.drain_task_lifecycle()
        assert len(provider.names) == calls
    finally:
        app.close()


def test_interrupted_learning_recovers_after_task_was_atomically_completed(
    tmp_path, monkeypatch
):
    provider = LifecycleProvider()
    app = loop_app(tmp_path, monkeypatch, provider)
    original = app.collaboration.roles.run

    def interrupt(role, inputs):
        if role == "ExperienceReview":
            raise KeyboardInterrupt("simulated process interruption")
        return original(role, inputs)

    monkeypatch.setattr(app.collaboration.roles, "run", interrupt)
    with pytest.raises(KeyboardInterrupt):
        app.process_chat_turn("Read both files")
    task = app.active_task_id
    assert app.runtime.task_service.get_task(task).status is TaskStatus.COMPLETED
    assert app.get_task_lifecycle_status()["jobs"][-1]["state"] == "PENDING"
    assert app.list_experiences()[0]["state"] == "CANDIDATE"
    session = app.active_session_id
    app.close()
    reopened = loop_app(tmp_path, monkeypatch, provider)
    try:
        assert reopened.resume_session(session)
        assert reopened.list_experiences()[0]["state"] == "ACTIVE"
        assert provider.names.count("collaboration_experience_v1") == 1
    finally:
        reopened.close()


@pytest.mark.parametrize("tamper", ["seal", "ledger", "reopen"])
def test_completed_task_provenance_does_not_ignore_mutation(
    tmp_path, monkeypatch, tamper
):
    app = loop_app(tmp_path, monkeypatch)
    try:
        first = app.process_chat_turn("Read both files")
        lesson = app.list_experiences()[0]
        tasks = app.runtime.task_service.repository
        if tamper == "reopen":
            tasks.set_task_status(first.task_id, TaskStatus.ACTIVE)
        else:
            with sqlite3.connect(tasks.db_path) as db:
                if tamper == "seal":
                    db.execute(
                        "UPDATE task_completion_receipts SET payload_json='{}' WHERE task_id=?",
                        (first.task_id,),
                    )
                else:
                    db.execute(
                        "UPDATE task_entries SET content='changed goal' WHERE task_id=? AND kind='GOAL'",
                        (first.task_id,),
                    )
        with pytest.raises(ValueError):
            app.collaboration.experiences.validate(lesson)
        assert (
            app.collaboration.experiences.get(lesson["experience_id"])["state"]
            == "STALE"
        )
    finally:
        app.close()


def test_jsonl_exposes_task_lifecycle_without_extra_model_calls(tmp_path, monkeypatch):
    provider = LifecycleProvider()
    app = loop_app(tmp_path, monkeypatch, provider)
    try:
        first = app.process_chat_turn("Read both files")
        calls = len(provider.names)
        server = JsonlAppServer(app, io.StringIO())
        assert server._handle("initialize", {})["capabilities"]["taskLifecycle"]
        assert (
            server._handle("task/lifecycle/read", {"taskId": first.task_id})["tasks"][
                0
            ]["state"]
            == "COMPLETED"
        )
        server._handle("task/lifecycle/drain", {})
        assert len(provider.names) == calls
    finally:
        app.close()


def test_crash_after_task3_commit_recovers_completion_and_learning_once(
    tmp_path, monkeypatch
):
    provider = LifecycleProvider()
    app = loop_app(tmp_path, monkeypatch, provider)
    tasks = app.runtime.task_service.repository
    original = tasks.seal_completion

    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise KeyboardInterrupt("crash after Task 3 commit, before outbox commit")

    monkeypatch.setattr(tasks, "seal_completion", interrupted)
    with pytest.raises(KeyboardInterrupt):
        app.process_chat_turn("Read both files")
    task = app.active_task_id
    assert tasks.get_completion_receipt(task)
    assert app.get_task_lifecycle_status()["jobs"][0]["state"] == "PENDING"
    session = app.active_session_id
    app.close()
    reopened = loop_app(tmp_path, monkeypatch, provider)
    try:
        assert reopened.resume_session(session)
        assert reopened.get_task_lifecycle_status()["tasks"][0]["state"] == "COMPLETED"
        assert reopened.list_experiences()[0]["state"] == "ACTIVE"
        assert provider.names.count("collaboration_taskreview_v1") == 1
        calls = len(provider.names)
        reopened.drain_task_lifecycle()
        assert len(provider.names) == calls
    finally:
        reopened.close()


def test_waiting_question_outbox_survives_interruption_and_does_not_duplicate(
    tmp_path, monkeypatch
):
    provider = LifecycleProvider()
    provider.task_decision = "WAITING_USER"
    app = loop_app(tmp_path, monkeypatch, provider)

    def interrupted(notification):
        raise KeyboardInterrupt("delivery happened but acknowledgement did not")

    monkeypatch.setattr(app.collaboration.lifecycle, "acknowledge", interrupted)
    with pytest.raises(KeyboardInterrupt):
        app.process_chat_turn("Read both files")
    messages_before = app.conversation_repo.list_messages(app.active_session_id)
    assert sum(m.content == "还需要处理哪些输入文件？" for m in messages_before) == 1
    calls = len(provider.names)
    session = app.active_session_id
    app.close()
    reopened = loop_app(tmp_path, monkeypatch, provider)
    try:
        assert reopened.resume_session(session)
        assert len(provider.names) == calls
        messages = reopened.conversation_repo.list_messages(session)
        assert sum(m.content == "还需要处理哪些输入文件？" for m in messages) == 1
        assert reopened.collaboration.lifecycle.notifications(session) == []
    finally:
        reopened.close()


def test_cancelled_task_cancels_pending_automation_without_model_calls(
    tmp_path, monkeypatch
):
    provider = LifecycleProvider()
    app = loop_app(tmp_path, monkeypatch, provider)
    original = app.collaboration.lifecycle.drain
    monkeypatch.setattr(
        app.collaboration.lifecycle, "drain", lambda *args, **kwargs: []
    )
    try:
        first = app.process_chat_turn("Read both files")
        assert app.get_task_lifecycle_status()["jobs"][0]["state"] == "PENDING"
        calls = len(provider.names)
        app.cancel_current_task()
        monkeypatch.setattr(app.collaboration.lifecycle, "drain", original)
        app.drain_task_lifecycle()
        assert len(provider.names) == calls
        assert (
            app.runtime.task_service.get_task(first.task_id).status
            is TaskStatus.CANCELLED
        )
        assert app.get_task_lifecycle_status()["tasks"][0]["state"] == "CANCELLED"
        assert app.get_task_lifecycle_status()["jobs"][0]["state"] == "CANCELLED"
    finally:
        app.close()


def test_tampered_job_is_rejected_before_any_model_call(tmp_path, monkeypatch):
    provider = LifecycleProvider()
    app = loop_app(tmp_path, monkeypatch, provider)
    original = app.collaboration.lifecycle.drain
    monkeypatch.setattr(
        app.collaboration.lifecycle, "drain", lambda *args, **kwargs: []
    )
    try:
        app.process_chat_turn("Read both files")
        calls = len(provider.names)
        with sqlite3.connect(app.coordination_repository.db_path) as db:
            db.execute("UPDATE lifecycle_jobs SET input_json='{}'")
        monkeypatch.setattr(app.collaboration.lifecycle, "drain", original)
        with pytest.raises(CoordinationConflict, match="integrity"):
            app.drain_task_lifecycle()
        assert len(provider.names) == calls
    finally:
        app.close()


def test_new_open_requirement_during_sealing_prevents_completion(tmp_path, monkeypatch):
    from src.memory.task_state.models import EntryKind

    app = loop_app(tmp_path, monkeypatch)
    tasks = app.runtime.task_service.repository
    original = tasks.seal_completion

    def changed(task_id, *args, **kwargs):
        tasks.add_entry(
            task_id, EntryKind.OPEN_QUESTION, "New requirement arrived concurrently"
        )
        return original(task_id, *args, **kwargs)

    monkeypatch.setattr(tasks, "seal_completion", changed)
    try:
        result = app.process_chat_turn("Read both files")
        assert (
            app.runtime.task_service.get_task(result.task_id).status
            is TaskStatus.ACTIVE
        )
        assert tasks.get_completion_receipt(result.task_id) is None
        assert app.list_experiences() == []
        assert app.get_task_lifecycle_status()["tasks"][0]["state"] == "FAILED"
    finally:
        app.close()


@pytest.mark.parametrize(
    "operation",
    ["agent_action", "collaboration_taskreview_v1", "collaboration_experience_v1"],
)
def test_one_structured_retry_is_charged_and_never_reexecutes_a_tool(
    tmp_path, monkeypatch, operation
):
    from src.memory.llm.errors import ProviderResponseError

    class RecoveringProvider(LifecycleProvider):
        def __init__(self):
            super().__init__()
            self.injected = False

        def generate_json(self, system_prompt, user_prompt, **kwargs):
            if kwargs.get("schema_name") == operation and not self.injected:
                self.injected = True
                self.names.append(operation)
                raise ProviderResponseError(
                    "invalid structured response", provider="test", operation=operation
                )
            return super().generate_json(system_prompt, user_prompt, **kwargs)

    provider = RecoveringProvider()
    app = loop_app(tmp_path, monkeypatch, provider)
    try:
        first = app.process_chat_turn("Read both files")
        assert first.status is AgentResultStatus.SUCCESS
        assert (
            app.runtime.task_service.get_task(first.task_id).status
            is TaskStatus.COMPLETED
        )
        assert provider.read_requests == ["left", "right"]
        with app.coordination_repository._connect() as db:
            failed = db.execute(
                "SELECT COUNT(*) FROM model_calls WHERE operation=? AND status='FAILED'",
                (operation,),
            ).fetchone()[0]
        assert failed == 1
        assert app.list_experiences()[0]["state"] == "ACTIVE"
        calls = len(provider.names)
        app.drain_task_lifecycle()
        assert len(provider.names) == calls
    finally:
        app.close()


def test_transport_errors_do_not_trigger_structured_recovery(tmp_path, monkeypatch):
    from src.memory.llm.errors import ProviderRequestError

    class FailedProvider(LifecycleProvider):
        def generate_json(self, system_prompt, user_prompt, **kwargs):
            if kwargs.get("schema_name") == "agent_action":
                self.names.append("agent_action")
                raise ProviderRequestError(
                    "unavailable", provider="test", operation="agent_action"
                )
            return super().generate_json(system_prompt, user_prompt, **kwargs)

    provider = FailedProvider()
    app = loop_app(tmp_path, monkeypatch, provider)
    try:
        result = app.process_chat_turn("Read both files")
        assert result.status is AgentResultStatus.FAILED
        assert provider.names.count("agent_action") == 1
        assert provider.read_requests == []
    finally:
        app.close()


def test_structured_recovery_cannot_reset_or_exceed_original_budget(tmp_path):
    from src.memory.collaboration.budget import (
        BudgetedLLMClient,
        CollaborationBudgetError,
        CollaborationLimits,
    )
    from src.memory.collaboration.repository import CoordinationRepository
    from src.memory.llm.errors import ProviderResponseError
    from test_collaboration import CollaborationProvider

    class InvalidProvider(CollaborationProvider):
        def generate_json(self, *args, **kwargs):
            self.names.append("invalid")
            raise ProviderResponseError("not JSON", provider="test", operation="test")

    provider = InvalidProvider()
    repo = CoordinationRepository(str(tmp_path / "budget.db"))
    client = BudgetedLLMClient(
        provider, repo, CollaborationLimits(max_model_calls=1), structured_recovery=True
    )
    with client.turn("session", "turn", "input") as run_id, pytest.raises(
        CollaborationBudgetError
    ):
        client.generate_json("system", "input", response_schema={"type": "object"})
    assert len(provider.names) == 1
    assert repo.usage(run_id)["model_calls"] == 1


def test_rejected_learning_keeps_completion_and_explicit_failed_experience_state(
    tmp_path, monkeypatch
):
    provider = LifecycleProvider()
    provider.mode = "forged"
    app = loop_app(tmp_path, monkeypatch, provider)
    try:
        result = app.process_chat_turn("Read both files")
        assert result.status is AgentResultStatus.SUCCESS
        assert (
            app.runtime.task_service.get_task(result.task_id).status
            is TaskStatus.COMPLETED
        )
        receipt = app.runtime.task_service.repository.get_completion_receipt(
            result.task_id
        )
        cycle = app.get_task_lifecycle_status()["tasks"][0]
        assert cycle["state"] == "COMPLETED"
        assert cycle["detail"]["completion_id"] == receipt["completion_id"]
        assert cycle["detail"]["experience_state"] == "FAILED"
        assert app.list_experiences() == []
        assert app.get_task_lifecycle_status()["jobs"][-1]["state"] == "FAILED"
        calls = len(provider.names)
        app.drain_task_lifecycle()
        assert len(provider.names) == calls
    finally:
        app.close()


@pytest.mark.parametrize(
    "action,expected", [("review", "ACTIVE"), ("revoke", "REVOKED")]
)
def test_experience_confirmation_resolves_the_waiting_job_and_question(
    tmp_path, monkeypatch, action, expected
):
    provider = LifecycleProvider()
    provider.mode = "uncertain"
    app = loop_app(tmp_path, monkeypatch, provider)
    try:
        first = app.process_chat_turn("Read both files")
        lesson = app.list_experiences()[0]
        assert lesson["state"] == "NEEDS_CONFIRMATION"
        assert app.get_task_lifecycle_status()["jobs"][-1]["state"] == "WAITING_USER"
        if action == "review":
            app.review_experience(
                lesson["experience_id"],
                "Use only for the same scoped two-file verification procedure",
            )
        else:
            app.revoke_experience(lesson["experience_id"], "decline-candidate")
        app.drain_task_lifecycle()
        cycle = app.get_task_lifecycle_status()["tasks"][0]
        assert cycle["state"] == "COMPLETED"
        assert cycle["detail"]["experience_state"] == expected
        assert cycle["detail"]["question"] is None
        assert all(
            job["state"] == "SUCCEEDED"
            for job in app.get_task_lifecycle_status()["jobs"]
        )
        assert (
            app.runtime.task_service.get_task(first.task_id).status
            is TaskStatus.COMPLETED
        )
        calls = len(provider.names)
        app.drain_task_lifecycle()
        assert len(provider.names) == calls
    finally:
        app.close()
