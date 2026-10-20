"""Main Agent Real-Provider Semantic Baseline Evaluation Harness."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import multiprocessing
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.memory.agent.journal import AgentExecutionJournal
from src.memory.agent.main_agent import MainAgent
from src.memory.agent.models import (
    ActionConfirmation,
    AgentRequest,
    AgentResultStatus,
    ConfirmationDecision,
    TurnRequest,
)
from src.memory.agent.reasoner import OpenAICompatibleAgentReasoner
from src.memory.agent.tooling import (
    IdempotencySupport,
    ToolEffect,
    ToolExecutionRequest,
    ToolExecutor,
    ToolRegistry,
    ToolSpec,
)
from src.memory.core.config import MemoryConfig, ProviderMode
from src.memory.core.diagnostics import log_boundary_failure
from src.memory.llm.errors import (
    ProviderError,
)
from src.memory.llm.llm_client import build_llm_client
from src.memory.retrieval.evidence import (
    ApplicabilityDecision,
    ApplicabilityStatus,
    ConflictStatus,
    DistilledEvidence,
    EvidenceBundle,
    EvidenceSufficiency,
    EvidenceSufficiencyStatus,
)
from src.memory.runtime.builder import build_agent_runtime
from src.memory.runtime.config import AgentRuntimeConfig
from src.memory.runtime.models import ToolCallResult, ToolCallStatus
from src.memory.task_state.models import (
    EntryKind,
    Priority,
    SourceKind,
    TaskEvent,
    TaskStateProposalBatch,
)

FROZEN_MECHANISM_TAG = "main-agent-mechanism-v1.2"
FROZEN_MECHANISM_COMMIT = "473d8c6cee58ea90cbe519f8112ba0477eb65795"

logger = logging.getLogger(__name__)


class EvaluationStatus(str, Enum):
    SUCCESS = "SUCCESS"
    SEMANTIC_FAIL = "SEMANTIC_FAIL"
    PROVIDER_FAIL = "PROVIDER_FAIL"
    RUNTIME_FAIL = "RUNTIME_FAIL"
    INVALID_ACTION = "INVALID_ACTION"
    STEP_LIMIT = "STEP_LIMIT"
    CASE_TIMEOUT = "CASE_TIMEOUT"
    HARNESS_FAIL = "HARNESS_FAIL"


class SafeFakeToolExecutor(ToolExecutor):
    """Deterministic, safe fake executor that records calls without real external side-effects."""

    def __init__(self, return_content: str = "Success") -> None:
        self.return_content = return_content
        self.calls: List[ToolExecutionRequest] = []

    def execute(self, request: ToolExecutionRequest) -> ToolCallResult:
        self.calls.append(request)
        return ToolCallResult(
            tool_call_id=request.tool_call_id,
            tool_name=request.tool_name,
            arguments=request.arguments,
            status=ToolCallStatus.SUCCESS,
            result_content=self.return_content,
        )


def _get_git_commit_hash() -> str:
    try:
        res = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
        return res.stdout.strip()
    except Exception as exc:
        log_boundary_failure(logger, "git_commit_resolution_failed", exc)
        return "UNKNOWN_COMMIT"


def _compute_file_sha256(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


def build_evaluation_registry() -> Tuple[ToolRegistry, Dict[str, SafeFakeToolExecutor]]:
    registry = ToolRegistry()
    executors: Dict[str, SafeFakeToolExecutor] = {}

    specs = [
        ToolSpec(
            name="weather_lookup",
            description="查询指定城市的天气预报。参数：city (str, 必需), date (str, 可选)。",
            parameters_schema={"type": "object", "properties": {"city": {"type": "string"}, "date": {"type": "string"}}, "required": ["city"]},
            effect=ToolEffect.READ_ONLY,
        ),
        ToolSpec(
            name="calendar_lookup",
            description="查询日程安排或是否有空。参数：date (str, 可选), query (str, 可选)。",
            parameters_schema={"type": "object", "properties": {"date": {"type": "string"}, "query": {"type": "string"}}},
            effect=ToolEffect.READ_ONLY,
        ),
        ToolSpec(
            name="email_search",
            description="搜索历史邮件记录。参数：query (str, 必需), before_date (str, 可选)。",
            parameters_schema={"type": "object", "properties": {"query": {"type": "string"}, "before_date": {"type": "string"}}, "required": ["query"]},
            effect=ToolEffect.READ_ONLY,
        ),
        ToolSpec(
            name="hotel_search",
            description="查询搜索符合条件的酒店列表。参数：city (str, 必需), district (str, 可选), max_price (integer, 可选), quiet (boolean, 可选)。",
            parameters_schema={"type": "object", "properties": {"city": {"type": "string"}, "district": {"type": "string"}, "max_price": {"type": "integer"}, "quiet": {"type": "boolean"}}, "required": ["city"]},
            effect=ToolEffect.READ_ONLY,
        ),
        ToolSpec(
            name="hotel_details",
            description="查看指定酒店 ID 的详细设施与服务。参数：hotel_id (str, 必需)。",
            parameters_schema={"type": "object", "properties": {"hotel_id": {"type": "string"}}, "required": ["hotel_id"]},
            effect=ToolEffect.READ_ONLY,
        ),
        ToolSpec(
            name="hotel_book",
            description="预订指定酒店房间 (高风险写操作)。参数：hotel_id (str, 必需)。",
            parameters_schema={"type": "object", "properties": {"hotel_id": {"type": "string"}}, "required": ["hotel_id"]},
            effect=ToolEffect.SIDE_EFFECT,
            idempotency_support=IdempotencySupport.NONE,
        ),
        ToolSpec(
            name="email_send",
            description="发送电子邮件 (高风险写操作)。参数：recipient (str, 必需), subject (str, 必需), body (str, 可选)。",
            parameters_schema={"type": "object", "properties": {"recipient": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}, "required": ["recipient", "subject"]},
            effect=ToolEffect.SIDE_EFFECT,
            idempotency_support=IdempotencySupport.NONE,
        ),
        ToolSpec(
            name="calendar_create",
            description="新建日程安排 (高风险写操作)。参数：title (str, 必需), start_time (str, 必需)。",
            parameters_schema={"type": "object", "properties": {"title": {"type": "string"}, "start_time": {"type": "string"}}, "required": ["title", "start_time"]},
            effect=ToolEffect.SIDE_EFFECT,
            idempotency_support=IdempotencySupport.NONE,
        ),
    ]

    for spec in specs:
        executor = SafeFakeToolExecutor()
        registry.register(spec, executor)
        executors[spec.name] = executor

    return registry, executors


@dataclass
class CaseEvaluationResult:
    case_id: str
    category: str
    passed: bool
    status: str  # EvaluationStatus value
    action_kind: Optional[str] = None
    tool_name: Optional[str] = None
    tool_arguments: Optional[Dict[str, Any]] = None
    final_response: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    elapsed_seconds: float = 0.0
    provider_requests: int = 0
    agent_steps: int = 0
    action_trace: List[Dict[str, Any]] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CaseEvaluationResult:
        return cls(**data)


class DeterministicTaskStateInterpreter:
    identity = "deterministic_fake_v1"
    version = "v1"

    def interpret(self, event: Any, context: Any) -> TaskStateProposalBatch:
        return TaskStateProposalBatch(
            task_id=event.task_id,
            event_id=event.event_id,
            proposals=(),
            interpreter=self.identity,
            interpreter_version=self.version,
            warnings=(),
        )


def evaluate_single_case_in_current_process(
    case: Dict[str, Any],
    config: MemoryConfig,
    db_dir: str,
    use_real_task3_interpreter: bool = False,
) -> CaseEvaluationResult:
    start_time = time.time()
    case_id = case["case_id"]
    category = case.get("category", "UNKNOWN")
    user_input = case["user_input"]
    expected_kind = case.get("expected_action_kind")
    expected_tool = case.get("expected_tool")
    required_args = case.get("required_arguments")
    forbidden_tools = case.get("forbidden_tools", [])
    required_facts = case.get("required_final_facts", [])
    fake_tool_res = case.get("fake_tool_result")

    journal = AgentExecutionJournal(os.path.join(db_dir, "journal.db"))
    registry, _ = build_evaluation_registry()

    llm_client = build_llm_client(config)
    reasoner = OpenAICompatibleAgentReasoner(llm_client)

    # Injected evidence bundle if present
    evidence_pipeline = None
    if "memory_evidences" in case:
        items = []
        for idx, text in enumerate(case["memory_evidences"]):
            items.append(
                DistilledEvidence(
                    memory_id=f"mem_{idx}",
                    revision_id=f"rev_{idx}",
                    text=text,
                    original_rank=idx + 1,
                    original_score=0.95,
                    score_breakdown={"bm25": 0.95},
                    provenance_refs=(),
                    applicability_status=ApplicabilityStatus.MATCH,
                    applicability_decision=ApplicabilityDecision.RETAIN,
                    applicability_reasons=("Direct match",),
                    conflict_status=ConflictStatus.NO_CONFLICT,
                    conflict_key=None,
                    conflict_losing_memory_ids=(),
                    source_trace=("eval_fixture",),
                )
            )
        bundle = EvidenceBundle(
            schema_version="v1",
            request_id=f"req_{case_id}",
            query=user_input,
            selected=tuple(items),
            sufficiency=EvidenceSufficiency(status=EvidenceSufficiencyStatus.SUFFICIENT),
        )
        class FakeEvidencePipeline:
            def execute(self, *args, **kwargs):
                return bundle
        evidence_pipeline = FakeEvidencePipeline()

    runtime_cfg = AgentRuntimeConfig(
        memory_config=MemoryConfig(
            sqlite_db_path=os.path.join(db_dir, "t1.db"),
            provider_mode=config.provider_mode,
            llm_provider_name=config.llm_provider_name,
            llm_api_key_env=config.llm_api_key_env,
            llm_api_key=config.llm_api_key,
            llm_api_base=config.llm_api_base,
            llm_model_name=config.llm_model_name,
        ),
        task3_db_path=os.path.join(db_dir, "t3.db"),
    )
    runtime = build_agent_runtime(config=runtime_cfg)
    if evidence_pipeline:
        runtime.evidence_pipeline = evidence_pipeline

    if not use_real_task3_interpreter:
        runtime.task_interpreter = DeterministicTaskStateInterpreter()

    # Construct task and setup working context / evidence
    task_id = f"task_{case_id.lower()}"
    runtime.task_service.create_task(task_id)

    # Ingest task constraints if present
    if "task_constraints" in case:
        for idx, const_str in enumerate(case["task_constraints"]):
            event = TaskEvent(
                task_id=task_id,
                event_id=f"evt_const_{idx}",
                source_kind=SourceKind.USER,
                content=f"Constraint: {const_str}",
            )
            runtime.task_service.repository.register_event(event)
            runtime.task_service.repository.add_entry(
                task_id=task_id,
                kind=EntryKind.CONSTRAINT,
                content=const_str,
                priority=Priority.HIGH,
            )

    agent = MainAgent(runtime, journal, registry, reasoner)
    turn_id = f"turn_{case_id.lower()}_01"

    req = AgentRequest(
        turn_request=TurnRequest(turn_id=turn_id, task_id=task_id, user_input=user_input),
    )

    try:
        res = agent.run(req)
    except ProviderError as exc:
        safe_info = exc.to_safe_projection()
        elapsed = time.time() - start_time
        return CaseEvaluationResult(
            case_id=case_id,
            category=category,
            passed=False,
            status=EvaluationStatus.PROVIDER_FAIL.value,
            error_code=safe_info.get("code", "PROVIDER_FAILED"),
            error_message=safe_info.get("safe_message", "Provider request failed."),
            elapsed_seconds=elapsed,
            provider_requests=getattr(llm_client, "_request_count", 1),
            details={"provider_error": safe_info},
        )
    except Exception as exc:
        metadata = log_boundary_failure(
            logger,
            "semantic_evaluation_case_runtime_failed",
            exc,
            case_id=case_id,
            category=category,
        )
        elapsed = time.time() - start_time
        return CaseEvaluationResult(
            case_id=case_id,
            category=category,
            passed=False,
            status=EvaluationStatus.RUNTIME_FAIL.value,
            error_code=str(metadata["error_type"]),
            error_message=f"Evaluation case failed ({metadata['error_type']}).",
            elapsed_seconds=elapsed,
            provider_requests=getattr(llm_client, "_request_count", 0),
        )

    # Multi-step handling if fake_tool_result is provided and action is AWAITING_CONFIRMATION
    if fake_tool_res and res.status == AgentResultStatus.AWAITING_CONFIRMATION and case.get("second_step_expected_action_kind"):
        conf_req = AgentRequest(
            turn_request=TurnRequest(turn_id=f"turn_{case_id.lower()}_02", task_id=task_id, user_input="确认继续"),
            confirmation=ActionConfirmation(pending_action_id=res.pending_action.pending_action_id, decision=ConfirmationDecision.CONFIRM),
        )
        tool_n = res.pending_action.tool_name
        if tool_n in registry._executors:
            registry._executors[tool_n].return_content = fake_tool_res
        try:
            res = agent.run(conf_req)
        except ProviderError as exc:
            safe_info = exc.to_safe_projection()
            elapsed = time.time() - start_time
            return CaseEvaluationResult(
                case_id=case_id,
                category=category,
                passed=False,
                status=EvaluationStatus.PROVIDER_FAIL.value,
                error_code=safe_info.get("code", "PROVIDER_FAILED"),
                error_message=safe_info.get("safe_message", "Provider request failed."),
                elapsed_seconds=elapsed,
                provider_requests=getattr(llm_client, "_request_count", 2),
                details={"provider_error": safe_info},
            )

    elapsed = time.time() - start_time
    provider_req_count = getattr(llm_client, "_request_count", res.step_count)

    passed = True
    details = {}
    eval_status = EvaluationStatus.SUCCESS.value

    actual_kind = None
    actual_tool = None
    actual_args = None
    actual_content = None

    if res.status == AgentResultStatus.SUCCESS:
        actual_kind = "FINAL_RESPONSE"
        actual_content = res.final_response
    elif res.status == AgentResultStatus.AWAITING_CONFIRMATION:
        actual_kind = "TOOL_CALL"
        actual_tool = res.pending_action.tool_name
        actual_args = res.pending_action.validated_arguments
    elif res.status == AgentResultStatus.STEP_LIMIT_REACHED:
        passed = False
        eval_status = EvaluationStatus.STEP_LIMIT.value
        details["step_limit_reached"] = f"Step limit of {res.step_count} reached"
    elif res.status == AgentResultStatus.FAILED:
        passed = False
        if res.error_info and res.error_info.category == "PROVIDER_ERROR":
            eval_status = EvaluationStatus.PROVIDER_FAIL.value
        else:
            eval_status = EvaluationStatus.RUNTIME_FAIL.value
        err_msg = res.error_info.safe_message if res.error_info else "AgentResultStatus.FAILED"
        # Preserve the sanitized failure on the public case result.  Leaving
        # error_message empty made a real provider failure appear as an
        # unexplained smoke failure even though MainAgent had the truth.
        error_code = res.error_info.code if res.error_info else "AGENT_FAILED"
        details["run_failed"] = err_msg
        if res.error_info and res.error_info.details:
            details["provider_error"] = dict(res.error_info.details)

    # Record safe action trace for multi-step / step limit inspection
    action_trace = []
    if hasattr(journal, "get_run_steps") and res.agent_run_id:
        try:
            journal_steps = journal.get_run_steps(res.agent_run_id)
            for st in journal_steps:
                action_trace.append({
                    "step_index": st.step_index,
                    "action_kind": st.action_kind,
                    "tool_name": st.tool_name,
                    "tool_call_id": st.tool_call_id,
                })
        except Exception as exc:
            metadata = log_boundary_failure(
                logger,
                "semantic_evaluation_action_trace_failed",
                exc,
                agent_run_id=res.agent_run_id,
            )
            details["action_trace_error"] = metadata["error_type"]

    if res.status in (AgentResultStatus.SUCCESS, AgentResultStatus.AWAITING_CONFIRMATION):
        if expected_kind and actual_kind != expected_kind:
            passed = False
            eval_status = EvaluationStatus.SEMANTIC_FAIL.value
            details["action_kind_mismatch"] = f"Expected {expected_kind}, got {actual_kind}"

        if expected_tool and actual_tool != expected_tool:
            passed = False
            eval_status = EvaluationStatus.SEMANTIC_FAIL.value
            details["tool_mismatch"] = f"Expected {expected_tool}, got {actual_tool}"

        if required_args and actual_args:
            for k, v in required_args.items():
                if k not in actual_args or actual_args[k] != v:
                    passed = False
                    eval_status = EvaluationStatus.SEMANTIC_FAIL.value
                    details[f"arg_mismatch_{k}"] = f"Expected {v}, got {actual_args.get(k)}"

        if forbidden_tools and actual_tool in forbidden_tools:
            passed = False
            eval_status = EvaluationStatus.SEMANTIC_FAIL.value
            details["forbidden_tool_used"] = f"Tool '{actual_tool}' is forbidden for case {case_id}"

        if required_facts and actual_content:
            for fact in required_facts:
                if fact.lower() not in actual_content.lower():
                    passed = False
                    eval_status = EvaluationStatus.SEMANTIC_FAIL.value
                    details[f"missing_fact_{fact}"] = f"Fact '{fact}' missing from final content"

    return CaseEvaluationResult(
        case_id=case_id,
        category=category,
        passed=passed,
        status=eval_status,
        action_kind=actual_kind,
        tool_name=actual_tool,
        tool_arguments=actual_args,
        final_response=actual_content,
        elapsed_seconds=elapsed,
        provider_requests=provider_req_count,
        agent_steps=res.step_count,
        action_trace=action_trace,
        error_code=(error_code if res.status == AgentResultStatus.FAILED else None),
        error_message=(err_msg if res.status == AgentResultStatus.FAILED else None),
        details=details,
    )


def _worker_entrypoint(queue: multiprocessing.Queue, case: Dict[str, Any], config: MemoryConfig, db_dir: str, use_real_task3: bool):
    try:
        res = evaluate_single_case_in_current_process(case, config, db_dir, use_real_task3)
        queue.put(res.to_dict())
    except Exception as exc:
        metadata = log_boundary_failure(
            logger,
            "semantic_evaluation_worker_failed",
            exc,
            case_id=case.get("case_id"),
        )
        queue.put(CaseEvaluationResult(
            case_id=case["case_id"],
            category=case.get("category", "UNKNOWN"),
            passed=False,
            status=EvaluationStatus.HARNESS_FAIL.value,
            error_code=str(metadata["error_type"]),
            error_message=f"Evaluation worker failed ({metadata['error_type']}).",
        ).to_dict())


def evaluate_single_case_with_timeout(
    case: Dict[str, Any],
    config: MemoryConfig,
    db_dir: str,
    case_timeout_seconds: float = 180.0,
    use_real_task3_interpreter: bool = False,
) -> CaseEvaluationResult:
    """Executes single case in an isolated subprocess with strict watchdog timeout boundary."""
    queue = multiprocessing.Queue()
    proc = multiprocessing.Process(
        target=_worker_entrypoint,
        args=(queue, case, config, db_dir, use_real_task3_interpreter),
    )
    start_t = time.time()
    proc.start()
    proc.join(timeout=case_timeout_seconds)

    if proc.is_alive():
        proc.terminate()
        proc.join(timeout=5.0)
        if proc.is_alive():
            proc.kill()
        elapsed = time.time() - start_t
        return CaseEvaluationResult(
            case_id=case["case_id"],
            category=case.get("category", "UNKNOWN"),
            passed=False,
            status=EvaluationStatus.CASE_TIMEOUT.value,
            error_code="CASE_TIMEOUT",
            error_message=f"Case execution exceeded hard deadline of {case_timeout_seconds}s",
            elapsed_seconds=elapsed,
        )

    if not queue.empty():
        data = queue.get()
        return CaseEvaluationResult.from_dict(data)

    elapsed = time.time() - start_t
    return CaseEvaluationResult(
        case_id=case["case_id"],
        category=case.get("category", "UNKNOWN"),
        passed=False,
        status=EvaluationStatus.HARNESS_FAIL.value,
        error_code="WORKER_PROCESS_CRASHED",
        error_message="Worker process exited without producing result",
        elapsed_seconds=elapsed,
    )


def save_checkpoint_atomic(checkpoint_path: str, checkpoint_data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(checkpoint_path)), exist_ok=True)
    tmp_path = checkpoint_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(checkpoint_data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, checkpoint_path)


def load_checkpoint(
    checkpoint_path: str,
    benchmark_digest: str,
    protocol_commit: str,
    mechanism_commit: str,
    provider: str,
    model: str,
) -> Optional[Dict[str, Any]]:
    if not os.path.exists(checkpoint_path):
        return None
    try:
        with open(checkpoint_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("benchmark_digest") != benchmark_digest:
            raise ValueError(f"Resume rejected: benchmark_digest mismatch ({data.get('benchmark_digest')} vs {benchmark_digest})")
        if data.get("protocol_commit") != protocol_commit:
            raise ValueError(f"Resume rejected: protocol_commit mismatch ({data.get('protocol_commit')} vs {protocol_commit})")
        if data.get("mechanism_commit") != mechanism_commit:
            raise ValueError(f"Resume rejected: mechanism_commit mismatch ({data.get('mechanism_commit')} vs {mechanism_commit})")
        if data.get("provider") != provider:
            raise ValueError(f"Resume rejected: provider mismatch ({data.get('provider')} vs {provider})")
        if data.get("model") != model:
            raise ValueError(f"Resume rejected: model mismatch ({data.get('model')} vs {model})")
        return data
    except Exception as exc:
        metadata = log_boundary_failure(
            logger,
            "semantic_evaluation_checkpoint_resume_failed",
            exc,
            checkpoint_path=checkpoint_path,
        )
        print(f"CHECKPOINT RESUME ERROR ({metadata['error_type']})")
        raise


def run_evaluation_suite(
    manifest_path: str,
    smoke_only: bool = False,
    resume: bool = False,
    checkpoint_path: str = "tmp/semantic_eval_checkpoint.json",
    case_timeout_seconds: float = 180.0,
    run_max_duration_seconds: float = 1800.0,
    smoke_pacing_seconds: float = 0.0,
) -> Dict[str, Any]:
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    manifest_digest = _compute_file_sha256(manifest_path)
    git_commit = _get_git_commit_hash()

    tier_a_cases = manifest.get("tier_a_cases", [])
    tier_b_cases = manifest.get("tier_b_smoke_cases", [])

    # Build the effective configuration first.  This merges process-level
    # overrides with the optional local dotenv file, so the run banner and
    # checkpoint identity describe the same provider/model that the client
    # actually uses (rather than only echoing .env.local).
    config = MemoryConfig.from_env()
    config.provider_mode = ProviderMode.REQUIRED
    provider_name = config.llm_provider_name
    model_name = config.llm_model_name

    print("=" * 60)
    print("MAIN AGENT REAL-PROVIDER SEMANTIC EVALUATION")
    print("=" * 60)
    print(f"Provider        : {provider_name}")
    print(f"Model           : {model_name}")
    print(f"Base URL        : {config.llm_api_base}")
    print(f"Provider Mode   : REQUIRED")
    print(f"Case Timeout    : {case_timeout_seconds}s")
    print(f"Run Max Duration: {run_max_duration_seconds}s")
    if smoke_pacing_seconds > 0:
        print(f"Smoke Pacing    : {smoke_pacing_seconds}s")
    print("=" * 60)

    try:
        build_llm_client(config)
    except Exception as exc:
        metadata = log_boundary_failure(logger, "semantic_evaluation_provider_configuration_failed", exc)
        safe_error = f"Provider configuration failed ({metadata['error_type']})."
        print(f"FATAL: {safe_error}")
        return {"status": "EVALUATION_BLOCKED", "error": safe_error}

    # Provider Smoke Test (3 cases) - Fail Fast Gate
    print("\n--- STAGE 1: REAL PROVIDER SMOKE (3 Cases) ---")
    smoke_cases = [
        {"case_id": "SMOKE_01", "category": "SMOKE", "user_input": "你好，请问你是谁？", "expected_action_kind": "FINAL_RESPONSE"},
        {"case_id": "SMOKE_02", "category": "SMOKE", "user_input": "查询上海明天天气", "expected_action_kind": "TOOL_CALL", "expected_tool": "weather_lookup"},
        {"case_id": "SMOKE_03", "category": "SMOKE", "user_input": "预订酒店 ID 为 h_9000 的大床房", "expected_action_kind": "TOOL_CALL", "expected_tool": "hotel_book"},
    ]

    smoke_passed = True
    smoke_results: List[CaseEvaluationResult] = []
    for idx_sc, sc in enumerate(smoke_cases, 1):
        with tempfile.TemporaryDirectory() as tmp_dir:
            res = evaluate_single_case_with_timeout(
                sc, config, tmp_dir, case_timeout_seconds=case_timeout_seconds, use_real_task3_interpreter=False
            )
            smoke_results.append(res)
            print(
                f"Smoke {res.case_id} ({sc['user_input']}) -> status={res.status}, action={res.action_kind}, tool={res.tool_name}, elapsed={res.elapsed_seconds:.1f}s | Passed: {res.passed} | Details: {res.details}"
            )
            if not res.passed or res.status in (EvaluationStatus.PROVIDER_FAIL.value, EvaluationStatus.RUNTIME_FAIL.value, EvaluationStatus.HARNESS_FAIL.value, EvaluationStatus.CASE_TIMEOUT.value):
                smoke_passed = False
                print(f"FATAL SMOKE FAILURE in {res.case_id}: status={res.status}, err={res.error_message}")
                break

            if smoke_pacing_seconds > 0 and idx_sc < len(smoke_cases):
                print(f"Pacing: sleeping {smoke_pacing_seconds:.1f}s before next smoke case...")
                time.sleep(smoke_pacing_seconds)

    if not smoke_passed:
        print("\nEVALUATION_BLOCKED: Stage 1 Provider Smoke failed! Aborting Tier A and Tier B runs to protect quota.")
        return {"status": "EVALUATION_BLOCKED", "smoke_results": [r.to_dict() for r in smoke_results]}

    if smoke_only:
        print("\nSmoke 3/3 PASSED successfully.")
        return {"status": "SMOKE_ONLY_PASS", "smoke_results": [r.to_dict() for r in smoke_results]}

    # Checkpoint Resume Setup
    checkpoint_data: Dict[str, Any] = {
        "protocol_commit": git_commit,
        "mechanism_commit": FROZEN_MECHANISM_COMMIT,
        "benchmark_digest": manifest_digest,
        "provider": provider_name,
        "model": model_name,
        "updated_at": time.time(),
        "tier_a_results": [],
        "tier_b_results": [],
        "status": "RUNNING",
    }

    if resume:
        loaded = load_checkpoint(
            checkpoint_path,
            manifest_digest,
            git_commit,
            FROZEN_MECHANISM_COMMIT,
            provider_name,
            model_name,
        )
        if loaded:
            checkpoint_data = loaded
            print(f"Resumed from checkpoint: {len(checkpoint_data['tier_a_results'])} Tier A cases loaded.")

    completed_a_ids = {r["case_id"] for r in checkpoint_data.get("tier_a_results", [])}
    tier_a_results: List[CaseEvaluationResult] = [CaseEvaluationResult.from_dict(r) for r in checkpoint_data.get("tier_a_results", [])]

    suite_start_time = time.time()

    # Stage 2: Tier A Semantic Evaluation
    total_a = len(tier_a_cases)
    print(f"\n--- STAGE 2: TIER A SEMANTIC EVALUATION ({total_a} Cases) ---")

    for idx, case in enumerate(tier_a_cases, 1):
        case_id = case["case_id"]
        if case_id in completed_a_ids:
            print(f"[{idx:02d}/{total_a}] {case_id} ({case['category']}) ... [SKIPPED (Resumed)]")
            continue

        if time.time() - suite_start_time > run_max_duration_seconds:
            print(f"\nRUN WATCHDOG DEADLINE REACHED ({run_max_duration_seconds}s). Saving PARTIAL checkpoint and stopping.")
            checkpoint_data["status"] = "PARTIAL"
            save_checkpoint_atomic(checkpoint_path, checkpoint_data)
            return {"status": "PARTIAL_WATCHDOG_STOP", "checkpoint": checkpoint_path}

        with tempfile.TemporaryDirectory() as tmp_dir:
            res = evaluate_single_case_with_timeout(
                case, config, tmp_dir, case_timeout_seconds=case_timeout_seconds, use_real_task3_interpreter=False
            )
            tier_a_results.append(res)
            completed_a_ids.add(case_id)

            checkpoint_data["tier_a_results"] = [r.to_dict() for r in tier_a_results]
            checkpoint_data["updated_at"] = time.time()
            save_checkpoint_atomic(checkpoint_path, checkpoint_data)

            symbol = "PASS" if res.passed else "FAIL"
            print(
                f"[{idx:02d}/{total_a}] {res.case_id:<20} elapsed={res.elapsed_seconds:5.1f}s requests={res.provider_requests} steps={res.agent_steps} status={res.status:<14} [{symbol}]"
            )

    # Stage 3: Tier B Integrated Smoke
    completed_b_ids = {r["case_id"] for r in checkpoint_data.get("tier_b_results", [])}
    tier_b_results: List[CaseEvaluationResult] = [CaseEvaluationResult.from_dict(r) for r in checkpoint_data.get("tier_b_results", [])]
    total_b = len(tier_b_cases)
    print(f"\n--- STAGE 3: TIER B INTEGRATED FULL-STACK SMOKE ({total_b} Cases) ---")

    for idx, case in enumerate(tier_b_cases, 1):
        case_id = case["case_id"]
        if case_id in completed_b_ids:
            print(f"[{idx:02d}/{total_b}] {case_id} ... [SKIPPED (Resumed)]")
            continue

        if time.time() - suite_start_time > run_max_duration_seconds:
            print(f"\nRUN WATCHDOG DEADLINE REACHED ({run_max_duration_seconds}s). Saving PARTIAL checkpoint and stopping.")
            checkpoint_data["status"] = "PARTIAL"
            save_checkpoint_atomic(checkpoint_path, checkpoint_data)
            return {"status": "PARTIAL_WATCHDOG_STOP", "checkpoint": checkpoint_path}

        with tempfile.TemporaryDirectory() as tmp_dir:
            res = evaluate_single_case_with_timeout(
                case, config, tmp_dir, case_timeout_seconds=case_timeout_seconds, use_real_task3_interpreter=True
            )
            tier_b_results.append(res)
            completed_b_ids.add(case_id)

            checkpoint_data["tier_b_results"] = [r.to_dict() for r in tier_b_results]
            checkpoint_data["updated_at"] = time.time()
            save_checkpoint_atomic(checkpoint_path, checkpoint_data)

            symbol = "PASS" if res.passed else "FAIL"
            print(
                f"[{idx:02d}/{total_b}] {res.case_id:<20} elapsed={res.elapsed_seconds:5.1f}s requests={res.provider_requests} steps={res.agent_steps} status={res.status:<14} [{symbol}]"
            )

    checkpoint_data["status"] = "COMPLETED"
    save_checkpoint_atomic(checkpoint_path, checkpoint_data)

    passed_a = sum(1 for r in tier_a_results if r.passed)
    metrics = {
        "total_tier_a": total_a,
        "passed_tier_a": passed_a,
        "accuracy_tier_a": (passed_a / total_a) if total_a > 0 else 0.0,
        "total_tier_b": total_b,
        "passed_tier_b": sum(1 for r in tier_b_results if r.passed),
    }

    print("\n" + "=" * 60)
    print(f"EVALUATION COMPLETE. Checkpoint written to {checkpoint_path}")
    print(f"Tier A Accuracy : {metrics['accuracy_tier_a']:.2%} ({passed_a}/{total_a})")
    print("=" * 60)

    return {"status": "SUCCESS", "metrics": metrics, "checkpoint": checkpoint_path}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Main Agent Real-Provider Semantic Baseline Evaluation")
    parser.add_argument("--manifest", default="benchmarks/main_agent_semantic_baseline_v1.json", help="Path to benchmark manifest JSON")
    parser.add_argument("--smoke-only", action="store_true", help="Run 3-case provider smoke only")
    parser.add_argument("--smoke-pacing-seconds", type=float, default=0.0, help="Pacing delay between smoke cases in seconds")
    parser.add_argument("--resume", action="store_true", help="Resume evaluation from atomic checkpoint")
    parser.add_argument("--checkpoint-path", default="tmp/semantic_eval_checkpoint.json", help="Path to evaluation checkpoint JSON")
    parser.add_argument("--case-timeout", type=float, default=180.0, help="Per-case watchdog timeout in seconds")
    parser.add_argument("--run-timeout", type=float, default=1800.0, help="Total run watchdog timeout in seconds")
    args = parser.parse_args()

    res = run_evaluation_suite(
        args.manifest,
        smoke_only=args.smoke_only,
        resume=args.resume,
        checkpoint_path=args.checkpoint_path,
        case_timeout_seconds=args.case_timeout,
        run_max_duration_seconds=args.run_timeout,
        smoke_pacing_seconds=args.smoke_pacing_seconds,
    )
    sys.exit(0 if res.get("status") in ("SUCCESS", "SMOKE_ONLY_PASS") else 1)
