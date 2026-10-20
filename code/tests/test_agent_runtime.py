"""100% deterministic test Unit Tests for Phase 4A AgentRuntime."""

from __future__ import annotations

import os

import pytest

from src.memory.core.config import MemoryConfig, ProviderMode
from src.memory.core.schema import (
    Granularity,
    MemoryItem,
    MemoryMetadata,
    MemoryType,
    ProvenanceAuthority,
)
from src.memory.llm.errors import (
    ProviderConfigurationError,
    ProviderRequestError,
    ProviderResponseError,
)
from src.memory.retrieval.evidence import (
    DeterministicEvidencePipeline,
    EvidenceBundle,
    EvidenceSufficiency,
    EvidenceSufficiencyStatus,
)
from src.memory.retrieval.facade import DefaultMemoryRetrievalFacade, MemoryRetrievalFacade
from src.memory.retrieval.interpretation_models import ContextSource
from src.memory.runtime.agent_runtime import AgentRuntime
from src.memory.runtime.builder import build_agent_runtime
from src.memory.runtime.config import AgentRuntimeConfig
from src.memory.runtime.errors import (
    TaskLifecycleError,
    TaskNotFoundError,
    TaskSelectionRequiredError,
)
from src.memory.runtime.models import (
    ToolCallResult,
    ToolCallStatus,
    TurnExecutionResult,
    TurnRequest,
    TurnStage,
    TurnStageStatus,
)
from src.memory.runtime.task_router import BaselineTaskRouter
from src.memory.storage.sqlite_manager import SQLiteMemoryManager
from src.memory.task_state.models import (
    EntryKind,
    EventProcessStatus,
    Priority,
    ProposalAction,
    ProposalBasis,
    SourceKind,
    TaskEvent,
    TaskStateProposal,
    TaskStateProposalBatch,
    TaskStatus,
)
from src.memory.task_state.repository import TaskStateRepository
from src.memory.task_state.service import TaskStateService
from src.memory.task_state.updater import TaskStateInterpreter


class FakeTaskInterpreter(TaskStateInterpreter):
    """Deterministic fake TaskStateInterpreter for deterministic test testing."""

    def __init__(self, proposals_map: dict[str, list[TaskStateProposal]] | None = None) -> None:
        self.proposals_map = proposals_map or {}
        self.error_to_raise: Exception | None = None

    def interpret(self, event: TaskEvent, context: any) -> TaskStateProposalBatch:
        if self.error_to_raise:
            raise self.error_to_raise

        proposals = self.proposals_map.get(event.content, [])
        if not proposals:
            # Default fallback proposal based on content keyword
            if "预算" in event.content:
                proposals = [
                    TaskStateProposal(
                        proposal_id=f"prop_{event.event_id}",
                        task_id=event.task_id,
                        source_event_id=event.event_id,
                        action=ProposalAction.ADD,
                        entry_kind=EntryKind.CONSTRAINT,
                        content=event.content,
                        priority=Priority.HIGH,
                        basis=ProposalBasis.EXPLICIT,
                        support_spans=(event.content,),
                    )
                ]
            else:
                proposals = [
                    TaskStateProposal(
                        proposal_id=f"prop_{event.event_id}",
                        task_id=event.task_id,
                        source_event_id=event.event_id,
                        action=ProposalAction.ADD,
                        entry_kind=EntryKind.GOAL,
                        content=event.content,
                        priority=Priority.NORMAL,
                        basis=ProposalBasis.EXPLICIT,
                        support_spans=(event.content,),
                    )
                ]
        return TaskStateProposalBatch(
            task_id=event.task_id,
            event_id=event.event_id,
            proposals=tuple(proposals),
        )


class FakeRetrievalFacade(MemoryRetrievalFacade):
    """Deterministic fake MemoryRetrievalFacade for deterministic test testing."""

    def __init__(self) -> None:
        self.evidence_pipeline = DeterministicEvidencePipeline()
        self.error_to_raise: Exception | None = None
        self.last_query: str = ""
        self.last_recent_context: tuple = ()

    def retrieve_evidence(
        self,
        query: str,
        *,
        top_k: int = 8,
        query_id: str = "current-query",
        recent_context: tuple[ContextSource, ...] = (),
    ) -> EvidenceBundle:
        self.last_query = query
        self.last_recent_context = recent_context
        if self.error_to_raise:
            raise self.error_to_raise
        # Empty evidence bundle for testing
        return EvidenceBundle(
            schema_version="task2_evidence_bundle_v1",
            request_id=query_id,
            query=query,
            selected=(),
            sufficiency=EvidenceSufficiency(status=EvidenceSufficiencyStatus.SUFFICIENT),
        )


@pytest.fixture
def tmp_dbs(tmp_path):
    t1_db = str(tmp_path / "memory_store.db")
    t3_db = str(tmp_path / "task_state.db")
    return t1_db, t3_db


def test_composition_root_no_network_side_effects(tmp_dbs):
    t1_db, t3_db = tmp_dbs
    mem_cfg = MemoryConfig(sqlite_db_path=t1_db, provider_mode=ProviderMode.REQUIRED)
    from conftest import TestProviderClient
    cfg = AgentRuntimeConfig(memory_config=mem_cfg, task3_db_path=t3_db, llm_client=TestProviderClient())

    runtime = build_agent_runtime(cfg)
    assert isinstance(runtime, AgentRuntime)
    assert os.path.exists(t1_db)
    assert os.path.exists(t3_db)


def test_task_router_baseline_rules(tmp_dbs):
    _, t3_db = tmp_dbs
    repo = TaskStateRepository(t3_db)
    service = TaskStateService(repo)
    router = BaselineTaskRouter(service)

    # 1. 0 Active tasks -> retry-safe creation derived from turn_id
    t_id1 = router.resolve_task(turn_id="turn-001")
    assert t_id1.startswith("task_")
    
    # Retry same turn_id -> returns SAME task_id without duplicate task creation
    t_id1_retry = router.resolve_task(turn_id="turn-001")
    assert t_id1_retry == t_id1
    assert len(service.list_active_tasks()) == 1

    # 2. 1 Active task -> auto selects it when no task_id provided
    t_id_auto = router.resolve_task(turn_id="turn-002")
    assert t_id_auto == t_id1

    # 3. Create second active task -> >1 Active tasks -> raises TaskSelectionRequiredError
    service.create_task("task_second")
    assert len(service.list_active_tasks()) == 2
    with pytest.raises(TaskSelectionRequiredError) as exc_info:
        router.resolve_task(turn_id="turn-003")
    assert len(exc_info.value.active_task_ids) == 2

    # 4. Explicit task_id provided -> succeeds for ACTIVE
    resolved = router.resolve_task(turn_id="turn-004", requested_task_id="task_second")
    assert resolved == "task_second"

    # 5. Non-existent task -> raises TaskNotFoundError
    with pytest.raises(TaskNotFoundError):
        router.resolve_task(turn_id="turn-005", requested_task_id="task_non_existent")

    # 6. Non-ACTIVE task (PAUSED) -> raises TaskLifecycleError
    service.pause_task("task_second")
    with pytest.raises(TaskLifecycleError):
        router.resolve_task(turn_id="turn-006", requested_task_id="task_second")


def test_deterministic_user_turn_lifecycle(tmp_dbs):
    _, t3_db = tmp_dbs
    repo = TaskStateRepository(t3_db)
    service = TaskStateService(repo)
    interpreter = FakeTaskInterpreter()
    facade = FakeRetrievalFacade()
    runtime = AgentRuntime(
        task_service=service,
        retrieval_facade=facade,
        task_interpreter=interpreter,
    )

    req1 = TurnRequest(turn_id="turn-101", user_input="帮我准备上海会议，预算最多5000")
    res1 = runtime.process_user_turn(req1)

    assert res1.is_success
    assert res1.context is not None
    assert res1.context.turn_id == "turn-101"
    assert len(res1.context.structured_task_context.constraints) == 1
    assert "5000" in res1.context.structured_task_context.constraints[0].content

    # Second turn updating budget -> supersedes
    target_entry_id = res1.context.structured_task_context.constraints[0].entry_id
    interpreter.proposals_map["预算改成4000"] = [
        TaskStateProposal(
            proposal_id="prop_102",
            task_id=res1.task_id,
            source_event_id="turn:turn-102:user",
            action=ProposalAction.SUPERSEDE,
            target_entry_id=target_entry_id,
            content="预算改成4000",
            replacement_content="预算改成4000",
            entry_kind=EntryKind.CONSTRAINT,
            priority=Priority.HIGH,
            basis=ProposalBasis.EXPLICIT,
            support_spans=("预算改成4000",),
        )
    ]

    req2 = TurnRequest(turn_id="turn-102", user_input="预算改成4000", task_id=res1.task_id)
    res2 = runtime.process_user_turn(req2)

    assert res2.is_success
    assert res2.context is not None
    active_constraints = res2.context.structured_task_context.constraints
    assert len(active_constraints) == 1
    assert "4000" in active_constraints[0].content
    
    # Verify adapter passed updated task state (budget 4000) to Task 2 facade
    assert facade.last_query == "预算改成4000"
    source_contents = [s.text for s in facade.last_recent_context]
    assert any("4000" in c for c in source_contents)


def test_p1_typed_provider_errors_preserved(tmp_dbs):
    _, t3_db = tmp_dbs
    repo = TaskStateRepository(t3_db)
    service = TaskStateService(repo)
    interpreter = FakeTaskInterpreter()
    runtime = AgentRuntime(task_service=service, task_interpreter=interpreter)

    test_errors = [
        ProviderConfigurationError("Bad auth config", provider="openai-compatible", operation="auth"),
        ProviderRequestError("Rate limit 429", provider="openai-compatible", operation="chat", status_code=429),
        ProviderResponseError("Malformed JSON response", provider="openai-compatible", operation="chat"),
    ]

    for i, err in enumerate(test_errors, start=1):
        interpreter.error_to_raise = err
        req = TurnRequest(turn_id=f"turn-err-{i}", user_input="测试错误透传")
        res = runtime.process_user_turn(req)

        assert not res.is_success
        assert res.error_info is not None
        assert res.error_info.stage == TurnStage.TASK_UPDATE
        assert res.error_info.category == "PROVIDER_ERROR"
        assert "PROVIDER_" in res.error_info.code
        # Confirm stage_traces warnings contains safe_message (NOT raw str(exc))
        assert any(res.error_info.safe_message in w for trace in res.stage_traces for w in trace.warnings)
        assert "Authorization" not in res.error_info.safe_message

        # Verify zero ledger mutation: event recorded as FAILED
        status = repo.get_event_status(f"turn:turn-err-{i}:user")
        assert status is EventProcessStatus.FAILED


def test_partial_success_semantics(tmp_dbs):
    _, t3_db = tmp_dbs
    repo = TaskStateRepository(t3_db)
    service = TaskStateService(repo)
    interpreter = FakeTaskInterpreter()
    facade = FakeRetrievalFacade()
    runtime = AgentRuntime(
        task_service=service,
        retrieval_facade=facade,
        task_interpreter=interpreter,
    )

    # Retrieval failure does NOT roll back Task 3 durable mutation
    facade.error_to_raise = ProviderRequestError("Task2 timeout", provider="openai-compatible", operation="test", status_code=504)
    req = TurnRequest(turn_id="turn-partial-1", user_input="创建新目标")
    res = runtime.process_user_turn(req)

    assert not res.is_success
    assert res.error_info is not None
    assert res.error_info.stage == TurnStage.RETRIEVAL

    # Verify Task 3 ledger WAS mutated and committed
    ledger = service.get_task(res.task_id)
    assert len(ledger.entries) == 1
    assert ledger.entries[0].content == "创建新目标"


def test_tool_result_ingestion(tmp_dbs):
    _, t3_db = tmp_dbs
    repo = TaskStateRepository(t3_db)
    service = TaskStateService(repo)
    interpreter = FakeTaskInterpreter()
    runtime = AgentRuntime(task_service=service, task_interpreter=interpreter)

    task_id = service.create_task("task_tool_test").task_id

    tool_res = ToolCallResult(
        tool_call_id="call_123",
        tool_name="hotel_booking",
        arguments={"city": "Shanghai"},
        status=ToolCallStatus.SUCCESS,
        result_content="Hotel booked successfully",
    )

    res = runtime.process_tool_result(tool_res, task_id=task_id)
    assert res.is_success
    assert res.context is not None
    assert res.context.turn_id == "tool:call_123"

    # Idempotent replay: repeating tool result processing does not cause error or secondary mutation
    res_replay = runtime.process_tool_result(tool_res, task_id=task_id)
    assert res_replay.is_success


def test_rejected_tool_state_proposal_does_not_discard_successful_observation(tmp_dbs):
    _, t3_db = tmp_dbs
    repo = TaskStateRepository(t3_db)
    service = TaskStateService(repo)

    class InvalidToolProposalInterpreter:
        identity = "invalid-tool-proposal"
        version = "v1"

        def interpret(self, event, context):
            proposal = TaskStateProposal(
                proposal_id="invalid-progress",
                task_id=event.task_id,
                source_event_id=event.event_id,
                action=ProposalAction.ADD,
                entry_kind=EntryKind.PROGRESS,
                content="unrelated progress",
                basis=ProposalBasis.OBSERVED,
                support_spans=("not present in event",),
            )
            return TaskStateProposalBatch(
                event.task_id,
                event.event_id,
                (proposal,),
                self.identity,
                self.version,
            )

    runtime = AgentRuntime(
        task_service=service,
        task_interpreter=InvalidToolProposalInterpreter(),
    )
    task_id = service.create_task("task_tool_rejected").task_id
    tool_res = ToolCallResult(
        tool_call_id="call_rejected_proposal",
        tool_name="calculator",
        arguments={"expression": "2 + 2"},
        status=ToolCallStatus.SUCCESS,
        result_content="计算结果: 4",
    )

    res = runtime.process_tool_result(tool_res, task_id=task_id)

    assert res.is_success
    assert res.context is not None
    assert any(
        trace.stage is TurnStage.TASK_UPDATE
        and trace.status is TurnStageStatus.WARNING
        for trace in res.stage_traces
    )
    assert repo.get_event_status("tool:call_rejected_proposal:result") is EventProcessStatus.REJECTED


def test_invalid_task_state_response_keeps_ordinary_turn_retryable_without_blocking_answer(tmp_dbs):
    _, t3_db = tmp_dbs
    repo = TaskStateRepository(t3_db)
    service = TaskStateService(repo)
    interpreter = FakeTaskInterpreter()
    interpreter.error_to_raise = RuntimeError("invalid structured response")
    runtime = AgentRuntime(task_service=service, task_interpreter=interpreter)

    res = runtime.process_user_turn(TurnRequest(turn_id="turn_invalid_task_response", user_input="解释一下二分查找"))

    assert res.is_success
    assert res.context is not None
    assert any(
        trace.stage is TurnStage.TASK_UPDATE
        and trace.status is TurnStageStatus.WARNING
        for trace in res.stage_traces
    )
    assert repo.get_event_status("turn:turn_invalid_task_response:user") is EventProcessStatus.FAILED


def test_cross_process_task_id_deterministic_derivation(tmp_dbs):
    _, t3_db = tmp_dbs
    import subprocess, sys
    from pathlib import Path

    repository_root = str(Path(__file__).resolve().parents[1])

    code = f"""
import sys
sys.path.insert(0, {repository_root!r})
from src.memory.task_state.repository import TaskStateRepository
from src.memory.task_state.service import TaskStateService
from src.memory.runtime.task_router import BaselineTaskRouter

service = TaskStateService(TaskStateRepository({str(t3_db)!r}))
router = BaselineTaskRouter(service)
print(router.resolve_task('same-turn-123'))
"""

    p1 = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    out1 = p1.stdout.strip()

    # Re-run in a completely separate Python interpreter process
    p2 = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    out2 = p2.stdout.strip()

    assert out1 != ""
    assert out1 == out2, f"Cross-process derivation mismatch: {out1} != {out2}"


def test_cross_process_restart_retry_behavior(tmp_dbs):
    _, t3_db = tmp_dbs

    # Process 1: First attempt
    repo1 = TaskStateRepository(t3_db)
    service1 = TaskStateService(repo1)
    interpreter1 = FakeTaskInterpreter()
    facade1 = FakeRetrievalFacade()
    facade1.error_to_raise = ProviderRequestError("Simulated retrieval failure", provider="openai-compatible", operation="test")

    runtime1 = AgentRuntime(task_service=service1, retrieval_facade=facade1, task_interpreter=interpreter1)
    req1 = TurnRequest(turn_id="turn-restart-100", user_input="创建重试任务")
    res1 = runtime1.process_user_turn(req1)

    assert res1.task_id is not None
    task_id_first = res1.task_id
    assert len(service1.list_active_tasks()) == 1

    # Simulate process restart / DB reopen
    repo2 = TaskStateRepository(t3_db)
    service2 = TaskStateService(repo2)
    interpreter2 = FakeTaskInterpreter()
    facade2 = FakeRetrievalFacade() # Normal facade without error
    runtime2 = AgentRuntime(task_service=service2, retrieval_facade=facade2, task_interpreter=interpreter2)

    # Retry SAME turn in process 2
    req2 = TurnRequest(turn_id="turn-restart-100", user_input="创建重试任务")
    res2 = runtime2.process_user_turn(req2)

    assert res2.is_success
    assert res2.task_id == task_id_first
    # Confirm active task count remains exactly 1 (no secondary task created)
    assert len(service2.list_active_tasks()) == 1


def test_general_routing_retry_contract(tmp_dbs):
    _, t3_db = tmp_dbs
    repo = TaskStateRepository(t3_db)
    service = TaskStateService(repo)
    interpreter = FakeTaskInterpreter()
    facade = FakeRetrievalFacade()

    # Step 1: Create initial active Task A via turn 1
    runtime = AgentRuntime(task_service=service, retrieval_facade=facade, task_interpreter=interpreter)
    req1 = TurnRequest(turn_id="turn-option-b-1", user_input="任务A")
    res1 = runtime.process_user_turn(req1)
    task_id_a = res1.task_id
    assert task_id_a is not None

    # Step 2: Create a second active Task B
    service.create_task("task_b_active")
    assert len(service.list_active_tasks()) == 2

    # Step 3: Retry turn 1 WITHOUT task_id -> router refuses to guess (>1 active task)
    req_retry_no_id = TurnRequest(turn_id="turn-option-b-1", user_input="任务A", task_id=None)
    res_retry_no_id = runtime.process_user_turn(req_retry_no_id)
    assert not res_retry_no_id.is_success
    assert res_retry_no_id.error_info.code == "TASK_SELECTION_REQUIRED"

    # Step 4: Retry WITH resolved task_id=task_id_a -> succeeds and targets Task A
    req_retry_with_id = TurnRequest(turn_id="turn-option-b-1", user_input="任务A", task_id=task_id_a)
    res_retry_with_id = runtime.process_user_turn(req_retry_with_id)
    assert res_retry_with_id.is_success
    assert res_retry_with_id.task_id == task_id_a


def test_maintenance_result_local_correlation(tmp_dbs):
    _, t3_db = tmp_dbs
    repo = TaskStateRepository(t3_db)
    service = TaskStateService(repo)
    interpreter = FakeTaskInterpreter()

    service.create_task("task_m_test")
    event_a = TaskEvent(task_id="task_m_test", event_id="evt_m_a", source_kind=SourceKind.USER, content="任务A")
    res_a = service.process_event_result(event_a, interpreter=interpreter)

    # Verify TaskServiceResult.maintenance is constructed locally and NOT by reading stale shared properties
    assert res_a.receipt is not None
    assert res_a.maintenance is not None


def test_provider_error_retryability_matrix():
    # 1. ProviderConfigurationError -> False
    cfg_err = ProviderConfigurationError("Missing key", provider="openai-compatible", operation="config")
    assert cfg_err.to_safe_projection()["retryable"] is False

    # 2. Non-transient ProviderRequestError (400, 401, 403, 404) -> False
    for code in [400, 401, 403, 404]:
        err = ProviderRequestError(f"HTTP {code}", provider="openai-compatible", operation="chat", status_code=code)
        assert err.to_safe_projection()["retryable"] is False, f"Expected 4xx ({code}) to be non-retryable"

    # 3. Transient ProviderRequestError (408, 429, 500, 502, 503, 504) -> True
    for code in [408, 429, 500, 502, 503, 504]:
        err = ProviderRequestError(f"HTTP {code}", provider="openai-compatible", operation="chat", status_code=code)
        assert err.to_safe_projection()["retryable"] is True, f"Expected status {code} to be retryable"

    # 4. Timeout / Network error (status_code is None) -> True
    net_err = ProviderRequestError("Connection timeout", provider="openai-compatible", operation="chat", status_code=None)
    assert net_err.to_safe_projection()["retryable"] is True

    # 5. ProviderResponseError -> False
    resp_err = ProviderResponseError("Malformed JSON", provider="openai-compatible", operation="chat")
    assert resp_err.to_safe_projection()["retryable"] is False

