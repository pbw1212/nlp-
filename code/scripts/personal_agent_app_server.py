#!/usr/bin/env python3
"""MAPAS JSONL host protocol.

The protocol borrows the useful Codex app-server boundary: a client
initializes once, addresses durable conversations as threads, starts turns,
and replays structured notifications from a sequence cursor.  It is a thin
adapter over ``PersonalAgentApp``; it does not create a second agent or state
store.

Usage:
    python code/scripts/personal_agent_app_server.py --data-dir ~/.agent_data

Each input line is one JSON-RPC-shaped request. Responses and event
notifications are written as one JSON object per line and flushed immediately.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any, Dict, Iterable, Mapping, Optional, TextIO


CODE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if CODE_ROOT not in sys.path:
    sys.path.insert(0, CODE_ROOT)

from src.memory.app.console import PersonalAgentApp
from src.memory.core.diagnostics import log_boundary_failure
from src.memory.app.serialization import _serialize_result


# Keep the v1 wire identifier for existing JSONL clients. The product-facing
# server name is MAPAS; changing this token would be a protocol break.
PROTOCOL_VERSION = "apaas-agent/1"
logger = logging.getLogger(__name__)


class JsonlAppServer:
    """Serve protocol requests over a pair of text streams."""

    def __init__(self, app: PersonalAgentApp, output: TextIO) -> None:
        self.app = app
        self.output = output
        self.initialized = False

    def _write(self, message: Mapping[str, Any]) -> None:
        safe_message = self.app._json_safe_event_value(dict(message))
        self.output.write(json.dumps(safe_message, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.output.flush()

    def _response(self, request_id: Any, result: Mapping[str, Any]) -> None:
        self._write({"jsonrpc": "2.0", "id": request_id, "result": dict(result)})

    def _error(self, request_id: Any, code: int, message: str, data: Optional[Mapping[str, Any]] = None) -> None:
        error: Dict[str, Any] = {"code": code, "message": message}
        if data:
            error["data"] = dict(data)
        self._write({"jsonrpc": "2.0", "id": request_id, "error": error})

    def _notify_events(self, events: Iterable[Any]) -> None:
        for event in events:
            self._write({
                "jsonrpc": "2.0",
                "method": "event",
                "params": event.to_dict(),
            })

    def _thread(self, session_id: str) -> Dict[str, Any]:
        session = self.app.conversation_repo.get_session(session_id)
        if session is None:
            if session_id == self.app.active_session_id and not getattr(self.app, "_session_persisted", False):
                return {
                    "threadId": session_id,
                    "title": getattr(self.app, "_pending_session_title", None) or "新会话（尚未开始）",
                    "taskId": None,
                    "status": "PENDING",
                    "createdAt": None,
                    "updatedAt": None,
                }
            raise ValueError(f"thread not found: {session_id}")
        return {
            "threadId": session.session_id,
            "title": session.title,
            "taskId": session.active_task_id,
            "status": session.status,
            "createdAt": session.created_at,
            "updatedAt": session.updated_at,
        }

    def _handle(self, method: str, params: Mapping[str, Any]) -> Dict[str, Any]:
        if method == "initialize":
            self.initialized = True
            return {
                "protocolVersion": PROTOCOL_VERSION,
                "serverInfo": {"name": "mapas-agent", "version": "1"},
                "capabilities": {
                    "threads": True,
                    "turns": True,
                    "turnResume": True,
                    "eventReplay": True,
                    "contextDiagnostics": True,
                    "approvals": True,
                    "approvalEvents": True,
                    "toolCatalog": True,
                    "interrupt": False,
                },
                "agentHome": self.app.data_dir,
            }
        if not self.initialized:
            raise RuntimeError("initialize must be called before other requests")
        if method in {"thread/start", "session/start"}:
            session_id = self.app.create_new_session(str(params.get("title")) if params.get("title") else None)
            return {"thread": self._thread(session_id)}
        if method in {"thread/resume", "session/resume"}:
            session_id = str(params.get("threadId") or params.get("sessionId") or "")
            if not session_id or not self.app.resume_session(session_id):
                raise ValueError(f"thread not found: {session_id}")
            return {"thread": self._thread(session_id)}
        if method in {"thread/list", "session/list"}:
            return {"threads": [self._thread(item["session_id"]) for item in self.app.list_sessions()]}
        if method in {"thread/read", "session/read"}:
            session_id = str(params.get("threadId") or params.get("sessionId") or self.app.active_session_id)
            thread = self._thread(session_id)
            if thread.get("status") == "PENDING":
                thread["messages"] = []
                thread["unfinishedTurns"] = []
                return {"thread": thread}
            messages = self.app.conversation_repo.list_messages(session_id)
            thread["messages"] = [
                {
                    "messageId": message.message_id,
                    "turnId": message.turn_id,
                    "role": message.role.value,
                    "content": message.content,
                    "createdAt": message.created_at,
                }
                for message in messages
            ]
            thread["unfinishedTurns"] = [
                item for item in self.app.list_unfinished_turns(session_id)
            ]
            return {"thread": thread}
        if method == "events/read":
            events = self.app.list_events(
                after_sequence=int(params.get("afterSequence", 0)),
                session_id=str(params["threadId"]) if params.get("threadId") else None,
                turn_id=str(params["turnId"]) if params.get("turnId") else None,
                limit=int(params.get("limit", 100)),
            )
            return {
                "events": [event.to_dict() for event in events],
                "nextSequence": events[-1].sequence if events else int(params.get("afterSequence", 0)),
            }
        if method == "status/read":
            return {
                "status": self.app.get_task_status_summary(),
                "memory": self.app.get_memory_update_status(),
                "unfinishedTurns": self.app.list_unfinished_turns(),
                "pendingApprovals": self.app.list_pending_approvals(),
            }
        if method in {"tools/list", "tool/list"}:
            return {"tools": self.app.get_tool_catalog()}
        if method in {"mcp/status", "mcp/read"}:
            return {"mcp": self.app.get_mcp_status()}
        if method in {"approvals/list", "approval/list"}:
            return {"approvals": self.app.list_pending_approvals()}
        if method == "context/read":
            return {"context": self.app.get_conversation_context_status()}
        if method == "turn/start":
            thread_id = str(params.get("threadId") or params.get("sessionId") or self.app.active_session_id)
            if thread_id != self.app.active_session_id and not self.app.resume_session(thread_id):
                raise ValueError(f"thread not found: {thread_id}")
            user_input = params.get("input", params.get("message"))
            if not isinstance(user_input, str) or not user_input.strip():
                raise ValueError("turn/start requires a non-empty input")
            before = self.app.event_store.latest_sequence()
            result = self.app._run_with_wait_notice(
                lambda: self.app.process_chat_turn(user_input),
                show_progress=False,
            )
            events = self.app.list_events(after_sequence=before, session_id=thread_id)
            self._notify_events(events)
            return {
                "threadId": thread_id,
                "turnId": result.request_turn_id,
                "result": _serialize_result(result),
                "lastEventSequence": events[-1].sequence if events else before,
            }
        if method == "turn/resume":
            thread_id = str(params.get("threadId") or params.get("sessionId") or self.app.active_session_id)
            if thread_id != self.app.active_session_id and not self.app.resume_session(thread_id):
                raise ValueError(f"thread not found: {thread_id}")
            turn_id = str(params.get("turnId") or "")
            if not turn_id:
                raise ValueError("turn/resume requires turnId")
            before = self.app.event_store.latest_sequence()
            result = self.app._run_with_wait_notice(
                lambda: self.app.resume_interrupted_turn(turn_id),
                show_progress=False,
            )
            events = self.app.list_events(after_sequence=before, session_id=thread_id, turn_id=turn_id)
            self._notify_events(events)
            return {
                "threadId": thread_id,
                "turnId": result.request_turn_id,
                "result": _serialize_result(result),
                "lastEventSequence": events[-1].sequence if events else before,
            }
        if method == "turn/interrupt":
            raise RuntimeError("turn interruption is not yet supported by the synchronous host")
        raise ValueError(f"method not found: {method}")

    def handle(self, request: Mapping[str, Any]) -> None:
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}
        if not isinstance(method, str) or not isinstance(params, Mapping):
            self._error(request_id, -32600, "invalid request")
            return
        try:
            self._response(request_id, self._handle(method, params))
        except ValueError as exc:
            self._error(request_id, -32602, str(exc))
        except RuntimeError as exc:
            self._error(request_id, -32000, str(exc))
        except Exception as exc:
            metadata = log_boundary_failure(logger, "app_server_request_failed", exc, method=method)
            self._error(
                request_id,
                -32603,
                "internal error",
                {"error_type": metadata.get("error_type", type(exc).__name__)},
            )

    def serve(self, input_stream: TextIO) -> None:
        try:
            for line in input_stream:
                if not line.strip():
                    continue
                try:
                    request = json.loads(line)
                except json.JSONDecodeError as exc:
                    self._error(None, -32700, f"invalid JSON: {exc.msg}")
                    continue
                if not isinstance(request, Mapping):
                    self._error(None, -32600, "request must be a JSON object")
                    continue
                self.handle(request)
        finally:
            self.app.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="MAPAS JSONL host protocol")
    parser.add_argument("--data-dir", default=None, help="Agent home or explicit isolated data directory")
    parser.add_argument("--execution-mode", choices=("single", "multi_fixed"), default="single")
    args = parser.parse_args()
    app = PersonalAgentApp(data_dir=args.data_dir, execution_mode=args.execution_mode)
    JsonlAppServer(app, sys.stdout).serve(sys.stdin)


if __name__ == "__main__":
    main()
