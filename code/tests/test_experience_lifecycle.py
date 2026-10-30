"""Procedural lesson admission, applicability, feedback, revision and revocation."""

import io
import json
import sqlite3

import pytest
from scripts.personal_agent_app_server import JsonlAppServer
from src.memory.agent.models import AgentResultStatus
from src.memory.collaboration.repository import CoordinationConflict
from test_file_source_repair import FileProvider, file_app


class ExperienceProvider(FileProvider):
    def __init__(self, mode="admit"):
        super().__init__()
        self.mode = mode
        self.selected_seen = []
        self.fail_consumer = False

    def generate_json(self, system_prompt, user_prompt, **kwargs):
        schema = kwargs.get("schema_name")
        if schema in {
            "collaboration_experience_v1",
            "collaboration_experiencereview_v1",
            "collaboration_experienceselect_v1",
        }:
            self.names.append(schema)
            data = json.loads(user_prompt)
            if schema == "collaboration_experience_v1":
                return {
                    "schema_version": 1,
                    "decision": "ABSTAIN" if self.mode == "abstain" else "PROPOSE",
                    "task_type": "Combining independent file values",
                    "preconditions": [
                        "Two scoped readable UTF-8 source files and a request to preserve both values"
                    ],
                    "action_summary": "Read both independent inputs separately, then combine their verified values",
                    "observed_outcome": data["outcome"],
                    "artifact_ids": (
                        ["forged"]
                        if self.mode == "forged"
                        else [row["artifact_id"] for row in data["artifacts"]]
                    ),
                    "applicability_scope": "File combination tasks with unchanged permissions and tool environment",
                    "reason": "Verified native reads support this bounded procedure",
                }
            if schema == "collaboration_experiencereview_v1":
                verdict = (
                    "QUARANTINE"
                    if self.mode == "quarantine"
                    else (
                        "NEEDS_CONFIRMATION"
                        if self.mode == "uncertain" and not data["user_confirmation"]
                        else "ADMIT"
                    )
                )
                return {
                    "schema_version": 1,
                    "verdict": verdict,
                    "artifact_ids": data["candidate"]["artifact_ids"],
                    "reason": "Scope and exact source artifacts reviewed",
                }
            return {
                "schema_version": 1,
                "assessments": [
                    {
                        "experience_id": row["experience_id"],
                        "verdict": (
                            "UNCERTAIN" if self.mode == "inapplicable" else "APPLICABLE"
                        ),
                        "reason": "Current task preconditions checked",
                    }
                    for row in data["experiences"]
                ],
            }
        if schema == "collaboration_planner_v1":
            self.selected_seen.append(
                json.loads(user_prompt).get("procedural_experiences", [])
            )
        result = super().generate_json(system_prompt, user_prompt, **kwargs)
        if schema == "collaboration_verifier_v1" and self.fail_consumer:
            result.update(verdict="BLOCKED", reason="Injected consumer failure")
        return result


def create_lesson(tmp_path, monkeypatch, mode="admit"):
    provider = ExperienceProvider(mode)
    app = file_app(tmp_path, monkeypatch, provider)
    source = app.process_chat_turn("Read both source files and combine their values")
    assert source.status is AgentResultStatus.SUCCESS, source.error_info
    before = len(app.long_term_memory_mgr.sqlite_mgr.list_current())
    lesson = app.learn_experience(source.agent_run_id)
    assert len(app.long_term_memory_mgr.sqlite_mgr.list_current()) == before
    return app, provider, source, lesson


def test_admitted_lesson_survives_restart_is_used_with_provenance_and_feedback(
    tmp_path, monkeypatch
):
    app, provider, source, lesson = create_lesson(tmp_path, monkeypatch)
    assert lesson["state"] == "ACTIVE"
    calls = len(provider.names)
    assert app.learn_experience(source.agent_run_id) == lesson
    assert len(provider.names) == calls
    session = app.active_session_id
    app.close()
    reopened = file_app(tmp_path, monkeypatch, provider)
    try:
        assert reopened.resume_session(session)
        consumer = reopened.process_chat_turn(
            "Again read both files and combine the values"
        )
        assert consumer.status is AgentResultStatus.SUCCESS, consumer.error_info
        assert consumer.trace_metadata["experience_inputs"] == [
            {
                "experience_id": lesson["experience_id"],
                "revision_id": lesson["revision_id"],
            }
        ]
        assert provider.selected_seen[-1][0]["experience_id"] == lesson["experience_id"]
        feedback = reopened.feedback_experience(
            lesson["experience_id"], consumer.agent_run_id, "feedback-one"
        )
        assert feedback["state"] == "ACTIVE"
        assert feedback["events"][-1]["causal_contribution"] == "UNKNOWN"
        assert (
            reopened.feedback_experience(
                lesson["experience_id"], consumer.agent_run_id, "feedback-one"
            )
            == feedback
        )
        server = JsonlAppServer(reopened, io.StringIO())
        assert server._handle("initialize", {})["capabilities"]["proceduralExperiences"]
        assert (
            server._handle("experience/list", {})["experiences"][0]["state"] == "ACTIVE"
        )
        assert (
            server._handle(
                "experience/revoke",
                {"experienceId": lesson["experience_id"], "eventId": "host-revoke"},
            )["experience"]["state"]
            == "REVOKED"
        )
        assert (
            reopened.collaboration.experiences.select(
                consumer.agent_run_id, {"user_goal": "same task"}
            )
            == []
        )
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("abstain", "ABSTAIN"),
        ("quarantine", "QUARANTINED"),
        ("uncertain", "NEEDS_CONFIRMATION"),
    ],
)
def test_unsupported_or_uncertain_lessons_never_become_active(
    tmp_path, monkeypatch, mode, expected
):
    app, _provider, _, lesson = create_lesson(tmp_path, monkeypatch, mode)
    try:
        assert lesson.get("state", lesson.get("decision")) == expected
        if mode == "uncertain":
            reviewed = app.review_experience(
                lesson["experience_id"],
                "Use only when both source files are in the granted workspace",
            )
            assert reviewed["state"] == "ACTIVE"
            calls = len(_provider.names)
            assert (
                app.review_experience(
                    lesson["experience_id"],
                    "Use only when both source files are in the granted workspace",
                )
                == reviewed
            )
            assert len(_provider.names) == calls
        else:
            assert not any(row["state"] == "ACTIVE" for row in app.list_experiences())
    finally:
        app.close()


def test_interrupted_review_resumes_without_reproposing_lesson(tmp_path, monkeypatch):
    provider = ExperienceProvider()
    app = file_app(tmp_path, monkeypatch, provider)
    source = app.process_chat_turn("Read both files")
    original = app.collaboration.roles.run

    def interrupt(role, inputs):
        if role == "ExperienceReview":
            raise KeyboardInterrupt("simulated review interruption")
        return original(role, inputs)

    monkeypatch.setattr(app.collaboration.roles, "run", interrupt)
    with pytest.raises(KeyboardInterrupt):
        app.learn_experience(source.agent_run_id)
    assert app.list_experiences()[0]["state"] == "CANDIDATE"
    session = app.active_session_id
    app.close()
    reopened = file_app(tmp_path, monkeypatch, provider)
    try:
        assert reopened.resume_session(session)
        lesson = reopened.learn_experience(source.agent_run_id)
        assert lesson["state"] == "ACTIVE"
        assert provider.names.count("collaboration_experience_v1") == 1
    finally:
        reopened.close()


def test_forged_lesson_source_is_rejected(tmp_path, monkeypatch):
    provider = ExperienceProvider("forged")
    app = file_app(tmp_path, monkeypatch, provider)
    try:
        source = app.process_chat_turn("Read both files")
        with pytest.raises(CoordinationConflict, match="grounded"):
            app.learn_experience(source.agent_run_id)
        assert app.list_experiences() == []
    finally:
        app.close()


@pytest.mark.parametrize("change", ["revoke", "file", "tamper"])
def test_source_revocation_or_change_invalidates_derived_lesson(
    tmp_path, monkeypatch, change
):
    app, _provider, source, lesson = create_lesson(tmp_path, monkeypatch)
    try:
        if change == "revoke":
            graph = app.coordination_repository.list_task_graphs(source.task_id)[0]
            app.coordination_repository.invalidate_artifact(
                source.agent_run_id,
                graph["nodes"][0]["attempts"][0]["artifact_id"],
                "source-revoked",
            )
        elif change == "file":
            (tmp_path / "left.txt").write_text("alpha=7", encoding="utf-8")
        else:
            with sqlite3.connect(app.coordination_repository.db_path) as db:
                db.execute(
                    "UPDATE experiences SET payload_json='{}' WHERE experience_id=?",
                    (lesson["experience_id"],),
                )
            with pytest.raises(CoordinationConflict, match="integrity"):
                app.list_experiences()
            return
        assert app.list_experiences()[0]["state"] == "STALE"
        with pytest.raises(CoordinationConflict):
            app.collaboration.experiences.validate(lesson)
    finally:
        app.close()


def test_failed_feedback_quarantines_lesson_and_new_revision_supersedes_it(
    tmp_path, monkeypatch
):
    app, provider, _source, lesson = create_lesson(tmp_path, monkeypatch)
    try:
        provider.fail_consumer = True
        failed = app.process_chat_turn("Use the procedure to combine both files")
        assert failed.status is AgentResultStatus.FAILED
        updated = app.feedback_experience(
            lesson["experience_id"], failed.agent_run_id, "bad-reuse"
        )
        assert updated["state"] == "QUARANTINED"
        with pytest.raises(CoordinationConflict, match="successful"):
            app.learn_experience(failed.agent_run_id)
        provider.fail_consumer = False
        new_source = app.process_chat_turn("Read both files with explicit verification")
        revised = app.learn_experience(
            new_source.agent_run_id, revision_of=lesson["experience_id"]
        )
        assert revised["state"] == "ACTIVE"
        assert revised["payload"]["revision_of"] == lesson["experience_id"]
        assert (
            app.collaboration.experiences.get(lesson["experience_id"])["state"]
            == "SUPERSEDED"
        )
    finally:
        app.close()


def test_uncertain_applicability_does_not_enter_task_context(tmp_path, monkeypatch):
    app, provider, _, _lesson = create_lesson(tmp_path, monkeypatch)
    try:
        provider.mode = "inapplicable"
        result = app.process_chat_turn(
            "Another file task whose assumptions are uncertain"
        )
        assert result.status is AgentResultStatus.SUCCESS
        assert result.trace_metadata["experience_inputs"] == []
        assert provider.selected_seen[-1] == []
    finally:
        app.close()
