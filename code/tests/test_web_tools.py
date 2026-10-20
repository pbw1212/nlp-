from __future__ import annotations

import json
import email.message
import zlib

from src.memory.agent.tooling import ToolExecutionRequest
from src.memory.app import tools as product_tools
from src.memory.app.tools import FileWriteExecutor, WeatherExecutor, WebFetchExecutor, WebSearchExecutor, build_product_tool_registry
from src.memory.runtime.models import ToolCallStatus


def _request(name: str, arguments: dict) -> ToolExecutionRequest:
    return ToolExecutionRequest(
        tool_call_id=f"call-{name}",
        tool_name=name,
        arguments=arguments,
        idempotency_key=f"idemp-{name}",
        agent_run_id="run-web-test",
        agent_step_id=f"step-{name}",
    )


def test_product_registry_exposes_search_and_fetch_tools():
    registry = build_product_tool_registry()

    assert registry.get_spec("web_search") is not None
    assert registry.get_spec("web_fetch") is not None
    valid, message = registry.validate_arguments("web_search", {"query": "agent tools"})
    assert valid, message
    invalid, _ = registry.validate_arguments("web_fetch", {})
    assert invalid is False
    assert registry.get_spec("weather") is not None
    valid, message = registry.validate_arguments("weather", {"location": "上海"})
    assert valid, message


def test_web_search_normalizes_bounded_results(monkeypatch):
    html = """
    <a class='result__a' href='//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fdoc'>A title</a>
    <div class='result__snippet'>A short summary.</div>
    """
    monkeypatch.setattr(product_tools, "_open_web_url", lambda *args, **kwargs: ("url", "text/html", False, html))

    result = WebSearchExecutor().execute(_request("web_search", {"query": "agent tools"}))

    assert result.status is ToolCallStatus.SUCCESS
    payload = json.loads(result.result_content)
    assert payload["results"][0]["url"] == "https://example.com/doc"
    assert payload["results"][0]["snippet"] == "A short summary."
    assert payload["schema_version"] == "external_observation.v1"
    assert payload["source"] == "DuckDuckGo"
    assert payload["fetched_at"]
    assert payload["results"][0]["citation"]["url"] == "https://example.com/doc"


def test_web_search_result_limit_never_breaks_json(monkeypatch):
    entries = []
    for index in range(5):
        entries.append(
            f"<a class='result__a' href='https://example.com/{index}/{'x' * 900}'>Title {'T' * 290}</a>"
            f"<div class='result__snippet'>{'Summary ' * 100}</div>"
        )
    monkeypatch.setattr(product_tools, "_open_web_url", lambda *args, **kwargs: ("url", "text/html", False, "".join(entries)))

    result = WebSearchExecutor().execute(_request("web_search", {"query": "bounded results"}))
    payload = json.loads(result.result_content)

    assert result.status is ToolCallStatus.SUCCESS
    assert len(result.result_content) <= product_tools._WEB_MAX_RESULT_CHARS
    assert payload["results"]
    assert payload["results_truncated"] is True


def test_web_fetch_extracts_text_and_rejects_local_targets(monkeypatch):
    page = "<html><title>Example</title><script>ignore()</script><body>Hello <b>world</b></body></html>"
    monkeypatch.setattr(product_tools, "_open_web_url", lambda *args, **kwargs: ("https://example.com", "text/html", False, page))

    result = WebFetchExecutor().execute(_request("web_fetch", {"url": "https://example.com"}))
    assert result.status is ToolCallStatus.SUCCESS
    payload = json.loads(result.result_content)
    assert payload["title"] == "Example"
    assert payload["content"] == "Hello world"
    assert payload["schema_version"] == "external_observation.v1"
    assert payload["source_url"] == "https://example.com"
    assert payload["fetched_at"]

    monkeypatch.undo()
    rejected = WebFetchExecutor().execute(_request("web_fetch", {"url": "http://localhost:8080/admin"}))
    assert rejected.status is ToolCallStatus.FAILED
    assert "网页抓取失败" in rejected.error_message


def test_web_target_keeps_public_ipv4_when_dns_also_returns_transition_ipv6(monkeypatch):
    mixed_dns = [
        (product_tools.socket.AF_INET6, product_tools.socket.SOCK_STREAM, 6, "", ("2001::b93c:d824", 443, 0, 0)),
        (product_tools.socket.AF_INET, product_tools.socket.SOCK_STREAM, 6, "", ("184.173.136.86", 443)),
    ]
    monkeypatch.setattr(product_tools.socket, "getaddrinfo", lambda *_args, **_kwargs: mixed_dns)

    hostname, port, addresses = product_tools._validated_web_target("https://lite.duckduckgo.com/lite/")

    assert hostname == "lite.duckduckgo.com"
    assert port == 443
    assert [entry[4][0] for entry in addresses] == ["184.173.136.86"]


def test_pinned_connection_never_uses_rejected_dns_address(monkeypatch):
    mixed_dns = [
        (product_tools.socket.AF_INET6, product_tools.socket.SOCK_STREAM, 6, "", ("::1", 443, 0, 0)),
        (product_tools.socket.AF_INET, product_tools.socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
    ]
    connected = []

    class FakeSocket:
        def settimeout(self, _timeout):
            return None

        def bind(self, _source):
            return None

        def connect(self, sockaddr):
            connected.append(sockaddr)

        def close(self):
            return None

    monkeypatch.setattr(product_tools.socket, "getaddrinfo", lambda *_args, **_kwargs: mixed_dns)
    monkeypatch.setattr(product_tools.socket, "socket", lambda *_args, **_kwargs: FakeSocket())

    product_tools._connect_public_host("example.com", 443, 5.0)

    assert connected == [("93.184.216.34", 443)]


def test_https_proxy_tunnels_to_validated_ip_but_keeps_hostname_for_tls(monkeypatch):
    connected = []
    tunneled = []
    wrapped = []

    class FakeProxySocket:
        pass

    class FakeContext:
        def wrap_socket(self, sock, *, server_hostname):
            wrapped.append((sock, server_hostname))
            return sock

    monkeypatch.setattr(
        product_tools,
        "_connect_configured_proxy",
        lambda host, port, timeout, source: connected.append((host, port)) or FakeProxySocket(),
    )
    monkeypatch.setattr(
        product_tools._PinnedProxyHTTPSConnection,
        "_tunnel",
        lambda self: tunneled.append((self._tunnel_host, self._tunnel_port)),
    )
    connection = product_tools._PinnedProxyHTTPSConnection(
        "127.0.0.1:7897",
        target_hostname="lite.duckduckgo.com",
        target_port=443,
        target_address="184.173.136.86",
        timeout=5.0,
        context=FakeContext(),
    )
    connection.set_tunnel("lite.duckduckgo.com", 443)

    connection.connect()

    assert connected == [("127.0.0.1", 7897)]
    assert tunneled == [("184.173.136.86", 443)]
    assert wrapped == [(connection.sock, "lite.duckduckgo.com")]


def test_web_fetch_decodes_http_compression_before_extracting_text(monkeypatch):
    page = b"<html><title>Compressed</title><body>Readable page text</body></html>"
    headers = email.message.Message()
    headers["Content-Type"] = "text/html; charset=utf-8"
    headers["Content-Encoding"] = "gzip"

    class FakeResponse:
        def __init__(self):
            self.headers = headers

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def geturl(self):
            return "https://example.com/page"

        def read(self, _limit):
            compressor = zlib.compressobj(wbits=zlib.MAX_WBITS | 16)
            return compressor.compress(page) + compressor.flush()

    class FakeOpener:
        def open(self, _request, timeout):
            assert timeout == product_tools._WEB_TIMEOUT_SECONDS
            return FakeResponse()

    monkeypatch.setattr(product_tools, "build_opener", lambda *_args, **_kwargs: FakeOpener())

    result = WebFetchExecutor().execute(_request("web_fetch", {"url": "https://example.com/page"}))

    assert result.status is ToolCallStatus.SUCCESS
    payload = json.loads(result.result_content)
    assert payload["title"] == "Compressed"
    assert payload["content"] == "Readable page text"


def test_web_fetch_large_page_returns_valid_bounded_json(monkeypatch):
    page = f"<html><title>Large page</title><body>{'research evidence ' * 2000}</body></html>"
    monkeypatch.setattr(
        product_tools,
        "_open_web_url",
        lambda *args, **kwargs: ("https://example.com/large", "text/html", False, page),
    )

    result = WebFetchExecutor().execute(_request("web_fetch", {"url": "https://example.com/large"}))
    payload = json.loads(result.result_content)

    assert result.status is ToolCallStatus.SUCCESS
    assert len(result.result_content) <= product_tools._WEB_MAX_RESULT_CHARS
    assert payload["title"] == "Large page"
    assert payload["content"]
    assert payload["truncated"] is True


def test_weather_combines_geocoding_and_forecast_with_source(monkeypatch):
    requested_urls = []
    responses = iter([
        ("https://geocoding-api.open-meteo.com", "application/json", False,
         json.dumps({"results": [{"name": "上海", "country": "中国", "latitude": 31.2, "longitude": 121.5}]})),
        ("https://api.open-meteo.com", "application/json", False,
         json.dumps({
             "timezone": "Asia/Shanghai",
             "current": {"temperature_2m": 25, "weather_code": 1},
             "daily": {
                 "time": ["2026-08-21"], "weather_code": [1],
                 "temperature_2m_max": [30], "temperature_2m_min": [24],
                 "precipitation_probability_max": [20],
             },
         })),
    ])
    def fake_open(url, *args, **kwargs):
        requested_urls.append(url)
        return next(responses)

    monkeypatch.setattr(product_tools, "_open_web_url", fake_open)

    result = WeatherExecutor().execute(_request("weather", {"location": "上海"}))

    assert result.status is ToolCallStatus.SUCCESS
    payload = json.loads(result.result_content)
    assert payload["location"] == "上海"
    assert payload["source"] == "Open-Meteo"
    assert payload["schema_version"] == "external_observation.v1"
    assert len(payload["source_urls"]) == 2
    assert payload["current"]["weather_description"] == "大致晴"
    assert payload["daily"][0]["temperature_max"] == 30
    assert payload["requested_forecast_days"] == 3
    assert "forecast_days=3" in requested_urls[1]


def test_weather_fetches_two_calendar_days_when_one_is_requested(monkeypatch):
    requested_urls = []
    responses = iter([
        ("geo", "application/json", False,
         json.dumps({"results": [{"name": "天津", "country": "中国", "latitude": 39.1, "longitude": 117.2}]})),
        ("forecast", "application/json", False,
         json.dumps({"timezone": "Asia/Shanghai", "current": {}, "daily": {"time": ["2026-08-26", "2026-08-27"]}})),
    ])

    def fake_open(url, *args, **kwargs):
        requested_urls.append(url)
        return next(responses)

    monkeypatch.setattr(product_tools, "_open_web_url", fake_open)
    result = WeatherExecutor().execute(_request("weather", {"location": "天津", "forecast_days": 1}))

    assert result.status is ToolCallStatus.SUCCESS
    payload = json.loads(result.result_content)
    assert payload["requested_forecast_days"] == 1
    assert [item["date"] for item in payload["daily"]] == ["2026-08-26", "2026-08-27"]
    assert "forecast_days=2" in requested_urls[1]


def test_file_write_executor_is_scoped_and_atomic(tmp_path):
    executor = FileWriteExecutor(str(tmp_path))
    result = executor.execute(_request("create_file", {"filename": "hello.txt", "content": "你好"}))

    assert result.status is ToolCallStatus.SUCCESS
    assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == "你好"
    rejected = executor.execute(_request("create_file", {"filename": "../escape.txt", "content": "bad"}))
    assert rejected.status is ToolCallStatus.FAILED
    assert not (tmp_path.parent / "escape.txt").exists()
