from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from src.memory.agent.journal import AgentExecutionJournal
from src.memory.agent.loop import AgentLoopConfig, AgentLoopController
from src.memory.agent.main_agent import MainAgent
from src.memory.agent.models import PendingAction
from src.memory.agent.tooling import ToolRegistry
from src.memory.api.memory_manager import LongTermMemoryManager
from src.memory.conversation import ConversationRepository
from src.memory.core.config import MemoryConfig, ProviderMode
from src.memory.core.errors import DuplicateEventConflictError, InvalidLifecycleTransitionError
from src.memory.llm.errors import ProviderConfigurationError, ProviderRequestError
from src.memory.llm.llm_client import LLMClientBase, OpenAILLMClient
from src.memory.runtime.agent_runtime import AgentRuntime
from src.memory.runtime.config import AgentRuntimeConfig
from src.memory.runtime.models import (
    AgentTurnContext,
    ToolCallResult,
    ToolCallStatus,
)
from src.memory.runtime.task_router import BaselineTaskRouter
from src.memory.product_trial.models import TrialActionKind, TrialScenario, TrialStep
from src.memory.product_trial.runner import ProductTrialRunner
from src.memory.storage.sqlite_manager import SQLiteMemoryManager
from src.memory.task_state.models import (
    SourceKind,
    TaskEvent,
    TaskStateProposalBatch,
)
from src.memory.task_state.repository import TaskStateRepository
from src.memory.task_state.service import TaskStateService


class _AddLifecycleClient(LLMClientBase):
    def generate_json(self, system_prompt, user_prompt, **kwargs):
        return {"operation": "ADD", "content": "我长期使用机械键盘"}

    def generate_chat_json(self, messages, enable_web_search=False, **kwargs):
        raise AssertionError("unexpected chat request")


def test_explicit_memory_write_failure_returns_durable_error_instead_of_masking_name_error():
    agent = MainAgent.__new__(MainAgent)

    def failing_writer(*_args):
        raise RuntimeError("test-only writer failure")

    agent.memory_writer = failing_writer
    result = agent._run_explicit_memory_write(
        request_turn_id="turn-failure",
        task_id="task-failure",
        analysis=SimpleNamespace(memory_content="测试记忆"),
        warnings=(),
    )

    assert result.status.value == "FAILED"
    assert result.error_info is not None
    assert result.error_info.code == "MEMORY_WRITE_FAILED"
    assert result.error_info.details == {"error_type": "RuntimeError"}


def test_explicit_memory_apply_never_fabricates_unknown_identity(tmp_path, monkeypatch):
    config = MemoryConfig(
        sqlite_db_path=str(tmp_path / "memory.db"),
        provider_mode=ProviderMode.REQUIRED,
    )
    manager = LongTermMemoryManager(config=config, llm_client=_AddLifecycleClient())
    monkeypatch.setattr(manager.sqlite_mgr, "apply_proposals_atomic", lambda *a, **k: [])

    with pytest.raises(InvalidLifecycleTransitionError, match="no durable memory identity"):
        manager.process_explicit_instruction("我长期使用机械键盘", source_event_id="event-empty")


def test_ingestion_replay_requires_same_source_identity(tmp_path):
    manager = SQLiteMemoryManager(str(tmp_path / "memory.db"))
    manager.register_ingestion_event(
        "相同文本", event_id="event-1", session_id="session-1", message_id="message-1"
    )

    with pytest.raises(DuplicateEventConflictError, match="source identity"):
        manager.register_ingestion_event(
            "相同文本", event_id="event-1", session_id="session-2", message_id="message-1"
        )


def test_pending_action_requires_registered_matching_run(tmp_path):
    journal = AgentExecutionJournal(str(tmp_path / "journal.db"))
    pending = PendingAction(
        pending_action_id="pending-1",
        agent_run_id="run-missing",
        task_id="task-1",
        agent_step_id="step-1",
        tool_call_id="call-1",
        tool_name="write",
        validated_arguments={"value": 1},
        arguments_hash="hash-1",
        idempotency_key="idemp-1",
        effect="SIDE_EFFECT",
    )

    with pytest.raises(ValueError, match="unregistered agent run"):
        journal.add_pending_action(pending)
    assert journal.get_run("run-missing") is None


def test_journal_rejects_invalid_arguments_and_incoherent_terminal_receipts(tmp_path):
    journal = AgentExecutionJournal(str(tmp_path / "journal.db"))
    journal.register_run("run-1", "turn-1", "task-1")

    with pytest.raises(ValueError, match="not valid JSON"):
        journal.claim_execution(
            "run-1", "step-1", "call-bad", "tool", "hash", "idemp-bad",
            "{broken", "READ_ONLY", "NONE",
        )
    assert journal.get_receipt("call-bad") is None

    journal.claim_execution(
        "run-1", "step-1", "call-1", "tool", "hash", "idemp-1",
        "{}", "READ_ONLY", "NONE",
    )
    with pytest.raises(ValueError, match="cannot contain result_content"):
        journal.complete_execution("call-1", "FAILED", result_content="looks successful")


def test_corrupt_receipt_fails_closed_instead_of_inventing_empty_arguments(tmp_path):
    journal = AgentExecutionJournal(str(tmp_path / "journal.db"))
    journal.register_run("run-1", "turn-1", "task-1")
    journal.claim_execution(
        "run-1", "step-1", "call-1", "tool", "hash", "idemp-1",
        "{}", "READ_ONLY", "NONE",
    )
    journal.complete_execution("call-1", "SUCCEEDED", result_content="done")
    with journal._connection() as connection:
        connection.execute(
            "UPDATE tool_execution_receipts SET arguments_json=? WHERE tool_call_id=?",
            ("{broken", "call-1"),
        )

    controller = AgentLoopController(
        runtime=object(),  # type: ignore[arg-type]
        journal=journal,
        registry=ToolRegistry(),
        reasoner=object(),  # type: ignore[arg-type]
    )
    context = AgentTurnContext(
        turn_id="turn-1",
        task_id="task-1",
        receipt=None,
        maintenance=None,
        evidence_bundle=None,
        working_context=None,  # type: ignore[arg-type]
        structured_task_context=None,  # type: ignore[arg-type]
    )

    reconciliation = controller._reconcile_tool_receipt(
        context, "run-1", "task-1", "call-1"
    )

    assert reconciliation.ok is False
    assert reconciliation.failure_result is not None
    assert reconciliation.failure_result.error_info is not None
    assert reconciliation.failure_result.error_info.code == "JOURNAL_RECEIPT_CORRUPT"


def test_receipt_payload_status_must_match_durable_row(tmp_path):
    journal = AgentExecutionJournal(str(tmp_path / "journal.db"))
    journal.register_run("run-1", "turn-1", "task-1")
    journal.claim_execution(
        "run-1", "step-1", "call-1", "tool", "hash", "idemp-1",
        "{}", "READ_ONLY", "NONE",
    )
    journal.complete_execution("call-1", "SUCCEEDED", result_content="done")
    with journal._connection() as connection:
        payload = {
            "tool_call_id": "call-1",
            "status": "FAILED",
            "result_content": "",
            "error_message": "failed",
        }
        connection.execute(
            "UPDATE tool_execution_receipts SET result_json=? WHERE tool_call_id=?",
            (json.dumps(payload), "call-1"),
        )

    controller = AgentLoopController(
        runtime=object(),  # type: ignore[arg-type]
        journal=journal,
        registry=ToolRegistry(),
        reasoner=object(),  # type: ignore[arg-type]
    )
    context = AgentTurnContext(
        turn_id="turn-1",
        task_id="task-1",
        receipt=None,
        maintenance=None,
        evidence_bundle=None,
        working_context=None,  # type: ignore[arg-type]
        structured_task_context=None,  # type: ignore[arg-type]
    )

    reconciliation = controller._reconcile_tool_receipt(
        context, "run-1", "task-1", "call-1"
    )

    assert reconciliation.ok is False
    assert reconciliation.failure_result is not None
    assert reconciliation.failure_result.error_info is not None
    assert reconciliation.failure_result.error_info.code == "JOURNAL_RECEIPT_CORRUPT"


def test_task_event_replay_requires_complete_identity(tmp_path):
    repository = TaskStateRepository(str(tmp_path / "task.db"))
    repository.create_task("task-1")
    original = TaskEvent(
        task_id="task-1",
        event_id="event-1",
        source_kind=SourceKind.TOOL,
        content="Tool 'search' (SUCCESS): done",
        structured_payload={"success": True, "status": "SUCCESS", "tool_name": "search"},
        tool_call_id="call-1",
    )
    repository.register_event(original)

    with pytest.raises(ValueError, match="different event identity"):
        repository.register_event(
            TaskEvent(
                task_id="task-1",
                event_id="event-1",
                source_kind=SourceKind.TOOL,
                content=original.content,
                structured_payload={"success": False, "status": "FAILED", "tool_name": "search"},
                tool_call_id="call-1",
            )
        )


def test_tool_result_contract_and_task_event_success_metadata(tmp_path):
    with pytest.raises(ValueError, match="error_message"):
        ToolCallResult(
            tool_call_id="call-failed",
            tool_name="search",
            arguments={},
            status=ToolCallStatus.FAILED,
        )

    class CapturingInterpreter:
        identity = "capture"
        version = "v1"

        def __init__(self):
            self.events = []

        def interpret(self, event, context):
            self.events.append(event)
            return TaskStateProposalBatch(
                task_id=event.task_id,
                event_id=event.event_id,
                proposals=(),
                interpreter=self.identity,
                interpreter_version=self.version,
            )

    repository = TaskStateRepository(str(tmp_path / "task.db"))
    service = TaskStateService(repository)
    service.create_task("task-1")
    interpreter = CapturingInterpreter()
    runtime = AgentRuntime(task_service=service, task_interpreter=interpreter)
    result = ToolCallResult(
        tool_call_id="call-1",
        tool_name="search",
        arguments={},
        status=ToolCallStatus.SUCCESS,
        result_content="done",
    )

    assert runtime.process_tool_result(result, "task-1").is_success
    assert interpreter.events[0].structured_payload == {
        "success": True,
        "status": "SUCCESS",
        "tool_name": "search",
    }


def test_router_does_not_misreport_storage_failure_as_missing_task():
    class BrokenService:
        def get_task(self, task_id):
            raise sqlite3.OperationalError("database is locked")

    with pytest.raises(sqlite3.OperationalError):
        BaselineTaskRouter(BrokenService()).resolve_task("turn-1", "task-1")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "factory",
    [
        lambda: AgentRuntimeConfig(default_top_k=0),
        lambda: AgentRuntimeConfig(max_history_turns=-1),
        lambda: AgentLoopConfig(max_steps=0),
        lambda: AgentLoopConfig(max_observation_chars=0),
        lambda: OpenAILLMClient("key", api_base="not-a-url"),
        lambda: OpenAILLMClient("key", max_retries=0),
    ],
)
def test_invalid_runtime_and_provider_parameters_fail_at_configuration(factory):
    with pytest.raises((ValueError, ProviderConfigurationError)):
        factory()


def test_conversation_touch_does_not_succeed_for_missing_session(tmp_path):
    repository = ConversationRepository(str(tmp_path / "conversation.db"))
    with pytest.raises(KeyError, match="session not found"):
        repository.touch_session("missing")


def test_product_trial_cannot_pass_an_unexpected_agent_failure():
    class FailingTrialClient(LLMClientBase):
        def generate_json(self, system_prompt, user_prompt, **kwargs):
            raise ProviderRequestError(
                "synthetic provider failure",
                provider="test",
                operation=kwargs.get("schema_name", "unknown"),
                status_code=503,
            )

        def generate_chat_json(self, messages, enable_web_search=False, **kwargs):
            raise AssertionError("unexpected chat request")

    scenario = TrialScenario(
        scenario_id="FAIL-1",
        category="failure-contract",
        description="unexpected provider failure",
        steps=(TrialStep(TrialActionKind.CHAT, "hello"),),
    )

    result = ProductTrialRunner(FailingTrialClient()).run_scenario(scenario)

    assert result.status == "FAIL"
    assert "PROVIDER_REQUEST_ERROR" in result.failed_reasons[0]
