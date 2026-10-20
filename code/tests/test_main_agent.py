"""deterministic test unit and integration tests for MainAgent (Phase 4B)."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from types import SimpleNamespace
from typing import Any, List, Tuple

import pytest

from src.memory.agent import (
    ActionConfirmation,
    AgentAction,
    AgentActionKind,
    AgentExecutionJournal,
    AgentRequest,
    AgentResultStatus,
    ConfirmationDecision,
    IdempotencySupport,
    MainAgent,
    PendingAction,
    ToolEffect,
    ToolExecutionRequest,
    ToolRegistry,
    ToolSpec,
    derive_stable_identities,
)
from src.memory.agent.loop import AgentLoopController
from src.memory.retrieval.evidence import EvidenceBundle, EvidenceSufficiency, EvidenceSufficiencyStatus
from src.memory.llm.errors import ContextWindowOverflowError, ProviderRequestError
from src.memory.retrieval.facade import MemoryRetrievalFacade
from src.memory.runtime.agent_runtime import AgentRuntime
from src.memory.runtime.models import (
    ToolCallResult,
    ToolCallStatus,
    TurnRequest,
    TurnStage,
    TurnStageStatus,
    TurnStageTrace,
)
from src.memory.task_state.models import TaskStateProposalBatch
from src.memory.task_state.repository import TaskStateRepository
from src.memory.task_state.service import TaskStateService
from src.memory.task_state.updater import TaskStateInterpreter


class FakeAgentReasoner:
    def __init__(self, actions: List[AgentAction]) -> None:
        self.actions = list(actions)
        self.calls: List[Any] = []

    def reason(self, input_data: Any) -> AgentAction:
        self.calls.append(input_data)
        if not self.actions:
            return AgentAction(action_kind=AgentActionKind.FINAL_RESPONSE, content="Fallback response")
        return self.actions.pop(0)


class FailingProviderReasoner:
    def reason(self, input_data: Any) -> AgentAction:
        raise ProviderRequestError(
            "raw-secret-provider-detail",
            provider="test-provider",
            operation="agent_action",
            status_code=503,
        )


class MockToolExecutor:
    def __init__(self, return_content: str = "Tool Execution Result") -> None:
        self.execution_count = 0
        self.last_request: Any = None
        self.return_content = return_content

    def execute(self, request: ToolExecutionRequest) -> ToolCallResult:
        self.execution_count += 1
        self.last_request = request
        return ToolCallResult(
            tool_call_id=request.tool_call_id,
            tool_name=request.tool_name,
            arguments=request.arguments,
            status=ToolCallStatus.SUCCESS,
            result_content=self.return_content,
        )


class FakeRetrievalFacade(MemoryRetrievalFacade):
    def retrieve_evidence(
        self, query: str, *, top_k: int = 8, query_id: str = "current-query", recent_context: Tuple[Any, ...] = ()
    ) -> EvidenceBundle:
        return EvidenceBundle(
            schema_version="v1",
            request_id=f"bundle_{query_id}",
            query=query,
            selected=(),
            sufficiency=EvidenceSufficiency(status=EvidenceSufficiencyStatus.SUFFICIENT),
        )


class FakeTaskInterpreter(TaskStateInterpreter):
    def interpret(self, event: Any, context: Any) -> TaskStateProposalBatch:
        return TaskStateProposalBatch(
            task_id=event.task_id,
            event_id=event.event_id,
            proposals=(),
        )


@pytest.fixture
def tmp_dbs():
    with tempfile.TemporaryDirectory() as tmpdir:
        t3_path = os.path.join(tmpdir, "t3_task.db")
        journal_path = os.path.join(tmpdir, "agent_journal.db")

        t3_repo = TaskStateRepository(t3_path)
        t3_service = TaskStateService(t3_repo)
        t3_interpreter = FakeTaskInterpreter()
        t2_facade = FakeRetrievalFacade()

        runtime = AgentRuntime(
            task_service=t3_service,
            retrieval_facade=t2_facade,
            task_interpreter=t3_interpreter,
        )

        journal = AgentExecutionJournal(journal_path)
        registry = ToolRegistry()

        yield runtime, journal, registry, t3_service


def test_stable_identities_sha256_cross_process():
    ids1 = derive_stable_identities("turn_100", "task_abc", step_index=0, tool_name="search", tool_arguments={"query": "shanghai"})
    ids2 = derive_stable_identities("turn_100", "task_abc", step_index=0, tool_name="search", tool_arguments={"query": "shanghai"})
    ids3_different_step = derive_stable_identities("turn_100", "task_abc", step_index=1, tool_name="search", tool_arguments={"query": "shanghai"})

    assert ids1["agent_run_id"] == ids2["agent_run_id"]
    assert ids1["tool_call_id"] == ids2["tool_call_id"]
    assert ids1["idempotency_key"] == ids2["idempotency_key"]

    # Different logical step index yields different tool_call_id and idempotency_key
    assert ids1["agent_step_id"] != ids3_different_step["agent_step_id"]
    assert ids1["tool_call_id"] != ids3_different_step["tool_call_id"]
    assert ids1["idempotency_key"] != ids3_different_step["idempotency_key"]


def test_main_agent_no_tool_final_response(tmp_dbs):
    runtime, journal, registry, t3_service = tmp_dbs

    reasoner = FakeAgentReasoner([
        AgentAction(action_kind=AgentActionKind.FINAL_RESPONSE, content="推荐静安区的酒店。")
    ])
    agent = MainAgent(runtime, journal, registry, reasoner)

    req = AgentRequest(turn_request=TurnRequest(turn_id="turn_no_tool_01", user_input="帮我查找上海精选酒店"))
    res = agent.run(req)

    assert res.status == AgentResultStatus.SUCCESS
    assert res.final_response == "推荐静安区的酒店。"
    assert res.step_count == 1
    assert len(reasoner.calls) == 1


def test_context_overflow_compacts_and_retries_once(tmp_dbs):
    runtime, journal, registry, _ = tmp_dbs

    class OverflowThenFinalReasoner:
        def __init__(self):
            self.calls = 0

        def reason(self, input_data):
            self.calls += 1
            if self.calls == 1:
                raise ContextWindowOverflowError(
                    "synthetic overflow",
                    provider="test",
                    operation="agent_action",
                    status_code=400,
                )
            return AgentAction(action_kind=AgentActionKind.FINAL_RESPONSE, content="重试成功")

    reasoner = OverflowThenFinalReasoner()
    agent = MainAgent(runtime, journal, registry, reasoner)
    rebuilt = []
    req = AgentRequest(
        turn_request=TurnRequest(turn_id="turn_overflow_01", user_input="继续"),
        context_overflow_handler=lambda: rebuilt.append(True) or SimpleNamespace(rebuilt=True),
    )

    res = agent.run(req)
    assert res.status == AgentResultStatus.SUCCESS
    assert res.final_response == "重试成功"
    assert reasoner.calls == 2
    assert rebuilt == [True]


def test_task_update_warning_makes_final_response_truthful():
    context = SimpleNamespace(
        stage_traces=(
            TurnStageTrace(
                stage=TurnStage.TASK_UPDATE,
                status=TurnStageStatus.WARNING,
                warnings=("state rejected",),
            ),
        )
    )
    response = AgentLoopController._truthful_final_response("已更新行程。", context)
    assert "已更新行程。" in response
    assert "任务状态未写入" in response


def test_reasoner_provider_failure_is_safely_logged(tmp_dbs, caplog):
    runtime, journal, registry, t3_service = tmp_dbs
    caplog.set_level(logging.ERROR)
    agent = MainAgent(runtime, journal, registry, FailingProviderReasoner())

    result = agent.run(
        AgentRequest(
            turn_request=TurnRequest(
                turn_id="turn_provider_failure_log",
                user_input="触发安全失败日志",
            )
        )
    )

    assert result.status == AgentResultStatus.FAILED
    assert result.error_info.code == "PROVIDER_REQUEST_ERROR"
    assert "agent_reasoner_provider_failed" in caplog.text
    assert "raw-secret-provider-detail" not in caplog.text


def test_main_agent_read_only_tool_execution(tmp_dbs):
    runtime, journal, registry, t3_service = tmp_dbs

    search_executor = MockToolExecutor(return_content="找到了3家上海高分酒店: 酒店A, 酒店B")
    registry.register(
        ToolSpec(
            name="hotel_search",
            description="搜索符合条件的酒店",
            parameters_schema={"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
            effect=ToolEffect.READ_ONLY,
        ),
        search_executor,
    )

    reasoner = FakeAgentReasoner([
        AgentAction(action_kind=AgentActionKind.TOOL_CALL, tool_name="hotel_search", tool_arguments={"city": "上海"}),
        AgentAction(action_kind=AgentActionKind.FINAL_RESPONSE, content="为您找到以下酒店：酒店A和酒店B。"),
    ])
    agent = MainAgent(runtime, journal, registry, reasoner)

    req = AgentRequest(turn_request=TurnRequest(turn_id="turn_tool_01", user_input="查下上海酒店"))
    res = agent.run(req)

    assert res.status == AgentResultStatus.SUCCESS
    assert res.final_response == "为您找到以下酒店：酒店A和酒店B。"
    assert res.step_count == 2
    assert search_executor.execution_count == 1
    assert search_executor.last_request.arguments == {"city": "上海"}


def test_duplicate_read_only_tool_call_reuses_successful_result(tmp_dbs):
    runtime, journal, registry, t3_service = tmp_dbs

    search_executor = MockToolExecutor(return_content="唯一可信结果")
    registry.register(
        ToolSpec(
            name="hotel_search",
            description="搜索符合条件的酒店",
            parameters_schema={
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
            effect=ToolEffect.READ_ONLY,
        ),
        search_executor,
    )
    duplicate_call = AgentAction(
        action_kind=AgentActionKind.TOOL_CALL,
        tool_name="hotel_search",
        tool_arguments={"city": "上海"},
    )
    reasoner = FakeAgentReasoner([duplicate_call, duplicate_call])
    agent = MainAgent(runtime, journal, registry, reasoner)

    result = agent.run(
        AgentRequest(
            turn_request=TurnRequest(
                turn_id="turn_duplicate_read_only",
                user_input="查下上海酒店",
            )
        )
    )

    assert result.status == AgentResultStatus.SUCCESS
    assert result.final_response == "唯一可信结果"
    assert result.trace_metadata["duplicate_read_only_tool_call_suppressed"] is True
    assert search_executor.execution_count == 1
    assert len(result.executed_tool_call_ids) == 1
    assert len(reasoner.calls) == 2


def test_user_facing_read_only_tool_result_returns_without_second_reasoner_call(tmp_dbs):
    runtime, journal, registry, t3_service = tmp_dbs

    calculator = MockToolExecutor(return_content="计算结果: 56088")
    registry.register(
        ToolSpec(
            name="calculator",
            description="安全计算算术表达式",
            parameters_schema={
                "type": "object",
                "properties": {"expression": {"type": "string"}},
                "required": ["expression"],
            },
            effect=ToolEffect.READ_ONLY,
            returns_user_facing_result=True,
        ),
        calculator,
    )
    reasoner = FakeAgentReasoner([
        AgentAction(
            action_kind=AgentActionKind.TOOL_CALL,
            tool_name="calculator",
            tool_arguments={"expression": "123 * 456"},
        ),
    ])
    agent = MainAgent(runtime, journal, registry, reasoner)

    result = agent.run(
        AgentRequest(
            turn_request=TurnRequest(
                turn_id="turn_direct_calculator",
                user_input="计算 123 * 456",
            )
        )
    )

    assert result.status == AgentResultStatus.SUCCESS
    assert result.final_response == "计算结果: 56088"
    assert result.trace_metadata["direct_tool_result"] is True
    assert calculator.execution_count == 1
    assert len(reasoner.calls) == 1


def test_side_effect_tool_cannot_be_marked_for_direct_return():
    with pytest.raises(ValueError, match="Only READ_ONLY"):
        ToolSpec(
            name="unsafe_direct",
            description="invalid test tool",
            parameters_schema={"type": "object"},
            effect=ToolEffect.SIDE_EFFECT,
            returns_user_facing_result=True,
        )


def test_main_agent_task_selection_required(tmp_dbs):
    runtime, journal, registry, t3_service = tmp_dbs

    # Create two active tasks in Task3
    t3_service.create_task("task_alpha")
    t3_service.create_task("task_beta")

    reasoner = FakeAgentReasoner([])
    agent = MainAgent(runtime, journal, registry, reasoner)

    # Turn without task_id when >1 task active triggers ambiguity error
    req = AgentRequest(turn_request=TurnRequest(turn_id="turn_ambiguous_01", user_input="更新一下我的需求"))
    res = agent.run(req)

    assert res.status == AgentResultStatus.TASK_SELECTION_REQUIRED
    assert res.error_info.code == "TASK_SELECTION_REQUIRED"
    # Reasoner is NOT invoked when runtime resolution fails
    assert len(reasoner.calls) == 0


def test_main_agent_side_effect_confirmation_halt(tmp_dbs):
    runtime, journal, registry, t3_service = tmp_dbs

    book_executor = MockToolExecutor(return_content="Booking Completed")
    registry.register(
        ToolSpec(
            name="hotel_book",
            description="预订指定酒店",
            parameters_schema={"type": "object", "properties": {"hotel_id": {"type": "string"}}, "required": ["hotel_id"]},
            effect=ToolEffect.SIDE_EFFECT,
        ),
        book_executor,
    )

    reasoner = FakeAgentReasoner([
        AgentAction(action_kind=AgentActionKind.TOOL_CALL, tool_name="hotel_book", tool_arguments={"hotel_id": "h_123"}),
    ])
    agent = MainAgent(runtime, journal, registry, reasoner)

    req = AgentRequest(turn_request=TurnRequest(turn_id="turn_book_01", user_input="预订酒店 h_123"))
    res = agent.run(req)

    assert res.status == AgentResultStatus.AWAITING_CONFIRMATION
    assert res.pending_action is not None
    assert res.pending_action.tool_name == "hotel_book"
    # Physical execution is HALTED until confirmed
    assert book_executor.execution_count == 0

    # PendingAction persisted in journal
    saved_pending = journal.get_pending_action(res.pending_action.pending_action_id)
    assert saved_pending is not None
    assert saved_pending.tool_name == "hotel_book"


def test_main_agent_confirmation_flow_exact_once(tmp_dbs):
    runtime, journal, registry, t3_service = tmp_dbs

    book_executor = MockToolExecutor(return_content="Booking Confirmation #998822")
    registry.register(
        ToolSpec(
            name="hotel_book",
            description="预订指定酒店",
            parameters_schema={"type": "object", "properties": {"hotel_id": {"type": "string"}}, "required": ["hotel_id"]},
            effect=ToolEffect.SIDE_EFFECT,
        ),
        book_executor,
    )

    # 1. Turn 1: Triggers AWAITING_CONFIRMATION
    reasoner_turn1 = FakeAgentReasoner([
        AgentAction(action_kind=AgentActionKind.TOOL_CALL, tool_name="hotel_book", tool_arguments={"hotel_id": "h_999"}),
    ])
    agent1 = MainAgent(runtime, journal, registry, reasoner_turn1)
    req1 = AgentRequest(turn_request=TurnRequest(turn_id="turn_confirm_01", user_input="我想订 h_999 酒店"))
    res1 = agent1.run(req1)

    assert res1.status == AgentResultStatus.AWAITING_CONFIRMATION
    pending_id = res1.pending_action.pending_action_id

    # 2. Turn 2: User confirms the pending action
    reasoner_turn2 = FakeAgentReasoner([
        AgentAction(action_kind=AgentActionKind.FINAL_RESPONSE, content="酒店已成功为您预订完成，单号 #998822！")
    ])
    agent2 = MainAgent(runtime, journal, registry, reasoner_turn2)
    req2 = AgentRequest(
        turn_request=TurnRequest(turn_id="turn_confirm_02", user_input="确认预订"),
        confirmation=ActionConfirmation(pending_action_id=pending_id, decision=ConfirmationDecision.CONFIRM),
    )
    res2 = agent2.run(req2)

    assert res2.status == AgentResultStatus.SUCCESS
    assert res2.final_response == "酒店已成功为您预订完成，单号 #998822！"
    assert book_executor.execution_count == 1
    assert book_executor.last_request.arguments == {"hotel_id": "h_999"}


def test_main_agent_rejection_flow(tmp_dbs):
    runtime, journal, registry, t3_service = tmp_dbs

    book_executor = MockToolExecutor(return_content="Booking Result")
    registry.register(
        ToolSpec(
            name="hotel_book",
            description="预订指定酒店",
            parameters_schema={"type": "object", "properties": {"hotel_id": {"type": "string"}}, "required": ["hotel_id"]},
            effect=ToolEffect.SIDE_EFFECT,
        ),
        book_executor,
    )

    # Turn 1: Trigger confirmation
    reasoner1 = FakeAgentReasoner([
        AgentAction(action_kind=AgentActionKind.TOOL_CALL, tool_name="hotel_book", tool_arguments={"hotel_id": "h_777"}),
    ])
    agent1 = MainAgent(runtime, journal, registry, reasoner1)
    res1 = agent1.run(AgentRequest(turn_request=TurnRequest(turn_id="turn_reject_01", user_input="预订 h_777")))
    pending_id = res1.pending_action.pending_action_id

    # Turn 2: User rejects
    reasoner2 = FakeAgentReasoner([
        AgentAction(action_kind=AgentActionKind.FINAL_RESPONSE, content="好的，已为您取消预订。")
    ])
    agent2 = MainAgent(runtime, journal, registry, reasoner2)
    res2 = agent2.run(
        AgentRequest(
            turn_request=TurnRequest(turn_id="turn_reject_02", user_input="取消，不订了"),
            confirmation=ActionConfirmation(pending_action_id=pending_id, decision=ConfirmationDecision.REJECT),
        )
    )

    assert res2.status == AgentResultStatus.CANCELLED
    # Zero physical execution occurred
    assert book_executor.execution_count == 0


def test_main_agent_multi_task_confirmation(tmp_dbs):
    runtime, journal, registry, t3_service = tmp_dbs

    # Two tasks exist
    t3_service.create_task("task_hotel_booking")
    t3_service.create_task("task_flight_booking")

    book_executor = MockToolExecutor(return_content="Booked")
    registry.register(
        ToolSpec(
            name="hotel_book",
            description="预订酒店",
            parameters_schema={"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
            effect=ToolEffect.SIDE_EFFECT,
        ),
        book_executor,
    )

    # Create pending action in task_hotel_booking
    ids = derive_stable_identities("turn_multi_01", "task_hotel_booking", step_index=0, tool_name="hotel_book", tool_arguments={"id": "h_10"})
    journal.register_run(ids["agent_run_id"], "turn_multi_01", "task_hotel_booking")
    pending = PendingAction(
        pending_action_id="pending_multi_test",
        agent_run_id=ids["agent_run_id"],
        task_id="task_hotel_booking",
        agent_step_id=ids["agent_step_id"],
        tool_call_id=ids["tool_call_id"],
        tool_name="hotel_book",
        validated_arguments={"id": "h_10"},
        arguments_hash=ids["arguments_hash"],
        idempotency_key=ids["idempotency_key"],
        effect="SIDE_EFFECT",
    )
    journal.add_pending_action(pending)

    reasoner = FakeAgentReasoner([
        AgentAction(action_kind=AgentActionKind.FINAL_RESPONSE, content="预订完成")
    ])
    agent = MainAgent(runtime, journal, registry, reasoner)

    # Confirmation request omits task_id but provides pending_action_id
    req = AgentRequest(
        turn_request=TurnRequest(turn_id="turn_multi_02", user_input="确认预订"),
        confirmation=ActionConfirmation(pending_action_id="pending_multi_test", decision=ConfirmationDecision.CONFIRM),
    )
    res = agent.run(req)

    # Multi-task ambiguity is bypassed because pending action explicitly resolves task_id=task_hotel_booking
    assert res.status == AgentResultStatus.SUCCESS
    assert res.task_id == "task_hotel_booking"
    assert book_executor.execution_count == 1


def test_main_agent_non_zero_step_confirmation_resume_identity(tmp_dbs):
    runtime, journal, registry, t3_service = tmp_dbs

    search_executor = MockToolExecutor("Found 2 hotels")
    book_executor = MockToolExecutor("Booked h_99")

    registry.register(
        ToolSpec(
            name="hotel_search",
            description="搜索酒店",
            parameters_schema={"type": "object", "properties": {"city": {"type": "string"}}},
            effect=ToolEffect.READ_ONLY,
        ),
        search_executor,
    )
    registry.register(
        ToolSpec(
            name="hotel_book",
            description="预订酒店",
            parameters_schema={"type": "object", "properties": {"hotel_id": {"type": "string"}}, "required": ["hotel_id"]},
            effect=ToolEffect.SIDE_EFFECT,
        ),
        book_executor,
    )

    # Turn 1: Step 0 search -> Step 1 book -> AWAITING_CONFIRMATION
    reasoner1 = FakeAgentReasoner([
        AgentAction(action_kind=AgentActionKind.TOOL_CALL, tool_name="hotel_search", tool_arguments={"city": "Shanghai"}),
        AgentAction(action_kind=AgentActionKind.TOOL_CALL, tool_name="hotel_book", tool_arguments={"hotel_id": "h_99"}),
    ])
    agent1 = MainAgent(runtime, journal, registry, reasoner1)
    res1 = agent1.run(AgentRequest(turn_request=TurnRequest(turn_id="turn_nz_01", user_input="帮我找酒店并预订")))

    assert res1.status == AgentResultStatus.AWAITING_CONFIRMATION
    assert search_executor.execution_count == 1
    assert book_executor.execution_count == 0
    pending = res1.pending_action
    assert pending is not None
    assert pending.tool_name == "hotel_book"

    # Turn 2: User confirms. Simulate restart by constructing a fresh MainAgent instance
    reasoner2 = FakeAgentReasoner([
        AgentAction(action_kind=AgentActionKind.FINAL_RESPONSE, content="预订成功！")
    ])
    agent2 = MainAgent(runtime, journal, registry, reasoner2)
    res2 = agent2.run(
        AgentRequest(
            turn_request=TurnRequest(turn_id="turn_nz_02", user_input="确认预订"),
            confirmation=ActionConfirmation(pending_action_id=pending.pending_action_id, decision=ConfirmationDecision.CONFIRM),
        )
    )

    assert res2.status == AgentResultStatus.SUCCESS
    assert book_executor.execution_count == 1
    req = book_executor.last_request
    assert req.tool_call_id == pending.tool_call_id
    assert req.agent_step_id == pending.agent_step_id
    assert req.idempotency_key == pending.idempotency_key


def test_main_agent_strict_action_cross_field_rejection():
    # FINAL_RESPONSE with tool_name
    with pytest.raises(ValueError, match="FINAL_RESPONSE action cannot specify"):
        from src.memory.agent.reasoner import OpenAICompatibleAgentReasoner
        OpenAICompatibleAgentReasoner._parse_and_validate_action({
            "action_kind": "FINAL_RESPONSE",
            "content": "Hi",
            "tool_name": "search",
        })

    # TOOL_CALL with content
    with pytest.raises(ValueError, match="TOOL_CALL action cannot specify"):
        from src.memory.agent.reasoner import OpenAICompatibleAgentReasoner
        OpenAICompatibleAgentReasoner._parse_and_validate_action({
            "action_kind": "TOOL_CALL",
            "tool_name": "search",
            "tool_arguments": {},
            "content": "Searching...",
        })


def test_strict_agent_action_schema_preserves_non_empty_tool_arguments():
    from src.memory.agent.reasoner import (
        AGENT_ACTION_RESPONSE_SCHEMA,
        OpenAICompatibleAgentReasoner,
    )

    arguments_schema = AGENT_ACTION_RESPONSE_SCHEMA["properties"]["tool_arguments"]
    assert arguments_schema == {"type": ["string", "null"]}

    action = OpenAICompatibleAgentReasoner._parse_and_validate_action({
        "action_kind": "TOOL_CALL",
        "content": None,
        "tool_name": "calculator",
        "tool_arguments": '{"expression":"1 + 1"}',
    })
    assert action.tool_arguments == {"expression": "1 + 1"}


@pytest.mark.parametrize("payload", ["not-json", "[]", "42"])
def test_serialized_tool_arguments_must_decode_to_an_object(payload):
    from src.memory.agent.reasoner import OpenAICompatibleAgentReasoner

    with pytest.raises(ValueError, match="tool_arguments"):
        OpenAICompatibleAgentReasoner._parse_and_validate_action({
            "action_kind": "TOOL_CALL",
            "content": None,
            "tool_name": "calculator",
            "tool_arguments": payload,
        })


def test_agent_prompt_matches_strict_serialized_argument_contract():
    from src.memory.agent.prompt_composer import PromptComposer

    prompt = PromptComposer()._build_system_prompt(())
    assert '"tool_name": null' in prompt
    assert '"tool_arguments": null' in prompt
    assert '"tool_arguments": "{\\"expression\\":\\"1 + 1\\"}"' in prompt


def test_agent_prompt_separates_user_state_from_assistant_suggestions():
    from src.memory.agent.prompt_composer import PromptComposer

    prompt = PromptComposer()._build_system_prompt(())

    assert "messages are proposals, explanations" in prompt
    assert "Do not report an Assistant suggestion as a completed user decision" in prompt


def test_agent_prompt_separates_task_state_from_workspace_files():
    from src.memory.agent.prompt_composer import PromptComposer

    prompt = PromptComposer()._build_system_prompt(())

    assert "structured Task State (Task 3)" in prompt
    assert "Do not call create_file for an internal task ledger" in prompt
    assert "external workspace file" in prompt


def test_agent_execution_journal_lifecycle_and_foreign_keys():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "journal_test.db")

        # 1. Fresh DB has schema version 4
        journal = AgentExecutionJournal(db_path)
        with journal._connect() as conn:
            ver = conn.execute("PRAGMA user_version").fetchone()[0]
            assert ver == 4

        # 2. Reopen DB works cleanly
        journal2 = AgentExecutionJournal(db_path)
        assert journal2.get_run("non_existent") is None

        # 3. Foreign key enforcement
        with pytest.raises(Exception):
            with journal2._connect() as conn:
                conn.execute(
                    "INSERT INTO pending_actions(pending_action_id, agent_run_id, task_id, agent_step_id, tool_call_id, tool_name, arguments_json, arguments_hash, idempotency_key, effect, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("p1", "invalid_run_id", "t1", "s1", "c1", "tool_x", "{}", "hash", "key", "SIDE_EFFECT", "PENDING", 0.0),
                )
                conn.commit()

        # 4. Future schema refusal
        with journal2._connect() as conn:
            conn.execute("PRAGMA user_version = 99")
            conn.commit()

        with pytest.raises(ValueError, match="unsupported agent-journal schema version 99"):
            AgentExecutionJournal(db_path)


def test_agent_execution_journal_claim_identity_mismatch():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "mismatch_test.db")
        journal = AgentExecutionJournal(db_path)
        journal.register_run("run_1", "turn_1", "task_1")

        claimed, status, _ = journal.claim_execution("run_1", "step_0", "call_1", "tool_a", "hash_a", "idemp_1", arguments_json="{}", tool_effect="READ_ONLY", idempotency_support="NONE")
        assert claimed is True

        # Same call_1 but mismatching arguments_hash throws ValueError
        with pytest.raises(ValueError, match="journal integrity error: tool execution claim identity mismatch"):
            journal.claim_execution("run_1", "step_0", "call_1", "tool_a", "mismatch_hash", "idemp_1", arguments_json="{}", tool_effect="READ_ONLY", idempotency_support="NONE")


def test_main_agent_journal_atomic_claim_concurrency(tmp_dbs):
    runtime, journal, registry, t3_service = tmp_dbs

    journal.register_run("run_test", "turn_test", "task_test")

    # 1. First claim succeeds
    claimed1, status1, rec1 = journal.claim_execution(
        agent_run_id="run_test",
        agent_step_id="step_0",
        tool_call_id="call_test_100",
        tool_name="search",
        arguments_hash="hash_123",
        idempotency_key="idemp_100",
        arguments_json="{}",
        tool_effect="READ_ONLY",
        idempotency_support="NONE",
    )
    assert claimed1 is True
    assert status1 == "DISPATCHING"

    # 2. Second claim for same idempotency_key fails atomically
    claimed2, status2, rec2 = journal.claim_execution(
        agent_run_id="run_test",
        agent_step_id="step_0",
        tool_call_id="call_test_100",
        tool_name="search",
        arguments_hash="hash_123",
        idempotency_key="idemp_100",
        arguments_json="{}",
        tool_effect="READ_ONLY",
        idempotency_support="NONE",
    )
    assert claimed2 is False
    assert status2 == "DISPATCHING"


def test_main_agent_crash_unknown_status(tmp_dbs):
    runtime, journal, registry, t3_service = tmp_dbs

    t3_service.create_task("task_crash")
    ids = derive_stable_identities("turn_crash", "task_crash", step_index=0, tool_name="non_idempotent_tool", tool_arguments={})

    # Simulate a crash where DISPATCHING was committed but tool had idempotency_support == NONE
    journal.register_run(ids["agent_run_id"], "turn_crash", "task_crash")
    journal.claim_execution(
        agent_run_id=ids["agent_run_id"],
        agent_step_id=ids["agent_step_id"],
        tool_call_id=ids["tool_call_id"],
        tool_name="non_idempotent_tool",
        arguments_hash=ids["arguments_hash"],
        idempotency_key=ids["idempotency_key"],
        arguments_json="{}",
        tool_effect="READ_ONLY",
        idempotency_support="NONE",
    )

    non_idemp_executor = MockToolExecutor()
    registry.register(
        ToolSpec(
            name="non_idempotent_tool",
            description="Non idempotent operation",
            parameters_schema={"type": "object"},
            effect=ToolEffect.READ_ONLY,
            idempotency_support=IdempotencySupport.NONE,
        ),
        non_idemp_executor,
    )

    reasoner = FakeAgentReasoner([
        AgentAction(action_kind=AgentActionKind.TOOL_CALL, tool_name="non_idempotent_tool", tool_arguments={})
    ])
    agent = MainAgent(runtime, journal, registry, reasoner)

    # Executing the same tool call again triggers EXECUTION_STATUS_UNKNOWN
    res = agent.run(AgentRequest(turn_request=TurnRequest(turn_id="turn_crash", user_input="重试任务", task_id="task_crash")))

    assert res.status == AgentResultStatus.EXECUTION_STATUS_UNKNOWN
    assert res.error_info.code == "EXECUTION_STATUS_UNKNOWN"
    # Executor is NOT invoked again
    assert non_idemp_executor.execution_count == 0


def test_tool_result_refreshes_task_context_before_next_reasoner(tmp_dbs):
    runtime, journal, registry, t3_service = tmp_dbs

    t3_service.create_task("task_ctx_ref")
    executor = MockToolExecutor(return_content="Hotel search results")
    registry.register(
        ToolSpec(
            name="hotel_search",
            description="Search hotel",
            parameters_schema={"type": "object"},
            effect=ToolEffect.READ_ONLY,
        ),
        executor,
    )

    # Injected fake task_interpreter that emits a progress update proposal upon tool event
    from src.memory.task_state.models import TaskStateProposalBatch, TaskStateProposal, ProposalAction, EntryKind, ProposalBasis
    class MutatingTaskInterpreter:
        identity = "fake_mutating"
        version = "v1"
        def interpret(self, event, context):
            if event.source_kind.value == "TOOL":
                return TaskStateProposalBatch(
                    task_id=event.task_id,
                    event_id=event.event_id,
                    proposals=[
                        TaskStateProposal(
                            proposal_id=f"prop_{event.event_id}",
                            task_id=event.task_id,
                            source_event_id=event.event_id,
                            action=ProposalAction.ADD,
                            entry_kind=EntryKind.PROGRESS,
                            content="hotel search results found",
                            basis=ProposalBasis.OBSERVED,
                        )
                    ],
                    warnings=[],
                )
            return TaskStateProposalBatch(task_id=event.task_id, event_id=event.event_id, proposals=[], warnings=[])

    runtime.task_interpreter = MutatingTaskInterpreter()

    # Reasoner step 1: TOOL_CALL hotel_search. Reasoner step 2: FINAL_RESPONSE.
    reasoner = FakeAgentReasoner([
        AgentAction(action_kind=AgentActionKind.TOOL_CALL, tool_name="hotel_search", tool_arguments={}),
        AgentAction(action_kind=AgentActionKind.FINAL_RESPONSE, content="Search done"),
    ])
    agent = MainAgent(runtime, journal, registry, reasoner)

    res = agent.run(AgentRequest(turn_request=TurnRequest(turn_id="turn_ctx_01", task_id="task_ctx_ref", user_input="搜索酒店")))

    assert res.status == AgentResultStatus.SUCCESS
    assert len(reasoner.calls) == 2
    # Verify that in reasoner call 2, turn_context contains updated Task3 progress entry!
    second_call_input = reasoner.calls[1]
    progress_entries = second_call_input.turn_context.structured_task_context.progress
    assert len(progress_entries) > 0
    assert any("hotel search results found" in p.content for p in progress_entries)


def test_confirmation_reconciliation_failure_preserves_root_identity(tmp_dbs):
    runtime, journal, registry, t3_service = tmp_dbs

    t3_service.create_task("task_conf_fail")
    book_executor = MockToolExecutor(return_content="Booked")
    registry.register(
        ToolSpec(
            name="hotel_book",
            description="Book hotel",
            parameters_schema={"type": "object", "properties": {"id": {"type": "string"}}},
            effect=ToolEffect.SIDE_EFFECT,
        ),
        book_executor,
    )

    # Turn 1: Root turn "turn_orig_99" -> AWAITING_CONFIRMATION
    reasoner1 = FakeAgentReasoner([
        AgentAction(action_kind=AgentActionKind.TOOL_CALL, tool_name="hotel_book", tool_arguments={"id": "h1"})
    ])
    agent1 = MainAgent(runtime, journal, registry, reasoner1)
    res1 = agent1.run(AgentRequest(turn_request=TurnRequest(turn_id="turn_orig_99", task_id="task_conf_fail", user_input="预订")))
    assert res1.status == AgentResultStatus.AWAITING_CONFIRMATION
    pending_id = res1.pending_action.pending_action_id
    orig_run_id = res1.agent_run_id

    # Inject Failing Task Interpreter before confirmation
    class FailingTaskInterpreter:
        def interpret(self, event, context):
            raise RuntimeError("Task state update failed completely.")

    runtime.task_interpreter = FailingTaskInterpreter()

    # Turn 2: Confirmation turn "turn_confirm_99"
    reasoner2 = FakeAgentReasoner([AgentAction(action_kind=AgentActionKind.FINAL_RESPONSE, content="Unreachable")])
    agent2 = MainAgent(runtime, journal, registry, reasoner2)
    res2 = agent2.run(
        AgentRequest(
            turn_request=TurnRequest(turn_id="turn_confirm_99", task_id="task_conf_fail", user_input="确认"),
            confirmation=ActionConfirmation(pending_action_id=pending_id, decision=ConfirmationDecision.CONFIRM),
        )
    )

    assert res2.status == AgentResultStatus.FAILED
    assert res2.root_turn_id == "turn_orig_99"  # Preserves original root turn ID!
    assert res2.request_turn_id == "turn_confirm_99"  # Current request turn ID!
    assert res2.agent_run_id == orig_run_id


def test_claim_requires_explicit_policy_snapshot(tmp_dbs):
    runtime, journal, registry, t3_service = tmp_dbs
    journal.register_run("run_policy", "turn_pol", "task_pol")

    # Calling claim_execution without required positional/keyword snapshot parameters raises TypeError
    with pytest.raises(TypeError):
        journal.claim_execution("run_policy", "step_0", "call_pol", "tool_a", "hash_a", "idemp_a")  # Missing arguments_json, tool_effect, idempotency_support


def test_claim_snapshot_mismatch_rejected(tmp_dbs):
    runtime, journal, registry, t3_service = tmp_dbs
    journal.register_run("run_snap_m", "turn_sm", "task_sm")

    claimed, status, _ = journal.claim_execution(
        "run_snap_m", "step_0", "call_sm", "tool_a", "hash_a", "idemp_sm",
        arguments_json='{"id":"1"}', tool_effect="SIDE_EFFECT", idempotency_support="NONE"
    )
    assert claimed is True

    # Same call_sm but mismatching tool_effect snapshot throws ValueError
    with pytest.raises(ValueError, match="journal configuration mismatch"):
        journal.claim_execution(
            "run_snap_m", "step_0", "call_sm", "tool_a", "hash_a", "idemp_sm",
            arguments_json='{"id":"1"}', tool_effect="READ_ONLY", idempotency_support="NONE"
        )


def test_terminal_reconciliation_query_is_deterministic(tmp_dbs):
    runtime, journal, registry, t3_service = tmp_dbs

    t3_service.create_task("task_det_q")
    journal.register_run("run_det_1", "turn_d1", "task_det_q")

    journal.claim_execution("run_det_1", "s1", "call_1", "tool_a", "h1", "idemp_1", "{}", "READ_ONLY", "NONE")
    journal.complete_execution("call_1", "SUCCEEDED", "Res 1")

    journal.claim_execution("run_det_1", "s2", "call_2", "tool_a", "h2", "idemp_2", "{}", "READ_ONLY", "NONE")
    journal.complete_execution("call_2", "FAILED", error_message="Res 2")

    unreconciled = journal.get_unreconciled_receipts_for_task("task_det_q")
    assert len(unreconciled) == 2
    assert [r["tool_call_id"] for r in unreconciled] == ["call_1", "call_2"]


def test_task_scoped_reconciliation_on_new_turn(tmp_dbs):
    runtime, journal, registry, t3_service = tmp_dbs

    t3_service.create_task("task_scoped_rec")
    ids_a = derive_stable_identities("turn_A", "task_scoped_rec", step_index=0, tool_name="hotel_book", tool_arguments={"id": "1"})

    # Run A physically executed and written to journal, but process crashed before Runtime ingestion
    journal.register_run(ids_a["agent_run_id"], "turn_A", "task_scoped_rec")
    journal.claim_execution(
        ids_a["agent_run_id"], ids_a["agent_step_id"], ids_a["tool_call_id"], "hotel_book", ids_a["arguments_hash"], ids_a["idempotency_key"],
        arguments_json='{"id":"1"}', tool_effect="SIDE_EFFECT", idempotency_support="NONE"
    )
    journal.complete_execution(ids_a["tool_call_id"], "SUCCEEDED", result_content="Booked Hotel 1")

    # User starts NEW Turn B (different turn_id, same task_id)
    reasoner_b = FakeAgentReasoner([AgentAction(action_kind=AgentActionKind.FINAL_RESPONSE, content="Done")])
    agent_b = MainAgent(runtime, journal, registry, reasoner_b)

    res_b = agent_b.run(AgentRequest(turn_request=TurnRequest(turn_id="turn_B", task_id="task_scoped_rec", user_input="继续")))

    assert res_b.status == AgentResultStatus.SUCCESS
    # Task3 has received Run A's tool result event!
    conn = t3_service.repository._connect()
    found_events = conn.execute("SELECT * FROM task_events WHERE event_id=?", (f"tool:{ids_a['tool_call_id']}:result",)).fetchall()
    conn.close()
    assert len(found_events) == 1

    # Receipt is now marked runtime_reconciled = 1
    rec = journal.get_receipt(ids_a["tool_call_id"])
    assert rec["runtime_reconciled"] == 1


def test_old_run_reconciliation_refreshes_new_turn_context(tmp_dbs):
    runtime, journal, registry, t3_service = tmp_dbs

    t3_service.create_task("task_old_run_no_pollute")
    ids_old = derive_stable_identities("turn_old", "task_old_run_no_pollute", step_index=0, tool_name="hotel_book", tool_arguments={"id": "1"})

    journal.register_run(ids_old["agent_run_id"], "turn_old", "task_old_run_no_pollute")
    journal.claim_execution(
        ids_old["agent_run_id"], ids_old["agent_step_id"], ids_old["tool_call_id"], "hotel_book", ids_old["arguments_hash"], ids_old["idempotency_key"],
        arguments_json='{"id":"1"}', tool_effect="SIDE_EFFECT", idempotency_support="NONE"
    )
    journal.complete_execution(ids_old["tool_call_id"], "SUCCEEDED", result_content="Old Run Hotel Result")

    reasoner_new = FakeAgentReasoner([AgentAction(action_kind=AgentActionKind.FINAL_RESPONSE, content="New Response")])
    agent_new = MainAgent(runtime, journal, registry, reasoner_new)

    res_new = agent_new.run(AgentRequest(turn_request=TurnRequest(turn_id="turn_new", task_id="task_old_run_no_pollute", user_input="新会话")))

    assert res_new.status == AgentResultStatus.SUCCESS
    assert len(reasoner_new.calls) == 1
    # Verify raw old ToolObservation was NOT appended to new turn's prompt observations!
    first_call_obs = reasoner_new.calls[0].tool_observations
    assert len(first_call_obs) == 0


def test_legacy_v3_dispatching_migrates_to_unknown():
    import sqlite3
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "v3_legacy.db")

        conn = sqlite3.connect(db_path)
        conn.execute("""CREATE TABLE agent_runs (agent_run_id TEXT PRIMARY KEY, root_turn_id TEXT NOT NULL, task_id TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL)""")
        conn.execute("""CREATE TABLE pending_actions (pending_action_id TEXT PRIMARY KEY, agent_run_id TEXT NOT NULL REFERENCES agent_runs(agent_run_id), task_id TEXT NOT NULL, agent_step_id TEXT NOT NULL, tool_call_id TEXT NOT NULL, tool_name TEXT NOT NULL, arguments_json TEXT NOT NULL, arguments_hash TEXT NOT NULL, idempotency_key TEXT NOT NULL, effect TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL)""")
        conn.execute("""CREATE TABLE tool_execution_receipts (tool_call_id TEXT PRIMARY KEY, agent_run_id TEXT NOT NULL REFERENCES agent_runs(agent_run_id), agent_step_id TEXT NOT NULL, tool_name TEXT NOT NULL, arguments_json TEXT NOT NULL DEFAULT '{}', arguments_hash TEXT NOT NULL, idempotency_key TEXT UNIQUE NOT NULL, tool_effect TEXT NOT NULL DEFAULT 'SIDE_EFFECT', idempotency_support TEXT NOT NULL DEFAULT 'NONE', status TEXT NOT NULL, runtime_reconciled INTEGER NOT NULL DEFAULT 0, result_json TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL)""")
        conn.execute("PRAGMA user_version = 3")
        conn.execute("INSERT INTO agent_runs VALUES ('run_v3', 'turn_v3', 'task_v3', 'RUNNING', 1.0, 1.0)")
        conn.execute("INSERT INTO tool_execution_receipts VALUES ('call_v3_disp', 'run_v3', 'step_v3', 'hotel_book', '{}', 'hash_v3', 'idemp_v3', 'SIDE_EFFECT', 'NONE', 'DISPATCHING', 0, NULL, 1.0, 1.0)")
        conn.commit()
        conn.close()

        # Journal initialization triggers v3 -> v4 migration
        journal = AgentExecutionJournal(db_path)

        with journal._connect() as connection:
            ver = connection.execute("PRAGMA user_version").fetchone()[0]
            assert ver == 4
            row = connection.execute("SELECT status FROM tool_execution_receipts WHERE tool_call_id='call_v3_disp'").fetchone()
            assert row["status"] == "UNKNOWN"


def test_unknown_never_reconciled_as_failed_tool_result(tmp_dbs):
    runtime, journal, registry, t3_service = tmp_dbs

    t3_service.create_task("task_unk_rec")
    journal.register_run("run_unk", "turn_unk", "task_unk_rec")
    journal.claim_execution("run_unk", "s1", "call_unk", "hotel_book", "h1", "idemp_unk", "{}", "SIDE_EFFECT", "NONE")

    # Manually transition claim to UNKNOWN in journal
    with journal._transaction() as conn:
        conn.execute("UPDATE tool_execution_receipts SET status='UNKNOWN' WHERE tool_call_id='call_unk'")

    # Reconcile call_unk
    controller = MainAgent(runtime, journal, registry, FakeAgentReasoner([])).loop_controller
    rec_res = controller._reconcile_tool_receipt(
        turn_context=runtime.process_user_turn(TurnRequest(turn_id="turn_unk", task_id="task_unk_rec", user_input="test")).context,
        agent_run_id="run_unk",
        task_id="task_unk_rec",
        tool_call_id="call_unk",
    )

    assert rec_res.ok is False
    assert rec_res.failure_result is not None
    assert rec_res.failure_result.error_info.code == "NON_TERMINAL_RECEIPT"

    # Verify Task3 received ZERO events for call_unk
    conn = t3_service.repository._connect()
    events = conn.execute("SELECT * FROM task_events WHERE event_id='tool:call_unk:result'").fetchall()
    conn.close()
    assert len(events) == 0


def test_failed_run_still_allows_runtime_reconciliation(tmp_dbs):
    runtime, journal, registry, t3_service = tmp_dbs

    t3_service.create_task("task_failed_run_rec")
    ids = derive_stable_identities("turn_fr_01", "task_failed_run_rec", step_index=0, tool_name="hotel_book", tool_arguments={"id": "1"})

    journal.register_run(ids["agent_run_id"], "turn_fr_01", "task_failed_run_rec")
    journal.claim_execution(ids["agent_run_id"], ids["agent_step_id"], ids["tool_call_id"], "hotel_book", ids["arguments_hash"], ids["idempotency_key"], "{}", "SIDE_EFFECT", "NONE")
    journal.complete_execution(ids["tool_call_id"], "SUCCEEDED", result_content="Result from Failed Run")

    # Update run status to FAILED
    journal.update_run_status(ids["agent_run_id"], "FAILED")

    # User starts new turn in same task
    reasoner = FakeAgentReasoner([AgentAction(action_kind=AgentActionKind.FINAL_RESPONSE, content="OK")])
    agent = MainAgent(runtime, journal, registry, reasoner)

    res = agent.run(AgentRequest(turn_request=TurnRequest(turn_id="turn_fr_02", task_id="task_failed_run_rec", user_input="继续")))

    assert res.status == AgentResultStatus.SUCCESS
    rec = journal.get_receipt(ids["tool_call_id"])
    assert rec["runtime_reconciled"] == 1


def test_bounded_receipt_is_valid_json(tmp_dbs):
    runtime, journal, registry, t3_service = tmp_dbs

    ids = derive_stable_identities("turn_bound_01", "task_bound")
    journal.register_run(ids["agent_run_id"], "turn_bound_01", "task_bound")
    journal.claim_execution(ids["agent_run_id"], ids["agent_step_id"], ids["tool_call_id"], "hotel_book", ids["arguments_hash"], ids["idempotency_key"], "{}", "READ_ONLY", "NONE")

    huge_text = "A" * 25000
    journal.complete_execution(ids["tool_call_id"], "SUCCEEDED", result_content=huge_text)

    # Fetch from SQLite and verify valid JSON
    with journal._connect() as conn:
        row = conn.execute("SELECT result_json FROM tool_execution_receipts WHERE tool_call_id=?", (ids["tool_call_id"],)).fetchone()
        parsed = json.loads(row["result_json"])
        assert parsed["status"] == "SUCCEEDED"
        assert "truncated from 25000 chars" in parsed["result_content"]


def test_ToolSpec_requires_explicit_effect():
    with pytest.raises(TypeError):
        ToolSpec(name="test_tool", description="desc", parameters_schema={})  # Missing effect argument


def test_duplicate_tool_registration_rejected():
    registry = ToolRegistry()
    executor = MockToolExecutor()
    spec = ToolSpec(name="search", description="desc", parameters_schema={}, effect=ToolEffect.READ_ONLY)
    registry.register(spec, executor)

    with pytest.raises(ValueError, match="already registered"):
        registry.register(spec, executor)


def test_jsonschema_invalid_schema_registration_rejected():
    registry = ToolRegistry()
    executor = MockToolExecutor()
    bad_spec = ToolSpec(
        name="bad_schema_tool",
        description="desc",
        parameters_schema={"type": "non_existent_type"},
        effect=ToolEffect.READ_ONLY,
    )

    with pytest.raises(ValueError, match="Invalid parameters_schema"):
        registry.register(bad_spec, executor)
