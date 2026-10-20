from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.task2_retrieval_eval_support import evaluate


def main() -> int:
    parser = argparse.ArgumentParser(description="Task 2D retrieval engineering evaluation")
    parser.add_argument(
        "--provider",
        choices=("deterministic-test", "fastembed"),
        default="deterministic-test",
        help="fastembed is explicit and may download a local model",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = evaluate(args.provider)
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return 0

    print("TASK 2D RETRIEVAL EVALUATION")
    print(f"Provider: {report.provider['provider']}")
    print(f"Model: {report.provider['model']}")
    if not report.semantic_performance_claim_allowed:
        print("Mode: CONTRACT-ONLY (NOT SEMANTIC PERFORMANCE)")
    print()
    print("Method       R@1      R@4      R@8      MRR")
    for method in report.methods:
        metrics = method.metrics
        print(
            f"{method.method:<12} {metrics.recall_at_1:.4f}   {metrics.recall_at_4:.4f}   "
            f"{metrics.recall_at_8:.4f}   {metrics.mrr:.4f}"
        )
    print("\nPer-subset R@4 / MRR")
    for method in report.methods:
        values = "  ".join(
            f"{subset.subset}={subset.metrics.recall_at_4:.4f}/{subset.metrics.mrr:.4f}"
            for subset in method.subsets
        )
        print(f"{method.method:<12} {values}")
    print()
    for name, query_ids in report.failure_analysis.items():
        print(f"{name}: {', '.join(query_ids) if query_ids else '-'}")
    print("\nResult: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
