"""P1 integration and fault boundaries; semantic choices come from test Providers."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict
from types import SimpleNamespace

import pytest
from conftest import TestProviderClient
from src.memory.agent.models import AgentRequest, AgentResult, AgentResultStatus
from src.memory.app.console import PersonalAgentApp
from src.memory.collaboration.budget import (
    BudgetedLLMClient,
    CollaborationBudgetError,
    CollaborationLimits,
)
from src.memory.collaboration.models import digest, parse_plan
from src.memory.collaboration.repository import (
    CoordinationConflict,
    CoordinationRepository,
)
from src.memory.core.config import MemoryConfig
from src.memory.core.schema import RetentionDisposition, SemanticValidity
from src.memory.llm.errors import ProviderRequestError
from src.memory.runtime.models import TurnRequest


class CollaborationProvider(TestProviderClient):
    __test__ = False

    def __init__(self, fault: str = "") -> None:
        self.fault = fault
        self.names = []
        self.executor_calls = 0

    @property
    def last_usage(self):
        return {"input_tokens": 100, "output_tokens": 20}

    def generate_json(
        self,
        system_prompt,
        user_prompt,
        *,
        response_schema=None,
        schema_name="structured_output",
    ):
        self.names.append(schema_name)
        if schema_name == "collaboration_planner_v1":
            tool = "create_file" if self.fault == "unauthorized" else "calculator"
            return {
                "schema_version": 1,
                "subtasks": [
                    {
                        "key": "calc",
                        "objective": "Compute 17*23",
                        "dependencies": [],
                        "tool_names": [tool],
                        "completion_criteria": "Result is supported by calculator receipt",
                    }
                ],
            }
        if schema_name == "collaboration_memory_v1":
            if self.fault == "provider":
                raise ProviderRequestError(
                    "not-for-public-output",
                    provider="test",
                    operation="memory",
                    status_code=503,
                )
            return {
                "schema_version": 1,
                "selected_memory_ids": ["invented"] if self.fault == "memory" else [],
                "missing_information": [],
            }
        if schema_name == "collaboration_verifier_v1":
            data = json.loads(user_prompt)
            if "receipts" in data:
                refs = [
                    row["evidence_id"]
                    for row in data["receipts"]
                    if row["status"] == "SUCCEEDED"
                ]
            else:
                refs = [
                    row["artifact_id"] for row in data["verified_subtasks"].values()
                ]
            if self.fault == "forged":
                refs = ["forged-receipt"]
            verdict = "BLOCKED" if self.fault == "verify" else "PASS"
            if (
                self.fault == "overall"
                and data.get("verification_scope") == "whole_user_goal"
            ):
                verdict = "BLOCKED"
            return {
                "schema_version": 1,
                "verdict": verdict,
                "evidence_ids": refs,
                "answer": "391",
                "reason": "Native calculator observation checked",
            }
        if schema_name == "agent_action":
            self.executor_calls += 1
            if self.executor_calls == 1:
                return {
                    "action_kind": "TOOL_CALL",
                    "content": None,
                    "tool_name": "calculator",
                    "tool_arguments": json.dumps({"expression": "17*23"}),
                }
            return {
                "action_kind": "FINAL_RESPONSE",
                "content": "391",
                "tool_name": None,
                "tool_arguments": None,
            }
        return super().generate_json(
            system_prompt,
            user_prompt,
            response_schema=response_schema,
            schema_name=schema_name,
        )


def make_app(tmp_path, monkeypatch, provider=None, limits=None):
    monkeypatch.chdir(tmp_path)
    return PersonalAgentApp(
        data_dir=str(tmp_path / "state"),
        config=MemoryConfig(),
        llm_client=provider or CollaborationProvider(),
        execution_mode="multi_fixed",
        collaboration_limits=limits,
    )


def test_four_roles_execute_real_calculator_and_replay_without_duplicate_ingestion(
    tmp_path, monkeypatch
):
    provider = CollaborationProvider()
    app = make_app(tmp_path, monkeypatch, provider)
    try:
        result = app._process_chat_turn_with_id(
            "Compute 17*23", "turn-one", resume=False
        )
        assert result.status is AgentResultStatus.SUCCESS
        assert result.final_response == "391"
        assert len(result.executed_tool_call_ids) == 1
        receipt = app.journal.get_receipt(result.executed_tool_call_ids[0])
        assert receipt["status"] == "SUCCEEDED"
        assert "391" in receipt["result_json"]
        assert {
            "collaboration_planner_v1",
            "collaboration_memory_v1",
            "agent_action",
            "collaboration_verifier_v1",
        } <= set(provider.names)
        roles = {
            row["role"] for row in result.trace_metadata["collaboration_usage"]["calls"]
        }
        assert {"Runtime", "Planner", "Memory", "Executor", "Verifier"} <= roles
        assert result.trace_metadata["collaboration_usage"][
            "charged_token_units"
        ] == 120 * len(provider.names)
        calls = len(provider.names)
        messages = len(app.conversation_repo.list_messages(app.active_session_id))
        replay = app._process_chat_turn_with_id(
            "Compute 17*23", "turn-one", resume=True
        )
        assert replay.status is AgentResultStatus.SUCCESS
        assert replay.executed_tool_call_ids == result.executed_tool_call_ids
        assert len(provider.names) == calls
        assert (
            len(app.conversation_repo.list_messages(app.active_session_id)) == messages
        )
        with sqlite3.connect(app.coordination_repository.db_path) as db:
            assert (
                db.execute("SELECT verification_status FROM artifacts").fetchone()[0]
                == "VERIFIED"
            )
            assert db.execute("SELECT COUNT(*) FROM handoffs").fetchone()[0] == 5
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        app.close()


@pytest.mark.parametrize(
    "fault", ["unauthorized", "memory", "forged", "verify", "overall", "provider"]
)
def test_invalid_handoff_or_provider_failure_never_reports_success(
    tmp_path, monkeypatch, fault
):
    provider = CollaborationProvider(fault)
    app = make_app(tmp_path, monkeypatch, provider)
    try:
        result = app.process_chat_turn("Compute 17*23")
        assert result.status is AgentResultStatus.FAILED
        assert result.error_info is not None
        assert "not-for-public-output" not in json.dumps(asdict(result))
        assert not (tmp_path / "test.txt").exists()
        if fault in {"unauthorized", "memory", "provider"}:
            assert provider.executor_calls == 0
        if fault == "verify":
            with sqlite3.connect(app.coordination_repository.db_path) as db:
                assert (
                    db.execute("SELECT verification_status FROM artifacts").fetchone()[
                        0
                    ]
                    == "PRODUCED"
                )
    finally:
        app.close()


def test_budget_covers_runtime_and_roles_not_separate_per_role_caps(
    tmp_path, monkeypatch
):
    provider = CollaborationProvider()
    app = make_app(
        tmp_path, monkeypatch, provider, CollaborationLimits(max_model_calls=3)
    )
    try:
        result = app.process_chat_turn("Compute 17*23")
        assert result.status is AgentResultStatus.FAILED
        assert len(provider.names) == 3
        assert provider.executor_calls == 0
        assert result.trace_metadata["collaboration_usage"]["model_calls"] == 3
    finally:
        app.close()


def test_reservations_and_handoffs_survive_restart_and_reject_changed_identity(
    tmp_path,
):
    path = str(tmp_path / "coord.db")
    repo = CoordinationRepository(path)
    limits = asdict(CollaborationLimits(max_model_calls=1))
    run = repo.begin("session", "turn", "input", limits)
    repo.bind_task(run, "task")
    repo.accept(
        run, "plan", "Planner", "Coordinator", {"goal": "input"}, {"value": 1}, 0
    )
    repo.reserve_call(run, "Memory", "operation", 100)
    restarted = CoordinationRepository(path)
    assert restarted.get_stage(run, "plan", "Planner", {"goal": "input"}) == {
        "value": 1
    }
    with pytest.raises(CoordinationConflict):
        restarted.reserve_call(run, "Executor", "operation", 100)
    with pytest.raises(CoordinationConflict):
        restarted.begin("session", "turn", "changed", limits)
    with pytest.raises(CoordinationConflict):
        restarted.bind_task(run, "other-task")
    with pytest.raises(CoordinationConflict):
        restarted.get_stage(run, "plan", "Memory", {"goal": "input"})
    with pytest.raises(CoordinationConflict):
        restarted.accept(run, "other", "Memory", "Coordinator", {}, {}, 0)


def test_corrupted_handoff_fails_closed(tmp_path):
    repo = CoordinationRepository(str(tmp_path / "coord.db"))
    run = repo.begin("session", "turn", "input", asdict(CollaborationLimits()))
    repo.accept(run, "plan", "Planner", "Coordinator", {}, {"value": 1}, 0)
    with sqlite3.connect(repo.db_path) as db:
        db.execute("UPDATE handoffs SET payload_json=?", ('{"value":2}',))
    with pytest.raises(CoordinationConflict, match="integrity"):
        repo.get_stage(run, "plan", "Planner", {})


def test_missing_usage_keeps_charge_and_provider_retry_cannot_hide_calls(tmp_path):
    repo = CoordinationRepository(str(tmp_path / "coord.db"))
    provider = TestProviderClient()
    client = BudgetedLLMClient(
        provider, repo, CollaborationLimits(max_token_units=5000)
    )
    with client.turn("session", "turn", "input") as run:
        client.generate_json("s", "u")
        assert repo.usage(run)["charged_token_units"] > 4096
        with pytest.raises(CollaborationBudgetError):
            client.generate_json("s", "u")
    provider.max_retries = 3
    with pytest.raises(ValueError, match="max_retries"):
        BudgetedLLMClient(provider, repo, CollaborationLimits())


@pytest.mark.parametrize(
    "subtasks",
    [
        [{"key": "a", "dependencies": ["b"]}, {"key": "b", "dependencies": ["a"]}],
        [{"key": "a", "dependencies": ["missing"]}],
        [{"key": "a", "dependencies": []}, {"key": "a", "dependencies": []}],
    ],
)
def test_task_graph_rejects_cycles_missing_dependencies_and_duplicate_ids(subtasks):
    rows = [
        {
            **row,
            "objective": "Task",
            "tool_names": [],
            "completion_criteria": "Verified result",
        }
        for row in subtasks
    ]
    with pytest.raises(ValueError):
        parse_plan({"schema_version": 1, "subtasks": rows}, set())


def test_withdrawn_or_revised_memory_cannot_be_reused(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    evidence = SimpleNamespace(
        memory_id="known", revision_id="old", text="value", provenance_refs=()
    )
    context = SimpleNamespace(evidence_bundle=SimpleNamespace(selected=(evidence,)))
    app.collaboration.memory_repository = SimpleNamespace(get_current=lambda _: None)
    try:
        with pytest.raises(CoordinationConflict, match="stale"):
            app.collaboration._current_memories(context)
    finally:
        app.close()


def test_completed_replay_cannot_revive_withdrawn_memory(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    current = SimpleNamespace(
        revision=SimpleNamespace(
            revision_id="revision-1",
            content="private-fact",
            semantic_validity=SemanticValidity.CURRENT,
        ),
        retention_disposition=RetentionDisposition.ACTIVE,
    )
    app.collaboration.memory_repository = SimpleNamespace(get_current=lambda _: current)
    try:
        with app.llm_client.turn("session", "source-turn", "request") as run:
            saved = AgentResult(
                "source-turn",
                "task",
                run,
                AgentResultStatus.SUCCESS,
                final_response="private-fact",
                trace_metadata={
                    "memory_inputs": [
                        {
                            "memory_id": "known",
                            "revision_id": "revision-1",
                            "content_hash": digest("private-fact"),
                        }
                    ]
                },
            )
            app.coordination_repository.finish(run, "SUCCEEDED", asdict(saved))
            request = AgentRequest(
                TurnRequest("source-turn", "request", session_id="session")
            )
            assert app.collaboration.replay(request).status is AgentResultStatus.SUCCESS
            app.collaboration.memory_repository = SimpleNamespace(
                get_current=lambda _: None
            )
            blocked = app.collaboration.replay(request)
            assert blocked.status is AgentResultStatus.FAILED
            assert blocked.final_response is None
            assert blocked.error_info.code == "COLLABORATION_STALE_REPLAY"
            assert "private-fact" not in json.dumps(asdict(blocked))
    finally:
        app.close()


def test_multi_subtask_handoff_accepts_only_verified_dependency_evidence(
    tmp_path, monkeypatch
):
    class DependencyProvider(CollaborationProvider):
        def generate_json(self, system_prompt, user_prompt, **kwargs):
            schema = kwargs.get("schema_name")
            result = super().generate_json(system_prompt, user_prompt, **kwargs)
            if schema == "collaboration_planner_v1":
                result["subtasks"].append(
                    {
                        "key": "report",
                        "objective": "Report the verified calculation",
                        "dependencies": ["calc"],
                        "tool_names": [],
                        "completion_criteria": "Use the predecessor's verified answer",
                    }
                )
            if schema == "collaboration_verifier_v1":
                data = json.loads(user_prompt)
                if "receipts" in data and not data["receipts"]:
                    result["evidence_ids"] = [
                        value["artifact_id"]
                        for value in data["verified_dependencies"].values()
                    ]
            return result

    app = make_app(tmp_path, monkeypatch, DependencyProvider())
    try:
        result = app.process_chat_turn(
            "Compute 17*23 and report the verified calculation"
        )
        assert result.status is AgentResultStatus.SUCCESS
        assert result.final_response == "391"
        assert len(result.executed_tool_call_ids) == 1
        with sqlite3.connect(app.coordination_repository.db_path) as db:
            assert (
                db.execute(
                    "SELECT COUNT(*) FROM artifacts WHERE verification_status='VERIFIED'"
                ).fetchone()[0]
                == 2
            )
    finally:
        app.close()


def test_late_provider_response_is_charged_but_not_accepted(tmp_path):
    class SlowProvider(CollaborationProvider):
        def generate_json(self, *args, **kwargs):
            time.sleep(0.03)
            return {"response": "too late"}

    repo = CoordinationRepository(str(tmp_path / "coord.db"))
    client = BudgetedLLMClient(
        SlowProvider(), repo, CollaborationLimits(deadline_seconds=0.02)
    )
    with client.turn("session", "turn", "input") as run:
        with pytest.raises(CollaborationBudgetError):
            client.generate_json("s", "u")
        usage = repo.usage(run)
        assert usage["model_calls"] == 1
        assert usage["charged_token_units"] == 120
        assert usage["calls"][0]["status"] == "FAILED"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_model_calls": 1.5},
        {"max_token_units": True},
        {"deadline_seconds": float("nan")},
    ],
)
def test_invalid_budget_types_do_not_disable_limits(kwargs):
    with pytest.raises(ValueError):
        CollaborationLimits(**kwargs)


def test_explicit_memory_route_still_commits_without_delegating(tmp_path, monkeypatch):
    from test_personal_agent_app import ProviderRoutedMemoryClient

    app = make_app(tmp_path, monkeypatch, ProviderRoutedMemoryClient())

    def forbidden(*args, **kwargs):
        raise AssertionError("explicit memory requests must not become delegated tasks")

    app.collaboration.run_prepared = forbidden
    try:
        result = app.process_chat_turn("请记住：我solotrip喜欢住在市中心")
        assert result.status is AgentResultStatus.SUCCESS
        assert result.trace_metadata["explicit_memory_write"] is True
        snapshots = app.long_term_memory_mgr.sqlite_mgr.list_current()
        assert any("市中心" in snapshot.revision.content for snapshot in snapshots)
        entries = app.runtime.task_service.repository.list_active_entries(
            result.task_id
        )
        assert all("solotrip" not in entry.content for entry in entries)
        assert app.long_term_memory_mgr.sqlite_mgr.list_pending_events() == []
    finally:
        app.close()
