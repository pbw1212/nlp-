"""Execute a repair through the real coordinator and durable tool journal."""

import json
from dataclasses import asdict, replace

import pytest
from src.memory.agent.models import AgentResultStatus
from src.memory.collaboration.models import digest
from src.memory.collaboration.repository import CoordinationConflict
from test_collaboration import CollaborationProvider, make_app


class BranchProvider(CollaborationProvider):
    def generate_json(self, system_prompt, user_prompt, **kwargs):
        output = super().generate_json(system_prompt, user_prompt, **kwargs)
        schema = kwargs.get("schema_name")
        if schema == "collaboration_planner_v1":
            output["subtasks"].extend(
                [
                    {
                        "key": "dependent",
                        "objective": "Report result",
                        "dependencies": ["calc"],
                        "tool_names": [],
                        "completion_criteria": "verified",
                    },
                    {
                        "key": "independent",
                        "objective": "Independent summary",
                        "dependencies": [],
                        "tool_names": [],
                        "completion_criteria": "verified",
                    },
                ]
            )
        if schema == "agent_action" and self.executor_calls == 4:
            output = {
                "action_kind": "TOOL_CALL",
                "content": None,
                "tool_name": "calculator",
                "tool_arguments": json.dumps({"expression": "17*23"}),
            }
        if schema == "collaboration_verifier_v1":
            data = json.loads(user_prompt)
            if "receipts" in data and not data["receipts"]:
                output["evidence_ids"] = [
                    r["artifact_id"] for r in data["verified_dependencies"].values()
                ]
        return output


@pytest.mark.parametrize("mode", ["normal", "interrupt", "no_reuse", "budget"])
def test_repair_executes_affected_branch_and_reuses_safe_root(
    tmp_path, monkeypatch, mode
):
    provider = BranchProvider()
    app = make_app(tmp_path, monkeypatch, provider)
    captured = {}
    original = app.collaboration.run_prepared

    def capture(context, request, **kwargs):
        captured.update(context=context, request=request)
        return original(context, request, **kwargs)

    monkeypatch.setattr(app.collaboration, "run_prepared", capture)
    if mode == "no_reuse":
        monkeypatch.setattr(
            app.collaboration, "_reuse_context_hash", lambda *args: None
        )
    try:
        result = app.process_chat_turn("Compute 17*23")
        assert result.status is AgentResultStatus.SUCCESS
        source_context, source_request = captured["context"], captured["request"]
        repo = app.coordination_repository
        graph = repo.list_task_graphs(result.task_id)[0]
        artifact = next(n for n in graph["nodes"] if n["node_key"] == "calc")[
            "attempts"
        ][0]["artifact_id"]
        repo.invalidate_artifact(graph["run_id"], artifact, "repair-execution")
        plan = app.collaboration.plan_repair(graph["run_id"], "repair-execution")
        if mode == "budget":
            app.collaboration.client.limits = replace(
                app.collaboration.client.limits, max_model_calls=1
            )
        if mode == "interrupt":
            original_role = app.collaboration.roles.run

            def interrupted(role, inputs):
                if (
                    role == "Memory"
                    and inputs.get("subgoal", {}).get("key") == "dependent"
                ):
                    raise KeyboardInterrupt("simulated worker death")
                return original_role(role, inputs)

            monkeypatch.setattr(app.collaboration.roles, "run", interrupted)
            with pytest.raises(KeyboardInterrupt):
                app.collaboration.execute_repair(
                    plan["repair_id"], source_context, source_request
                )
            monkeypatch.setattr(app.collaboration.roles, "run", original_role)
        repaired = app.collaboration.execute_repair(
            plan["repair_id"], source_context, source_request
        )
        if mode == "budget":
            assert repaired.status is AgentResultStatus.FAILED
            assert repaired.trace_metadata["collaboration_usage"]["model_calls"] == 1
            calls_before = len(provider.names)
            repeated = app.collaboration.execute_repair(
                plan["repair_id"], source_context, source_request
            )
            assert repeated.status is AgentResultStatus.FAILED
            assert len(provider.names) == calls_before
            return
        assert repaired.status is AgentResultStatus.SUCCESS, repaired.error_info
        assert repaired.agent_run_id != result.agent_run_id
        assert repaired.trace_metadata["reused_subtasks"] == (
            [] if mode == "no_reuse" else ["independent"]
        )
        assert (
            app.collaboration.repair_status(plan["repair_id"])["status"] == "SUCCEEDED"
        )
        assert provider.executor_calls == (6 if mode == "no_reuse" else 5)
        calls_before = len(provider.names)
        replay = app.collaboration.execute_repair(
            plan["repair_id"], source_context, source_request
        )
        assert digest(asdict(replay)) == digest(asdict(repaired))
        assert len(provider.names) == calls_before
        assert len(repaired.executed_tool_call_ids) == 1
        child = next(
            g
            for g in repo.list_task_graphs(result.task_id)
            if g["run_id"] == repaired.agent_run_id
        )
        assert all(n["status"] == "VERIFIED" for n in child["nodes"])
        assert repo.get_run(result.agent_run_id)["result_json"]
        if mode == "normal":
            repo.invalidate_artifact(graph["run_id"], artifact, "second-event")
            with app.collaboration.client.turn(
                source_request.turn_request.session_id,
                plan["repair_id"],
                source_request.turn_request.user_input,
            ):
                replay_request = replace(
                    source_request,
                    turn_request=replace(
                        source_request.turn_request, turn_id=plan["repair_id"]
                    ),
                )
                invalid = app.collaboration.replay(replay_request)
            assert invalid.status is AgentResultStatus.FAILED
            assert invalid.final_response is None
            assert invalid.error_info.code == "COLLABORATION_STALE_REPLAY"
    finally:
        app.close()


def test_process_lock_excludes_second_worker_and_releases(tmp_path):
    from src.memory.collaboration.repair import repair_lock

    with repair_lock(  # noqa: SIM117
        str(tmp_path / "coord.db"), "known-id"
    ), pytest.raises(CoordinationConflict, match="already running"):
        with repair_lock(str(tmp_path / "coord.db"), "known-id"):
            pass
    with repair_lock(str(tmp_path / "coord.db"), "known-id"):
        pass


def test_process_lock_released_after_abrupt_process_exit(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    from src.memory.collaboration.repair import repair_lock

    code_root = str(Path(__file__).resolve().parents[1])
    script = "import os,sys; sys.path.insert(0,sys.argv[1]); from src.memory.collaboration.repair import repair_lock; lock=repair_lock(sys.argv[2], 'crashed'); lock.__enter__(); os._exit(0)"
    env = dict(os.environ)
    completed = subprocess.run(
        [sys.executable, "-c", script, code_root, str(tmp_path / "coord.db")],
        env=env,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0
    with repair_lock(str(tmp_path / "coord.db"), "crashed"):
        pass


def test_repair_restores_prepared_context_after_restart(tmp_path, monkeypatch):
    provider = BranchProvider()
    app = make_app(tmp_path, monkeypatch, provider)
    result = app.process_chat_turn("Compute 17*23")
    assert result.status is AgentResultStatus.SUCCESS
    repo = app.coordination_repository
    graph = repo.list_task_graphs(result.task_id)[0]
    artifact = next(n for n in graph["nodes"] if n["node_key"] == "calc")["attempts"][
        0
    ]["artifact_id"]
    app.close()
    reopened = make_app(tmp_path, monkeypatch, provider)
    try:
        with pytest.raises(ValueError, match="active session"):
            reopened.revoke_and_repair_collaboration(
                graph["run_id"], artifact, "restart-event"
            )
        assert reopened.resume_session(repo.get_run(graph["run_id"])["session_id"])
        repaired = reopened.revoke_and_repair_collaboration(
            graph["run_id"], artifact, "restart-event"
        )
        assert repaired.status is AgentResultStatus.SUCCESS, repaired.error_info
        assert repaired.trace_metadata["reused_subtasks"] == ["independent"]
        assert (
            reopened.revoke_and_repair_collaboration(
                graph["run_id"], artifact, "restart-event"
            ).agent_run_id
            == repaired.agent_run_id
        )
    finally:
        reopened.close()


def test_prepared_snapshot_integrity_rejects_mutation(tmp_path, monkeypatch):
    import sqlite3

    app = make_app(tmp_path, monkeypatch)
    try:
        result = app.process_chat_turn("Compute 17*23")
        graph = app.coordination_repository.list_task_graphs(result.task_id)[0]
        artifact = graph["nodes"][0]["attempts"][0]["artifact_id"]
        app.coordination_repository.invalidate_artifact(
            graph["run_id"], artifact, "snapshot-event"
        )
        with sqlite3.connect(app.coordination_repository.db_path) as db:
            db.execute(
                "UPDATE prepared_snapshots SET payload_json='{}' WHERE run_id=?",
                (graph["run_id"],),
            )
        with pytest.raises(CoordinationConflict, match="snapshot"):
            app.repair_collaboration(graph["run_id"], "snapshot-event")
    finally:
        app.close()


def test_pending_invalidation_is_repaired_on_session_resume(tmp_path, monkeypatch):
    import sqlite3

    provider = BranchProvider()
    app = make_app(tmp_path, monkeypatch, provider)
    result = app.process_chat_turn("Compute 17*23")
    repo = app.coordination_repository
    graph = repo.list_task_graphs(result.task_id)[0]
    session = app.active_session_id
    artifact = next(n for n in graph["nodes"] if n["node_key"] == "calc")["attempts"][
        0
    ]["artifact_id"]
    repo.invalidate_artifact(graph["run_id"], artifact, "auto-event")
    assert repo.list_repairs(session)[0]["status"] == "PENDING"
    with sqlite3.connect(repo.db_path) as db:
        db.execute(
            "UPDATE runs SET status='RUNNING' WHERE run_id=?", (graph["run_id"],)
        )
    assert repo.list_repairs(session, pending_only=True) == []
    with sqlite3.connect(repo.db_path) as db:
        db.execute(
            "UPDATE runs SET status='SUCCEEDED' WHERE run_id=?", (graph["run_id"],)
        )
    app.close()
    reopened = make_app(tmp_path, monkeypatch, provider)
    try:
        assert reopened.resume_session(session)
        row = reopened.coordination_repository.list_repairs(session)[0]
        assert row["status"] == "SUCCEEDED"
        plan = reopened.collaboration.plan_repair(graph["run_id"], "auto-event")
        messages = reopened.conversation_repo.list_messages(session)
        assert any(
            m.turn_id == plan["repair_id"] and m.role.value == "ASSISTANT"
            for m in messages
        )
        count = len(provider.names)
        assert reopened.resume_session(session)
        assert len(provider.names) == count
    finally:
        reopened.close()


def test_jsonl_revoke_and_repair_returns_result(tmp_path, monkeypatch):
    import io

    from scripts.personal_agent_app_server import JsonlAppServer

    app = make_app(tmp_path, monkeypatch, BranchProvider())
    try:
        result = app.process_chat_turn("Compute 17*23")
        graph = app.coordination_repository.list_task_graphs(result.task_id)[0]
        artifact = next(n for n in graph["nodes"] if n["node_key"] == "calc")[
            "attempts"
        ][0]["artifact_id"]
        server = JsonlAppServer(app, io.StringIO())
        assert (
            server._handle("initialize", {})["capabilities"]["collaborationRepair"]
            is True
        )
        assert (
            server._handle("repair/graphs", {})["graphs"][0]["run_id"]
            == graph["run_id"]
        )
        reply = server._handle(
            "repair/revoke",
            {
                "runId": graph["run_id"],
                "artifactId": artifact,
                "eventId": "jsonl-event",
            },
        )
        assert reply["result"]["status"] == "SUCCESS"
        assert server._handle("repair/list", {})["repairs"][0]["status"] == "SUCCEEDED"
    finally:
        app.close()


def test_auto_repair_records_source_failure_without_replaying_stale_answer(
    tmp_path, monkeypatch
):
    import sqlite3

    app = make_app(tmp_path, monkeypatch)
    result = app.process_chat_turn("Compute 17*23")
    repo = app.coordination_repository
    graph = repo.list_task_graphs(result.task_id)[0]
    session = app.active_session_id
    artifact = graph["nodes"][0]["attempts"][0]["artifact_id"]
    repo.invalidate_artifact(graph["run_id"], artifact, "bad-snapshot")
    with sqlite3.connect(repo.db_path) as db:
        db.execute(
            "UPDATE prepared_snapshots SET payload_json='{}' WHERE run_id=?",
            (graph["run_id"],),
        )
    app.close()
    reopened = make_app(tmp_path, monkeypatch)
    try:
        assert reopened.resume_session(session)
        row = reopened.coordination_repository.list_repairs(session)[0]
        assert row["status"] == "FAILED"
        assert row["error_type"] == "CoordinationConflict"
        graph = reopened.coordination_repository.list_task_graphs(result.task_id)[0]
        assert graph["nodes"][0]["status"] == "STALE"
        assert not any(
            m.turn_id.startswith("ma_") and m.role.value == "ASSISTANT"
            for m in reopened.conversation_repo.list_messages(session)
        )
    finally:
        reopened.close()


def test_interrupted_automatic_repair_resumes_without_duplicate_tool(
    tmp_path, monkeypatch
):
    provider = BranchProvider()
    app = make_app(tmp_path, monkeypatch, provider)
    result = app.process_chat_turn("Compute 17*23")
    repo = app.coordination_repository
    graph = repo.list_task_graphs(result.task_id)[0]
    session = app.active_session_id
    artifact = next(n for n in graph["nodes"] if n["node_key"] == "calc")["attempts"][
        0
    ]["artifact_id"]
    repo.invalidate_artifact(graph["run_id"], artifact, "crash-event")
    app.close()
    worker = make_app(tmp_path, monkeypatch, provider)
    original_role = worker.collaboration.roles.run

    def interrupted(role, inputs):
        if role == "Memory" and inputs.get("subgoal", {}).get("key") == "dependent":
            raise KeyboardInterrupt("simulated worker death")
        return original_role(role, inputs)

    monkeypatch.setattr(worker.collaboration.roles, "run", interrupted)
    with pytest.raises(KeyboardInterrupt):
        worker.resume_session(session)
    assert (
        worker.coordination_repository.list_repairs(session)[0]["status"] == "PENDING"
    )
    assert provider.executor_calls == 4
    worker.close()
    recovered = make_app(tmp_path, monkeypatch, provider)
    try:
        assert recovered.resume_session(session)
        assert (
            recovered.coordination_repository.list_repairs(session)[0]["status"]
            == "SUCCEEDED"
        )
        assert provider.executor_calls == 5
    finally:
        recovered.close()
