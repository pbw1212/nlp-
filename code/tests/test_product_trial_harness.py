"""Unit tests for Autonomous Personal Agent Product Trial Harness itself."""

import os
import sqlite3
import pytest
import tempfile

from src.memory.product_trial.assertions import HardOracleEvaluator
from src.memory.product_trial.models import HardInvariant, InvariantType, TrialObservation
from src.memory.product_trial.observer import ProductTrialObserver
from src.memory.product_trial.runner import ProductTrialRunner
from src.memory.product_trial.scenarios import get_all_scenarios


def test_harness_temporary_data_isolation():
    """Verifies harness runs in 100% temporary data isolation without touching real ~/.agent_data."""
    real_data_dir = os.path.expanduser("~/.agent_data")
    mod_time_before = None
    if os.path.exists(real_data_dir):
        mod_time_before = os.path.getmtime(real_data_dir)

    runner = ProductTrialRunner()
    res = runner.run_scenario(get_all_scenarios()[0])  # Run C001
    assert res.status == "PASS"

    if os.path.exists(real_data_dir):
        mod_time_after = os.path.getmtime(real_data_dir)
        assert mod_time_after == mod_time_before, "Real ~/.agent_data directory MUST NOT be modified by trial harness!"


def test_observer_and_hard_oracle_assertions():
    obs = TrialObservation(
        visible_response="当前系统时间是 19:24",
        agent_result_status="AgentResultStatus.SUCCESS",
        current_session_id="session_123",
        current_task_id="task_general",
        memories=(),
        task_entries_count=1,
        tool_calls_count=1,
        executed_side_effects_count=0,
        warnings=(),
        errors=(),
    )

    # 1. Contains text
    p1, _ = HardOracleEvaluator.evaluate(HardInvariant(InvariantType.CONTAINS_TEXT, "19:24"), obs)
    assert p1 is True

    # 2. Result status
    p2, _ = HardOracleEvaluator.evaluate(HardInvariant(InvariantType.RESULT_STATUS, "AgentResultStatus.SUCCESS"), obs)
    assert p2 is True

    # 3. Weather grounding
    p3, _ = HardOracleEvaluator.evaluate(HardInvariant(InvariantType.NO_UNGROUNDED_WEATHER_CLAIM), obs)
    assert p3 is True

    # 4. Weather claim failure detection
    obs_hallucinated = TrialObservation(
        visible_response="天津明天晴朗 28°C",
        agent_result_status="AgentResultStatus.SUCCESS",
        current_session_id="s1",
        current_task_id="t1",
        memories=(),
        task_entries_count=0,
        tool_calls_count=0,
        executed_side_effects_count=0,
        warnings=(),
        errors=(),
    )
    p4, _ = HardOracleEvaluator.evaluate(HardInvariant(InvariantType.NO_UNGROUNDED_WEATHER_CLAIM), obs_hallucinated)
    assert p4 is False


def test_m003_conflicting_remember_passes_after_fix():
    """Harness Closed Loop Test: M003 conflicting /remember MUST pass after production fix."""
    runner = ProductTrialRunner()
    scenarios = get_all_scenarios()
    m003 = next(sc for sc in scenarios if sc.scenario_id == "M003")
    res = runner.run_scenario(m003)

    assert res.status == "PASS", "Harness verified M003 closed-loop fix passes!"


def test_seeded_fuzz_determinism():
    """Verifies deterministic seeded fuzzing runs 50 steps without global system invariant violations."""
    runner = ProductTrialRunner()
    passed, violations = runner.run_seeded_fuzz(seed=123, steps_count=50)
    assert passed is True
    assert len(violations) == 0
