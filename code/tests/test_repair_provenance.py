"""Repair inputs must retain source identities and honest freshness boundaries."""

import json
import sqlite3
from dataclasses import replace

import pytest
from src.memory.agent.models import AgentResultStatus
from src.memory.collaboration.coordinator import SourceFrontierChanged
from src.memory.collaboration.models import canonical, digest
from src.memory.collaboration.repository import (
    CoordinationConflict,
    ObservationFrontierConflict,
)
from src.memory.conversation.models import ConversationRole
from src.memory.conversation.repository import ConversationRepository
from src.memory.core.schema import payload_hash
from test_collaboration import CollaborationProvider, make_app


def test_truncated_projection_retains_original_identity_and_hash(tmp_path):
    repo = ConversationRepository(str(tmp_path / "conversation.db"))
    repo.create_session("session")
    original = repo.append_message("session", "turn", ConversationRole.USER, "A" * 200)
    projected = repo.build_conversation_context(
        "session", max_chars=40
    ).recent_messages[0]
    assert projected.message_id == original.message_id
    assert projected.source_content_hash == payload_hash(original.content)
    assert projected.content != original.content


def test_projection_must_match_its_original_source(tmp_path, monkeypatch):
    from src.memory.agent.models import AgentRequest
    from src.memory.runtime.models import TurnRequest

    app = make_app(tmp_path, monkeypatch)
    try:
        app._ensure_persisted_session()
        session = app.active_session_id
        app.conversation_repo.append_message(
            session, "earlier", ConversationRole.USER, "A" * 200
        )
        context = app.conversation_repo.build_conversation_context(
            session, max_chars=40
        )
        app.conversation_repo.append_message(
            session, "current", ConversationRole.USER, "Compute 17*23"
        )
        request = AgentRequest(
            TurnRequest("current", "Compute 17*23", session_id=session),
            conversation_context=context,
        )
        frontier = app.collaboration._conversation_frontier(request)
        assert frontier["untracked"] is False
        assert frontier["messages"][-1]["projection_kind"] == "PREFIX_MARKED"
        app.collaboration._validate_conversation_frontier(frontier)
        forged_view = replace(context.recent_messages[0], content="forged content")
        forged_request = replace(
            request,
            conversation_context=replace(context, recent_messages=(forged_view,)),
        )
        forged = app.collaboration._conversation_frontier(forged_request)
        assert forged["untracked"] is True
        assert forged["messages"][-1]["projection_kind"] == "UNVERIFIED"
        frontier["messages"][-1]["projection_hash"] = "forged"
        with pytest.raises(SourceFrontierChanged, match="projection"):
            app.collaboration._validate_conversation_frontier(frontier)
    finally:
        app.close()


@pytest.mark.parametrize("change", ["delete_prior", "modify_prior", "delete_current"])
def test_changed_conversation_prevents_stale_replay(tmp_path, monkeypatch, change):
    app = make_app(tmp_path, monkeypatch)
    try:
        app._ensure_persisted_session()
        old = app.conversation_repo.append_message(
            app.active_session_id, "earlier", ConversationRole.USER, "earlier fact"
        )
        result = app._process_chat_turn_with_id("Compute 17*23", "turn", resume=False)
        assert result.status is AgentResultStatus.SUCCESS
        refs = result.trace_metadata["conversation_frontier"]["messages"]
        assert old.message_id in {row["message_id"] for row in refs}
        current = app.conversation_repo.get_message_by_turn(
            app.active_session_id, "turn", ConversationRole.USER
        )
        assert current.message_id in {row["message_id"] for row in refs}
        with sqlite3.connect(app.conversation_db_path) as db:
            if change == "modify_prior":
                db.execute(
                    "UPDATE conversation_messages SET content='changed' WHERE message_id=?",
                    (old.message_id,),
                )
            else:
                db.execute(
                    "DELETE FROM conversation_messages WHERE message_id=?",
                    (
                        (
                            current.message_id
                            if change == "delete_current"
                            else old.message_id
                        ),
                    ),
                )
        if change == "delete_current":
            from src.memory.agent.models import AgentRequest
            from src.memory.runtime.models import TurnRequest

            with pytest.raises(ValueError):
                app._process_chat_turn_with_id("Compute 17*23", "turn", resume=True)
            with app.llm_client.turn(app.active_session_id, "turn", "Compute 17*23"):
                replay = app.collaboration.replay(
                    AgentRequest(
                        TurnRequest(
                            "turn", "Compute 17*23", session_id=app.active_session_id
                        )
                    )
                )
        else:
            replay = app._process_chat_turn_with_id(
                "Compute 17*23", "turn", resume=True
            )
        assert (
            replay.status is AgentResultStatus.FAILED and replay.final_response is None
        )
        assert replay.error_info.code == "COLLABORATION_STALE_REPLAY"
        assert (
            app.coordination_repository.list_task_graphs(result.task_id)[0]["nodes"][0][
                "status"
            ]
            == "STALE"
        )
    finally:
        app.close()


def test_repair_candidate_checks_receipts_but_not_external_freshness(
    tmp_path, monkeypatch
):
    class ToolBranchProvider(CollaborationProvider):
        def generate_json(self, system_prompt, user_prompt, **kwargs):
            result = super().generate_json(system_prompt, user_prompt, **kwargs)
            schema = kwargs.get("schema_name")
            if schema == "collaboration_planner_v1":
                result["subtasks"].append(
                    {
                        "key": "independent",
                        "objective": "Independent calculation",
                        "dependencies": [],
                        "tool_names": ["calculator"],
                        "completion_criteria": "verified",
                    }
                )
            if schema == "agent_action" and self.executor_calls == 2:
                result = {
                    "action_kind": "TOOL_CALL",
                    "content": None,
                    "tool_name": "calculator",
                    "tool_arguments": json.dumps({"expression": "17*23"}),
                }
            return result

    provider = ToolBranchProvider()
    app = make_app(tmp_path, monkeypatch, provider)
    try:
        result = app.process_chat_turn("Compute 17*23")
        assert result.status is AgentResultStatus.SUCCESS
        graph = app.coordination_repository.list_task_graphs(result.task_id)[0]
        nodes = {row["node_key"]: row for row in graph["nodes"]}
        app.coordination_repository.invalidate_artifact(
            graph["run_id"], nodes["calc"]["attempts"][0]["artifact_id"], "revocation"
        )
        candidate = app.collaboration.prepare_repair(graph["run_id"], "revocation")
        independent = candidate["reuse_candidates"][0]
        assert (
            independent["tool_validation"]
            == "RECEIPTS_VERIFIED_EXTERNAL_FRESHNESS_UNKNOWN"
        ), (
            provider.names,
            provider.executor_calls,
            result.executed_tool_call_ids,
            independent,
        )
        assert independent["reuse_authorized"] is False
        call_id = independent["observations"][0]["tool_call_id"]
        with sqlite3.connect(app.coordination_repository.db_path) as db:
            db.execute(
                "UPDATE attempt_observations SET payload_json='{}' WHERE attempt_id=?",
                (independent["attempt_id"],),
            )
        with pytest.raises(ObservationFrontierConflict, match="observations changed"):
            app.coordination_repository.record_attempt_observations(
                graph["run_id"], independent["attempt_id"], independent["observations"]
            )
        with sqlite3.connect(app.coordination_repository.db_path) as db:
            db.execute(
                "UPDATE attempt_observations SET payload_json=? WHERE attempt_id=?",
                (canonical(independent["observations"]), independent["attempt_id"]),
            )
        with sqlite3.connect(app.coordination_repository.db_path) as db:
            db.execute(
                "UPDATE attempt_observations SET payload_json=?, payload_hash=? WHERE attempt_id=?",
                (canonical([]), digest([]), independent["attempt_id"]),
            )
        with pytest.raises(CoordinationConflict, match="observation ownership"):
            app.collaboration.prepare_repair(graph["run_id"], "revocation")
        with sqlite3.connect(app.coordination_repository.db_path) as db:
            db.execute(
                "UPDATE attempt_observations SET payload_json=?, payload_hash=? WHERE attempt_id=?",
                (
                    canonical(independent["observations"]),
                    digest(independent["observations"]),
                    independent["attempt_id"],
                ),
            )
        with sqlite3.connect(app.journal_db_path) as db:
            db.execute(
                "UPDATE tool_execution_receipts SET result_json='{}' WHERE tool_call_id=?",
                (call_id,),
            )
        with pytest.raises(SourceFrontierChanged, match="receipt changed"):
            app.collaboration.prepare_repair(graph["run_id"], "revocation")
        nodes = {
            row["node_key"]: row
            for row in app.coordination_repository.list_task_graphs(result.task_id)[0][
                "nodes"
            ]
        }
        assert nodes["independent"]["status"] == "STALE"
    finally:
        app.close()


@pytest.mark.parametrize(
    "change", ["modify_receipt", "delete_receipt", "delete_observation"]
)
def test_replay_rejects_changed_tool_provenance(tmp_path, monkeypatch, change):
    provider = CollaborationProvider()
    app = make_app(tmp_path, monkeypatch, provider)
    try:
        result = app._process_chat_turn_with_id(
            "Compute 17*23", "receipt-turn", resume=False
        )
        assert result.status is AgentResultStatus.SUCCESS
        calls_before = len(provider.names)
        graph = app.coordination_repository.list_task_graphs(result.task_id)[0]
        attempt = graph["nodes"][0]["attempts"][0]
        if change == "delete_observation":
            with sqlite3.connect(app.coordination_repository.db_path) as db:
                db.execute(
                    "DELETE FROM attempt_observations WHERE attempt_id=?",
                    (attempt["attempt_id"],),
                )
        else:
            with sqlite3.connect(app.journal_db_path) as db:
                if change == "modify_receipt":
                    db.execute(
                        "UPDATE tool_execution_receipts SET result_json='{}' WHERE tool_call_id=?",
                        (result.executed_tool_call_ids[0],),
                    )
                else:
                    db.execute(
                        "DELETE FROM tool_execution_receipts WHERE tool_call_id=?",
                        (result.executed_tool_call_ids[0],),
                    )
        replay = app._process_chat_turn_with_id(
            "Compute 17*23", "receipt-turn", resume=True
        )
        assert replay.status is AgentResultStatus.FAILED
        assert replay.final_response is None
        assert replay.error_info.code == "COLLABORATION_STALE_REPLAY"
        assert len(provider.names) == calls_before
        assert (
            app.coordination_repository.list_task_graphs(result.task_id)[0]["nodes"][0][
                "status"
            ]
            == "STALE"
        )
    finally:
        app.close()


@pytest.mark.parametrize("change", ["none", "tool", "model", "role"])
def test_repair_checks_partial_execution_contract(tmp_path, monkeypatch, change):
    from src.memory.agent.tooling import ToolEffect, ToolSpec
    from src.memory.collaboration.roles import INSTRUCTIONS

    class TwoBranchProvider(CollaborationProvider):
        def generate_json(self, system_prompt, user_prompt, **kwargs):
            output = super().generate_json(system_prompt, user_prompt, **kwargs)
            if kwargs.get("schema_name") == "collaboration_planner_v1":
                output["subtasks"].append(
                    {
                        "key": "independent",
                        "objective": "Independent summary",
                        "dependencies": [],
                        "tool_names": [],
                        "completion_criteria": "verified",
                    }
                )
            return output

    provider = TwoBranchProvider()
    app = make_app(tmp_path, monkeypatch, provider)
    try:
        result = app.process_chat_turn("Compute 17*23")
        assert result.status is AgentResultStatus.SUCCESS
        graph = app.coordination_repository.list_task_graphs(result.task_id)[0]
        app.coordination_repository.invalidate_artifact(
            graph["run_id"],
            graph["nodes"][0]["attempts"][0]["artifact_id"],
            "contract-event",
        )
        plan = app.collaboration.plan_repair(graph["run_id"], "contract-event")
        assert app.collaboration.get_validated_repair_plan(plan["repair_id"]) == plan
        if change == "tool":
            app.registry.register(
                ToolSpec("new_tool", "A new read-only tool", {}, ToolEffect.READ_ONLY),
                object(),
            )
        elif change == "model":
            provider.model_name = "changed-model"
        elif change == "role":
            monkeypatch.setitem(
                INSTRUCTIONS, "Verifier", "Changed verifier instruction"
            )
        candidate = app.collaboration.prepare_repair(graph["run_id"], "contract-event")
        expected = "PARTIAL_MATCH" if change == "none" else "CHANGED"
        assert candidate["execution_contract_validation"] == expected
        assert (
            candidate["reuse_candidates"][0]["execution_contract_validation"]
            == expected
        )
        assert candidate["reuse_candidates"][0]["reuse_authorized"] is False
        if change != "none":
            with pytest.raises(
                CoordinationConflict, match="execution contract changed"
            ):
                app.collaboration.get_validated_repair_plan(plan["repair_id"])
    finally:
        app.close()


@pytest.mark.parametrize("change", ["handoff", "new_event", "global_invalidation"])
def test_repair_plan_rejects_source_change_after_snapshot(
    tmp_path, monkeypatch, change
):
    app = make_app(tmp_path, monkeypatch)
    try:
        result = app.process_chat_turn("Compute 17*23")
        assert result.status is AgentResultStatus.SUCCESS
        repo = app.coordination_repository
        graph = repo.list_task_graphs(result.task_id)[0]
        artifact = graph["nodes"][0]["attempts"][0]["artifact_id"]
        repo.invalidate_artifact(graph["run_id"], artifact, "first-event")
        before = repo.repair_state_hash(graph["run_id"], "first-event")
        plan = app.collaboration.plan_repair(graph["run_id"], "first-event")
        if change == "handoff":
            with sqlite3.connect(repo.db_path) as db:
                db.execute(
                    "UPDATE handoffs SET payload_json='{}' WHERE run_id=? AND stage_key='plan'",
                    (graph["run_id"],),
                )
        elif change == "new_event":
            repo.invalidate_artifact(graph["run_id"], artifact, "second-event")
        else:
            repo.invalidate_run(graph["run_id"], "SOURCE_FRONTIER")
        with pytest.raises(
            CoordinationConflict, match="source graph changed|global invalidation"
        ):
            repo.get_repair_plan(plan["repair_id"])
        with pytest.raises(
            CoordinationConflict, match="source graph changed|global invalidation"
        ):
            repo.store_repair_plan(
                graph["run_id"], "first-event", before, {"anything": True}
            )
    finally:
        app.close()


def test_repair_plan_is_idempotent_across_concurrent_callers(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    app = make_app(tmp_path, monkeypatch)
    try:
        result = app.process_chat_turn("Compute 17*23")
        assert result.status is AgentResultStatus.SUCCESS
        repo = app.coordination_repository
        graph = repo.list_task_graphs(result.task_id)[0]
        artifact = graph["nodes"][0]["attempts"][0]["artifact_id"]
        repo.invalidate_artifact(graph["run_id"], artifact, "concurrent-event")
        with ThreadPoolExecutor(max_workers=2) as pool:
            plans = list(
                pool.map(
                    lambda _: app.collaboration.plan_repair(
                        graph["run_id"], "concurrent-event"
                    ),
                    range(2),
                )
            )
        assert plans[0] == plans[1]
        with sqlite3.connect(repo.db_path) as db:
            assert db.execute("SELECT COUNT(*) FROM repair_plans").fetchone()[0] == 1
            db.execute(
                "UPDATE repair_plans SET plan_json='{}' WHERE repair_id=?",
                (plans[0]["repair_id"],),
            )
        with pytest.raises(CoordinationConflict, match="repair plan integrity failure"):
            repo.get_repair_plan(plans[0]["repair_id"])
    finally:
        app.close()


@pytest.mark.parametrize("change", ["content", "invalid_role"])
def test_repair_preparation_rejects_changed_conversation_source(
    tmp_path, monkeypatch, change
):
    app = make_app(tmp_path, monkeypatch)
    try:
        app._ensure_persisted_session()
        old = app.conversation_repo.append_message(
            app.active_session_id, "earlier", ConversationRole.USER, "earlier fact"
        )
        result = app.process_chat_turn("Compute 17*23")
        assert result.status is AgentResultStatus.SUCCESS
        graph = app.coordination_repository.list_task_graphs(result.task_id)[0]
        artifact = graph["nodes"][0]["attempts"][0]["artifact_id"]
        app.coordination_repository.invalidate_artifact(
            graph["run_id"], artifact, "repair-event"
        )
        plan = app.collaboration.plan_repair(graph["run_id"], "repair-event")
        with sqlite3.connect(app.conversation_db_path) as db:
            if change == "content":
                db.execute(
                    "UPDATE conversation_messages SET content='changed' WHERE message_id=?",
                    (old.message_id,),
                )
            else:
                db.execute(
                    "UPDATE conversation_messages SET role='INVALID' WHERE message_id=?",
                    (old.message_id,),
                )
        with pytest.raises(SourceFrontierChanged, match="conversation source"):
            app.collaboration.get_validated_repair_plan(plan["repair_id"])
        assert (
            app.coordination_repository.list_task_graphs(result.task_id)[0]["nodes"][0][
                "status"
            ]
            == "STALE"
        )
    finally:
        app.close()
