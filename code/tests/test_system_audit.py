"""Fault injection at scheduler commit boundaries and cross-session diagnostics."""

import io

import pytest
from scripts.personal_agent_app_server import JsonlAppServer
from src.memory.collaboration.budget import CollaborationLimits
from src.memory.collaboration.models import identity
from src.memory.collaboration.repair import repair_lock
from src.memory.collaboration.repository import (
    CoordinationConflict,
    CoordinationRepository,
)
from src.memory.task_state.models import TaskStatus
from test_task_lifecycle import LifecycleProvider, loop_app


def test_format_retry_uses_schema_feedback_without_copying_model_values(tmp_path):
    from jsonschema import Draft202012Validator
    from src.memory.collaboration.budget import BudgetedLLMClient
    from src.memory.llm.errors import ProviderResponseError
    from test_collaboration import CollaborationProvider

    schema = {
        "type": "object",
        "properties": {"content": {"type": "string"}},
        "required": ["content"],
    }

    class InvalidThenValid(CollaborationProvider):
        def __init__(self):
            super().__init__()
            self.prompts = []

        def generate_json(self, system_prompt, user_prompt, **kwargs):
            self.prompts.append(system_prompt)
            if len(self.prompts) == 1:
                try:
                    Draft202012Validator(schema).validate(
                        {"injection": "DO_NOT_COPY_MODEL_VALUES"}
                    )
                except Exception as cause:
                    raise ProviderResponseError(
                        "invalid", provider="test", operation="test"
                    ) from cause
            return {"content": "valid"}

    provider = InvalidThenValid()
    repository = CoordinationRepository(str(tmp_path / "coordination.db"))
    client = BudgetedLLMClient(
        provider, repository, CollaborationLimits(), structured_recovery=True
    )
    with client.turn("session", "turn", "input") as run:
        assert client.generate_json(
            "trusted", "original evidence", response_schema=schema
        ) == {"content": "valid"}
        assert repository.usage(run)["model_calls"] == 2
    assert '"missing_required_fields":["content"]' in provider.prompts[-1]
    assert "DO_NOT_COPY_MODEL_VALUES" not in provider.prompts[-1]


def test_terminal_run_status_cannot_flip_with_identical_payload(tmp_path):
    from dataclasses import asdict

    repository = CoordinationRepository(str(tmp_path / "coordination.db"))
    run = repository.begin("session", "turn", "input", asdict(CollaborationLimits()))
    payload = {"value": "accepted"}
    repository.finish(run, "SUCCEEDED", payload)
    with pytest.raises(CoordinationConflict):
        repository.finish(run, "FAILED", payload)
    assert repository.get_run(run)["status"] == "SUCCEEDED"


def test_source_result_and_outbox_roll_back_in_the_same_transaction(tmp_path):
    import sqlite3
    from dataclasses import asdict

    repository = CoordinationRepository(str(tmp_path / "coordination.db"))
    run = repository.begin("session", "turn", "input", asdict(CollaborationLimits()))
    with repository._connect() as db:
        db.executescript(
            "CREATE TRIGGER fail_outbox BEFORE INSERT ON lifecycle_result_outbox BEGIN SELECT RAISE(ABORT,'injected write failure'); END;"
        )
    with pytest.raises(sqlite3.IntegrityError):
        repository.finish(run, "SUCCEEDED", {"trace_metadata": {"task_frontier": {}}})
    assert repository.get_run(run)["status"] == "RUNNING"
    assert repository.get_run(run)["result_json"] is None


def test_other_workspace_diagnostics_never_invalidate_a_valid_source(
    tmp_path, monkeypatch
):
    provider = LifecycleProvider()
    app = loop_app(tmp_path, monkeypatch, provider)
    try:
        result = app.process_chat_turn("Read both files")
        source_session = app.active_session_id
        lesson = app.list_experiences()[0]
        active_session = app.create_new_session("different workspace")
        other = tmp_path / "other"
        other.mkdir()
        app.workspace.replace_scope([str(other)], str(other))
        calls = len(provider.names)
        server = JsonlAppServer(app, io.StringIO())
        server._handle("initialize", {})
        report = server._handle("task/lifecycle/read", {"threadId": source_session})
        assert report["tasks"][0]["current_result_validity"] == "NOT_CHECKED_WORKSPACE"
        assert (
            app.collaboration.experiences.get(lesson["experience_id"])["state"]
            == "ACTIVE"
        )
        assert app.active_session_id == active_session
        assert app.workspace.snapshot()["current_root"] == str(other)
        assert len(provider.names) == calls
        assert (
            app.runtime.task_service.get_task(result.task_id).status
            is TaskStatus.COMPLETED
        )
    finally:
        app.close()


def test_missing_task_outbox_is_recovered_after_result_commit(tmp_path, monkeypatch):
    provider = LifecycleProvider()
    app = loop_app(tmp_path, monkeypatch, provider)

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt("result committed, enqueue not committed")

    monkeypatch.setattr(app.collaboration.lifecycle, "enqueue_result", interrupted)
    with pytest.raises(KeyboardInterrupt):
        app.process_chat_turn("Read both files")
    session = app.active_session_id
    with app.coordination_repository._connect() as db:
        task = db.execute(
            "SELECT task_id FROM runs WHERE result_json IS NOT NULL AND task_id IS NOT NULL ORDER BY started DESC LIMIT 1"
        ).fetchone()[0]
    assert app.runtime.task_service.get_task(task).status is TaskStatus.ACTIVE
    app.close()
    reopened = loop_app(tmp_path, monkeypatch, provider)
    try:
        assert reopened.resume_session(session)
        assert (
            reopened.runtime.task_service.get_task(task).status is TaskStatus.COMPLETED
        )
        assert reopened.list_experiences()[0]["state"] == "ACTIVE"
    finally:
        reopened.close()


def test_normal_exception_after_task3_commit_does_not_lose_recovery(
    tmp_path, monkeypatch
):
    provider = LifecycleProvider()
    app = loop_app(tmp_path, monkeypatch, provider)
    original = app.runtime.task_service.repository.seal_completion

    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("coordination commit failed after Task 3 commit")

    monkeypatch.setattr(
        app.runtime.task_service.repository, "seal_completion", interrupted
    )
    app.process_chat_turn("Read both files")
    task, session = app.active_task_id, app.active_session_id
    assert app.runtime.task_service.get_task(task).status is TaskStatus.COMPLETED
    app.close()
    reopened = loop_app(tmp_path, monkeypatch, provider)
    try:
        assert reopened.resume_session(session)
        assert (
            reopened.get_task_lifecycle_status(task)["tasks"][0]["state"] == "COMPLETED"
        )
        assert reopened.list_experiences()[0]["state"] == "ACTIVE"
        assert provider.names.count("collaboration_taskreview_v1") == 1
    finally:
        reopened.close()


def test_cancelled_job_cannot_overwrite_cancelled_cycle_on_late_failure(
    tmp_path, monkeypatch
):
    app = loop_app(tmp_path, monkeypatch)
    monkeypatch.setattr(
        app.collaboration.lifecycle, "drain", lambda *args, **kwargs: []
    )
    try:
        app.process_chat_turn("Read both files")
        controller = app.collaboration.lifecycle
        job = controller.jobs(app.active_session_id, pending=True)[0]
        app.cancel_current_task()
        controller._finish(
            job, "FAILED", {"reason": "late worker exception"}, cycle_state="FAILED"
        )
        assert (
            controller.status(app.active_session_id)["tasks"][0]["state"] == "CANCELLED"
        )
        assert controller.jobs(app.active_session_id)[0]["state"] == "CANCELLED"
    finally:
        app.close()


def test_busy_task_does_not_starve_an_independent_pending_task(tmp_path, monkeypatch):
    app = loop_app(tmp_path, monkeypatch)
    controller = app.collaboration.lifecycle
    original = controller.drain
    monkeypatch.setattr(controller, "drain", lambda *args, **kwargs: [])
    first = app.process_chat_turn("Read both files")
    second_task = app.create_new_task("Read both files independently")
    app.process_chat_turn("Read both files")
    monkeypatch.setattr(controller, "drain", original)
    try:
        with repair_lock(
            app.coordination_repository.db_path, identity(second_task, "task-lifecycle")
        ):
            controller.drain(app.active_session_id)
        assert (
            app.runtime.task_service.get_task(first.task_id).status
            is TaskStatus.COMPLETED
        )
        assert (
            app.runtime.task_service.get_task(second_task).status is TaskStatus.ACTIVE
        )
    finally:
        app.close()


@pytest.mark.parametrize(
    "method", ["task/lifecycle/read", "repair/list", "repair/graphs", "experience/list"]
)
def test_lifecycle_read_of_other_session_is_read_only(tmp_path, monkeypatch, method):
    provider = LifecycleProvider()
    app = loop_app(tmp_path, monkeypatch, provider)
    controller = app.collaboration.lifecycle
    original = controller.drain
    monkeypatch.setattr(controller, "drain", lambda *args, **kwargs: [])
    first = app.process_chat_turn("Read both files")
    source_session = app.active_session_id
    other_session = app.create_new_session("other")
    calls = len(provider.names)
    monkeypatch.setattr(controller, "drain", original)
    try:
        server = JsonlAppServer(app, io.StringIO())
        server._handle("initialize", {})
        server._handle(method, {"threadId": source_session, "taskId": first.task_id})
        assert (
            controller.status(source_session)["tasks"][0]["state"] == "ASSESS_PENDING"
        )
        assert app.active_session_id == other_session
        assert len(provider.names) == calls
        assert (
            app.runtime.task_service.get_task(first.task_id).status is TaskStatus.ACTIVE
        )
    finally:
        app.close()


def test_task_review_model_run_is_final_after_restart(tmp_path, monkeypatch):
    provider = LifecycleProvider()
    app = loop_app(tmp_path, monkeypatch, provider)
    original = app.coordination_repository.finish

    def interrupted(run_id, status, payload):
        if payload.get("kind") == "task_review":
            raise KeyboardInterrupt("job result committed before model run terminal")
        return original(run_id, status, payload)

    monkeypatch.setattr(app.coordination_repository, "finish", interrupted)
    with pytest.raises(KeyboardInterrupt):
        app.process_chat_turn("Read both files")
    session = app.active_session_id
    job = app.collaboration.lifecycle.jobs(session)[0]
    model_run = identity(session, job["job_id"])
    app.close()
    reopened = loop_app(tmp_path, monkeypatch, provider)
    try:
        assert reopened.resume_session(session)
        assert (
            reopened.coordination_repository.get_run(model_run)["status"] == "SUCCEEDED"
        )
        assert provider.names.count("collaboration_taskreview_v1") == 1
    finally:
        reopened.close()


def test_completed_task_diagnostics_report_invalidated_current_evidence(
    tmp_path, monkeypatch
):
    app = loop_app(tmp_path, monkeypatch)
    try:
        result = app.process_chat_turn("Read both files")
        (tmp_path / "left.txt").write_text("alpha=changed", encoding="utf-8")
        app.drain_task_lifecycle()
        state = app.get_task_lifecycle_status(result.task_id)["tasks"][0]
        assert state["state"] == "COMPLETED"  # historical completion stays immutable
        assert state["current_result_validity"] == "STALE"
        assert state["current_experience_state"] == "STALE"
    finally:
        app.close()


def test_duplicate_enqueue_does_not_reset_completed_projection(tmp_path, monkeypatch):
    app = loop_app(tmp_path, monkeypatch)
    try:
        result = app.process_chat_turn("Read both files")
        before = app.get_task_lifecycle_status()
        app.collaboration.lifecycle.enqueue_result(result.agent_run_id)
        app.drain_task_lifecycle()
        assert app.get_task_lifecycle_status() == before
        with app.coordination_repository._connect() as db:
            assert (
                db.execute("SELECT state FROM lifecycle_result_outbox").fetchone()[0]
                == "ENQUEUED"
            )
    finally:
        app.close()


@pytest.mark.parametrize("fault", ["forged", "omitted"])
def test_failed_review_does_not_leave_an_orphan_running_model_budget(
    tmp_path, monkeypatch, fault
):
    provider = LifecycleProvider()
    provider.review_fault = fault
    app = loop_app(tmp_path, monkeypatch, provider)
    try:
        app.process_chat_turn("Read both files")
        with app.coordination_repository._connect() as db:
            assert (
                db.execute(
                    "SELECT COUNT(*) FROM runs WHERE status='RUNNING'"
                ).fetchone()[0]
                == 0
            )
    finally:
        app.close()


def test_revoked_experience_is_visible_in_current_completion_diagnostics(
    tmp_path, monkeypatch
):
    app = loop_app(tmp_path, monkeypatch)
    try:
        app.process_chat_turn("Read both files")
        lesson = app.list_experiences()[0]
        app.revoke_experience(lesson["experience_id"], "audit-revoke")
        cycle = app.get_task_lifecycle_status()["tasks"][0]
        assert cycle["state"] == "COMPLETED"
        assert cycle["current_result_validity"] == "VALID"
        assert cycle["current_experience_state"] == "REVOKED"
    finally:
        app.close()
