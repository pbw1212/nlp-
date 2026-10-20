import json
import logging
import queue
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from typing import Callable, Dict, Any, List

from jsonschema import Draft202012Validator, ValidationError

from src.memory.core.config import MemoryConfig
from src.memory.core.diagnostics import log_boundary_failure
from src.memory.llm.errors import (
    ContextWindowOverflowError,
    ProviderConfigurationError,
    ProviderRequestError,
    ProviderResponseError,
)


logger = logging.getLogger(__name__)
_MAX_PROVIDER_RESPONSE_BYTES = 8_000_000


class _ProviderDeadlineExceeded(TimeoutError):
    """Internal wall-clock deadline; unlike socket timeout it is total elapsed time."""


class LLMClientBase(ABC):
    @property
    def last_reasoning_content(self) -> str:
        """Provider-reported reasoning text for the latest response, if any.

        This is intentionally an optional transport field.  The application
        never fabricates a chain of thought when the provider does not return
        one.
        """
        return ""

    def clear_last_reasoning_content(self) -> None:
        """Clear optional reasoning retained from the previous application turn."""
        return None

    @property
    def last_usage(self) -> Dict[str, int]:
        """Token usage for the most recently completed provider response, if known."""
        return {}

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        response_schema: Dict[str, Any] | None = None,
        schema_name: str = "structured_output",
    ) -> Dict[str, Any]:
        """执行大模型推理，并强制解析/生成结构化 JSON"""
        pass

    def generate_reasoned_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        response_schema: Dict[str, Any] | None = None,
        schema_name: str = "structured_output",
    ) -> Dict[str, Any]:
        """Generate a user-facing structured answer with provider reasoning enabled.

        Structured background operations use ``generate_json`` and should not
        spend their completion budget on hidden reasoning.  The agent's final
        action is the explicit exception: it may expose provider-returned
        reasoning in developer/user presentation modes when the provider
        returns that optional field.

        The base implementation deliberately delegates without adding new
        keyword arguments so lightweight injected test clients remain
        source-compatible.
        """
        return self.generate_json(
            system_prompt,
            user_prompt,
            response_schema=response_schema,
            schema_name=schema_name,
        )

    @abstractmethod
    def generate_chat_json(self, messages: List[Dict[str, str]], enable_web_search: bool = False) -> Dict[str, Any]:
        """支持完整 Dialogue Messages 数组的 JSON 大模型推理，支持开启 web_search 原生联网搜索"""
        pass


class OpenAILLMClient(LLMClientBase):
    """
    DeepSeek Chat Completions 客户端。

    DeepSeek exposes an OpenAI-compatible wire format, but its request
    contract is intentionally encoded here rather than relying on generic
    provider fallbacks: JSON Output uses ``json_object`` and token budgeting
    uses ``max_tokens``.
    """
    def __init__(
        self,
        api_key: str,
        api_base: str = "https://api.deepseek.com",
        model_name: str = "deepseek-v4-flash",
        request_timeout_seconds: float = 60.0,
        max_retries: int = 3,
        retry_delay_seconds: float = 1.0,
        provider_name: str = "deepseek",
        request_interval_seconds: float = 0.0,
        reasoning_effort: str | None = None,
        max_completion_tokens: int | None = None,
        progress_callback: Callable[[str, int, int], None] | None = None,
    ):
        if not api_key:
            raise ProviderConfigurationError(
                "REQUIRED 模式缺少 API key",
                provider=provider_name,
                operation="configure",
            )
        if not isinstance(api_base, str) or not api_base.startswith(("http://", "https://")):
            raise ProviderConfigurationError(
                "api_base 必须是 HTTP(S) URL",
                provider=provider_name,
                operation="configure",
            )
        if not isinstance(model_name, str) or not model_name.strip():
            raise ProviderConfigurationError(
                "model_name 不能为空",
                provider=provider_name,
                operation="configure",
            )
        if request_timeout_seconds <= 0:
            raise ProviderConfigurationError(
                "request_timeout_seconds 必须大于 0",
                provider=provider_name,
                operation="configure",
            )
        if max_retries < 1:
            raise ProviderConfigurationError(
                "max_retries 必须至少为 1",
                provider=provider_name,
                operation="configure",
            )
        if retry_delay_seconds < 0 or request_interval_seconds < 0:
            raise ProviderConfigurationError(
                "retry/request interval 不能为负数",
                provider=provider_name,
                operation="configure",
            )
        if max_completion_tokens is not None and max_completion_tokens <= 0:
            raise ProviderConfigurationError(
                "max_completion_tokens 必须大于 0",
                provider=provider_name,
                operation="configure",
            )
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.model_name = model_name
        self.request_timeout_seconds = request_timeout_seconds
        self.max_retries = max_retries
        self.retry_delay_seconds = retry_delay_seconds
        self.provider_name = provider_name
        self.request_interval_seconds = request_interval_seconds
        self.reasoning_effort = reasoning_effort
        self.max_completion_tokens = max_completion_tokens
        self.progress_callback = progress_callback
        self._last_request_started: float | None = None
        self._request_count = 0
        self._input_tokens = 0
        self._output_tokens = 0
        self._prompt_cache_hit_tokens = 0
        self._prompt_cache_miss_tokens = 0
        self._reasoning_tokens = 0
        self._last_usage: Dict[str, int] = {}
        self._last_reasoning_content = ""

    def set_progress_callback(
        self,
        callback: Callable[[str, int, int], None] | None,
    ) -> None:
        self.progress_callback = callback

    def _report_progress(
        self,
        schema_name: str,
        attempt: int,
        max_attempts: int | None = None,
    ) -> None:
        if self.progress_callback is None:
            return
        try:
            self.progress_callback(
                schema_name,
                attempt,
                self.max_retries if max_attempts is None else max_attempts,
            )
        except Exception as exc:
            log_boundary_failure(
                logger,
                "progress_callback_failed",
                exc,
                schema_name=schema_name,
                attempt=attempt,
            )

    @property
    def usage(self) -> Dict[str, int]:
        usage = {
            "request_count": self._request_count,
            "input_tokens": self._input_tokens,
            "output_tokens": self._output_tokens,
        }
        for key, value in {
            "prompt_cache_hit_tokens": self._prompt_cache_hit_tokens,
            "prompt_cache_miss_tokens": self._prompt_cache_miss_tokens,
            "reasoning_tokens": self._reasoning_tokens,
        }.items():
            if value:
                usage[key] = value
        return usage

    @property
    def last_usage(self) -> Dict[str, int]:
        return dict(self._last_usage)

    @property
    def last_reasoning_content(self) -> str:
        return self._last_reasoning_content

    def clear_last_reasoning_content(self) -> None:
        self._last_reasoning_content = ""

    def _read_response_with_deadline(self, req: urllib.request.Request) -> bytes:
        """Open and read one response under a hard wall-clock deadline.

        ``urllib`` applies its timeout to individual blocking socket operations,
        so a trickling or wedged response can otherwise exceed the configured
        limit indefinitely. The worker is daemonized because Python cannot
        safely cancel a thread blocked inside the platform networking stack.
        """
        outcome: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)

        def perform_request() -> None:
            try:
                with urllib.request.urlopen(req, timeout=self.request_timeout_seconds) as resp:
                    try:
                        raw = resp.read(_MAX_PROVIDER_RESPONSE_BYTES + 1)
                    except TypeError:
                        # Lightweight test doubles and legacy custom
                        # transports may expose read() without a size arg.
                        raw = resp.read()
                outcome.put(("result", raw))
            except BaseException as exc:  # propagated on the calling thread
                outcome.put(("error", exc))

        worker = threading.Thread(
            target=perform_request,
            name="provider-http-request",
            daemon=True,
        )
        worker.start()
        worker.join(self.request_timeout_seconds)
        if worker.is_alive():
            raise _ProviderDeadlineExceeded(
                "Provider request exceeded the configured wall-clock deadline"
            )

        outcome_kind, value = outcome.get_nowait()
        if outcome_kind == "error":
            raise value
        if not isinstance(value, bytes):
            raise ProviderResponseError(
                "Provider 返回内容必须是 bytes",
                provider=self.provider_name,
                operation="chat.completions",
            )
        return value

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        response_schema: Dict[str, Any] | None = None,
        schema_name: str = "structured_output",
    ) -> Dict[str, Any]:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        return self.generate_chat_json(
            messages,
            enable_web_search=False,
            response_schema=response_schema,
            schema_name=schema_name,
            thinking_enabled=False,
        )

    def generate_reasoned_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        response_schema: Dict[str, Any] | None = None,
        schema_name: str = "structured_output",
    ) -> Dict[str, Any]:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        return self.generate_chat_json(
            messages,
            enable_web_search=False,
            response_schema=response_schema,
            schema_name=schema_name,
            thinking_enabled=True,
        )

    def generate_chat_json(
        self,
        messages: List[Dict[str, str]],
        enable_web_search: bool = False,
        *,
        response_schema: Dict[str, Any] | None = None,
        schema_name: str = "structured_output",
        thinking_enabled: bool = True,
    ) -> Dict[str, Any]:
        request_messages = [dict(message) for message in messages]
        if response_schema is not None:
            # DeepSeek supports JSON Output rather than the OpenAI
            # json_schema response format.  Preserve the typed contract by
            # injecting the trusted application schema into the system
            # instruction; the application parser remains the final authority
            # after transport.  This is a structural transport concern, not a
            # semantic trigger or a task-specific prompt rule.
            schema_text = json.dumps(response_schema, ensure_ascii=False, sort_keys=True)
            contract = (
                "\n\nSTRUCTURED OUTPUT CONTRACT (trusted application schema)\n"
                f"Schema name: {schema_name}\n"
                "Return exactly one JSON object that satisfies this schema. "
                "Do not omit required fields and do not add fields outside the schema.\n"
                f"JSON Schema: {schema_text}\n"
            )
            system_index = next(
                (index for index, message in enumerate(request_messages)
                 if message.get("role") == "system"),
                None,
            )
            if system_index is None:
                request_messages.insert(0, {"role": "system", "content": contract})
            else:
                original = request_messages[system_index].get("content", "")
                request_messages[system_index]["content"] = f"{original}{contract}"
        url = f"{self.api_base}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": "mapas-agent-memory/0.1",
        }
        payload: Dict[str, Any] = {
            "model": self.model_name,
            "messages": request_messages,
            "temperature": 0.2,
            # DeepSeek JSON Output is the provider-supported structured mode.
            # The application validates the returned object against its
            # trusted local schema after transport; no provider schema
            # downgrade is needed or permitted.
            "response_format": {"type": "json_object"},
        }
        if thinking_enabled:
            payload["thinking"] = {"type": "enabled"}
            if self.reasoning_effort is not None:
                payload["reasoning_effort"] = self.reasoning_effort
        else:
            payload["thinking"] = {"type": "disabled"}
        if self.max_completion_tokens is not None:
            payload["max_tokens"] = self.max_completion_tokens

        # DeepSeek Chat Completions supports function tools, not a provider
        # specific built-in web_search tool.  Product web access is supplied
        # by the agent's own validated web_search/web_fetch tools.  Keep the
        # legacy flag in the protocol for test doubles, but never emit an
        # unsupported tool shape to DeepSeek.

        self._last_reasoning_content = ""
        self._last_usage = {}
        attempt = 0
        attempt_budget = self.max_retries
        while attempt < attempt_budget:
            failure_cause: Exception
            retry_delay = self.retry_delay_seconds
            try:
                self._report_progress(schema_name, attempt + 1, attempt_budget)
                headers["Authorization"] = f"Bearer {self.api_key}"
                req = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers=headers,
                    method="POST"
                )
                if self._last_request_started is not None and self.request_interval_seconds:
                    remaining = self.request_interval_seconds - (
                        time.monotonic() - self._last_request_started
                    )
                    if remaining > 0:
                        time.sleep(remaining)
                self._last_request_started = time.monotonic()
                self._request_count += 1
                logger.info(
                    "provider_request_started operation=%s attempt=%d request_count=%d",
                    schema_name, attempt + 1, self._request_count,
                )
                raw_response = self._read_response_with_deadline(req)
                try:
                    if len(raw_response) > _MAX_PROVIDER_RESPONSE_BYTES:
                        raise ProviderResponseError(
                            "Provider 返回内容超过安全大小上限",
                            provider=self.provider_name,
                            operation="chat.completions",
                        )
                    result = json.loads(raw_response.decode("utf-8"))
                    msg_obj = result["choices"][0]["message"]
                except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
                    raise ProviderResponseError(
                        "Provider 返回了无法解析的响应结构",
                        provider=self.provider_name,
                        operation="chat.completions",
                    ) from exc
                if result["choices"][0].get("finish_reason") == "length":
                    raise ProviderResponseError(
                        "Provider 输出达到 token 上限，结构化结果未提交",
                        provider=self.provider_name,
                        operation="chat.completions",
                    )
                if not isinstance(msg_obj, dict):
                    raise ProviderResponseError(
                        "Provider 返回的 message 必须是 JSON object",
                        provider=self.provider_name,
                        operation="chat.completions",
                    )
                reasoning = msg_obj.get("reasoning_content")
                if not isinstance(reasoning, str):
                    reasoning = msg_obj.get("reasoning")
                if not isinstance(reasoning, str):
                    reasoning = result.get("reasoning_content")
                self._last_reasoning_content = reasoning.strip() if isinstance(reasoning, str) else ""
                content = msg_obj.get("content") or ""

                if not isinstance(content, str) or not content.strip():
                    raise ProviderResponseError(
                        "Provider 返回了空 content",
                        provider=self.provider_name,
                        operation="chat.completions",
                    )

                # 2. 清洗可能存在的 ```json ... ``` Markdown 代码块包装
                clean_content = content.strip()
                if clean_content.startswith("```json"):
                    clean_content = clean_content[7:]
                elif clean_content.startswith("```"):
                    clean_content = clean_content[3:]
                if clean_content.endswith("```"):
                    clean_content = clean_content[:-3]
                clean_content = clean_content.strip()

                try:
                    parsed = json.loads(clean_content)
                except json.JSONDecodeError as exc:
                    if enable_web_search and response_schema is None:
                        return {"response": content, "raw_content": content}
                    raise ProviderResponseError(
                        "Provider 返回的 content 不是有效 JSON object",
                        provider=self.provider_name,
                        operation="chat.completions",
                    ) from exc
                if not isinstance(parsed, dict):
                    raise ProviderResponseError(
                        "Provider 返回的结构化 content 必须是 JSON object",
                        provider=self.provider_name,
                        operation="chat.completions",
                    )
                if response_schema is not None:
                    try:
                        Draft202012Validator(response_schema).validate(parsed)
                    except ValidationError as exc:
                        raise ProviderResponseError(
                            "Provider 返回的 JSON 不符合请求 schema",
                            provider=self.provider_name,
                            operation="chat.completions",
                        ) from exc
                usage = result.get("usage", {})
                request_usage: Dict[str, int] = {}
                if isinstance(usage, dict):
                    input_tokens = usage.get("prompt_tokens", usage.get("input_tokens", 0))
                    output_tokens = usage.get("completion_tokens", usage.get("output_tokens", 0))
                    usage_fields = {
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "prompt_cache_hit_tokens": usage.get("prompt_cache_hit_tokens"),
                        "prompt_cache_miss_tokens": usage.get("prompt_cache_miss_tokens"),
                    }
                    completion_details = usage.get("completion_tokens_details")
                    if isinstance(completion_details, dict):
                        usage_fields["reasoning_tokens"] = completion_details.get("reasoning_tokens")
                    for field, value in usage_fields.items():
                        if isinstance(value, int) and value >= 0:
                            request_usage[field] = value
                    if isinstance(input_tokens, int) and input_tokens >= 0:
                        self._input_tokens += input_tokens
                    if isinstance(output_tokens, int) and output_tokens >= 0:
                        self._output_tokens += output_tokens
                    self._prompt_cache_hit_tokens += request_usage.get("prompt_cache_hit_tokens", 0)
                    self._prompt_cache_miss_tokens += request_usage.get("prompt_cache_miss_tokens", 0)
                    self._reasoning_tokens += request_usage.get("reasoning_tokens", 0)
                self._last_usage = request_usage
                logger.info(
                    "provider_request_completed operation=%s attempt=%d input_tokens=%d output_tokens=%d",
                    schema_name, attempt + 1,
                    request_usage.get("input_tokens", 0), request_usage.get("output_tokens", 0),
                )
                return parsed
            except _ProviderDeadlineExceeded as exc:
                # A timed-out worker may still be unwinding an OS-level
                # connect call. Do not launch an overlapping automatic retry.
                raise ProviderRequestError(
                    "Provider 请求超过总时限",
                    provider=self.provider_name,
                    operation="chat.completions",
                ) from exc
            except ProviderResponseError as exc:
                log_boundary_failure(logger, "provider_response_invalid", exc,
                                     schema_name=schema_name, attempt=attempt + 1)
                if attempt < attempt_budget - 1:
                    attempt += 1
                    time.sleep(self.retry_delay_seconds)
                    continue
                raise
            except urllib.error.HTTPError as exc:
                failure_cause = exc
                if exc.code == 429:
                    retry_delay = max(
                        retry_delay,
                        _rate_limit_delay_seconds(exc.headers),
                    )
                failure = ProviderRequestError(
                    f"Provider HTTP 请求失败（status={exc.code}）",
                    provider=self.provider_name,
                    operation="chat.completions",
                    status_code=exc.code,
                )
                log_boundary_failure(logger, "provider_http_failed", failure,
                                     schema_name=schema_name, attempt=attempt + 1)
                # Preserve only sanitized provider diagnostics; never retain
                # request bodies or credentials in the typed error.
                provider_error_message = ""
                try:
                    body = getattr(exc, "_mapas_error_body", None)
                    if body is None:
                        body = getattr(exc, "_apaas_error_body", None)
                    if body is None:
                        body = json.loads(exc.read().decode("utf-8", errors="replace"))
                    error = body.get("error", {}) if isinstance(body, dict) else {}
                    if isinstance(error, dict):
                        provider_error_message = error.get("message") if isinstance(error.get("message"), str) else ""
                        failure.error_type = error.get("type") if isinstance(error.get("type"), str) else None
                        failure.error_code = error.get("code") if isinstance(error.get("code"), str) else None
                except Exception as diagnostics_exc:
                    log_boundary_failure(
                        logger,
                        "provider_error_diagnostics_parse_failed",
                        diagnostics_exc,
                        provider=self.provider_name,
                        status_code=exc.code,
                    )
                failure.request_id = exc.headers.get("x-request-id") or exc.headers.get("request-id")
                failure.retry_after = exc.headers.get("retry-after")
                if _is_context_window_overflow(
                    exc.code,
                    provider_error_message,
                    failure.error_type,
                    failure.error_code,
                ):
                    overflow = ContextWindowOverflowError(
                        "Provider request exceeded the model context window",
                        provider=self.provider_name,
                        operation="chat.completions",
                        status_code=exc.code,
                    )
                    overflow.error_type = failure.error_type
                    overflow.error_code = failure.error_code
                    overflow.request_id = failure.request_id
                    overflow.retry_after = failure.retry_after
                    raise overflow from exc
                # Retrying deterministic client/auth errors only adds latency,
                # cost and duplicate load. Retries, when configured, always
                # reuse this one DeepSeek endpoint and key; there is no API
                # rotation or provider fallback.
                if exc.code not in {
                    408, 429, 500, 502, 503, 504
                }:
                    raise failure from exc
            except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
                failure_cause = exc
                failure = ProviderRequestError(
                    "Provider 请求因网络或 I/O 错误失败",
                    provider=self.provider_name,
                    operation="chat.completions",
                )
                log_boundary_failure(logger, "provider_network_failed", failure,
                                     schema_name=schema_name, attempt=attempt + 1)

            if attempt < attempt_budget - 1:
                attempt += 1
                time.sleep(retry_delay)
            else:
                raise failure from failure_cause

        raise ProviderRequestError(
            "Provider 请求未执行",
            provider=self.provider_name,
            operation="chat.completions",
        )


def build_llm_client(config: MemoryConfig) -> LLMClientBase:
    """构造唯一的 DeepSeek V4 Flash 客户端；配置失败时显式报错。"""
    if not config.llm_api_key:
        raise ProviderConfigurationError(
            f"MEMORY_PROVIDER_MODE=REQUIRED 时必须配置 {config.llm_api_key_env}",
            provider=config.llm_provider_name,
            operation="configure",
        )
    return OpenAILLMClient(
        api_key=config.llm_api_key,
        api_base=config.llm_api_base,
        model_name=config.llm_model_name,
        request_timeout_seconds=config.llm_request_timeout_seconds,
        max_retries=config.llm_max_retries,
        retry_delay_seconds=config.llm_retry_delay_seconds,
        provider_name=config.llm_provider_name,
        request_interval_seconds=config.llm_request_interval_seconds,
        reasoning_effort=config.llm_reasoning_effort,
        max_completion_tokens=config.llm_max_completion_tokens,
    )


def _rate_limit_delay_seconds(headers: Any) -> float:
    """解析 provider 公开 reset header；有限上限避免无限阻塞。"""
    if headers is None:
        return 0.0
    for name in ("retry-after", "x-ratelimit-reset-tokens"):
        raw = headers.get(name)
        if not raw:
            continue
        try:
            if str(raw).endswith("s"):
                seconds = float(str(raw)[:-1])
            else:
                seconds = float(raw)
        except (TypeError, ValueError):
            continue
        return min(max(seconds, 0.0), 120.0)
    return 0.0


def _is_context_window_overflow(
    status_code: int,
    message: str,
    error_type: str | None,
    error_code: str | None,
) -> bool:
    """Classify only strong provider signals; do not turn generic 400s into compaction."""
    signals = " ".join(value or "" for value in (message, error_type, error_code)).casefold()
    # DeepSeek documents context failures through the error payload.  Do not
    # reinterpret a bare HTTP status (including 413) as a context overflow:
    # that would trigger compaction for an unrelated transport failure.
    return any(
        phrase in signals
        for phrase in (
            "context_length_exceeded",
            "context length exceeded",
            "maximum context",
            "context window",
            "too many tokens",
            "token limit",
            "prompt is too long",
            "input is too long",
            "exceeds the maximum",
        )
    )


# =====================================================================
#  System Prompt Templates (English instructions with Few-Shot Examples)
# =====================================================================

PROMPT_NEMORI_PARTITION = """You are an expert agentic conversation analyzer.
Preserve the language of each source user message in its cue and narrative.
Do not translate the user's statements merely because this instruction is in English.
Your task is to analyze multi-turn dialogue records, partition them into semantically coherent "Episodes", and generate third-person objective narrative summaries along with minimal topic cues.

### Rules
1. Maintain semantic integrity: Do not split questions and their direct answers into separate episodes.
2. Narrative Generation: Eliminate colloquial pronouns (e.g., replace "this", "it" with explicit entities).
3. Source authority: Durable narratives may contain only facts, preferences, constraints, commitments, or events stated by the User. AI/Assistant messages are context for resolving the User's meaning, never durable user facts themselves. Do not turn assistant acknowledgements, promises, advice, or confirmations into claims about the User.
4. Retention decision: Every episode must include `durability`: `DURABLE` only when the user-stated information is likely useful beyond the current conversation; `TRANSIENT` for one-off requests, temporary experiments, hypotheticals, unresolved considerations, or discussion the user says should not become a lasting preference; `UNCERTAIN` when the evidence is insufficient. The application will persist only `DURABLE` episodes.
5. Uncertainty: If the User did not state a durable fact, return an episode marked `TRANSIENT` or `UNCERTAIN` rather than inferring one from the assistant response.

### Few-Shot Example
Input Dialogue:
[1] User: I prefer using Python for microservices prototyping.
[2] AI: Understood. Note that Python's GIL can be a bottleneck for CPU-bound tasks.
[3] User: Right, so I want to isolate heavy computation to Go modules later.

Output JSON:
{
  "episodes": [
    {
      "message_indices": [1, 2, 3],
      "cue": "Python prototyping and Go computation isolation",
      "narrative": "The user prefers Python for microservice prototyping and intends to isolate CPU-heavy computational modules to Go to circumvent Python GIL limits.",
      "durability": "DURABLE"
    }
  ]
}
"""

PROMPT_NEMORI_PREDICT_AND_RESIDUE = """You are a Memory Prediction Error Auditor for a long-term AI Assistant.
Given existing long-term memories (S_in) and an incoming narrative, your goal is to evaluate if the input contains SUBSTANTIVE long-term cognitive value and extract the novel information residual (K_in).

### General Cognitive Criteria (CRITICAL - STRICTLY ENFORCED)
1. SUBSTANTIVE LONG-TERM VALUE: Information regarding User Identity, Long-term Preferences, Behavioral Constraints, Technical SOPs, or Non-trivial Substantive Events.
2. ZERO LONG-TERM VALUE (NOOP): Generic greetings, casual pleasantries, transient phatic expressions, short acknowledgements, single digits, option selections, brief clarifying questions (e.g. "why", "why?", "what", "how"), or conversational small talk possess ZERO long-term value. For these inputs, you MUST ALWAYS set `is_novel_info: false` and `prediction_error_residual: ""`.

### Few-Shot Examples

Example 1 (Substantive Novel Residual):
Existing Memories (S_in):
- User prefers lightweight Python backends.
Narrative: "User requested setting up gRPC endpoints in Python and emphasized strict rejection of REST APIs."
Output JSON:
{
  "predicted_content": "User will request standard Python backend setup.",
  "prediction_error_residual": "User specifically demands gRPC and strictly rejects REST APIs.",
  "is_novel_info": true,
  "novelty_score": 0.85
}

Example 2 (Zero Substantive Value / Greeting / Casual Talk):
Existing Memories (S_in):
- User prefers lightweight Python backends.
Narrative: "User greeted the assistant and asked if it can help today."
Output JSON:
{
  "predicted_content": "User initiates standard conversation.",
  "prediction_error_residual": "",
  "is_novel_info": false,
  "novelty_score": 0.0
}

Example 3 (Zero Substantive Value / Brief Question / "why" / Single Digit):
Existing Memories (S_in):
- User prefers lightweight Python backends.
Narrative: "User asked 'why' or entered a single character/digit choice."
Output JSON:
{
  "predicted_content": "User asked a brief transient question or entered an ambiguous choice.",
  "prediction_error_residual": "",
  "is_novel_info": false,
  "novelty_score": 0.0
}
"""

PROMPT_ABSTRACT_RISE = """You are a Cognitive Synthesis Engine.
Your task is to review multiple fine-grained episodic memory items, synthesize them, and abstract higher-level User Profiles, SOPs, or Cognitive Preferences (RISE operator).

### Rules
1. The output must summarize overarching principles or patterns.
2. The fine-grained source items will be preserved and linked to this new abstract node via DERIVED_TO relationships.

### Few-Shot Example
Input Episodic Memory Items:
1. "User asked to remove Docker dependencies from the test script."
2. "User refused to install Redis for caching in local demo."
3. "User expressed strong preference for zero external service dependencies in prototyping."

Output JSON:
{
  "abstract_insight": "User consistently demands zero external service dependencies (no Docker/Redis) for local development and prototyping.",
  "memory_type": "PREFERENCE_RULES",
  "granularity": "HIGH_ABSTRACT",
  "importance_score": 0.90
}
"""
