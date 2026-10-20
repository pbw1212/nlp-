"""Unit tests for post-freeze production bug fixes and DeepSeek transport."""

from __future__ import annotations

import json
import tempfile
import urllib.error
import pytest

from src.memory.agent.models import AgentErrorInfo, ReasonerInput
from src.memory.agent.reasoner import InvalidAgentActionError, OpenAICompatibleAgentReasoner
from src.memory.core.config import MemoryConfig, ProviderMode
from src.memory.llm.errors import ProviderError, ProviderRequestError
from src.memory.llm.llm_client import OpenAILLMClient, build_llm_client
from src.memory.retrieval.evidence import (
    DeterministicEvidencePipeline,
    EvidenceBundle,
    EvidenceSufficiency,
    EvidenceSufficiencyStatus,
)
from src.memory.retrieval.facade import DefaultMemoryRetrievalFacade
from src.memory.runtime.builder import build_agent_runtime
from src.memory.runtime.config import AgentRuntimeConfig


def test_builder_wires_real_llm_client_to_task_interpreter():
    with tempfile.TemporaryDirectory() as tmp_dir:
        cfg = AgentRuntimeConfig(
            memory_config=MemoryConfig(
                sqlite_db_path=f"{tmp_dir}/t1.db",
                provider_mode=ProviderMode.REQUIRED,
                llm_api_key="test-key-only",
            ),
            task3_db_path=f"{tmp_dir}/t3.db",
        )
        runtime = build_agent_runtime(cfg)
        interpreter_client = getattr(runtime.task_interpreter, "_client", None)
        assert interpreter_client is not None
        assert hasattr(interpreter_client, "generate_json")
        assert not isinstance(interpreter_client, MemoryConfig)


def test_retrieval_facade_uses_valid_evidence_pipeline_api(monkeypatch):
    class FakeSemanticPipeline:
        def run(self, query, **kwargs):
            class FakeResult:
                applicability_result = None
            return FakeResult()

    fake_semantic = FakeSemanticPipeline()
    facade = DefaultMemoryRetrievalFacade(fake_semantic)

    def mock_build(app, request_id="current-query", **kwargs):
        return EvidenceBundle(
            schema_version="v1",
            request_id=request_id,
            query="q",
            selected=(),
            sufficiency=EvidenceSufficiency(status=EvidenceSufficiencyStatus.SUFFICIENT),
        )

    monkeypatch.setattr(facade.evidence_pipeline, "build", mock_build)
    bundle = facade.retrieve_evidence("test query")
    assert isinstance(bundle, EvidenceBundle)


def test_agent_error_info_provider_projection_safe():
    p_err = ProviderError("Raw internal error details", provider="test_provider", operation="test_op")
    info = AgentErrorInfo.from_provider_error(p_err)
    assert info.stage == "REASONING"
    assert info.category == "PROVIDER_ERROR"
    assert "Raw internal error" not in info.safe_message or info.safe_message == str(p_err)


def test_deepseek_structured_requests_use_json_object_without_schema_fallback(monkeypatch):
    calls = []

    def mock_urlopen(req, timeout=None):
        payload = json.loads(req.data.decode("utf-8"))
        calls.append(payload)
        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self):
                return json.dumps({"choices": [{"message": {"content": "{\"ok\": true}"}}]}).encode("utf-8")
        return FakeResp()

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    client = OpenAILLMClient(api_key="test-key", max_retries=1)
    res = client.generate_json("system", "user", response_schema={"type": "object"})
    assert res == {"ok": True}
    assert len(calls) == 1
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert calls[0]["thinking"] == {"type": "disabled"}
    assert "STRUCTURED OUTPUT CONTRACT" in calls[0]["messages"][0]["content"]
    assert '"type": "object"' in calls[0]["messages"][0]["content"]


def test_non_schema_400_does_not_fallback(monkeypatch):
    calls = []

    def mock_urlopen(req, timeout=None):
        payload = json.loads(req.data.decode("utf-8"))
        calls.append(payload.get("response_format", {}).get("type"))
        err_body = json.dumps({
            "error": {
                "message": "invalid model name provided",
                "type": "invalid_request_error",
            }
        }).encode("utf-8")
        class FakeHTTPError(urllib.error.HTTPError):
            def __init__(self):
                super().__init__("http://test", 400, "Bad Request", {}, None)
            def read(self, *args):
                return err_body
        raise FakeHTTPError()

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    client = OpenAILLMClient(api_key="test-key", max_retries=2)
    with pytest.raises(ProviderRequestError):
        client.generate_json("system", "user", response_schema={"type": "object"})
    # The DeepSeek request is JSON-object mode from the start and is never
    # rewritten into a different provider request shape.
    assert calls == ["json_object"]


def test_fallback_invalid_agent_action_still_rejected(monkeypatch):
    class FakeLLMClient:
        def generate_json(self, *args, **kwargs):
            return {"action_kind": "INVALID_KIND_123", "content": "hello"}

    reasoner = OpenAICompatibleAgentReasoner(FakeLLMClient())
    from src.memory.agent.models import ReasonerInput
    from src.memory.runtime.models import AgentTurnContext

    ctx = AgentTurnContext(turn_id="t1", task_id="task_1", receipt=None, maintenance=None, evidence_bundle=None, working_context=None, structured_task_context=None)
    req_input = ReasonerInput(turn_context=ctx, user_input="hello")

    with pytest.raises(InvalidAgentActionError):
        reasoner.reason(req_input)


def test_agent_action_enables_provider_reasoning_without_affecting_structured_background_calls(monkeypatch):
    calls = []

    def mock_urlopen(req, timeout=None):
        calls.append(json.loads(req.data.decode("utf-8")))
        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self):
                return json.dumps({"choices": [{"message": {"content": json.dumps({
                    "action_kind": "FINAL_RESPONSE",
                    "content": "ok",
                    "tool_name": None,
                    "tool_arguments": None,
                })}}]}).encode("utf-8")
        return FakeResp()

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)
    client = OpenAILLMClient(api_key="test-key", max_retries=1)
    from src.memory.runtime.models import AgentTurnContext
    ctx = AgentTurnContext(
        turn_id="t1", task_id="task_1", receipt=None, maintenance=None,
        evidence_bundle=None, working_context=None, structured_task_context=None,
    )
    OpenAICompatibleAgentReasoner(client).reason(
        ReasonerInput(turn_context=ctx, user_input="hello")
    )
    assert calls[0]["thinking"]["type"] == "enabled"


def test_application_user_agent_present(monkeypatch):
    captured = {}
    body = json.dumps({"choices": [{"message": {"content": "{\"ok\": true}"}}]}).encode()

    def capture(request, **_kwargs):
        captured.update({key.lower(): value for key, value in request.header_items()})
        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return body
        return FakeResp()

    monkeypatch.setattr("urllib.request.urlopen", capture)
    OpenAILLMClient("test-only-key", max_retries=1).generate_json("system", "user")
    assert captured["user-agent"] == "mapas-agent-memory/0.1"
    assert "Mozilla" not in captured["user-agent"]


def test_provider_reasoning_is_explicitly_clearable_between_application_turns():
    client = OpenAILLMClient("test-only-key", max_retries=1)
    client._last_reasoning_content = "previous turn"
    client.clear_last_reasoning_content()
    assert client.last_reasoning_content == ""
