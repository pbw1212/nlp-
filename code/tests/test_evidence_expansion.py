"""Model-proposed recall and original-text windows, including recovery gates."""

import json
from types import SimpleNamespace

import pytest
from src.memory.agent.models import AgentResultStatus
from src.memory.retrieval.evidence import (
    EvidenceBundle,
    EvidenceSufficiency,
    EvidenceSufficiencyStatus,
)
from test_collaboration import make_app
from test_collaboration_evidence import SourceProvider
from test_source_resolver import seed_memory


class ExpansionProvider(SourceProvider):
    def __init__(self, mode="window"):
        super().__init__()
        self.mode = mode
        self.search_calls = 0

    def generate_json(self, system_prompt, user_prompt, **kwargs):
        schema = kwargs.get("schema_name")
        data = json.loads(user_prompt) if schema != "agent_action" else None
        if schema == "collaboration_memory_v2":
            self.names.append(schema)
            return {
                "schema_version": 2,
                "selected_memory_ids": [row["memory_id"] for row in data["memories"]],
                "missing_information": [],
                "evidence_needs": [
                    {
                        "need_id": "budget",
                        "requirement": "酒店每晚预算的明确上限",
                        "requires_original": True,
                    }
                ],
                "source_requests": [
                    {"source_ref": row["source_ref"], "need_ids": ["budget"]}
                    for row in data["source_catalog"][:1]
                ],
            }
        if schema == "collaboration_evidencesearch_v1":
            self.names.append(schema)
            self.search_calls += 1
            if self.mode in {"query", "missing", "forged"}:
                return {
                    "schema_version": 1,
                    "queries": [
                        {
                            "query": "酒店预算的明确上限原始记录",
                            "need_ids": [
                                "invented" if self.mode == "forged" else "budget"
                            ],
                        }
                    ],
                    "source_windows": [],
                    "reason": "Need exact original budget",
                }
            packet = data["source_packets"][0]
            return {
                "schema_version": 1,
                "queries": [],
                "source_windows": [
                    {
                        "source_ref": data["source_catalog"][0]["source_ref"],
                        "offset": max(0, packet["source_length"] - 40),
                        "length": 40,
                        "need_ids": ["budget"],
                    }
                ],
                "reason": "Read the original ending beyond the truncated prefix",
            }
        if schema == "collaboration_evidencecheck_v1":
            self.names.append(schema)
            good = [
                row
                for row in data["source_packets"]
                if row["status"] == "RESOLVED" and "500" in row["text"]
            ]
            return {
                "schema_version": 1,
                "assessments": [
                    {
                        "need_id": "budget",
                        "status": "SUPPORTED" if good else "MISSING",
                        "evidence_ids": (
                            [good[0]["source"]["source_ref"]] if good else []
                        ),
                        "reason": (
                            "Exact original budget found"
                            if good
                            else "Original amount is missing"
                        ),
                    }
                ],
            }
        if schema == "collaboration_verifier_v1":
            output = super().generate_json(system_prompt, user_prompt, **kwargs)
            if data.get("source_packets"):
                output["evidence_ids"] = [
                    row["source"]["source_ref"]
                    for row in data["source_packets"]
                    if row["status"] == "RESOLVED"
                ]
            return output
        return super().generate_json(system_prompt, user_prompt, **kwargs)


def test_original_window_resolves_truncated_evidence_and_replays_without_calls(
    tmp_path, monkeypatch
):
    provider = ExpansionProvider()
    app = make_app(tmp_path, monkeypatch, provider)
    seed_memory(
        app.long_term_memory_mgr.sqlite_mgr,
        app.conversation_repo,
        text="说明" * 2600 + "酒店预算上限为每晚500元",
        summary="酒店预算偏好",
    )
    try:
        result = app._process_chat_turn_with_id(
            "酒店预算偏好：请核实酒店预算上限", "expanded-window", resume=False
        )
        assert result.status is AgentResultStatus.SUCCESS, result.error_info
        assert provider.search_calls == 1
        assert any(
            source["offset"] > 4000
            for read in result.trace_metadata["source_reads"]
            for source in read["sources"]
        )
        calls = len(provider.names)
        assert (
            app._process_chat_turn_with_id(
                "酒店预算偏好：请核实酒店预算上限", "expanded-window", resume=True
            ).status
            is AgentResultStatus.SUCCESS
        )
        assert len(provider.names) == calls
    finally:
        app.close()


@pytest.mark.parametrize("mode", ["query", "missing", "forged"])
def test_query_expansion_uses_task2_and_never_invents_missing_evidence(
    tmp_path, monkeypatch, mode
):
    provider = ExpansionProvider(mode)
    app = make_app(tmp_path, monkeypatch, provider)
    ref, _ = seed_memory(
        app.long_term_memory_mgr.sqlite_mgr,
        app.conversation_repo,
        text="酒店预算上限为每晚500元",
        summary="预算记录",
    )
    queries = []
    empty = EvidenceBundle(
        "1",
        "empty",
        "initial",
        (),
        EvidenceSufficiency(EvidenceSufficiencyStatus.INSUFFICIENT),
    )

    def retrieve(query, **kwargs):
        queries.append(query)
        if query != "酒店预算的明确上限原始记录" or mode == "missing":
            return empty
        return SimpleNamespace(
            to_dict=lambda: {
                "selected": [{**ref, "text": "预算记录", "provenance_refs": []}]
            }
        )

    app.runtime.retrieval_facade = SimpleNamespace(retrieve_evidence=retrieve)
    try:
        result = app.process_chat_turn("请核实当前预算上限")
        if mode == "query":
            assert result.status is AgentResultStatus.SUCCESS, result.error_info
            assert ref["memory_id"] in {
                row["memory_id"] for row in result.trace_metadata["memory_inputs"]
            }
            assert "500" in result.final_response
        else:
            assert result.status is AgentResultStatus.FAILED
            assert provider.executor_calls == 0
        assert provider.search_calls <= 2
        if mode != "forged":
            assert "酒店预算的明确上限原始记录" in queries
    finally:
        app.close()
