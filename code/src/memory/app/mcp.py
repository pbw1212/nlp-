"""Explicit MCP stdio extension boundary for the Personal Agent.

MCP servers are user-configured extensions, not model-controlled plugins. A
server is discovered from the global Agent home, its tools are namespaced, and
every call is dispatched through the normal ToolRegistry. The implementation
uses a short-lived stdio process per discovery/call so crashes remain bounded.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from src.memory.agent.tooling import (
    IdempotencySupport,
    ToolEffect,
    ToolExecutionRequest,
    ToolExecutor,
    ToolRegistry,
    ToolSpec,
)
from src.memory.app.workspace import WorkspaceManager, WorkspaceScopeError
from src.memory.runtime.models import ToolCallResult, ToolCallStatus


MCP_PROTOCOL_VERSION = "2024-11-05"
MCP_MAX_RESPONSE_CHARS = 20_000
MCP_REQUEST_TIMEOUT_SECONDS = 15.0


class McpConfigurationError(ValueError):
    """Raised for invalid user-authored MCP configuration."""


@dataclass(frozen=True)
class McpServerConfig:
    name: str
    command: Tuple[str, ...]
    cwd: Optional[str] = None
    env: Tuple[Tuple[str, str], ...] = ()
    read_only_tools: Tuple[str, ...] = ()
    enabled: bool = True

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "McpServerConfig":
        if not isinstance(raw, Mapping):
            raise McpConfigurationError("MCP server 配置必须是对象")
        name = raw.get("name")
        command = raw.get("command")
        if not isinstance(name, str) or not name.strip():
            raise McpConfigurationError("MCP server name 必须是非空字符串")
        if not isinstance(command, list) or not command or any(not isinstance(item, str) or not item for item in command):
            raise McpConfigurationError("MCP server command 必须是非空字符串数组")
        cwd = raw.get("cwd")
        if cwd is not None and (not isinstance(cwd, str) or not cwd.strip()):
            raise McpConfigurationError("MCP server cwd 必须是字符串")
        env_raw = raw.get("env", {})
        if not isinstance(env_raw, Mapping) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in env_raw.items()):
            raise McpConfigurationError("MCP server env 必须是字符串键值对象")
        read_only = raw.get("read_only_tools", [])
        if not isinstance(read_only, list) or any(not isinstance(item, str) or not item.strip() for item in read_only):
            raise McpConfigurationError("read_only_tools 必须是字符串数组")
        enabled = raw.get("enabled", True)
        if not isinstance(enabled, bool):
            raise McpConfigurationError("MCP server enabled 必须是布尔值")
        return cls(
            name=name.strip(),
            command=tuple(command),
            cwd=cwd.strip() if isinstance(cwd, str) else None,
            env=tuple(sorted((str(k), str(v)) for k, v in env_raw.items())),
            read_only_tools=tuple(read_only),
            enabled=enabled,
        )

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "command": list(self.command),
            "cwd": self.cwd,
            "env_keys": [key for key, _ in self.env],
            "read_only_tools": list(self.read_only_tools),
            "enabled": self.enabled,
        }


class _McpStdioClient:
    """One bounded MCP JSONL exchange over a short-lived child process."""

    def __init__(self, server: McpServerConfig, workspace: WorkspaceManager) -> None:
        self.server = server
        self.workspace = workspace

    def _cwd(self) -> str:
        try:
            return str(self.workspace.resolve_path(self.server.cwd or ".", must_exist=True, directory=True))
        except WorkspaceScopeError as exc:
            raise McpConfigurationError(str(exc)) from exc

    def request(self, method: str, params: Mapping[str, Any], request_id: int = 1) -> Dict[str, Any]:
        environment = os.environ.copy()
        environment.update(dict(self.server.env))
        process = subprocess.Popen(
            list(self.server.command),
            cwd=self._cwd(),
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        output_queue: "queue.Queue[Optional[str]]" = queue.Queue()

        def read_lines() -> None:
            assert process.stdout is not None
            try:
                for line in process.stdout:
                    output_queue.put(line)
            finally:
                output_queue.put(None)

        reader = threading.Thread(target=read_lines, name=f"mcp-{self.server.name}-reader", daemon=True)
        reader.start()
        try:
            if process.stdin is None:
                raise RuntimeError("MCP stdio stdin unavailable")
            messages = [
                {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "mapas-agent", "version": "1"},
                }},
                {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
                {"jsonrpc": "2.0", "id": request_id, "method": method, "params": dict(params)},
            ]
            for message in messages:
                process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            process.stdin.flush()
            deadline = time.monotonic() + MCP_REQUEST_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                try:
                    line = output_queue.get(timeout=max(0.05, deadline - time.monotonic()))
                except queue.Empty:
                    break
                if line is None:
                    break
                try:
                    response = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(response, dict) and response.get("id") == request_id:
                    return response
            raise TimeoutError(f"MCP server '{self.server.name}' 未在规定时间内返回 {method}")
        finally:
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
            reader.join(timeout=1.0)

    def list_tools(self) -> List[Dict[str, Any]]:
        response = self.request("tools/list", {}, request_id=1)
        result = response.get("result") if isinstance(response, dict) else None
        tools = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(tools, list):
            raise ValueError(f"MCP server '{self.server.name}' 返回的 tools/list 无效")
        return [item for item in tools if isinstance(item, dict)]

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Tuple[ToolCallStatus, str, str]:
        response = self.request("tools/call", {"name": tool_name, "arguments": arguments}, request_id=1)
        if "error" in response:
            error = response.get("error")
            message = error.get("message", "MCP tool call failed") if isinstance(error, dict) else "MCP tool call failed"
            return ToolCallStatus.FAILED, "", str(message)[:2000]
        result = response.get("result")
        if not isinstance(result, dict):
            return ToolCallStatus.FAILED, "", "MCP tool returned no result"
        parts = result.get("content", [])
        text_parts = [str(item.get("text", "")) for item in parts if isinstance(item, dict) and item.get("type") == "text"] if isinstance(parts, list) else []
        text = "\n".join(item for item in text_parts if item)
        is_error = bool(result.get("isError"))
        return (
            ToolCallStatus.FAILED if is_error else ToolCallStatus.SUCCESS,
            "" if is_error else text[:MCP_MAX_RESPONSE_CHARS],
            text[:2000] if is_error else "",
        )


class _McpToolExecutor(ToolExecutor):
    def __init__(self, client: _McpStdioClient, server_name: str, tool_name: str) -> None:
        self.client = client
        self.server_name = server_name
        self.tool_name = tool_name

    def execute(self, request: ToolExecutionRequest) -> ToolCallResult:
        try:
            status, content, error = self.client.call_tool(self.tool_name, request.arguments)
            return ToolCallResult(
                tool_call_id=request.tool_call_id,
                tool_name=request.tool_name,
                arguments=request.arguments,
                status=status,
                result_content=content,
                error_message=error,
            )
        except Exception as exc:
            return ToolCallResult(
                tool_call_id=request.tool_call_id,
                tool_name=request.tool_name,
                arguments=request.arguments,
                status=ToolCallStatus.FAILED,
                error_message=f"MCP server '{self.server_name}' 调用失败 ({type(exc).__name__})",
            )


class McpManager:
    """Loads explicit MCP declarations and registers namespaced tools."""

    def __init__(self, config_path: str, workspace: WorkspaceManager, registry: ToolRegistry) -> None:
        self.config_path = Path(config_path).resolve()
        self.workspace = workspace
        self.registry = registry
        self.servers: List[McpServerConfig] = []
        self.statuses: List[Dict[str, Any]] = []
        self._load_config()

    def _load_config(self) -> None:
        if not self.config_path.exists():
            return
        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
            rows = raw.get("servers") if isinstance(raw, dict) else None
            if not isinstance(rows, list):
                raise McpConfigurationError("MCP 配置根对象必须包含 servers 数组")
            self.servers = [McpServerConfig.from_mapping(item) for item in rows]
        except (OSError, UnicodeError, json.JSONDecodeError, McpConfigurationError) as exc:
            self.statuses = [{"config_path": str(self.config_path), "status": "CONFIGURATION_ERROR", "error_type": type(exc).__name__}]

    @staticmethod
    def _qualified_name(server_name: str, tool_name: str) -> str:
        return f"mcp.{server_name}.{tool_name}"

    def refresh(self) -> List[Dict[str, Any]]:
        self.statuses = []
        for server in self.servers:
            if not server.enabled:
                self.statuses.append({**server.describe(), "status": "DISABLED"})
                continue
            client = _McpStdioClient(server, self.workspace)
            try:
                discovered = client.list_tools()
                registered = 0
                for item in discovered:
                    tool_name = item.get("name")
                    if not isinstance(tool_name, str) or not tool_name.strip():
                        continue
                    schema = item.get("inputSchema")
                    if not isinstance(schema, dict):
                        schema = {"type": "object", "properties": {}}
                    qualified = self._qualified_name(server.name, tool_name)
                    if self.registry.get_spec(qualified) is not None:
                        continue
                    effect = ToolEffect.READ_ONLY if tool_name in server.read_only_tools else ToolEffect.SIDE_EFFECT
                    self.registry.register(
                        ToolSpec(
                            name=qualified,
                            description=f"[{server.name}] {str(item.get('description') or tool_name)}",
                            parameters_schema=schema,
                            effect=effect,
                            idempotency_support=IdempotencySupport.NONE,
                        ),
                        _McpToolExecutor(client, server.name, tool_name),
                    )
                    registered += 1
                self.statuses.append({**server.describe(), "status": "READY", "tools_discovered": len(discovered), "tools_registered": registered})
            except Exception as exc:
                self.statuses.append({**server.describe(), "status": "FAILED", "error_type": type(exc).__name__})
        return [dict(item) for item in self.statuses]

    def describe(self) -> Dict[str, Any]:
        return {"config_path": str(self.config_path), "servers": [dict(item) for item in self.statuses]}

    def close(self) -> None:
        return None
