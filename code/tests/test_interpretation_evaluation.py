from __future__ import annotations

from scripts.task2_interpretation_eval_support import evaluate, load_dataset, _context_metrics
from scripts.task2_semantic_hardening_eval import HARD_ROOT, run
from pathlib import Path
import json


def test_natural_language_dataset_is_independent_multilingual_and_complete():
    dataset = load_dataset()
    assert len(dataset.contexts) == 30
    assert len(dataset.memories) == 30
    assert len(dataset.pairs) == 10
    assert {item.language for item in dataset.contexts + dataset.memories} == {"zh", "en", "mixed"}


def test_oracle_evaluator_is_deterministic_and_not_semantic_claim():
    first, second = evaluate().to_dict(), evaluate().to_dict()
    assert first == second
    assert first["semantic_performance_claim_allowed"] is False
    assert first["provider"]["provider"] == "oracle-fixture"


def test_oracle_attribution_and_interpreter_metrics_are_exact():
    report = evaluate()
    assert report.context_metrics.f1 == 1.0
    assert report.context_metrics.false_known_rate == 0.0
    assert report.scope_metrics.scope_state_accuracy == 1.0
    assert report.attribution.oracle_oracle == 1.0
    assert report.attribution.predicted_context_oracle_scope == 1.0
    assert report.attribution.oracle_context_predicted_scope == 1.0
    assert report.attribution.predicted_predicted == 1.0


def test_hardening_dataset_is_independent_and_oracle_exact():
    dataset = load_dataset(HARD_ROOT, expected_counts=(12, 12, 6))
    assert len(dataset.contexts) + len(dataset.memories) == 24
    assert {item.language for item in dataset.contexts + dataset.memories} == {
        "zh", "en", "mixed"
    }
    frozen, hard, gate = run("oracle-fixture")
    assert frozen.context_metrics.f1 == 1.0
    assert hard.context_metrics.f1 == 1.0
    assert hard.scope_metrics.scope_state_accuracy == 1.0
    assert hard.attribution.predicted_predicted == 1.0
    assert all(gate.values())


def test_undefined_unsupported_abstention_is_reported_as_na():
    frozen = evaluate()
    hard = evaluate(
        dataset_root=HARD_ROOT,
        dataset_version="context_interpretation_hard_v1",
        expected_counts=(12, 12, 6),
    )
    assert frozen.diagnostics["unsupported_field_abstention_rate"] is None
    assert hard.diagnostics["unsupported_field_abstention_rate"] == 1.0


def test_unsupported_known_rate_uses_predicted_known_denominator():
    from src.memory.retrieval.applicability_models import ApplicabilityContext, ApplicabilityDimension, ContextKnowledgeState, ContextValue
    gold = {"q": ApplicabilityContext(())}
    predicted = {"q": ApplicabilityContext((ContextValue(ApplicabilityDimension.TASK, "activity", ContextKnowledgeState.KNOWN, ("hiking",)),))}
    metrics, _ = _context_metrics(gold, predicted)
    assert metrics.unsupported_known_count == 1
    assert metrics.predicted_known_count == 1
    assert metrics.unsupported_known_rate == 1.0
    assert metrics.supported_known_precision == 0.0


def test_unsupported_known_rate_is_na_when_no_known_predictions():
    from src.memory.retrieval.applicability_models import ApplicabilityContext
    metrics, _ = _context_metrics({"q": ApplicabilityContext(())}, {"q": ApplicabilityContext(())})
    assert metrics.unsupported_known_rate is None
    assert metrics.supported_known_precision is None


def test_prediction_checkpoint_is_typed_private_and_resumable(tmp_path):
    checkpoint = tmp_path / "predictions.json"
    first = evaluate("oracle-fixture", prediction_checkpoint=checkpoint)
    payload = checkpoint.read_text(encoding="utf-8")
    assert "周末我和几个朋友" not in payload
    assert set(__import__("json").loads(payload)) == {"contexts", "scopes", "usage"}
    second = evaluate("oracle-fixture", prediction_checkpoint=checkpoint)
    assert first.to_dict() == second.to_dict()


def test_v1_gold_is_preserved_and_v2_migration_is_complete():
    root = Path(__file__).resolve().parents[2] / "benchmarks/task2"
    v1_scope = json.loads((root / "scope_gold_migration_v1_to_v2.json").read_text())
    v1_context = json.loads((root / "context_gold_migration_v1_to_v2.json").read_text())
    v1_pairs = json.loads((root / "pair_gold_migration_v1_to_v2.json").read_text())
    assert v1_scope
    assert v1_context
    assert v1_pairs
    for name in ("eval", "hard"):
        v2 = root / f"context_interpretation_{name}_v2"
        gold = json.loads((v2 / "gold.json").read_text())
        evidence = json.loads((v2 / "scope_evidence_v2.json").read_text())
        keys = {(item["case_id"], item["rule_index"]) for item in evidence}
        assert gold["schema_version"] == 2
        assert all((item["case_id"], index) in keys for item in gold["scope_gold"] for index, _ in enumerate(item["rules"]))
        assert all(rule[2] != "UNSPECIFIED" or item["case_id"] in {"m08", "m30", "hm10"}
                   for item in gold["scope_gold"] for rule in item["rules"])
    assert json.loads((root / "context_interpretation_eval_v1/gold.json").read_text())["schema_version"] == 1
    assert json.loads((root / "context_interpretation_hard_v1/gold.json").read_text())["schema_version"] == 1
