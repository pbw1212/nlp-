"""Real files, durable multi-branch repair, repeated updates and scope changes."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from src.memory.agent.freshness import receipt_freshness
from src.memory.agent.models import AgentResultStatus
from src.memory.agent.tooling import ToolExecutionRequest
from test_collaboration import CollaborationProvider, make_app


class FileProvider(CollaborationProvider):
    def __init__(self):
        super().__init__()
        self.read_requests = []
        self.executor_keys = []
        self.before_verify = None

    def generate_json(self, system_prompt, user_prompt, **kwargs):
        schema = kwargs.get("schema_name")
        if schema == "collaboration_planner_v1":
            self.names.append(schema)
            return {
                "schema_version": 1,
                "subtasks": [
                    {
                        "key": key,
                        "objective": f"Read {key}.txt",
                        "dependencies": [],
                        "tool_names": ["read_file"],
                        "completion_criteria": "Native file content supports the answer",
                    }
                    for key in ("left", "right")
                ]
                + [
                    {
                        "key": "merge",
                        "objective": "Combine both current values",
                        "dependencies": ["left", "right"],
                        "tool_names": [],
                        "completion_criteria": "Include both values exactly",
                    }
                ],
            }
        if schema == "agent_action":
            self.names.append(schema)
            self.executor_calls += 1
            request = user_prompt.rsplit("### CURRENT USER REQUEST\n", 1)[1]
            data = json.loads(request.split("\n", 1)[1])
            key = data["subgoal"]["key"]
            self.executor_keys.append(key)
            if (
                key != "merge"
                and "### CURRENT RUN TOOL OBSERVATIONS" not in user_prompt
            ):
                self.read_requests.append(key)
                return {
                    "action_kind": "TOOL_CALL",
                    "content": None,
                    "tool_name": "read_file",
                    "tool_arguments": json.dumps({"filename": key + ".txt"}),
                }
            return {
                "action_kind": "FINAL_RESPONSE",
                "content": (
                    "File values obtained"
                    if key != "merge"
                    else " | ".join(
                        row["answer"] for row in data["verified_dependencies"].values()
                    )
                ),
                "tool_name": None,
                "tool_arguments": None,
            }
        if schema == "collaboration_verifier_v1":
            self.names.append(schema)
            data = json.loads(user_prompt)
            if self.before_verify:
                self.before_verify(data)
            if "receipts" in data:
                if data["receipts"]:
                    refs = [row["evidence_id"] for row in data["receipts"]]
                    answer = data["receipts"][0]["result"][
                        "result_content"
                    ].splitlines()[-1]
                else:
                    refs = [
                        row["artifact_id"]
                        for row in data["verified_dependencies"].values()
                    ]
                    answer = " | ".join(
                        row["answer"] for row in data["verified_dependencies"].values()
                    )
            else:
                refs = [
                    row["artifact_id"] for row in data["verified_subtasks"].values()
                ]
                answer = data["verified_subtasks"]["merge"]["answer"]
            return {
                "schema_version": 1,
                "verdict": "PASS",
                "evidence_ids": refs,
                "answer": answer,
                "reason": "Current file receipts and dependency artifacts checked",
            }
        return super().generate_json(system_prompt, user_prompt, **kwargs)


def file_app(tmp_path, monkeypatch, provider=None):
    for key, value in (("left", "alpha=10"), ("right", "beta=20")):
        path = tmp_path / (key + ".txt")
        if not path.exists():
            path.write_text(value, encoding="utf-8")
    return make_app(tmp_path, monkeypatch, provider or FileProvider())


def test_bound_file_task_restarts_repairs_only_affected_branch_and_handles_second_update(
    tmp_path, monkeypatch
):
    provider = FileProvider()
    app = file_app(tmp_path, monkeypatch, provider)
    first = app.process_chat_turn("Read left.txt and right.txt and combine both values")
    assert first.status is AgentResultStatus.SUCCESS, first.error_info
    assert first.final_response == "alpha=10 | beta=20"
    assert first.task_id != "__unbound__"
    session = app.active_session_id
    task = first.task_id
    provider.read_requests.clear()
    (tmp_path / "left.txt").write_text("alpha=99", encoding="utf-8")
    app.close()
    reopened = file_app(tmp_path, monkeypatch, provider)
    try:
        assert reopened.resume_session(session)
        queues = reopened.coordination_repository.list_repairs(session)
        assert len(queues) == 1 and queues[0]["status"] == "SUCCEEDED", queues
        graphs = reopened.coordination_repository.list_task_graphs(task)
        repaired = graphs[-1]
        payload = json.loads(
            reopened.coordination_repository.get_run(repaired["run_id"])["result_json"]
        )
        assert payload["final_response"] == "alpha=99 | beta=20"
        assert payload["trace_metadata"]["reused_subtasks"] == ["right"]
        assert provider.read_requests == ["left"]
        root_nodes = {row["node_key"]: row["status"] for row in graphs[0]["nodes"]}
        assert root_nodes == {"left": "STALE", "right": "VERIFIED", "merge": "STALE"}
        calls = len(provider.names)
        assert reopened.resume_session(session)
        assert len(provider.names) == calls
        provider.read_requests.clear()
        (tmp_path / "right.txt").write_text("beta=88", encoding="utf-8")
        assert reopened.resume_session(session)
        queues = reopened.coordination_repository.list_repairs(session)
        assert len(queues) == 2 and all(
            row["status"] == "SUCCEEDED" for row in queues
        ), queues
        latest = reopened.coordination_repository.list_task_graphs(task)[-1]
        final = json.loads(
            reopened.coordination_repository.get_run(latest["run_id"])["result_json"]
        )
        assert final["final_response"] == "alpha=99 | beta=88"
        assert final["trace_metadata"]["reused_subtasks"] == ["left"]
        assert provider.read_requests == ["right"]
    finally:
        reopened.close()


@pytest.mark.parametrize("change", ["content", "delete", "scope"])
def test_replay_rejects_changed_unavailable_or_ungranted_file(
    tmp_path, monkeypatch, change
):
    app = file_app(tmp_path, monkeypatch)
    try:
        result = app._process_chat_turn_with_id(
            "Read both files", "file-replay", resume=False
        )
        assert result.status is AgentResultStatus.SUCCESS
        if change == "content":
            (tmp_path / "left.txt").write_text("changed", encoding="utf-8")
        elif change == "delete":
            (tmp_path / "left.txt").unlink()
        else:
            app.workspace.remove_root(str(tmp_path))
        replay = app._process_chat_turn_with_id(
            "Read both files", "file-replay", resume=True
        )
        assert replay.status is AgentResultStatus.FAILED
        assert replay.final_response is None
    finally:
        app.close()


def test_source_change_during_verification_never_admits_old_result(
    tmp_path, monkeypatch
):
    provider = FileProvider()
    app = file_app(tmp_path, monkeypatch, provider)

    def change(data):
        if data.get("subgoal", {}).get("key") == "left":
            (tmp_path / "left.txt").write_text("new value", encoding="utf-8")

    provider.before_verify = change
    try:
        result = app.process_chat_turn("Read both files")
        assert result.status is AgentResultStatus.FAILED
        assert result.final_response is None
    finally:
        app.close()


def test_truncated_source_cannot_authorize_reuse_and_reads_are_bounded(
    tmp_path, monkeypatch
):
    app = file_app(tmp_path, monkeypatch)
    try:
        path = tmp_path / "large.txt"
        path.write_text("x" * 25000, encoding="utf-8")
        tool = app.registry.get_executor("read_file")
        req = ToolExecutionRequest(
            "test", "read_file", {"filename": "large.txt"}, "test", "test", "test"
        )
        result = tool.execute(req)
        assert result.source_observations[0]["truncated"]
        receipt = {
            "tool_name": "read_file",
            "status": "SUCCEEDED",
            "result_json": json.dumps(
                {
                    "source_observations": result.source_observations,
                    "result_content": result.result_content,
                }
            ),
        }
        assert receipt_freshness(app.registry, receipt) == "UNKNOWN"
        monkeypatch.setattr(tool, "MAX_BYTES", 4)
        assert tool.execute(req).status.value == "FAILED"
    finally:
        app.close()


def test_two_source_changes_are_coalesced_into_one_repair_run(tmp_path, monkeypatch):
    provider = FileProvider()
    app = file_app(tmp_path, monkeypatch, provider)
    try:
        result = app.process_chat_turn("Read both files")
        provider.read_requests.clear()
        (tmp_path / "left.txt").write_text("alpha=8", encoding="utf-8")
        (tmp_path / "right.txt").write_text("beta=9", encoding="utf-8")
        outcomes = app.process_pending_collaboration_repairs()
        assert len(outcomes) == 1 and outcomes[0]["status"] == "SUCCESS"
        assert provider.read_requests == ["left", "right"]
        queues = app.coordination_repository.list_repairs(app.active_session_id)
        assert len(queues) == 2 and all(row["status"] == "SUCCEEDED" for row in queues)
        calls = len(provider.names)
        for item in queues:
            repaired = app.repair_collaboration(result.agent_run_id, item["event_id"])
            assert repaired.final_response == "alpha=8 | beta=9"
        assert len(provider.names) == calls
        assert len(app.coordination_repository.list_task_graphs(result.task_id)) == 2
    finally:
        app.close()


def test_worker_context_does_not_receive_undeclared_task3_peer_state(
    tmp_path, monkeypatch
):
    provider = FileProvider()
    app = file_app(tmp_path, monkeypatch, provider)
    original = app.runtime.process_tool_result
    prompts = []
    generate = provider.generate_json

    def capture(system, user, **kwargs):
        if kwargs.get("schema_name") == "agent_action":
            prompts.append(user)
        return generate(system, user, **kwargs)

    monkeypatch.setattr(provider, "generate_json", capture)

    def polluted(result, task_id):
        outcome = original(result, task_id)
        if outcome.context:
            structured = replace(
                outcome.context.structured_task_context,
                progress=(SimpleNamespace(content="UNDECLARED_PEER_INPUT"),),
            )
            return replace(
                outcome,
                context=replace(
                    outcome.context,
                    structured_task_context=structured,
                    warnings=("UNDECLARED_PEER_INPUT",),
                ),
            )
        return outcome

    monkeypatch.setattr(app.runtime, "process_tool_result", polluted)
    try:
        result = app.process_chat_turn("Read both independent files")
        assert result.status is AgentResultStatus.SUCCESS, result.error_info
        assert prompts and all(
            "UNDECLARED_PEER_INPUT" not in prompt for prompt in prompts
        )
        assert len(result.executed_tool_call_ids) == 2
    finally:
        app.close()
