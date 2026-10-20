"""Unit tests for Main Agent Evaluation Harness stabilization, fail-fast, timeout, checkpointing, and resume logic."""

from __future__ import annotations

import json
import os
import tempfile
import time
import pytest

from scripts.main_agent_semantic_eval import (
    CaseEvaluationResult,
    EvaluationStatus,
    FROZEN_MECHANISM_COMMIT,
    _compute_file_sha256,
    evaluate_single_case_with_timeout,
    load_checkpoint,
    run_evaluation_suite,
    save_checkpoint_atomic,
)
from src.memory.core.config import MemoryConfig, ProviderMode


def test_smoke_pacing_seconds_honored(monkeypatch, tmp_path):
    manifest = {
        "protocol_version": "v1",
        "tier_a_cases": [],
        "tier_b_smoke_cases": [],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def mock_eval(case, config, db_dir, case_timeout_seconds=180.0, use_real_task3_interpreter=False):
        return CaseEvaluationResult(case_id=case["case_id"], category="SMOKE", passed=True, status="SUCCESS")

    monkeypatch.setattr("scripts.main_agent_semantic_eval.evaluate_single_case_with_timeout", mock_eval)
    monkeypatch.setattr("scripts.main_agent_semantic_eval.build_llm_client", lambda cfg: None)

    sleeps = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))

    res = run_evaluation_suite(str(manifest_path), smoke_only=True, smoke_pacing_seconds=65.0)
    assert res["status"] == "SMOKE_ONLY_PASS"
    assert sleeps == [65.0, 65.0]


def test_smoke_failure_blocks_tier_a_and_tier_b(monkeypatch, tmp_path):
    manifest = {
        "protocol_version": "v1",
        "tier_a_cases": [{"case_id": "TA_01", "category": "NO_TOOL", "user_input": "hi"}],
        "tier_b_smoke_cases": [],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    # Mock evaluate_single_case_with_timeout to fail on Smoke 01
    def mock_eval(case, config, db_dir, case_timeout_seconds=180.0, use_real_task3_interpreter=False):
        if case["case_id"] == "SMOKE_01":
            return CaseEvaluationResult(
                case_id="SMOKE_01",
                category="SMOKE",
                passed=False,
                status=EvaluationStatus.PROVIDER_FAIL.value,
                error_code="PROVIDER_FAILED",
                error_message="Simulated provider failure",
            )
        return CaseEvaluationResult(case_id=case["case_id"], category="SMOKE", passed=True, status="SUCCESS")

    monkeypatch.setattr("scripts.main_agent_semantic_eval.evaluate_single_case_with_timeout", mock_eval)

    # Monkeypatch build_llm_client to avoid provider config failure
    monkeypatch.setattr("scripts.main_agent_semantic_eval.build_llm_client", lambda cfg: None)

    res = run_evaluation_suite(str(manifest_path))
    assert res["status"] == "EVALUATION_BLOCKED"
    assert len(res["smoke_results"]) == 1
    assert res["smoke_results"][0]["case_id"] == "SMOKE_01"


def test_case_timeout_watchdog(tmp_path):
    slow_case = {"case_id": "SLOW_01", "category": "NO_TOOL", "user_input": "slow call"}
    cfg = MemoryConfig()
    cfg.provider_mode = ProviderMode.REQUIRED

    # Evaluate case with a tiny 0.001s timeout
    res = evaluate_single_case_with_timeout(slow_case, cfg, str(tmp_path), case_timeout_seconds=0.001)
    assert res.status == EvaluationStatus.CASE_TIMEOUT.value
    assert res.passed is False
    assert res.error_code == "CASE_TIMEOUT"


def test_atomic_checkpointing_and_resume(tmp_path):
    checkpoint_file = str(tmp_path / "checkpoint.json")
    digest = "test_digest_123"
    commit = "test_commit_456"

    initial_data = {
        "protocol_commit": commit,
        "mechanism_commit": FROZEN_MECHANISM_COMMIT,
        "benchmark_digest": digest,
        "provider": "test_provider",
        "model": "test_model",
        "updated_at": time.time(),
        "tier_a_results": [{"case_id": "C01", "passed": True}],
        "tier_b_results": [],
        "status": "RUNNING",
    }

    save_checkpoint_atomic(checkpoint_file, initial_data)
    assert os.path.exists(checkpoint_file)

    loaded = load_checkpoint(checkpoint_file, digest, commit, FROZEN_MECHANISM_COMMIT, "test_provider", "test_model")
    assert loaded is not None
    assert loaded["tier_a_results"][0]["case_id"] == "C01"

    # Verify resume rejects changed commit or digest
    with pytest.raises(ValueError, match="protocol_commit mismatch"):
        load_checkpoint(checkpoint_file, digest, "changed_commit", FROZEN_MECHANISM_COMMIT, "test_provider", "test_model")

    with pytest.raises(ValueError, match="benchmark_digest mismatch"):
        load_checkpoint(checkpoint_file, "changed_digest", commit, FROZEN_MECHANISM_COMMIT, "test_provider", "test_model")


def test_provider_failure_is_not_counted_as_semantic_failure():
    res = CaseEvaluationResult(
        case_id="C02",
        category="NO_TOOL",
        passed=False,
        status=EvaluationStatus.PROVIDER_FAIL.value,
        error_code="HTTP_429",
        error_message="Rate limit exceeded",
    )
    assert res.status == EvaluationStatus.PROVIDER_FAIL.value
    assert res.status != EvaluationStatus.SEMANTIC_FAIL.value


def test_step_limit_action_trace_has_no_cot():
    trace = [
        {"step_index": 0, "action_kind": "TOOL_CALL", "tool_name": "weather_lookup", "tool_call_id": "call_1"},
        {"step_index": 1, "action_kind": "TOOL_CALL", "tool_name": "weather_lookup", "tool_call_id": "call_2"},
    ]
    res = CaseEvaluationResult(
        case_id="C03",
        category="READ_ONLY",
        passed=False,
        status=EvaluationStatus.STEP_LIMIT.value,
        action_trace=trace,
    )
    assert res.status == EvaluationStatus.STEP_LIMIT.value
    for item in res.action_trace:
        assert "thought" not in item
        assert "cot" not in item

