from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.task2_eval_support import evaluate_dataset


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 2C controlled applicability evaluation")
    parser.add_argument("--json", action="store_true", help="输出 deterministic machine-readable JSON")
    args = parser.parse_args()
    report = evaluate_dataset()
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    print("TASK 2C CONTROLLED APPLICABILITY EVALUATION\n")
    print(
        f"Dataset: {report.memory_count} memories / {report.query_count} queries / "
        f"{report.labeled_pair_count} labeled pairs\n"
    )
    print("Method            Recall@4  MRR      MatchRetain  MismatchReject  FalseReject  FalseAccept  Contam")
    print("------------------------------------------------------------------------------------------------")
    for result in report.methods_at_k4:
        metrics = result.metrics
        print(
            f"{result.method:<17} {result.recall_at_k:<9.4f} {result.mrr:<8.4f} "
            f"{metrics.applicable_retention_rate:<12.4f} {metrics.mismatch_rejection_rate:<15.4f} "
            f"{metrics.false_rejection_rate:<12.4f} {metrics.false_acceptance_rate:<12.4f} "
            f"{metrics.mismatch_contamination:.4f}"
        )
    print("\nUncertainty / coverage at K=4")
    for result in report.methods_at_k4:
        metrics = result.metrics
        print(
            f"{result.method:<17} unknown={metrics.unknown_preservation_rate:.4f} "
            f"ambiguous={metrics.ambiguous_preservation_rate:.4f} "
            f"unknown-retain={metrics.unknown_retention_rate:.4f} "
            f"ambiguous-retain={metrics.ambiguous_retention_rate:.4f} "
            f"coverage={metrics.definite_decision_coverage:.4f} "
            f"uncertainty={metrics.uncertainty_rate:.4f}"
        )
    print("\nK sensitivity (Recall / Mismatch contamination)")
    for method in ("B0_RECALL_ONLY", "B1_PERMISSIVE", "B1_STRICT", "B2_APPLICABILITY"):
        values = [result for result in report.k_sensitivity if result.method == method]
        rendered = "  ".join(
            f"K={item.k}:{item.recall_at_k:.3f}/{item.metrics.mismatch_contamination:.3f}"
            for item in values
        )
        print(f"{method:<17} {rendered}")
    print(f"\nHigh-rank mismatch cases: {len(report.high_rank_mismatch)}")
    for case in report.high_rank_mismatch[:10]:
        print(f"  {case}")
    print(f"False accepts (B2@4): {len(report.false_accepts)}")
    print(f"False rejects (B2@4): {len(report.false_rejects)}")
    print(f"Uncertain cases (B2@4): {len(report.uncertain_cases)}")

    b0 = next(item for item in report.methods_at_k4 if item.method == "B0_RECALL_ONLY")
    b2 = next(item for item in report.methods_at_k4 if item.method == "B2_APPLICABILITY")
    passed = (
        b0.metrics.mismatch_contamination > 0
        and b2.metrics.mismatch_rejection_rate > b0.metrics.mismatch_rejection_rate
        and b2.metrics.false_rejection_rate <= 0.05
        and b2.metrics.unknown_preservation_rate > 0
        and b2.metrics.ambiguous_preservation_rate > 0
        and bool(report.high_rank_mismatch)
    )
    print(f"\nResult:\n{'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
