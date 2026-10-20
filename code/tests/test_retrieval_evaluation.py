from __future__ import annotations

import json

import pytest

from scripts.task2_retrieval_eval_support import (
    DATASET_PATH,
    RetrievalDatasetError,
    evaluate,
    load_dataset,
)


def test_retrieval_dataset_validates_size_domains_subsets_and_gold():
    dataset = load_dataset()
    assert len(dataset.memories) == 40
    assert len(dataset.queries) == 32
    assert len(dataset.hard_negatives) == 32
    assert len({item.domain for item in dataset.memories}) == 8
    assert {item.subset for item in dataset.queries} == {
        "lexical_exact", "semantic_paraphrase", "mixed"
    }


def test_retrieval_dataset_rejects_missing_positive(tmp_path):
    payload = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    payload["queries"][0]["positive_memory_ids"] = ["missing"]
    bad_path = tmp_path / "bad.json"
    bad_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RetrievalDatasetError):
        load_dataset(bad_path)


def test_retrieval_evaluation_is_deterministic_and_provenance_labeled():
    first = evaluate().to_dict()
    second = evaluate().to_dict()
    assert first == second
    assert first["semantic_performance_claim_allowed"] is False
    assert [item["method"] for item in first["methods"]] == ["BM25", "DENSE", "HYBRID_RRF"]
    assert all(item["provenance"] == "OUR_BASELINE" for item in first["methods"])


def test_retrieval_metrics_and_subsets_are_bounded():
    report = evaluate()
    for method in report.methods:
        assert len(method.subsets) == 3
        for value in (
            method.metrics.recall_at_1,
            method.metrics.recall_at_4,
            method.metrics.recall_at_8,
            method.metrics.mrr,
        ):
            assert 0 <= value <= 1
        assert method.metrics.recall_at_1 <= method.metrics.recall_at_4 <= method.metrics.recall_at_8
