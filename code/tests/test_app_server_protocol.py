from __future__ import annotations

import io
import json

from src.memory.app.console import PersonalAgentApp
from src.memory.conversation import ConversationRole
from src.memory.core.config import MemoryConfig, ProviderMode
from scripts.personal_agent_app_server import JsonlAppServer


class ProtocolProvider:
    usage = {"request_count": 0, "input_tokens": 0, "output_tokens": 0}
    last_reasoning_content = ""

    def generate_json(self, system_prompt, user_prompt, *, response_schema=None, schema_name="structured_output"):
        self.usage["request_count"] += 1
        if schema_name == "task_binding_decision_v1":
            payload = json.loads(user_prompt)
            current_task_id = payload.get("current_task_id")
            if current_task_id:
                return {"action": "EXISTING", "task_id": current_task_id, "title": None}
            return {"action": "NEW", "task_id": None, "title": "协议测试任务"}
        if "task_state" in schema_name or "proposal" in schema_name:
            return {"proposals": [], "warnings": []}
        if "selection" in schema_name or "context" in schema_name or "scope" in schema_name:
            return {"schema_version": 1, "selected": [], "values": [], "rules": [], "warnings": []}
        if "Dialogue Window:" in user_prompt:
            return {"episodes": []}
        if "Existing Memories S_in:" in user_prompt:
            return {"predicted_content": "", "prediction_error_residual": "", "is_novel_info": False, "novelty_score": 0.0}
        if "Cognitive Synthesis Engine" in system_prompt:
            return {"abstract_insight": "", "granularity": "HIGH_ABSTRACT"}
        if schema_name == "agent_action":
            return {"action_kind": "FINAL_RESPONSE", "content": "协议路径成功"}
        return {"response": "ok"}

    def generate_reasoned_json(self, system_prompt, user_prompt, *, response_schema=None, schema_name="structured_output"):
        return self.generate_json(system_prompt, user_prompt, response_schema=response_schema, schema_name=schema_name)

    def generate_chat_json(self, messages, enable_web_search=False):
        return {"response": "ok"}

    def set_progress_callback(self, _callback):
        return None


def _app(tmp_path):
    config = MemoryConfig(
        sqlite_db_path=str(tmp_path / "memory.db"),
        provider_mode=ProviderMode.REQUIRED,
    )
    return PersonalAgentApp(data_dir=str(tmp_path / "agent"), config=config, llm_client=ProtocolProvider())


def test_jsonl_protocol_initializes_starts_turn_and_replays_events(tmp_path):
    app = _app(tmp_path)
    output = io.StringIO()
    server = JsonlAppServer(app, output)
    server.handle({"id": 1, "method": "initialize", "params": {}})
    server.handle({"id": 2, "method": "turn/start", "params": {"input": "你好"}})

    messages = [json.loads(line) for line in output.getvalue().splitlines()]
    assert messages[0]["id"] == 1
    assert messages[0]["result"]["capabilities"]["eventReplay"] is True
    notifications = [item for item in messages if item.get("method") == "event"]
    event_types = [item["params"]["event_type"] for item in notifications]
    assert event_types[:3] == ["thread.started", "turn.started", "item.completed"]
    assert event_types[-2:] == ["turn.completed", "turn.usage"]
    assert event_types.count("stage.completed") == 6
    response = [item for item in messages if item.get("id") == 2][0]
    assert response["result"]["result"]["final_response"] == "协议路径成功"
    status = server._handle("status/read", {})
    assert status["memory"]["mode"] == "agent_tool"
    assert status["memory"]["threshold_trigger_enabled"] is False
    assert response["result"]["lastEventSequence"] == notifications[-1]["params"]["sequence"]
    app.close()


def test_jsonl_protocol_requires_initialize_and_supports_cursor_read(tmp_path):
    app = _app(tmp_path)
    output = io.StringIO()
    server = JsonlAppServer(app, output)
    server.handle({"id": 1, "method": "status/read", "params": {}})
    assert json.loads(output.getvalue()) ["error"]["code"] == -32000

    output.seek(0)
    output.truncate(0)
    server.handle({"id": 2, "method": "initialize", "params": {}})
    server.handle({"id": 3, "method": "events/read", "params": {"afterSequence": 0}})
    messages = [json.loads(line) for line in output.getvalue().splitlines()]
    assert messages[-1]["result"]["events"] == []
    app.close()


def test_jsonl_protocol_exposes_tool_catalog_and_pending_approvals(tmp_path):
    app = _app(tmp_path)
    output = io.StringIO()
    server = JsonlAppServer(app, output)
    server.handle({"id": 1, "method": "initialize", "params": {}})
    server.handle({"id": 2, "method": "tools/list", "params": {}})
    server.handle({"id": 3, "method": "approvals/list", "params": {}})

    messages = [json.loads(line) for line in output.getvalue().splitlines()]
    tools = next(item for item in messages if item.get("id") == 2)["result"]["tools"]
    assert tools
    assert all({"name", "description", "inputSchema", "effect"} <= set(item) for item in tools)
    approvals = next(item for item in messages if item.get("id") == 3)["result"]["approvals"]
    assert approvals == []
    app.close()


def test_personal_agent_can_resume_a_started_turn_from_persisted_input(tmp_path):
    app = _app(tmp_path)
    turn_id = "turn_interrupted"
    app._ensure_persisted_session()
    app.conversation_repo.append_message(
        app.active_session_id,
        turn_id,
        ConversationRole.USER,
        "中断后继续",
    )
    app.event_store.append(
        "turn.started",
        session_id=app.active_session_id,
        turn_id=turn_id,
        payload={"kind": "user_turn", "resume": False},
    )

    result = app.resume_interrupted_turn(turn_id)

    assert result.final_response == "协议路径成功"
    assert app.list_unfinished_turns() == []
    assert [event.event_type for event in app.list_events(turn_id=turn_id)][-1] == "turn.completed"
    app.close()
