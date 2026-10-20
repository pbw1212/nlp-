"""deterministic test Unit Tests for Personal Agent CLI Application Surface."""

import json

import pytest
import threading
import time
from typing import Any, Dict, List

from src.memory.agent.models import (
    AgentResult,
    AgentResultStatus,
    ConfirmationDecision,
    PendingAction,
)
from src.memory.agent.tooling import ToolEffect, ToolSpec, ToolExecutor
from src.memory.runtime.models import ToolCallResult, ToolCallStatus
from src.memory.app.console import OutputMode, PersonalAgentApp
from src.memory.app.tools import build_product_tool_registry
from src.memory.conversation import ConversationRole
from src.memory.core.config import MemoryConfig, ProviderMode
from src.memory.llm.llm_client import LLMClientBase
from src.memory.llm.errors import ProviderRequestError


class FakeDeterministicLLMClient(LLMClientBase):
    """Deterministic deterministic test mock LLM client for application unit tests."""

    def __init__(self) -> None:
        self.next_action_kind = "FINAL_RESPONSE"
        self.next_tool_name = "get_current_time"
        self.next_final_response = "Hello from Personal Agent App!"

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        response_schema: Dict[str, Any] | None = None,
        schema_name: str = "structured_output",
    ) -> Dict[str, Any]:
        if schema_name == "task_binding_decision_v1":
            payload = json.loads(user_prompt)
            current_task_id = payload.get("current_task_id")
            if current_task_id:
                return {"action": "EXISTING", "task_id": current_task_id, "title": None}
            return {"action": "NEW", "task_id": None, "title": "测试任务"}
        if "partition them into semantically coherent" in system_prompt:
            return {"episodes": []}
        if "Prediction Error Auditor" in system_prompt:
            return {
                "predicted_content": "",
                "prediction_error_residual": "",
                "is_novel_info": False,
                "novelty_score": 0.0,
            }
        if ("task_state" in schema_name or "proposal" in schema_name) and schema_name != "explicit_proposal":
            return {"proposals": [], "warnings": []}
        if "selection" in schema_name:
            return {"schema_version": 1, "selected": [], "warnings": []}
        if "context" in schema_name:
            return {"schema_version": 1, "values": [], "warnings": []}
        if "scope" in schema_name:
            return {"schema_version": 1, "memory_id": "mem_test", "rules": [], "warnings": []}
        if schema_name == "explicit_proposal":
            import re
            target_id = None
            m = re.search(r"mem_[a-z0-9]{12}", user_prompt)
            if m:
                target_id = m.group(0)

            if "我不吃香菜" in user_prompt and "我爱吃香菜" in user_prompt:
                return {"operation": "CORRECT", "target_memory_id": target_id, "content": "我爱吃香菜"}
            if "我不吃香菜" in user_prompt and target_id:
                return {"operation": "NOOP", "target_memory_id": target_id}
            if "我喜欢Python" in user_prompt and "机器人" in user_prompt:
                return {"operation": "UPDATE", "target_memory_id": target_id, "content": "我喜欢Python，尤其喜欢机器人开发"}

            cand_text = user_prompt.split("Candidate Statement:\n")[-1].strip()
            return {"operation": "ADD", "content": cand_text}

        if self.next_action_kind == "TOOL_CALL":
            tool_arguments = {}
            if self.next_tool_name == "calculator":
                tool_arguments = {"expression": "1 + 1"}
            elif self.next_tool_name == "create_file":
                tool_arguments = {"filename": "test.txt", "content": "hello"}
            return {
                "action_kind": "TOOL_CALL",
                "tool_name": self.next_tool_name,
                "tool_arguments": tool_arguments,
                "final_response": "",
            }
        return {
            "action_kind": "FINAL_RESPONSE",
            "tool_name": "",
            "tool_arguments": {},
            "final_response": self.next_final_response,
            "content": self.next_final_response,
        }

    def generate_chat_json(self, messages: List[Dict[str, str]], enable_web_search: bool = False) -> Dict[str, Any]:
        return self.generate_json("", "")


class FailingProviderClient(LLMClientBase):
    """Provider double that exercises the production API failure path."""

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        response_schema: Dict[str, Any] | None = None,
        schema_name: str = "structured_output",
    ) -> Dict[str, Any]:
        raise ProviderRequestError(
            "request failed with Authorization: bearer secret-value",
            provider="test-provider",
            operation=schema_name,
            status_code=503,
        )

    def generate_chat_json(self, messages: List[Dict[str, str]], enable_web_search: bool = False) -> Dict[str, Any]:
        return self.generate_json("", "")


class ProviderRoutedMemoryClient(FakeDeterministicLLMClient):
    """Provider fixture for semantic natural-language memory routing."""

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        response_schema: Dict[str, Any] | None = None,
        schema_name: str = "structured_output",
    ) -> Dict[str, Any]:
        if schema_name == "task_state_proposal_batch_v1":
            return {
                "analysis": {
                    "task_stage": "preference_capture",
                    "query_intent": "EXPLICIT_MEMORY_WRITE",
                    "memory_write_requested": True,
                    "information_needs": [],
                    "retrieval_needed": False,
                    "important_kinds": [],
                    "focus_entry_ids": [],
                    "search_query": "",
                    "warnings": [],
                    "memory_content": "我solotrip喜欢住在市中心",
                    "memory_support_span": "我solotrip喜欢住在市中心",
                },
                "proposals": [],
                "warnings": [],
            }
        return super().generate_json(
            system_prompt,
            user_prompt,
            response_schema=response_schema,
            schema_name=schema_name,
        )


@pytest.fixture
def app_env(tmp_path):
    data_dir = tmp_path / ".agent_data"
    cfg = MemoryConfig(
        sqlite_db_path=str(data_dir / "memory.db"),
        provider_mode=ProviderMode.REQUIRED,
    )
    fake_llm = FakeDeterministicLLMClient()
    app = PersonalAgentApp(
        data_dir=str(data_dir),
        config=cfg,
        llm_client=fake_llm,
    )
    return app, fake_llm, data_dir


def test_natural_language_memory_request_uses_provider_route_and_durable_write(tmp_path):
    data_dir = tmp_path / ".agent_data"
    cfg = MemoryConfig(
        sqlite_db_path=str(data_dir / "memory.db"),
        provider_mode=ProviderMode.REQUIRED,
    )
    app = PersonalAgentApp(
        data_dir=str(data_dir),
        config=cfg,
        llm_client=ProviderRoutedMemoryClient(),
    )

    result = app.process_chat_turn("写入一个记忆，我solotrip喜欢住在市中心")

    assert result.status is AgentResultStatus.SUCCESS
    assert result.trace_metadata["explicit_memory_write"] is True
    assert app.list_memories()[0]["content"] == "我solotrip喜欢住在市中心"
    assert app.long_term_memory_mgr.sqlite_mgr.list_pending_events() == []


def test_unbound_natural_language_memory_request_stays_out_of_task3(tmp_path):
    class UnboundExplicitMemoryClient(ProviderRoutedMemoryClient):
        def generate_json(self, system_prompt, user_prompt, *, response_schema=None, schema_name="structured_output"):
            if schema_name == "task_binding_decision_v1":
                return {"action": "UNBOUND", "task_id": None, "title": None}
            return super().generate_json(
                system_prompt,
                user_prompt,
                response_schema=response_schema,
                schema_name=schema_name,
            )

    data_dir = tmp_path / ".agent_data"
    cfg = MemoryConfig(sqlite_db_path=str(data_dir / "memory.db"), provider_mode=ProviderMode.REQUIRED)
    app = PersonalAgentApp(
        data_dir=str(data_dir),
        config=cfg,
        llm_client=UnboundExplicitMemoryClient(),
    )

    result = app.process_chat_turn("写入一个记忆，我solotrip喜欢住在市中心")

    assert result.status is AgentResultStatus.SUCCESS
    assert result.trace_metadata["explicit_memory_write"] is True
    assert result.trace_metadata["long_term_memory_update"]["route"] == "explicit_request"
    assert app.active_task_id is None
    assert app.runtime.task_service.repository.list_tasks() == ()
    assert app.list_memories()[0]["content"] == "我solotrip喜欢住在市中心"


def test_stale_provider_memory_candidate_falls_back_to_normal_chat(tmp_path):
    data_dir = tmp_path / ".agent_data"
    cfg = MemoryConfig(
        sqlite_db_path=str(data_dir / "memory.db"),
        provider_mode=ProviderMode.REQUIRED,
    )
    app = PersonalAgentApp(
        data_dir=str(data_dir),
        config=cfg,
        llm_client=ProviderRoutedMemoryClient(),
    )
    app.runtime.config.enable_auto_retrieval = False

    first = app.process_chat_turn("写入一个记忆，我solotrip喜欢住在市中心")
    second = app.process_chat_turn("如果我去杭州玩儿，只有我一个人，你给我推荐什么位置的酒店")

    assert first.status is AgentResultStatus.SUCCESS
    assert second.status is AgentResultStatus.SUCCESS
    assert second.final_response != "已记录到长期记忆。"
    assert len(app.list_memories()) == 1


def test_app_bootstrap_and_persistence(tmp_path):
    data_dir = tmp_path / ".agent_data"
    cfg = MemoryConfig(
        sqlite_db_path=str(data_dir / "memory.db"),
        provider_mode=ProviderMode.REQUIRED,
    )
    fake_llm = FakeDeterministicLLMClient()

    # Initial App Boot
    app1 = PersonalAgentApp(data_dir=str(data_dir), config=cfg, llm_client=fake_llm)
    assert app1.active_task_id is None
    assert app1.list_sessions() == []
    tasks1 = app1.list_tasks()
    assert tasks1 == []

    # Create custom task in App 1
    t2_id = app1.create_new_task("项目规划")
    assert app1.active_task_id == t2_id

    # App Restart (Persistence Check)
    app2 = PersonalAgentApp(data_dir=str(data_dir), config=cfg, llm_client=fake_llm)
    tasks2 = app2.list_tasks()
    task_ids_2 = {t["task_id"] for t in tasks2}
    assert t2_id in task_ids_2


def test_startup_and_new_session_are_lazy_and_unbound(tmp_path):
    data_dir = tmp_path / ".agent_data"
    cfg = MemoryConfig(
        sqlite_db_path=str(data_dir / "memory.db"),
        provider_mode=ProviderMode.REQUIRED,
    )
    app = PersonalAgentApp(data_dir=str(data_dir), config=cfg, llm_client=FakeDeterministicLLMClient())

    first_session = app.active_session_id
    assert app.active_task_id is None
    assert app.list_sessions() == []
    assert app.compact_current_session()["transcript_message_count"] == 0
    assert app.list_sessions() == []

    app.process_chat_turn("第一会话")
    assert len(app.list_sessions()) == 1

    second_session = app.create_new_session("第二会话")
    assert second_session != first_session
    assert app.active_task_id is None
    assert len(app.list_sessions()) == 1
    assert app.conversation_repo.get_session(second_session) is None

    app.process_chat_turn("第二会话首条消息")
    assert len(app.list_sessions()) == 2
    assert [m.content for m in app.conversation_repo.list_messages(second_session)][0] == "第二会话首条消息"


def test_new_process_starts_a_fresh_session_without_resuming_previous_state(tmp_path):
    data_dir = tmp_path / ".agent_data"
    cfg = MemoryConfig(sqlite_db_path=str(data_dir / "memory.db"), provider_mode=ProviderMode.REQUIRED)

    app1 = PersonalAgentApp(data_dir=str(data_dir), config=cfg, llm_client=FakeDeterministicLLMClient())
    app1.process_chat_turn("第一个终端的对话")
    session1 = app1.active_session_id
    assert len(app1.list_sessions()) == 1

    app2 = PersonalAgentApp(data_dir=str(data_dir), config=cfg, llm_client=FakeDeterministicLLMClient())
    assert app2.active_session_id != session1
    assert app2.active_task_id is None
    assert len(app2.list_sessions()) == 1
    assert app2.conversation_repo.get_session(app2.active_session_id) is None

    app2.process_chat_turn("第二个终端的对话")
    assert len(app2.list_sessions()) == 2
    assert [m.content for m in app2.conversation_repo.list_messages(app2.active_session_id)] == [
        "第二个终端的对话",
        "Hello from Personal Agent App!",
    ]


def test_provider_can_release_task_without_creating_hidden_task(tmp_path):
    class BindingSequenceProvider(FakeDeterministicLLMClient):
        def __init__(self):
            super().__init__()
            self.bindings = ["UNBOUND", "NEW", "UNBOUND"]

        def generate_json(self, system_prompt, user_prompt, *, response_schema=None, schema_name="structured_output"):
            if schema_name == "task_binding_decision_v1":
                payload = json.loads(user_prompt)
                action = self.bindings.pop(0)
                if action == "EXISTING":
                    task_id = payload["active_tasks"][0]["task_id"]
                    return {"action": action, "task_id": task_id, "title": None}
                if action == "NEW":
                    return {"action": action, "task_id": None, "title": "Provider 创建的任务"}
                return {"action": action, "task_id": None, "title": None}
            return super().generate_json(
                system_prompt,
                user_prompt,
                response_schema=response_schema,
                schema_name=schema_name,
            )

    data_dir = tmp_path / ".agent_data"
    cfg = MemoryConfig(sqlite_db_path=str(data_dir / "memory.db"), provider_mode=ProviderMode.REQUIRED)
    app = PersonalAgentApp(data_dir=str(data_dir), config=cfg, llm_client=BindingSequenceProvider())

    first = app.process_chat_turn("只是闲聊")
    assert first.status is AgentResultStatus.SUCCESS
    assert app.active_task_id is None
    assert app.runtime.task_service.repository.list_tasks() == ()

    second = app.process_chat_turn("开始一个持续任务")
    assert second.status is AgentResultStatus.SUCCESS
    created_task = app.active_task_id
    assert created_task is not None
    assert len(app.runtime.task_service.repository.list_tasks()) == 1

    third = app.process_chat_turn("换个话题")
    assert third.status is AgentResultStatus.SUCCESS
    assert app.active_task_id is None
    assert app.conversation_repo.get_session(app.active_session_id).active_task_id is None
    assert len(app.runtime.task_service.repository.list_tasks()) == 1


def test_bracketed_multiline_input_is_one_turn(tmp_path, monkeypatch):
    data_dir = tmp_path / ".agent_data"
    cfg = MemoryConfig(sqlite_db_path=str(data_dir / "memory.db"), provider_mode=ProviderMode.REQUIRED)
    app = PersonalAgentApp(data_dir=str(data_dir), config=cfg, llm_client=FakeDeterministicLLMClient(), output_mode="USER")
    supplied = iter(["\x1b[200~第一行\n第二行\x1b[201~", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(supplied))

    app.run_interactive_loop()

    messages = app.conversation_repo.list_messages(app.active_session_id)
    user_messages = [message for message in messages if message.role is ConversationRole.USER]
    assert [message.content for message in user_messages] == ["第一行\n第二行"]


def test_product_app_requires_provider_credentials_by_default(tmp_path, monkeypatch):
    """The product path must never silently bootstrap a test provider."""
    monkeypatch.setenv("MEMORY_LOAD_LOCAL_ENV", "0")
    monkeypatch.setenv("MEMORY_PROVIDER_MODE", "REQUIRED")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    data_dir = tmp_path / ".agent_data"
    with pytest.raises(RuntimeError, match="未配置 API 凭据"):
        PersonalAgentApp(data_dir=str(data_dir))


def test_product_app_process_environment_overrides_local_env(tmp_path, monkeypatch):
    (tmp_path / ".env.local").write_text(
        "MEMORY_PROVIDER_MODE=REQUIRED\n"
        "MEMORY_LLM_MODEL=local-slow-model\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEMORY_LLM_MODEL", "process-fast-model")

    app = PersonalAgentApp(
        data_dir=str(tmp_path / ".agent_data"),
        llm_client=FakeDeterministicLLMClient(),
    )

    assert app.config.llm_model_name == "deepseek-v4-flash"


def test_task_management_commands(app_env):
    app, _, _ = app_env

    # 1. Create task
    new_id = app.create_new_task("旅行指南")
    assert app.active_task_id == new_id

    # 2. List tasks
    tasks = app.list_tasks()
    assert any(t["task_id"] == new_id and t["is_active"] for t in tasks)

    # 3. Switch task
    second_id = app.create_new_task("第二个任务")
    ok = app.switch_task(new_id)
    assert ok is True
    assert app.active_task_id == new_id

    # 4. Task status summary
    status_sum = app.get_task_status_summary()
    assert status_sum["task_id"] == new_id
    assert "status" in status_sum


def test_direct_cli_utility_tools_avoid_provider_round_trip(app_env):
    app, fake_llm, _ = app_env

    calculator_result = app.run_direct_utility_tool(
        "calculator", {"expression": "123 * 456"}
    )
    time_result = app.run_direct_utility_tool("get_current_time", {})

    assert calculator_result.status == ToolCallStatus.SUCCESS
    assert calculator_result.result_content == "计算结果: 56088"
    assert time_result.status == ToolCallStatus.SUCCESS
    assert time_result.result_content.startswith("当前系统时间:")
    assert fake_llm.next_action_kind == "FINAL_RESPONSE"


def test_direct_cli_tool_exchange_is_available_to_follow_up_turns(app_env, monkeypatch):
    app, _, _ = app_env
    calls = iter(["/calc 2 + 2", EOFError()])

    def fake_input(_):
        value = next(calls)
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr("builtins.input", fake_input)
    app.run_interactive_loop()

    messages = app.conversation_repo.list_messages(app.active_session_id)
    assert any(message.role.value == "USER" and message.content == "/calc 2 + 2" for message in messages)
    assert any(message.role.value == "TOOL" and "计算结果: 4" in message.content for message in messages)


def test_memory_remember_and_query(app_env):
    app, _, _ = app_env

    # Ingest explicit memory
    res = app.remember_explicit("我不吃花生与海鲜")
    assert res["route"] == "FAST_PATH"
    assert res["memory_id"] is not None

    mem_id = res["memory_id"]

    # Query memories
    mems = app.list_memories()
    assert len(mems) >= 1
    assert any(m["memory_id"] == mem_id for m in mems)

    # Get memory detail
    detail = app.get_memory_detail(mem_id)
    assert detail is not None
    assert detail["content"] == "我不吃花生与海鲜"

    # Get memory history
    hist = app.get_memory_history(mem_id)
    assert len(hist) >= 1
    assert hist[0]["content"] == "我不吃花生与海鲜"

    provenance = app.get_memory_provenance(mem_id)
    assert provenance[0]["refs"][0]["authority"] == "EXPLICIT_USER"

    assert app.forget_memory(mem_id) is True
    assert app.get_memory_detail(mem_id)["retention"] == "RECYCLED"
    assert app.list_memories() == []
    assert app.restore_memory(mem_id) is True
    assert any(item["memory_id"] == mem_id for item in app.list_memories())


def test_invalid_memory_history_command_does_not_crash_cli(app_env, monkeypatch, capsys):
    app, _, _ = app_env
    monkeypatch.setattr("builtins.input", lambda _: "/memory-history missing-memory")
    # Exercise the command branch directly through the same interactive loop
    # boundary; EOF is used to terminate after the command is handled.
    calls = iter(["/memory-history missing-memory", EOFError()])

    def fake_input(_):
        value = next(calls)
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr("builtins.input", fake_input)
    app.run_interactive_loop()
    output = capsys.readouterr().out
    assert "未找到记忆历史或 ID" in output
    assert "Traceback" not in output


def test_multiline_cli_input_is_processed_as_one_user_turn(app_env, monkeypatch):
    app, _, _ = app_env
    multiline_input = "第一段内容\n\n第二段内容\n第三段内容"
    calls = iter([multiline_input, "/quit"])

    monkeypatch.setattr("builtins.input", lambda _prompt: next(calls))
    app.run_interactive_loop()

    user_messages = [
        message
        for message in app.conversation_repo.list_messages(app.active_session_id)
        if message.role is ConversationRole.USER
    ]
    assert [message.content for message in user_messages] == [multiline_input]


def test_cli_input_normalizes_terminal_bracketed_paste_markers():
    from src.memory.app.console import _normalize_cli_input

    pasted = "\x1b[200~第一段\n第二段\x1b[201~"
    assert _normalize_cli_input(pasted) == "第一段\n第二段"


def test_normal_chat_turn(app_env):
    app, fake_llm, _ = app_env
    fake_llm.next_action_kind = "FINAL_RESPONSE"
    fake_llm.next_final_response = "你好！我是你的个人助理。"

    res = app.process_chat_turn("你好")
    assert res.status == AgentResultStatus.SUCCESS
    assert res.final_response == "你好！我是你的个人助理。"
    with open(app.retrieval_trace_path, encoding="utf-8") as handle:
        assert '"turn_id": "turn_' in handle.read()


def test_api_request_failure_is_explicit_and_sanitized(tmp_path, capsys):
    data_dir = tmp_path / ".agent_data"
    cfg = MemoryConfig(
        sqlite_db_path=str(data_dir / "memory.db"),
        provider_mode=ProviderMode.REQUIRED,
    )
    app = PersonalAgentApp(
        data_dir=str(data_dir),
        config=cfg,
        llm_client=FailingProviderClient(),
    )

    result = app.process_chat_turn("请处理这个请求")
    assert result.status == AgentResultStatus.FAILED
    assert result.error_info is not None
    assert result.error_info.category == "PROVIDER_ERROR"
    assert result.error_info.code == "PROVIDER_REQUEST_ERROR"
    assert result.error_info.details == {
        "provider": "test-provider",
        "operation": "task_binding_decision_v1",
        "status_code": 503,
    }
    assert "secret-value" not in result.error_info.safe_message

    app._render_agent_result(result)
    output = capsys.readouterr().out
    assert "[API请求失败] PROVIDER_REQUEST_ERROR HTTP 503" in output
    assert "Provider: test-provider" in output
    assert "Operation: task_binding_decision" in output
    assert "Provider 服务暂时不可用" in output
    assert "secret-value" not in output


def test_api_payment_error_explains_insufficient_credits(app_env, capsys):
    app, _, _ = app_env

    app._print_provider_error(
        code="PROVIDER_REQUEST_ERROR",
        safe_message="Provider request failed.",
        retryable=False,
        provider="openai-compatible",
        operation="chat.completions",
        status_code=402,
    )

    output = capsys.readouterr().out
    assert "[API请求失败] PROVIDER_REQUEST_ERROR HTTP 402" in output
    assert "额度不足" in output
    assert "免费模型" in output


def test_interactive_loop_rejects_invalid_terminal_unicode(app_env, monkeypatch, capsys):
    app, _, _ = app_env
    inputs = iter(["invalid-\udcbd", "/quit"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))

    app.run_interactive_loop()

    output = capsys.readouterr().out
    assert "[输入错误]" in output
    assert "UTF-8" in output
    assert "正在关闭连接并保存状态" in output


def test_wait_notice_reports_liveness_until_operation_finishes(app_env, capsys):
    app, _, _ = app_env

    result = app._run_with_wait_notice(
        lambda: (time.sleep(0.03), "done")[1],
        notice_interval_seconds=0.01,
    )

    output = capsys.readouterr().out
    assert result == "done"
    assert "已收到请求" in output
    assert "模型仍在响应" in output


def test_developer_output_mode_is_default_and_user_mode_hides_diagnostics(app_env, capsys):
    app, _, _ = app_env
    assert app.output_mode is OutputMode.DEVELOPER

    result = AgentResult(
        root_turn_id="turn-output-mode",
        task_id="task_general",
        agent_run_id="run-output-mode",
        status=AgentResultStatus.SUCCESS,
        final_response="最终回答",
        trace_metadata={"turn_elapsed_seconds": 1.234, "tool_details": ({"tool_name": "calculator"},)},
    )

    app.set_output_mode("user")
    shared_user_output = app.format_agent_result(result)
    app._render_agent_result(result)
    user_output = capsys.readouterr().out
    assert shared_user_output == user_output.strip()
    assert "思考用时: 1.234 秒" in user_output
    assert "最终回答" in user_output
    assert "开发模式执行详情" not in user_output
    assert "calculator" not in user_output

    app.set_output_mode("developer")
    shared_developer_output = app.format_agent_result(result)
    app._render_agent_result(result)
    developer_output = capsys.readouterr().out
    assert shared_developer_output != developer_output.strip()
    assert "开发模式执行详情" in shared_developer_output
    assert "Worked for 1.234 秒" in developer_output


def test_non_tty_resume_picker_selects_by_number_without_session_id(app_env, monkeypatch, capsys):
    app, fake_llm, _ = app_env
    fake_llm.next_final_response = "第一会话回答"
    first_session = app.active_session_id
    app.process_chat_turn("第一个会话的主题是杭州旅行。")
    app.create_new_session("第二会话")
    fake_llm.next_final_response = "第二会话回答"
    app.process_chat_turn("第二个会话的主题是上海旅行。")

    monkeypatch.setattr("builtins.input", lambda _prompt: "2")
    selected = app.pick_session()

    sessions = app.list_sessions()
    assert selected == sessions[1]["session_id"]
    assert selected == first_session
    output = capsys.readouterr().out
    assert "第一个会话的主题是杭州旅行。" in output
    assert "第二个会话的主题是上海旅行。" in output


def test_side_effect_confirmation_and_resume(app_env):
    app, fake_llm, data_dir = app_env
    test_workspace = data_dir / "workspace"
    test_workspace.mkdir()
    app.add_workspace_root(str(test_workspace))
    app.use_workspace_root(str(test_workspace))
    workspace_file = app.workspace.resolve_path("test.txt")

    # Trigger SIDE_EFFECT tool
    fake_llm.next_action_kind = "TOOL_CALL"
    fake_llm.next_tool_name = "create_file"

    res1 = app.process_chat_turn("在本地创建一个 test.txt 文件")
    assert res1.status == AgentResultStatus.AWAITING_CONFIRMATION
    assert app.pending_confirmation_id is not None
    assert not workspace_file.exists()  # Safety gate: zero execution before confirmation!

    approval_events = app.list_events(session_id=app.active_session_id)
    requested = [event for event in approval_events if event.event_type == "approval.requested"]
    assert len(requested) == 1
    assert requested[0].payload["pending_action_id"] == res1.pending_action.pending_action_id
    assert requested[0].payload["tool_name"] == "create_file"

    # Confirm action
    fake_llm.next_action_kind = "FINAL_RESPONSE"
    fake_llm.next_final_response = "已成功为你创建文件 test.txt。"

    res2 = app.process_confirmation(ConfirmationDecision.CONFIRM)
    assert res2.status == AgentResultStatus.SUCCESS
    assert app.pending_confirmation_id is None
    assert workspace_file.read_text(encoding="utf-8") == "hello"

    confirmation_events = app.list_events(session_id=app.active_session_id)
    confirmation_types = [event.event_type for event in confirmation_events]
    assert "approval.decision" in confirmation_types
    assert "approval.completed" in confirmation_types
    completed = [event for event in confirmation_events if event.event_type == "approval.completed"][-1]
    assert completed.payload["decision"] == ConfirmationDecision.CONFIRM.value
    assert completed.payload["executed"] is True
    tool_events = [
        event
        for event in confirmation_events
        if event.event_type in {"tool.started", "tool.completed"}
    ]
    assert [event.event_type for event in tool_events] == ["tool.started", "tool.completed"]
    assert tool_events[-1].payload["policy_snapshot"]["effect"] == ToolEffect.SIDE_EFFECT.value
    assert tool_events[-1].payload["runtime_reconciled"] is True

    messages = app.conversation_repo.list_messages(app.active_session_id)
    assert any(
        message.role.value == "SYSTEM"
        and "用户确认了待执行的副作用操作" in message.content
        for message in messages
    )


def test_chat_turn_while_confirmation_pending_returns_structured_state(app_env):
    app, fake_llm, _ = app_env
    fake_llm.next_action_kind = "TOOL_CALL"
    fake_llm.next_tool_name = "create_file"

    first = app.process_chat_turn("请创建一个文件")
    assert first.status == AgentResultStatus.AWAITING_CONFIRMATION
    pending_id = first.pending_action.pending_action_id
    event_count = len(app.list_events(session_id=app.active_session_id))

    repeated = app.process_chat_turn("我还有一个普通问题")

    assert repeated.status == AgentResultStatus.AWAITING_CONFIRMATION
    assert repeated.pending_action is not None
    assert repeated.pending_action.pending_action_id == pending_id
    assert repeated.trace_metadata["existing_pending_confirmation"] is True
    assert len(app.list_events(session_id=app.active_session_id)) == event_count


def test_pending_confirmation_is_restored_after_restart(app_env):
    app, fake_llm, data_dir = app_env
    fake_llm.next_action_kind = "TOOL_CALL"
    fake_llm.next_tool_name = "create_file"
    result = app.process_chat_turn("请创建文件")
    assert result.status == AgentResultStatus.AWAITING_CONFIRMATION

    restarted = PersonalAgentApp(data_dir=str(data_dir), config=app.config, llm_client=FakeDeterministicLLMClient())
    assert restarted.pending_confirmation_id is None
    assert restarted.active_task_id is None
    assert restarted.active_session_id != app.active_session_id


def test_new_task_title_is_persisted_as_goal(app_env):
    app, _, _ = app_env

    task_id = app.create_new_task("整理 CLI 使用文档")

    assert app.active_task_id == task_id
    assert app.list_tasks()[-1]["summary"] == "整理 CLI 使用文档"
    assert app.get_task_status_summary()["summary"] == "整理 CLI 使用文档"


def test_restart_does_not_resume_terminal_task(app_env):
    app, _, data_dir = app_env
    old_task_id = app.create_new_task("待完成任务")
    app.complete_current_task()

    restarted = PersonalAgentApp(data_dir=str(data_dir), config=app.config, llm_client=FakeDeterministicLLMClient())

    assert restarted.active_task_id is None
    assert restarted.runtime.task_service.repository.get_task_status(old_task_id).value == "COMPLETED"


def test_terminal_task_listing_keeps_user_title(app_env):
    app, _, _ = app_env
    task_id = app.create_new_task("已完成的 CLI 整理")
    app.complete_current_task()

    listed = {item["task_id"]: item for item in app.list_tasks()}

    assert listed[task_id]["summary"] == "已完成的 CLI 整理"


def test_confirmation_resume_tracks_a_new_pending_action(app_env, monkeypatch):
    app, _, _ = app_env
    app.create_new_task("确认测试任务")
    app._ensure_persisted_session()
    app.pending_confirmation_id = "pending_old"
    next_pending = PendingAction(
        pending_action_id="pending_next",
        agent_run_id="run_next",
        task_id=app.active_task_id,
        agent_step_id="step_next",
        tool_call_id="call_next",
        tool_name="create_file",
        validated_arguments={"filename": "next.txt"},
        arguments_hash="hash_next",
        idempotency_key="idemp_next",
        effect="SIDE_EFFECT",
    )

    monkeypatch.setattr(
        app.agent,
        "run",
        lambda _request: AgentResult(
            root_turn_id="turn_root",
            task_id=app.active_task_id,
            agent_run_id="run_next",
            status=AgentResultStatus.AWAITING_CONFIRMATION,
            pending_action=next_pending,
        ),
    )

    result = app.process_confirmation(ConfirmationDecision.CONFIRM)

    assert result.pending_action == next_pending
    assert app.pending_confirmation_id == "pending_next"


def test_product_tool_registry():
    registry = build_product_tool_registry()
    time_spec = registry.get_spec("get_current_time")
    assert time_spec is not None
    assert time_spec.effect == ToolEffect.READ_ONLY

    calc_spec = registry.get_spec("calculator")
    assert calc_spec is not None
    assert calc_spec.effect == ToolEffect.READ_ONLY

    catalog = registry.describe()
    assert [item["name"] for item in catalog] == sorted(item["name"] for item in catalog)
    calculator_public = next(item for item in catalog if item["name"] == "calculator")
    calculator_public["inputSchema"]["properties"]["expression"]["type"] = "integer"
    assert registry.get_spec("calculator").parameters_schema["properties"]["expression"]["type"] == "string"

    # Test calculator execution
    calc_exec = registry.get_executor("calculator")
    req = type("Req", (), {
        "tool_call_id": "call_1",
        "tool_name": "calculator",
        "arguments": {"expression": "2.5 * 4 + 10"},
    })()
    calc_res = calc_exec.execute(req)
    assert calc_res.status == ToolCallStatus.SUCCESS
    assert "20.0" in calc_res.result_content

    invalid_req = type("Req", (), {
        "tool_call_id": "call_2",
        "tool_name": "calculator",
        "arguments": {"expression": "1 / 0"},
    })()
    invalid_res = calc_exec.execute(invalid_req)
    assert invalid_res.status == ToolCallStatus.FAILED
    assert invalid_res.result_content == ""
    assert "除数不能为 0" in invalid_res.error_message


def test_readline_history_and_non_tty_compatibility(tmp_path):
    from src.memory.app.console import _setup_readline, _save_readline_history, PersonalAgentApp

    data_dir = tmp_path / ".agent_data"
    data_dir.mkdir(parents=True, exist_ok=True)

    hist_file, readline_ok = _setup_readline(str(data_dir))
    assert hist_file == str(data_dir / "cli_history")

    _save_readline_history(hist_file)
    assert (data_dir / "cli_history").exists()

    cfg = MemoryConfig(sqlite_db_path=str(data_dir / "memory.db"), provider_mode=ProviderMode.REQUIRED)
    fake_llm = FakeDeterministicLLMClient()
    app = PersonalAgentApp(data_dir=str(data_dir), config=cfg, llm_client=fake_llm)

    status = app.get_memory_update_status()
    assert status["mode"] == "agent_tool"
    assert status["tool_name"] == "update_long_term_memory"
    assert status["update_is_agent_selected"] is True
    assert status["threshold_trigger_enabled"] is False
    assert status["time_trigger_enabled"] is False
    memory_tool = next(
        item for item in app.get_tool_catalog()
        if item["name"] == "update_long_term_memory"
    )
    assert memory_tool["effect"] == "INTERNAL"
    assert memory_tool["inputSchema"] == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }


def test_readline_setup_enables_supported_composer_bindings(tmp_path, monkeypatch):
    readline = pytest.importorskip("readline", reason="readline is optional on Windows")

    from src.memory.app.console import _READLINE_KEY_BINDINGS, _setup_readline

    commands = []
    monkeypatch.setattr(readline, "parse_and_bind", commands.append)
    _setup_readline(str(tmp_path / ".agent_data"))

    assert commands[0] == "set enable-bracketed-paste on"
    assert tuple(commands[1:]) == _READLINE_KEY_BINDINGS


def test_completed_transient_chat_does_not_enter_hidden_memory_buffer(app_env):
    app, _, _ = app_env

    result = app.process_chat_turn("今天路上有点堵")

    assert result.status == AgentResultStatus.SUCCESS
    assert app.long_term_memory_mgr.sqlite_mgr.list_pending_events() == []
    assert app.long_term_memory_mgr.list_current() == []


def test_memory_update_tool_is_agent_selected_and_binds_exact_user_provenance(tmp_path):
    class MemoryToolProvider(FakeDeterministicLLMClient):
        def __init__(self):
            super().__init__()
            self.agent_action_count = 0

        def generate_json(self, system_prompt, user_prompt, *, response_schema=None, schema_name="structured_output"):
            if schema_name == "nemori_partition":
                return {
                    "episodes": [{
                        "message_indices": [1],
                        "cue": "稳定用户偏好",
                        "narrative": "用户长期偏好耐用的产品",
                        "durability": "DURABLE",
                    }]
                }
            if schema_name == "automatic_lifecycle":
                return {
                    "operation": "ADD",
                    "target_memory_id": None,
                    "content": "用户长期偏好耐用的产品",
                    "memory_type": "PREFERENCE_RULES",
                    "granularity": "FINE_EPISODE",
                    "confidence": 0.95,
                    "importance_score": None,
                    "semantic_metadata": {},
                }
            if schema_name == "agent_action":
                self.agent_action_count += 1
                if self.agent_action_count == 1:
                    return {
                        "action_kind": "TOOL_CALL",
                        "tool_name": "update_long_term_memory",
                        "tool_arguments": "{}",
                    }
                return {"action_kind": "FINAL_RESPONSE", "content": "已处理本轮请求"}
            return super().generate_json(
                system_prompt,
                user_prompt,
                response_schema=response_schema,
                schema_name=schema_name,
            )

    data_dir = tmp_path / ".agent_data"
    cfg = MemoryConfig(sqlite_db_path=str(data_dir / "memory.db"), provider_mode=ProviderMode.REQUIRED)
    app = PersonalAgentApp(data_dir=str(data_dir), config=cfg, llm_client=MemoryToolProvider())
    user_input = "我买东西更看重耐用性，不喜欢为了外观多花钱"

    result = app.process_chat_turn(user_input)

    assert result.status is AgentResultStatus.SUCCESS
    assert result.trace_metadata["long_term_memory_update"]["attempted"] is True
    memories = app.list_memories()
    assert [item["content"] for item in memories] == ["用户长期偏好耐用的产品"]
    assert app.long_term_memory_mgr.sqlite_mgr.list_pending_events() == []
    provenance = app.get_memory_provenance(memories[0]["memory_id"])[0]["refs"][0]
    assert provenance["source_event_id"].startswith("memory-update:")
    assert provenance["session_id"] == app.active_session_id
    assert provenance["message_id"]


def test_memory_update_tool_reports_noop_without_claiming_memory_change(tmp_path):
    class NoopMemoryToolProvider(FakeDeterministicLLMClient):
        def __init__(self):
            super().__init__()
            self.agent_action_count = 0

        def generate_json(self, system_prompt, user_prompt, *, response_schema=None, schema_name="structured_output"):
            if schema_name == "nemori_partition":
                return {"episodes": []}
            if schema_name == "agent_action":
                self.agent_action_count += 1
                if self.agent_action_count == 1:
                    return {
                        "action_kind": "TOOL_CALL",
                        "tool_name": "update_long_term_memory",
                        "tool_arguments": "{}",
                    }
                return {"action_kind": "FINAL_RESPONSE", "content": "本轮无需持久化变更"}
            return super().generate_json(
                system_prompt,
                user_prompt,
                response_schema=response_schema,
                schema_name=schema_name,
            )

    data_dir = tmp_path / ".agent_data"
    cfg = MemoryConfig(sqlite_db_path=str(data_dir / "memory.db"), provider_mode=ProviderMode.REQUIRED)
    app = PersonalAgentApp(data_dir=str(data_dir), config=cfg, llm_client=NoopMemoryToolProvider())

    result = app.process_chat_turn("今天下午阳光很好")

    assert result.status is AgentResultStatus.SUCCESS
    assert app.list_memories() == []
    tool_messages = [
        message for message in app.conversation_repo.list_messages(app.active_session_id)
        if message.role is ConversationRole.TOOL
    ]
    exchange = json.loads(tool_messages[0].content)["exchanges"][0]
    result_payload = json.loads(exchange["result"]["result_content"])
    assert result_payload["processed"] is True
    assert result_payload["updated"] is False


def test_remember_conflicting_preference_corrects_same_memory_identity(app_env):
    """M003 Regression Test: Conflicting /remember updates existing memory identity via CORRECT revision."""
    app, _, _ = app_env

    # Step 1: Ingest initial preference
    res1 = app.remember_explicit("我不吃香菜")
    mid1 = res1["memory_id"]
    assert mid1 is not None

    # Step 2: Ingest conflicting preference
    res2 = app.remember_explicit("我爱吃香菜")
    mid2 = res2["memory_id"]

    # Invariant 1: Same memory ID retained
    assert mid1 == mid2

    # Invariant 2: Exactly 1 CURRENT memory item
    mems = app.list_memories()
    assert len(mems) == 1
    assert mems[0]["memory_id"] == mid1
    assert mems[0]["content"] == "我爱吃香菜"

    # Invariant 3: Revision history contains 2 revisions (old contradicted & new current)
    hist = app.get_memory_history(mid1)
    assert len(hist) == 2
    assert hist[0]["content"] == "我不吃香菜"
    assert hist[1]["content"] == "我爱吃香菜"


def test_generic_explicit_route_arbitrary_unseen_domain(app_env):
    """Verifies production explicit routing is 100% vocabulary-agnostic across unseen domains."""
    app, _, _ = app_env

    # Test double returning CORRECT for arbitrary unseen domain
    class ArbitraryUnseenProposerLLM(FakeDeterministicLLMClient):
        def generate_json(self, system_prompt, user_prompt, *, response_schema=None, schema_name="structured_output"):
            if schema_name == "explicit_proposal":
                import re
                m = re.search(r"mem_[a-z0-9]{12}", user_prompt)
                target_id = m.group(0) if m else None
                if target_id:
                    return {"operation": "CORRECT", "target_memory_id": target_id, "content": "arbitrary_unseen_new_fact"}
                return {"operation": "ADD", "content": "arbitrary_unseen_old_fact_123"}
            return super().generate_json(system_prompt, user_prompt, response_schema=response_schema, schema_name=schema_name)

    custom_llm = ArbitraryUnseenProposerLLM()
    app.llm_client = custom_llm
    app.long_term_memory_mgr.llm_client = custom_llm
    app.long_term_memory_mgr.distiller.llm_client = custom_llm

    # Step 1: Initial unseen statement
    res1 = app.remember_explicit("arbitrary_unseen_old_fact_123")
    mid1 = res1["memory_id"]

    # Step 2: Unseen statement correction
    res2 = app.remember_explicit("arbitrary_unseen_new_fact_456")
    mid2 = res2["memory_id"]

    assert mid1 == mid2
    mems = app.list_memories()
    assert len(mems) == 1
    assert mems[0]["content"] == "arbitrary_unseen_new_fact"


def test_production_hardcoding_overfit_guard():
    """Overfit Guard: Scans src/memory production files to confirm NO case-specific test keywords exist."""
    import os
    prod_files = [
        "code/src/memory/api/memory_manager.py",
        "code/src/memory/lifecycle/crud_engine.py",
        "code/src/memory/lifecycle/nemori_distiller.py",
        "code/src/memory/storage/sqlite_manager.py",
    ]
    forbidden_keywords = ["香菜", "花生", "辣椒", "Python", "C++", "代号", "中文", "英文", "北京"]
    found_violations = []

    for pf in prod_files:
        if os.path.exists(pf):
            with open(pf, "r", encoding="utf-8") as f:
                content = f.read()
                for kw in forbidden_keywords:
                    if kw in content:
                        found_violations.append(f"Production file '{pf}' contains hardcoded keyword '{kw}'")

    assert len(found_violations) == 0, f"Overfit Guard Rejected Production Code: {found_violations}"
