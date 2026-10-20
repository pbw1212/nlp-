import json
import sys

from src.memory.agent.tooling import ToolExecutionRequest, ToolEffect
from src.memory.app.mcp import McpManager
from src.memory.app.tools import build_product_tool_registry
from src.memory.app.workspace import WorkspaceManager
from src.memory.runtime.models import ToolCallStatus


def _request(name, arguments):
    return ToolExecutionRequest(
        tool_call_id="call_mcp",
        tool_name=name,
        arguments=arguments,
        idempotency_key="idemp_mcp",
        agent_run_id="run_mcp",
        agent_step_id="step_mcp",
    )


def test_mcp_manager_discovers_namespaced_tools_and_calls_stdio_server(tmp_path):
    server_code = (
        "import json,sys\n"
        "for line in sys.stdin:\n"
        " request=json.loads(line)\n"
        " if request.get('id') == 0:\n"
        "  print(json.dumps({'jsonrpc':'2.0','id':0,'result':{'protocolVersion':'2024-11-05'}}), flush=True)\n"
        " elif request.get('method') == 'tools/list':\n"
        "  print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':{'tools':[{'name':'echo','description':'echo text','inputSchema':{'type':'object','properties':{'text':{'type':'string'}},'required':['text']}}]}}), flush=True)\n"
        " elif request.get('method') == 'tools/call':\n"
        "  text=request.get('params',{}).get('arguments',{}).get('text','')\n"
        "  print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':{'content':[{'type':'text','text':'echo:'+text}]}}), flush=True)\n"
    )
    root = tmp_path / "workspace"
    root.mkdir()
    manager_workspace = WorkspaceManager(str(tmp_path / "agent"), default_root=str(root))
    registry = build_product_tool_registry(workspace=manager_workspace)
    config_path = tmp_path / "mcp.json"
    config_path.write_text(json.dumps({
        "servers": [{
            "name": "demo",
            "command": [sys.executable, "-u", "-c", server_code],
            "read_only_tools": ["echo"],
        }]
    }), encoding="utf-8")

    manager = McpManager(str(config_path), manager_workspace, registry)
    statuses = manager.refresh()

    assert statuses[0]["status"] == "READY"
    assert registry.get_spec("mcp.demo.echo").effect is ToolEffect.READ_ONLY
    result = registry.get_executor("mcp.demo.echo").execute(_request("mcp.demo.echo", {"text": "hello"}))
    assert result.status is ToolCallStatus.SUCCESS
    assert result.result_content == "echo:hello"
    manager.close()


def test_mcp_tools_default_to_side_effect_and_invalid_config_is_reported(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    workspace = WorkspaceManager(str(tmp_path / "agent"), default_root=str(root))
    registry = build_product_tool_registry(workspace=workspace)
    config_path = tmp_path / "mcp.json"
    config_path.write_text(json.dumps({"servers": [{"name": "bad"}]}), encoding="utf-8")

    manager = McpManager(str(config_path), workspace, registry)

    assert manager.describe()["servers"][0]["status"] == "CONFIGURATION_ERROR"
