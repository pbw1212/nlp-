from __future__ import annotations

import json
import shutil
import socket
from dataclasses import asdict

import pytest

from scripts.task2_eval_support import (
    DATASET_ROOT,
    DatasetValidationError,
    EvaluationDataset,
    K_VALUES,
    METHODS,
    calculate_metrics,
    evaluate_dataset,
    load_evaluation_dataset,
    _populate,
    _ScopedReadRepository,
)
from src.memory.retrieval.recall import MemoryRecallEngine
from src.memory.storage.sqlite_manager import SQLiteMemoryManager


def _methods(report, k=4):
    return {item.method: item for item in report.k_sensitivity if item.k == k}


def test_dataset_validator_and_gold_runtime_separation():
    dataset = load_evaluation_dataset()
    assert len(dataset.memories) == 32
    assert len(dataset.queries) == 28
    assert sum(len(items) for items in dataset.gold.values()) == 224
    assert all(len(query.candidate_universe) == 8 for query in dataset.queries)
    runtime_files = ("memories.json", "profiles.json", "queries.json", "contexts.json")
    for name in runtime_files:
        text = (DATASET_ROOT / name).read_text(encoding="utf-8")
        assert '"gold_label"' not in text
        assert '"labels"' not in text


def test_validator_rejects_bad_reference_and_incomplete_gold(tmp_path):
    copied = tmp_path / "dataset"
    shutil.copytree(DATASET_ROOT, copied)
    queries = json.loads((copied / "queries.json").read_text(encoding="utf-8"))
    queries["queries"][0]["candidate_universe"][0] = "missing-memory"
    (copied / "queries.json").write_text(json.dumps(queries), encoding="utf-8")
    with pytest.raises(DatasetValidationError, match="不存在 memory"):
        load_evaluation_dataset(copied)

    shutil.rmtree(copied)
    shutil.copytree(DATASET_ROOT, copied)
    labels = json.loads((copied / "labels.json").read_text(encoding="utf-8"))
    labels["labels"][0]["items"].pop()
    (copied / "labels.json").write_text(json.dumps(labels), encoding="utf-8")
    with pytest.raises(DatasetValidationError, match="完整覆盖"):
        load_evaluation_dataset(copied)


def test_controlled_evaluation_is_deterministic_and_repeatable(monkeypatch):
    def forbidden_network(*args, **kwargs):
        raise AssertionError("evaluation attempted network access")

    monkeypatch.setattr(socket, "create_connection", forbidden_network)
    first = evaluate_dataset()
    second = evaluate_dataset()
    assert first == second
    assert json.dumps(first.to_dict(), sort_keys=True) == json.dumps(second.to_dict(), sort_keys=True)


def test_b0_b1_b2_controlled_ablation_metrics():
    report = evaluate_dataset()
    methods = _methods(report)
    b0 = methods["B0_RECALL_ONLY"].metrics
    permissive = methods["B1_PERMISSIVE"].metrics
    strict = methods["B1_STRICT"].metrics
    b2 = methods["B2_APPLICABILITY"].metrics

    assert methods["B0_RECALL_ONLY"].recall_at_k == 1.0
    assert methods["B0_RECALL_ONLY"].mrr == pytest.approx(0.9821428571)
    assert b0.mismatch_contamination > 0
    assert b0.mismatch_rejection_rate == 0.0
    assert permissive.mismatch_rejection_rate == 1.0
    assert permissive.unknown_retention_rate == 1.0
    assert permissive.unknown_preservation_rate == 0.0
    assert strict.unknown_retention_rate == 0.0
    assert strict.unknown_preservation_rate == 0.0
    assert b2.mismatch_rejection_rate == 1.0
    assert b2.applicable_retention_rate == 1.0
    assert b2.false_rejection_rate == 0.0
    assert b2.false_acceptance_rate == 0.0
    assert b2.unknown_preservation_rate == pytest.approx(10 / 13)
    assert b2.ambiguous_preservation_rate == pytest.approx(1.0)
    assert b2.definite_decision_coverage < 1.0
    assert b2.uncertainty_rate > 0.0


def test_k_sensitivity_and_high_rank_hard_negatives():
    report = evaluate_dataset()
    b0 = [_methods(report, k)["B0_RECALL_ONLY"] for k in K_VALUES]
    b2 = [_methods(report, k)["B2_APPLICABILITY"] for k in K_VALUES]
    assert [item.recall_at_k for item in b0] == sorted(item.recall_at_k for item in b0)
    assert [item.metrics.mismatch_contamination for item in b0] == sorted(
        item.metrics.mismatch_contamination for item in b0
    )
    assert all(item.metrics.mismatch_contamination == 0.0 for item in b2)
    assert report.high_rank_mismatch
    assert any(case.endswith("@1") for case in report.high_rank_mismatch)


def test_per_dimension_family_and_subset_breakdowns_are_present():
    report = evaluate_dataset()
    b2 = _methods(report)["B2_APPLICABILITY"]
    assert {item.name for item in b2.by_dimension} == {
        "PERSON", "LOCATION", "TIME", "TASK", "SCENARIO", "CONSTRAINT"
    }
    assert all(item.gold_count > 0 for item in b2.by_dimension)
    assert len(b2.by_family) == 8
    subsets = {item.name: item for item in b2.by_subset}
    assert subsets["supported"].gold_count > 100
    assert subsets["supported"].accuracy == pytest.approx(11 / 12)
    assert subsets["rich_reasoning"].gold_count == 1
    assert subsets["rich_reasoning"].accuracy == 1.0


def test_rich_reasoning_case_is_preserved_as_unknown():
    report = evaluate_dataset()
    case = next(
        item for item in report.cases_at_k4
        if item.query_id == "q09_soundproof_conditional"
        and item.memory_id == "m_travel_evolved_soundproof"
    )
    assert case.subset == "rich_reasoning"
    assert case.gold_label == "UNKNOWN"
    assert case.predicted_label == "UNKNOWN"
    assert case.retained


def test_preference_evolution_uses_current_revision_without_historical_leakage(tmp_path):
    dataset = load_evaluation_dataset()
    repository = SQLiteMemoryManager(str(tmp_path / "current-safe.db"))
    _populate(repository, dataset.memories)
    history = repository.get_history("m_travel_evolved_soundproof")
    assert len(history) == 2
    assert history[0].semantic_validity.value == "SUPERSEDED"
    assert "安静的郊区" in history[0].content
    assert history[1].semantic_validity.value == "CURRENT"
    scoped = _ScopedReadRepository(repository, ("m_travel_evolved_soundproof",))
    result = MemoryRecallEngine(scoped).recall("非常安静的郊区酒店", top_k=8)
    assert all(candidate.snapshot.revision.revision_id == history[1].revision_id for candidate in result.candidates)
    assert all("安静的郊区" not in candidate.snapshot.revision.content for candidate in result.candidates)


def test_metric_formulas_distinguish_false_accept_and_false_reject():
    report = evaluate_dataset()
    source = report.cases_at_k4
    match = next(case for case in source if case.gold_label == "MATCH")
    mismatch = next(case for case in source if case.gold_label == "MISMATCH")
    synthetic = (
        type(match)(**{**asdict(match), "retained": False, "predicted_label": "MISMATCH"}),
        type(mismatch)(**{**asdict(mismatch), "retained": True, "predicted_label": "MATCH"}),
    )
    metrics = calculate_metrics(synthetic)
    assert metrics.false_rejection_rate == 1.0
    assert metrics.false_acceptance_rate == 1.0
    assert metrics.applicable_retention_rate == 0.0
    assert metrics.mismatch_rejection_rate == 0.0

