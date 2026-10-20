"""Run staged semantic interpretation on a calibration split.

The holdout is intentionally a separate invocation so development cannot
silently tune against its predictions.  Real runs use the configured provider;
deterministic test contract tests use the staged interpreter unit tests instead.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.task2_interpretation_eval_support import evaluate

ROOT = Path(__file__).resolve().parents[2] / "benchmarks/task2"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=("dev", "holdout"), required=True)
    parser.add_argument("--provider", choices=("real",), default="real")
    parser.add_argument("--staged", action="store_true", help="use selector + conditioned interpretation")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--checkpoint-dir", type=Path)
    args = parser.parse_args()
    if args.checkpoint_dir is not None:
        args.checkpoint_dir.mkdir(parents=True, exist_ok=True)
    root = ROOT / f"semantic_calibration_{args.split}_v1"
    report = evaluate(
        args.provider,
        dataset_root=root,
        dataset_version=root.name,
        expected_counts=(20, 20, 10),
        prediction_checkpoint=(args.checkpoint_dir / f"{args.split}.json") if args.checkpoint_dir else None,
        staged=args.staged,
    )
    payload = report.to_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    else:
        print(f"{args.split}: context_f1={report.context_metrics.f1:.4f} "
              f"unsupported_known={report.context_metrics.unsupported_known_rate} "
              f"scope_axis={report.diagnostics['scope_field_selection_accuracy']:.4f} "
              f"scope_state={report.scope_metrics.scope_state_accuracy:.4f} "
              f"D={report.end_to_end_accuracy:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
