import pytest

from scripts.task2_applicability_acceptance import run_acceptance


def test_phase_2b_engineering_fixture_is_deterministic():
    first = run_acceptance()
    second = run_acceptance()
    assert first == second
    assert first.passed
    assert first.applicability_accuracy == 1.0
    assert first.mismatch_rejection_accuracy == 1.0
    assert first.unknown_preservation_accuracy == pytest.approx(1.0)
    assert first.contamination_before > 0
    assert first.contamination_after == 0.0
