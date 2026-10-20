from scripts.task2_recall_acceptance import run_acceptance


def test_phase_2a_engineering_benchmark_is_deterministic():
    first = run_acceptance()
    second = run_acceptance()
    assert first == second
    assert first.passed
    assert first.recall_at_4 == 1.0
    assert first.mrr == 1.0
