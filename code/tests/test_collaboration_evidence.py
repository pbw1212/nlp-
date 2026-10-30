"""Source-backed evidence reaches execution and verification, with truthful gaps."""

import json
import sqlite3

import pytest
from src.memory.agent.models import AgentResultStatus
from src.memory.llm.errors import ProviderRequestError
from test_collaboration import CollaborationProvider, make_app
from test_source_resolver import seed_memory


class SourceProvider(CollaborationProvider):
    def __init__(self, evidence_fault=""):
        super().__init__()
        self.evidence_fault = evidence_fault
        self.executor_inputs = []
        self.source_inputs = []
        self.memory_inputs = []
        self.before_executor = None

    def generate_json(self, system_prompt, user_prompt, **kwargs):
        schema = kwargs.get("schema_name")
        if schema == "collaboration_memory_v2":
            self.names.append(schema)
            data = json.loads(user_prompt)
            self.memory_inputs.append(data)
            assert data["memories"] and data["source_catalog"]
            source_ref = data["source_catalog"][0]["source_ref"]
            if self.evidence_fault == "source":
                source_ref = "source:invented"
            needs = [
                {
                    "need_id": "budget",
                    "requirement": "酒店每晚预算的明确上限",
                    "requires_original": True,
                }
            ]
            if self.evidence_fault == "summary_sufficient":
                needs[0]["requires_original"] = False
            response = {
                "schema_version": 2,
                "selected_memory_ids": [data["memories"][0]["memory_id"]],
                "missing_information": [],
                "evidence_needs": needs,
                "source_requests": (
                    []
                    if self.evidence_fault == "summary_sufficient"
                    else [
                        {
                            "source_ref": source_ref,
                            "need_ids": [
                                (
                                    "invented"
                                    if self.evidence_fault == "need"
                                    else "budget"
                                )
                            ],
                        }
                    ]
                ),
            }
            if self.evidence_fault == "duplicate":
                response["source_requests"].append(dict(response["source_requests"][0]))
            return response
        if schema == "collaboration_evidencecheck_v1":
            self.names.append(schema)
            data = json.loads(user_prompt)
            self.source_inputs.append(data)
            if self.evidence_fault == "provider":
                raise ProviderRequestError(
                    "private response", provider="test", operation="evidence"
                )
            packet = data["source_packets"][0] if data["source_packets"] else None
            supported = (
                self.evidence_fault == "summary_sufficient"
                or packet["status"] == "RESOLVED"
            )
            assessment = {
                "need_id": "budget",
                "status": "SUPPORTED" if supported else "MISSING",
                "evidence_ids": (
                    (
                        [data["memories"][0]["memory_id"]]
                        if packet is None
                        else [packet["source"]["source_ref"]]
                    )
                    if supported
                    else []
                ),
                "reason": "原始用户消息包含预算上限" if supported else "缺少可核验原文",
            }
            if self.evidence_fault == "forged":
                assessment["evidence_ids"] = ["invented-support"]
            if self.evidence_fault == "summary_only":
                assessment["evidence_ids"] = [data["memories"][0]["memory_id"]]
            if self.evidence_fault == "uncertain":
                assessment.update(status="UNCERTAIN", evidence_ids=[])
            return {
                "schema_version": 1,
                "assessments": [] if self.evidence_fault == "omitted" else [assessment],
            }
        if schema == "agent_action":
            self.names.append(schema)
            self.executor_calls += 1
            self.executor_inputs.append(user_prompt)
            if self.before_executor:
                self.before_executor()
            return {
                "action_kind": "FINAL_RESPONSE",
                "content": "酒店预算上限为每晚500元",
                "tool_name": None,
                "tool_arguments": None,
            }
        result = super().generate_json(system_prompt, user_prompt, **kwargs)
        if schema == "collaboration_planner_v1":
            result["subtasks"][0].update(
                objective="核实用户酒店预算",
                tool_names=[],
                completion_criteria="由原始记录支持明确金额",
            )
        if schema == "collaboration_verifier_v1":
            data = json.loads(user_prompt)
            result["answer"] = "酒店预算上限为每晚500元"
            if data.get("source_packets"):
                assert data["source_packets"][0]["text"]
                result["evidence_ids"] = [
                    data["source_packets"][0]["source"]["source_ref"]
                ]
            elif "memories" in data and "receipts" in data:
                result["evidence_ids"] = [data["memories"][0]["memory_id"]]
        return result


def seeded_app(tmp_path, monkeypatch, fault="", text="酒店预算上限为每晚500元"):
    provider = SourceProvider(fault)
    app = make_app(tmp_path, monkeypatch, provider)
    _, message = seed_memory(
        app.long_term_memory_mgr.sqlite_mgr,
        app.conversation_repo,
        text=text,
        summary=(
            "酒店预算偏好：酒店预算上限为每晚500元"
            if fault == "summary_sufficient"
            else "酒店预算偏好"
        ),
    )
    return app, provider, message


def test_original_reaches_executor_verifier_and_persistent_replay(
    tmp_path, monkeypatch
):
    app, provider, message = seeded_app(tmp_path, monkeypatch)
    try:
        result = app._process_chat_turn_with_id(
            "酒店预算偏好：请核实酒店预算上限", "query", resume=False
        )
        assert result.status is AgentResultStatus.SUCCESS
        assert "500" in result.final_response
        assert message.content in provider.executor_inputs[0]
        assert provider.source_inputs[0]["source_packets"][0]["text"] == message.content
        assert result.trace_metadata["source_reads"]
        assert (
            "source_packets" not in result.trace_metadata
        )  # Raw originals live in bounded handoffs.
        calls = len(provider.names)
        replay = app._process_chat_turn_with_id(
            "酒店预算偏好：请核实酒店预算上限", "query", resume=True
        )
        assert (
            replay.status is AgentResultStatus.SUCCESS and len(provider.names) == calls
        )
        with sqlite3.connect(app.coordination_repository.db_path) as db:
            rows = dict(db.execute("SELECT stage_key,payload_json FROM handoffs"))
            assert "sources:calc" in rows and "evidence-check:calc" in rows
        with sqlite3.connect(app.conversation_db_path) as db:
            db.execute(
                "DELETE FROM conversation_messages WHERE message_id=?",
                (message.message_id,),
            )
        stale = app._process_chat_turn_with_id(
            "酒店预算偏好：请核实酒店预算上限", "query", resume=True
        )
        assert stale.status is AgentResultStatus.FAILED and stale.final_response is None
        assert stale.error_info.code == "COLLABORATION_STALE_REPLAY"
        assert len(provider.names) == calls
    finally:
        app.close()


@pytest.mark.parametrize(
    "fault",
    [
        "source",
        "need",
        "duplicate",
        "forged",
        "summary_only",
        "omitted",
        "provider",
        "uncertain",
    ],
)
def test_invalid_or_uncertain_evidence_cannot_reach_executor(
    tmp_path, monkeypatch, fault
):
    app, provider, _ = seeded_app(tmp_path, monkeypatch, fault)
    try:
        result = app.process_chat_turn("酒店预算偏好：请核实酒店预算上限")
        assert result.status is AgentResultStatus.FAILED
        assert provider.executor_calls == 0
        assert "private response" not in json.dumps(result.trace_metadata)
        if fault == "uncertain":
            assert result.error_info.code == "COLLABORATION_EVIDENCE_INSUFFICIENT"
    finally:
        app.close()


@pytest.mark.parametrize("fault", ["missing", "hash", "truncated"])
def test_unavailable_or_truncated_original_preserves_gap(tmp_path, monkeypatch, fault):
    app, provider, message = seeded_app(
        tmp_path,
        monkeypatch,
        text="酒店预算上限为每晚500元"
        + ("额外背景" * 2000 if fault == "truncated" else ""),
    )
    try:
        with sqlite3.connect(app.conversation_db_path) as db:
            if fault == "missing":
                db.execute(
                    "DELETE FROM conversation_messages WHERE message_id=?",
                    (message.message_id,),
                )
            if fault == "hash":
                db.execute(
                    "UPDATE conversation_messages SET content='another private fact' WHERE message_id=?",
                    (message.message_id,),
                )
        result = app.process_chat_turn("酒店预算偏好：请核实酒店预算上限")
        assert result.status is AgentResultStatus.FAILED
        assert result.error_info.code == "COLLABORATION_EVIDENCE_INSUFFICIENT"
        assert provider.executor_calls == 0
        packet = provider.source_inputs[0]["source_packets"][0]
        assert packet["status"] != "RESOLVED"
        assert "another private fact" not in packet["text"]
    finally:
        app.close()


def test_sufficient_summary_does_not_read_original(tmp_path, monkeypatch):
    app, provider, _ = seeded_app(tmp_path, monkeypatch, "summary_sufficient")
    try:
        result = app.process_chat_turn("酒店预算偏好：请核实酒店预算上限")
        assert result.status is AgentResultStatus.SUCCESS
        assert result.trace_metadata["source_reads"] == []
        assert provider.source_inputs[0]["source_packets"] == []
        with sqlite3.connect(app.coordination_repository.db_path) as db:
            assert not db.execute(
                "SELECT 1 FROM role_tasks WHERE role='SourceResolver'"
            ).fetchall()
    finally:
        app.close()


def test_source_changed_during_execution_cannot_be_verified(tmp_path, monkeypatch):
    app, provider, message = seeded_app(tmp_path, monkeypatch)

    def withdraw_source():
        with sqlite3.connect(app.conversation_db_path) as db:
            db.execute(
                "DELETE FROM conversation_messages WHERE message_id=?",
                (message.message_id,),
            )

    provider.before_executor = withdraw_source
    try:
        result = app.process_chat_turn("酒店预算偏好：请核实酒店预算上限")
        assert result.status is AgentResultStatus.FAILED
        assert result.error_info.code == "COLLABORATION_SOURCE_STALE"
        graphs = app.coordination_repository.list_task_graphs(result.task_id)
        assert graphs[0]["nodes"][0]["status"] == "STALE"
        with sqlite3.connect(app.coordination_repository.db_path) as db:
            assert not db.execute(
                "SELECT 1 FROM artifacts WHERE verification_status='VERIFIED'"
            ).fetchall()
    finally:
        app.close()


def test_source_catalog_change_invalidates_graph(tmp_path, monkeypatch):
    from src.memory.retrieval.sources import SourceAccessError

    app, provider, _ = seeded_app(tmp_path, monkeypatch)

    def changed_catalog(*args, **kwargs):
        raise SourceAccessError("source changed while listing")

    app.collaboration.sources.catalog = changed_catalog
    try:
        result = app.process_chat_turn("酒店预算偏好：请核实酒店预算上限")
        assert result.status is AgentResultStatus.FAILED
        assert result.error_info.code == "COLLABORATION_SOURCE_STALE"
        assert provider.executor_calls == 0
        assert (
            app.coordination_repository.list_task_graphs(result.task_id)[0]["nodes"][0][
                "status"
            ]
            == "BLOCKED"
        )
    finally:
        app.close()


def test_cached_preparation_rechecks_original_without_calling_provider(
    tmp_path, monkeypatch
):
    from src.memory.collaboration.repository import CoordinationConflict

    app, provider, message = seeded_app(tmp_path, monkeypatch)
    try:
        result = app.process_chat_turn("酒店预算偏好：请核实酒店预算上限")
        assert result.status is AgentResultStatus.SUCCESS
        run = result.trace_metadata["collaboration_run_id"]
        inputs = provider.memory_inputs[0]
        selection = app.coordination_repository.get_stage(
            run, "memory:v2:calc", "Memory", inputs
        )
        calls = len(provider.names)
        preparation = app.collaboration._prepare_evidence(
            run, "calc", inputs, selection, []
        )
        assert preparation["source_packets"][0]["text"] == message.content
        assert len(provider.names) == calls
        with sqlite3.connect(app.conversation_db_path) as db:
            db.execute(
                "DELETE FROM conversation_messages WHERE message_id=?",
                (message.message_id,),
            )
        with pytest.raises(CoordinationConflict, match="cached original source"):
            app.collaboration._prepare_evidence(run, "calc", inputs, selection, [])
        assert len(provider.names) == calls
    finally:
        app.close()
