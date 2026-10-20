import json
import threading
import time
import urllib.error

import pytest

from src.memory.core.config import MemoryConfig, ProviderMode
from src.memory.llm.errors import (
    ContextWindowOverflowError,
    ProviderConfigurationError,
    ProviderRequestError,
    ProviderResponseError,
)
from src.memory.llm.llm_client import OpenAILLMClient, build_llm_client


def test_required_mode_without_key_fails_explicitly():
    with pytest.raises(ProviderConfigurationError):
        build_llm_client(MemoryConfig(provider_mode=ProviderMode.REQUIRED, llm_api_key=None))


def test_default_provider_deadline_supports_long_agent_turns():
    config = MemoryConfig(provider_mode=ProviderMode.REQUIRED, llm_api_key="test-only-key")
    client = OpenAILLMClient("test-only-key")

    assert config.llm_request_timeout_seconds == 60.0
    assert client.request_timeout_seconds == 60.0


def test_required_request_failure_does_not_fallback(monkeypatch):
    client = OpenAILLMClient("test-only-key", max_retries=1, retry_delay_seconds=0)

    def fail_request(*args, **kwargs):
        raise urllib.error.URLError("synthetic network failure")

    monkeypatch.setattr("urllib.request.urlopen", fail_request)
    with pytest.raises(ProviderRequestError) as raised:
        client.generate_json("system", "user")

    assert raised.value.provider == "deepseek"
    assert isinstance(raised.value.as_dict(), dict)


def test_provider_request_has_hard_wall_clock_deadline(monkeypatch):
    blocker = threading.Event()

    def never_returns(*_args, **_kwargs):
        blocker.wait(5)

    monkeypatch.setattr("urllib.request.urlopen", never_returns)
    client = OpenAILLMClient(
        "test-only-key",
        request_timeout_seconds=0.05,
        max_retries=2,
        retry_delay_seconds=0,
    )

    started = time.perf_counter()
    with pytest.raises(ProviderRequestError, match="总时限"):
        client.generate_json("system", "user")
    elapsed = time.perf_counter() - started

    assert elapsed < 0.5
    assert client.usage["request_count"] == 1


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.body


def test_required_structured_response_is_validated(monkeypatch):
    body = json.dumps({"choices": [{"message": {"content": "not-json"}}]}).encode()
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: FakeResponse(body))
    client = OpenAILLMClient("test-only-key", max_retries=1, retry_delay_seconds=0)

    with pytest.raises(ProviderResponseError):
        client.generate_json("system", "user")


def test_oversized_provider_response_is_rejected_before_json_parse(monkeypatch):
    class OversizedResponse(FakeResponse):
        def read(self, size=None):
            assert size is not None
            return b"x" * size

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: OversizedResponse(b""),
    )
    client = OpenAILLMClient(
        "test-only-key",
        max_retries=1,
        retry_delay_seconds=0,
    )

    with pytest.raises(ProviderResponseError, match="安全大小上限"):
        client.generate_json("system", "user")


def test_invalid_provider_response_is_retried(monkeypatch):
    responses = iter([
        FakeResponse(json.dumps({"choices": [{"message": {"content": None}}]}).encode()),
        FakeResponse(json.dumps({"choices": [{"message": {"content": "{\"ok\": true}"}}]}).encode()),
    ])
    call_count = 0
    progress = []

    def next_response(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return next(responses)

    monkeypatch.setattr("urllib.request.urlopen", next_response)
    client = OpenAILLMClient(
        "test-only-key",
        max_retries=2,
        retry_delay_seconds=0,
        progress_callback=lambda schema, attempt, total: progress.append(
            (schema, attempt, total)
        ),
    )

    assert client.generate_json("system", "user") == {"ok": True}
    assert call_count == 2
    assert progress == [
        ("structured_output", 1, 2),
        ("structured_output", 2, 2),
    ]


def test_config_from_env_requires_api_mode(tmp_path):
    config = MemoryConfig.from_env(
        environ={
            "MEMORY_DB_PATH": str(tmp_path / "configured.db"),
        }
    )
    assert config.provider_mode is ProviderMode.REQUIRED
    assert config.sqlite_db_path == str(tmp_path / "configured.db")


def test_deepseek_endpoint_model_and_credential_are_fixed():
    config = MemoryConfig.from_env(environ={
        "MEMORY_PROVIDER_MODE": "REQUIRED",
        "DEEPSEEK_API_KEY": "test-only-value",
        "MEMORY_LLM_PROVIDER": "other-provider",
        "MEMORY_LLM_BASE_URL": "https://example.invalid/other",
        "MEMORY_LLM_MODEL": "other-model",
    })
    assert config.llm_provider_name == "deepseek"
    assert config.llm_api_key_env == "DEEPSEEK_API_KEY"
    assert config.llm_api_key == "test-only-value"
    assert config.llm_api_base == "https://api.deepseek.com"
    assert config.llm_model_name == "deepseek-v4-flash"


def test_consolidation_schedule_settings_are_no_longer_supported():
    config = MemoryConfig.from_env(environ={
        "DEEPSEEK_API_KEY": "test-only-value",
        "CONSOLIDATION_TURN_THRESHOLD": "3",
        "CONSOLIDATION_TIME_THRESHOLD_HOURS": "0.5",
    })
    assert not hasattr(config, "consolidation_turn_threshold")
    assert not hasattr(config, "consolidation_time_threshold_hours")


def test_openai_compatible_usage_is_aggregated_without_response_logging(monkeypatch):
    body = json.dumps({
        "choices": [{"message": {"content": "{\"ok\": true}"}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7},
    }).encode()
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: FakeResponse(body))
    client = OpenAILLMClient("test-only-key", max_retries=1, retry_delay_seconds=0)
    assert client.generate_json("system", "user") == {"ok": True}
    assert client.usage == {"request_count": 1, "input_tokens": 11, "output_tokens": 7}
    assert client.last_usage == {"input_tokens": 11, "output_tokens": 7}


def test_context_window_overflow_is_typed_and_safe(monkeypatch):
    import io
    import email.message

    headers = email.message.Message()
    body = json.dumps({
        "error": {
            "type": "context_length_exceeded",
            "code": "context_length_exceeded",
            "message": "prompt is too long",
        }
    }).encode()

    def reject(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            "https://example.invalid", 400, "too long", headers, io.BytesIO(body)
        )

    monkeypatch.setattr("urllib.request.urlopen", reject)
    client = OpenAILLMClient("test-only-key", max_retries=1, retry_delay_seconds=0)
    with pytest.raises(ContextWindowOverflowError) as raised:
        client.generate_json("system", "user")
    projection = raised.value.to_safe_projection()
    assert projection["code"] == "CONTEXT_WINDOW_OVERFLOW"
    assert "prompt is too long" not in projection["safe_message"]


def test_413_token_rate_limit_is_not_misclassified_as_context_overflow(monkeypatch):
    import io
    import email.message

    headers = email.message.Message()
    body = json.dumps({
        "error": {
            "type": "tokens",
            "code": "rate_limit_exceeded",
            "message": "tokens per minute exceeded",
        }
    }).encode()

    def reject(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            "https://example.invalid", 413, "rate limited", headers, io.BytesIO(body)
        )

    monkeypatch.setattr("urllib.request.urlopen", reject)
    client = OpenAILLMClient("test-only-key", max_retries=1, retry_delay_seconds=0)
    with pytest.raises(ProviderRequestError) as raised:
        client.generate_json("system", "user")
    assert not isinstance(raised.value, ContextWindowOverflowError)
    assert raised.value.status_code == 413


def test_openai_compatible_request_has_explicit_json_and_user_agent_headers(monkeypatch):
    captured = {}
    body = json.dumps({"choices": [{"message": {"content": "{\"ok\": true}"}}]}).encode()

    def capture(request, **_kwargs):
        captured.update({key.lower(): value for key, value in request.header_items()})
        return FakeResponse(body)

    monkeypatch.setattr("urllib.request.urlopen", capture)
    OpenAILLMClient("test-only-key", max_retries=1).generate_json("system", "user")
    assert captured["accept"] == "application/json"
    assert captured["user-agent"] == "mapas-agent-memory/0.1"


def test_global_agent_env_is_loaded_but_process_environment_wins(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    agent_home = tmp_path / "agent-home"
    config_path = agent_home / "config" / ".env.local"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        "MEMORY_PROVIDER_MODE=REQUIRED\n"
        "MEMORY_LLM_PROVIDER=local-compatible\n"
        "MEMORY_LLM_API_KEY_ENV=LOCAL_TEST_KEY\n"
        "LOCAL_TEST_KEY=local-test-only\n"
        "MEMORY_LLM_BASE_URL=https://local.invalid/v1\n"
        "MEMORY_LLM_MODEL=local-model\n"
        "DEEPSEEK_API_KEY=local-deepseek-test-only\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENT_HOME", str(agent_home))
    monkeypatch.delenv("MEMORY_LOAD_LOCAL_ENV", raising=False)
    monkeypatch.setenv("MEMORY_LLM_MODEL", "process-model")
    config = MemoryConfig.from_env()
    assert config.provider_mode is ProviderMode.REQUIRED
    assert config.llm_provider_name == "deepseek"
    assert config.llm_api_key == "local-deepseek-test-only"
    assert config.llm_api_base == "https://api.deepseek.com"
    assert config.llm_model_name == "deepseek-v4-flash"


def test_local_env_can_be_explicitly_disabled_for_test_ci(tmp_path, monkeypatch):
    (tmp_path / ".env.local").write_text(
        "MEMORY_PROVIDER_MODE=REQUIRED\nGROQ_API_KEY=local-test-only\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "empty-agent-home"))
    monkeypatch.setenv("MEMORY_LOAD_LOCAL_ENV", "0")
    assert MemoryConfig.from_env().provider_mode is ProviderMode.REQUIRED


def test_rate_limit_retry_respects_provider_reset_header(monkeypatch):
    import email.message

    body = json.dumps({"choices": [{"message": {"content": "{\"ok\": true}"}}]}).encode()
    calls = []
    headers = email.message.Message()
    headers["retry-after"] = "2.5"

    def rate_limited_then_ok(*_args, **_kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise urllib.error.HTTPError("https://example.invalid", 429, "limited", headers, None)
        return FakeResponse(body)

    sleeps = []
    monkeypatch.setattr("urllib.request.urlopen", rate_limited_then_ok)
    monkeypatch.setattr("time.sleep", sleeps.append)
    client = OpenAILLMClient("test-only-key", max_retries=2, retry_delay_seconds=0)
    assert client.generate_json("system", "user") == {"ok": True}
    assert sleeps == [2.5]


def test_deepseek_reasoning_and_token_fields_use_documented_payload(monkeypatch):
    captured = {}
    body = json.dumps({"choices": [{"message": {"content": "{\"ok\": true}"}}]}).encode()

    def capture(request, **_kwargs):
        captured.update(json.loads(request.data.decode("utf-8")))
        return FakeResponse(body)

    monkeypatch.setattr("urllib.request.urlopen", capture)
    config = MemoryConfig.from_env(environ={
        "MEMORY_PROVIDER_MODE": "REQUIRED",
        "DEEPSEEK_API_KEY": "test-only-key",
        "LLM_REASONING_EFFORT": "low",
        "DEEPSEEK_MAX_TOKENS": "2048",
    })
    assert config.llm_reasoning_effort == "low"
    assert config.llm_max_completion_tokens == 2048
    OpenAILLMClient(
        "test-only-key", max_retries=1, reasoning_effort=config.llm_reasoning_effort,
        max_completion_tokens=config.llm_max_completion_tokens,
    ).generate_json("system", "user")
    assert captured["thinking"] == {"type": "disabled"}
    assert captured["max_tokens"] == 2048

    OpenAILLMClient(
        "test-only-key", max_retries=1, reasoning_effort=config.llm_reasoning_effort,
        max_completion_tokens=config.llm_max_completion_tokens,
    ).generate_reasoned_json("system", "user")
    assert captured["thinking"] == {"type": "enabled"}
    assert captured["reasoning_effort"] == "low"
