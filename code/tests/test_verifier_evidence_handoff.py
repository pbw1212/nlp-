"""A downstream verifier needs native evidence, not just peer answers and IDs."""

import json

import pytest
from src.memory.agent.models import AgentResultStatus
from src.memory.collaboration.models import canonical, digest
from src.memory.collaboration.repository import CoordinationConflict
from test_file_source_repair import FileProvider, file_app


class NativeEvidenceProvider(FileProvider):
    def __init__(self, gate="dependent"):
        super().__init__()
        self.gate = gate
        self.verifier_inputs = []

    def generate_json(self, system_prompt, user_prompt, **kwargs):
        result = super().generate_json(system_prompt, user_prompt, **kwargs)
        if kwargs.get("schema_name") == "collaboration_verifier_v1":
            data = json.loads(user_prompt)
            self.verifier_inputs.append(data)
            overall = data.get("verification_scope") == "whole_user_goal"
            dependent = bool(data.get("verified_dependencies"))
            if (overall and self.gate == "overall") or (
                dependent and self.gate == "dependent"
            ):
                receipts = data.get("supporting_evidence", {}).get("receipts", [])
                if not receipts:
                    result.update(
                        verdict="BLOCKED",
                        evidence_ids=[],
                        answer="Native file evidence was not handed to this verifier",
                    )
                else:
                    result["evidence_ids"] = [row["evidence_id"] for row in receipts]
        return result


def test_native_evidence_survives_two_dependency_hops_without_duplication(
    tmp_path, monkeypatch
):
    class ChainProvider(NativeEvidenceProvider):
        def generate_json(self, system_prompt, user_prompt, **kwargs):
            schema = kwargs.get("schema_name")
            if schema == "agent_action":
                request = user_prompt.rsplit("### CURRENT USER REQUEST\n", 1)[1]
                data = json.loads(request.split("\n", 1)[1])
                if not data["subgoal"]["tool_names"]:
                    self.names.append(schema)
                    return {
                        "action_kind": "FINAL_RESPONSE",
                        "content": "alpha=10",
                        "tool_name": None,
                        "tool_arguments": None,
                    }
            result = super().generate_json(system_prompt, user_prompt, **kwargs)
            if schema == "collaboration_planner_v1":
                result["subtasks"][1].update(
                    key="middle", dependencies=["left"], tool_names=[]
                )
                result["subtasks"][2]["dependencies"] = ["middle"]
            return result

    provider = ChainProvider()
    app = file_app(tmp_path, monkeypatch, provider)
    try:
        result = app.process_chat_turn(
            "Read left.txt and pass the exact value through two summaries"
        )
        assert result.status is AgentResultStatus.SUCCESS, result.error_info
        assert provider.read_requests == ["left"]
        assert len(result.executed_tool_call_ids) == 1
        for data in provider.verifier_inputs:
            support = data["supporting_evidence"]
            assert len(support["receipts"]) == 1
            assert (
                support["receipts"][0]["evidence_id"]
                == result.executed_tool_call_ids[0]
            )
            assert "alpha=10" in support["receipts"][0]["result"]["result_content"]
    finally:
        app.close()


def test_fabricated_file_answer_still_blocks_when_no_native_read_happened(
    tmp_path, monkeypatch
):
    class NoReadProvider(NativeEvidenceProvider):
        def generate_json(self, system_prompt, user_prompt, **kwargs):
            if kwargs.get("schema_name") == "agent_action":
                self.names.append("agent_action")
                return {
                    "action_kind": "FINAL_RESPONSE",
                    "content": "made-up file contents",
                    "tool_name": None,
                    "tool_arguments": None,
                }
            result = super().generate_json(system_prompt, user_prompt, **kwargs)
            if kwargs.get("schema_name") == "collaboration_verifier_v1":
                data = json.loads(user_prompt)
                if not data["supporting_evidence"]["receipts"]:
                    result.update(
                        verdict="BLOCKED",
                        evidence_ids=[],
                        answer="No native read_file evidence",
                    )
            return result

    app = file_app(tmp_path, monkeypatch, NoReadProvider())
    try:
        result = app.process_chat_turn("Read both files and combine the values")
        assert result.status is AgentResultStatus.FAILED
        assert result.error_info.code == "COLLABORATION_VERIFICATION_BLOCKED"
        assert not result.executed_tool_call_ids
    finally:
        app.close()


@pytest.mark.parametrize("gate", ["dependent", "overall"])
def test_native_file_evidence_reaches_downstream_verifiers(tmp_path, monkeypatch, gate):
    provider = NativeEvidenceProvider(gate)
    app = file_app(tmp_path, monkeypatch, provider)
    try:
        result = app.process_chat_turn(
            "Read both files and combine their literal values"
        )
        assert result.status is AgentResultStatus.SUCCESS, result.error_info
        assert result.final_response == "alpha=10 | beta=20"
        target = next(
            data
            for data in provider.verifier_inputs
            if (
                data.get("verification_scope") == "whole_user_goal"
                if gate == "overall"
                else data.get("subgoal", {}).get("key") == "merge"
            )
        )
        receipts = target["supporting_evidence"]["receipts"]
        assert {row["evidence_id"] for row in receipts} == set(
            result.executed_tool_call_ids
        )
        assert len(receipts) == 2
        for row in receipts:
            native = app.journal.get_receipt(row["evidence_id"])
            assert row["result"] == json.loads(native["result_json"])
            assert row["arguments"] == json.loads(native["arguments_json"])
            assert row["status"] == "SUCCEEDED"
    finally:
        app.close()


def test_repair_restores_reused_evidence_and_omits_superseded_receipt(
    tmp_path, monkeypatch
):
    provider = NativeEvidenceProvider()
    app = file_app(tmp_path, monkeypatch, provider)
    first = app.process_chat_turn("Read both files and combine their literal values")
    assert first.status is AgentResultStatus.SUCCESS
    session = app.active_session_id
    old_left = first.executed_tool_call_ids[0]
    old_right = first.executed_tool_call_ids[1]
    (tmp_path / "left.txt").write_text("alpha=99", encoding="utf-8")
    app.close()
    reopened = file_app(tmp_path, monkeypatch, provider)
    try:
        assert reopened.resume_session(session)
        queue = reopened.coordination_repository.list_repairs(session)
        assert queue and queue[0]["status"] == "SUCCEEDED", queue
        merge = [
            data
            for data in provider.verifier_inputs
            if data.get("subgoal", {}).get("key") == "merge"
        ][-1]
        receipts = merge["supporting_evidence"]["receipts"]
        assert len(receipts) == 2
        refs = {row["evidence_id"] for row in receipts}
        assert old_right in refs and old_left not in refs
        assert any("alpha=99" in row["result"]["result_content"] for row in receipts)
        assert provider.read_requests == ["left", "right", "left"]
    finally:
        reopened.close()


def test_declared_dependency_does_not_receive_an_unrelated_branch_receipt(
    tmp_path, monkeypatch
):
    class ScopedProvider(NativeEvidenceProvider):
        def generate_json(self, system_prompt, user_prompt, **kwargs):
            result = super().generate_json(system_prompt, user_prompt, **kwargs)
            if kwargs.get("schema_name") == "collaboration_planner_v1":
                result["subtasks"][-1]["dependencies"] = ["left"]
            return result

    provider = ScopedProvider()
    app = file_app(tmp_path, monkeypatch, provider)
    try:
        result = app.process_chat_turn(
            "Read both files; summarize the left file independently"
        )
        assert result.status is AgentResultStatus.SUCCESS, result.error_info
        merge = next(
            row
            for row in provider.verifier_inputs
            if row.get("subgoal", {}).get("key") == "merge"
        )
        assert set(merge["verified_dependencies"]) == {"left"}
        assert [
            row["evidence_id"] for row in merge["supporting_evidence"]["receipts"]
        ] == [result.executed_tool_call_ids[0]]
        assert "beta=20" not in json.dumps(merge["supporting_evidence"])
    finally:
        app.close()


@pytest.mark.parametrize("tamper", ["payload", "rehashed", "owner", "revoked"])
def test_reused_support_is_bound_to_an_intact_verified_artifact(
    tmp_path, monkeypatch, tamper
):
    app = file_app(tmp_path, monkeypatch, NativeEvidenceProvider())
    try:
        result = app.process_chat_turn("Read both files and combine them")
        assert result.status is AgentResultStatus.SUCCESS
        repository = app.coordination_repository
        graph = repository.list_task_graphs(result.task_id)[0]
        artifact = next(node for node in graph["nodes"] if node["node_key"] == "right")[
            "attempts"
        ][0]["artifact_id"]
        assert repository.get_verified_support(artifact)["receipts"]
        if tamper == "revoked":
            repository.invalidate_artifact(
                result.agent_run_id, artifact, "revoke-support"
            )
        else:
            with repository._write() as db:
                row = db.execute(
                    "SELECT payload_json FROM handoffs WHERE run_id=? AND stage_key='support:right'",
                    (result.agent_run_id,),
                ).fetchone()
                payload = json.loads(row[0])
                if tamper == "owner":
                    payload["artifact_id"] = "different-artifact"
                else:
                    payload["supporting_evidence"]["receipts"][0]["result"][
                        "result_content"
                    ] = "forged evidence"
                db.execute(
                    "UPDATE handoffs SET payload_json=?,payload_hash=CASE WHEN ?='payload' THEN payload_hash ELSE ? END WHERE run_id=? AND stage_key='support:right'",
                    (canonical(payload), tamper, digest(payload), result.agent_run_id),
                )
        with pytest.raises(CoordinationConflict):
            repository.get_verified_support(artifact)
    finally:
        app.close()


def test_overall_verifier_receives_original_memory_window_and_revision(
    tmp_path, monkeypatch
):
    from test_collaboration_evidence import seeded_app

    app, provider, message = seeded_app(tmp_path, monkeypatch)
    original = provider.generate_json
    captured = []

    def strict(system_prompt, user_prompt, **kwargs):
        result = original(system_prompt, user_prompt, **kwargs)
        if kwargs.get("schema_name") == "collaboration_verifier_v1":
            data = json.loads(user_prompt)
            if data.get("verification_scope") == "whole_user_goal":
                captured.append(data)
                packets = data.get("supporting_evidence", {}).get("source_packets", [])
                if not packets or message.content not in [
                    row["text"] for row in packets
                ]:
                    result.update(
                        verdict="BLOCKED",
                        evidence_ids=[],
                        answer="Original window missing",
                    )
        return result

    monkeypatch.setattr(provider, "generate_json", strict)
    try:
        result = app.process_chat_turn("酒店预算偏好：请核实酒店预算上限")
        assert result.status is AgentResultStatus.SUCCESS, result.error_info
        support = captured[0]["supporting_evidence"]
        assert support["source_packets"][0]["text"] == message.content
        assert support["memories"][0]["revision_id"]
    finally:
        app.close()
