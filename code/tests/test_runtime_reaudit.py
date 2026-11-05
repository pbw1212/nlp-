"""Operational fault injection for host parsing, tool receipts and diagnostics."""

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts.personal_agent_app_server import JsonlAppServer
from src.memory.app.mcp import McpManager
from src.memory.collaboration.models import canonical, digest
from test_app_server_protocol import _app
from test_task_lifecycle import loop_app


def _mcp_fixture(tmp_path, script, *arguments):
    from src.memory.agent.tooling import ToolRegistry
    from src.memory.app.workspace import WorkspaceManager

    registry = ToolRegistry()
    path = tmp_path / "mcp.json"
    path.write_text(
        json.dumps(
            {
                "servers": [
                    {
                        "name": "strict",
                        "command": [sys.executable, "-u", "-c", script, *arguments],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    workspace = WorkspaceManager(str(tmp_path / "state"), default_root=str(tmp_path))
    return McpManager(str(path), workspace, registry), registry


@pytest.mark.parametrize("initialization", ["denied", "wrong_version"])
def test_mcp_failed_initialization_never_registers_tools(tmp_path, initialization):
    script = """import json,sys
for line in sys.stdin:
 request=json.loads(line)
 if request.get('id')==0:
  reply={'error':{'code':-32602,'message':'denied'}} if sys.argv[1]=='denied' else {'result':{'protocolVersion':'unsupported'}}
  print(json.dumps({'jsonrpc':'2.0','id':0,**reply}),flush=True)
 elif request.get('method')=='tools/list':
  print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':{'tools':[{'name':'echo','inputSchema':{'type':'object'}}]}}),flush=True)
"""
    manager, registry = _mcp_fixture(tmp_path, script, initialization)
    assert manager.refresh()[0]["status"] == "FAILED"
    assert not registry.list_specs()


def test_mcp_waits_for_init_response_before_initialized_and_tools(tmp_path):
    script = """import json,sys,threading,queue
q=queue.Queue()
def read():
 for line in sys.stdin: q.put(json.loads(line))
threading.Thread(target=read,daemon=True).start()
assert q.get(timeout=3)['method']=='initialize'
try:
 q.get(timeout=0.15)
 print(json.dumps({'jsonrpc':'2.0','id':1,'error':{'code':-32000,'message':'premature operation'}}),flush=True)
except queue.Empty:
 print(json.dumps({'jsonrpc':'2.0','id':0,'result':{'protocolVersion':'2024-11-05'}}),flush=True)
 assert q.get(timeout=3)['method']=='notifications/initialized'
 request=q.get(timeout=3)
 assert request['method']=='tools/list'
 print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':{'tools':[{'name':'echo','inputSchema':{'type':'object'}}]}}),flush=True)
"""
    manager, registry = _mcp_fixture(tmp_path, script)
    assert manager.refresh()[0]["status"] == "READY"
    assert registry.get_spec("mcp.strict.echo") is not None


def test_mcp_timeout_also_covers_a_child_that_stops_reading_stdin(
    tmp_path, monkeypatch
):
    import time

    from src.memory.app import mcp
    from src.memory.app.workspace import WorkspaceManager

    script = """import json,sys,time
request=json.loads(sys.stdin.readline())
print(json.dumps({'jsonrpc':'2.0','id':0,'result':{'protocolVersion':'2024-11-05'}}),flush=True)
time.sleep(4)
"""
    workspace = WorkspaceManager(str(tmp_path / "state"), default_root=str(tmp_path))
    client = mcp._McpStdioClient(
        mcp.McpServerConfig(name="stall", command=(sys.executable, "-u", "-c", script)),
        workspace,
    )
    monkeypatch.setattr(mcp, "MCP_REQUEST_TIMEOUT_SECONDS", 0.5)
    started = time.monotonic()
    with pytest.raises((TimeoutError, OSError)):
        client.request("tools/call", {"arguments": {"text": "x" * 200000}})
    assert time.monotonic() - started < 2.5


@pytest.mark.parametrize(
    "entrypoint", ["personal_agent.py", "personal_agent_app_server.py"]
)
def test_invalid_agent_home_reports_startup_error_without_a_traceback(
    tmp_path, entrypoint
):
    root = Path(__file__).resolve().parents[2]
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("keep", encoding="utf-8")
    environment = os.environ.copy()
    environment["AGENT_DATA_DIR"] = str(blocked)
    environment["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [sys.executable, str(root / "code/scripts" / entrypoint)],
        cwd=root,
        env=environment,
        input="",
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 1
    assert "Traceback" not in result.stdout + result.stderr
    assert blocked.read_text(encoding="utf-8") == "keep"
    if entrypoint == "personal_agent_app_server.py":
        assert json.loads(result.stdout)["error"]["code"] == -32000
    else:
        assert "启动失败" in result.stdout


@pytest.mark.parametrize("kind", ["depth", "integer"])
def test_bad_json_scalar_or_depth_cannot_kill_jsonl_host(tmp_path, kind):
    app = _app(tmp_path)
    out = io.StringIO()
    server = JsonlAppServer(app, out)
    old_depth = sys.getrecursionlimit()
    old_integer_limit = (
        sys.get_int_max_str_digits() if hasattr(sys, "get_int_max_str_digits") else None
    )
    try:
        if kind == "depth":
            sys.setrecursionlimit(1000)
            wire = "[" * 4000 + "0" + "]" * 4000
        else:
            if old_integer_limit is None:
                pytest.skip("Python has no JSON integer conversion limit")
            sys.set_int_max_str_digits(640)
            wire = "9" * 5000
        server.serve(io.StringIO(wire + '\n{"id":2,"method":"initialize"}\n'))
    finally:
        sys.setrecursionlimit(old_depth)
        if old_integer_limit is not None:
            sys.set_int_max_str_digits(old_integer_limit)
    messages = [json.loads(line) for line in out.getvalue().splitlines()]
    assert messages[0]["error"]["code"] == -32700
    assert messages[-1]["id"] == 2 and "result" in messages[-1]


@pytest.mark.parametrize("params", [[], False, 0, ""])
def test_invalid_falsey_params_are_not_silently_accepted(tmp_path, params):
    app = _app(tmp_path)
    try:
        out = io.StringIO()
        JsonlAppServer(app, out).handle(
            {"id": 1, "method": "initialize", "params": params}
        )
        assert json.loads(out.getvalue())["error"]["code"] == -32600
    finally:
        app.close()


@pytest.mark.parametrize(
    "configuration",
    ['{"servers":[{"name":"incomplete"}]}', "{broken", "[" * 4000 + "0" + "]" * 4000],
    ids=["shape", "syntax", "depth"],
)
def test_mcp_configuration_error_survives_startup_refresh(tmp_path, configuration):
    path = tmp_path / "mcp.json"
    path.write_text(configuration, encoding="utf-8")
    manager = McpManager(str(path), SimpleNamespace(), SimpleNamespace())
    assert manager.describe()["servers"][0]["status"] == "CONFIGURATION_ERROR"
    statuses = manager.refresh()
    assert statuses and statuses[0]["status"] == "CONFIGURATION_ERROR"
    assert manager.describe()["servers"] == statuses


@pytest.mark.parametrize(
    "fault",
    [
        "call_id",
        "tool_name",
        "arguments",
        "oversized_observation",
        "unserializable_observation",
        "status",
    ],
)
def test_bad_read_only_tool_result_becomes_a_terminal_failure_not_a_crash(
    tmp_path, fault
):
    from src.memory.agent.journal import AgentExecutionJournal
    from src.memory.agent.loop import AgentLoopController
    from src.memory.agent.tooling import ToolEffect, ToolRegistry, ToolSpec
    from src.memory.runtime.models import ToolCallResult, ToolCallStatus
    from src.memory.runtime.task_router import UNBOUND_TASK_ID

    class BadExecutor:
        def execute(self, request):
            return ToolCallResult(
                tool_call_id="wrong-call"
                if fault == "call_id"
                else request.tool_call_id,
                tool_name="wrong-tool" if fault == "tool_name" else request.tool_name,
                arguments={"wrong": True}
                if fault == "arguments"
                else request.arguments,
                status="SUCCESS" if fault == "status" else ToolCallStatus.SUCCESS,
                result_content="untrusted output",
                source_observations=({"bad": "x" * 17000},)
                if fault == "oversized_observation"
                else (
                    ({"bad": b"bytes"},)
                    if fault == "unserializable_observation"
                    else ()
                ),
            )

    journal = AgentExecutionJournal(str(tmp_path / "journal.db"))
    journal.register_run("run", "turn", UNBOUND_TASK_ID)
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="faulty",
            description="fault injection",
            parameters_schema={},
            effect=ToolEffect.READ_ONLY,
        ),
        BadExecutor(),
    )
    loop = AgentLoopController(object(), journal, registry, object())
    context = SimpleNamespace(task_id=UNBOUND_TASK_ID, turn_id="turn", warnings=())
    result, observation, _ = loop._execute_tool_dispatch(
        context, "run", 0, "faulty", {}
    )
    assert result.status.value == "FAILED"
    assert observation.status == "FAILED"
    with journal._connection() as db:
        row = db.execute(
            "SELECT status,result_json FROM tool_execution_receipts"
        ).fetchone()
    assert row[0] == "FAILED"
    payload = json.loads(row[1])
    assert payload["result_content"] == "" and payload["error_message"]


def test_lifecycle_diagnostics_show_newest_jobs_in_a_long_session(
    tmp_path, monkeypatch
):
    app = loop_app(tmp_path, monkeypatch)
    try:
        controller = app.collaboration.lifecycle
        with app.coordination_repository._write() as db:
            for number in range(105):
                payload = {
                    "task_id": "task",
                    "session_id": app.active_session_id,
                    "source_run_id": "source",
                    "kind": "ASSESS",
                }
                db.execute(
                    "INSERT INTO lifecycle_jobs VALUES(?,?,?,?,?,'SUCCEEDED',?,?,NULL,NULL,NULL,?)",
                    (
                        str(number),
                        "task",
                        app.active_session_id,
                        "source",
                        "ASSESS",
                        canonical(payload),
                        digest(payload),
                        number,
                    ),
                )
        jobs = controller.jobs(app.active_session_id)
        assert len(jobs) == 100
        assert jobs[0]["job_id"] == "5" and jobs[-1]["job_id"] == "104"
    finally:
        app.close()


def test_lifecycle_task_filter_does_not_return_another_tasks_jobs(
    tmp_path, monkeypatch
):
    app = loop_app(tmp_path, monkeypatch)
    try:
        controller = app.collaboration.lifecycle
        with app.coordination_repository._write() as db:
            for task in ("first", "second"):
                payload = {
                    "task_id": task,
                    "session_id": app.active_session_id,
                    "source_run_id": "source",
                    "kind": "ASSESS",
                }
                db.execute(
                    "INSERT INTO lifecycle_jobs VALUES(?,?,?,?,?,'SUCCEEDED',?,?,NULL,NULL,NULL,?)",
                    (
                        task,
                        task,
                        app.active_session_id,
                        "source",
                        "ASSESS",
                        canonical(payload),
                        digest(payload),
                        1,
                    ),
                )
        jobs = controller.status(app.active_session_id, "second")["jobs"]
        assert [row["task_id"] for row in jobs] == ["second"]
    finally:
        app.close()
