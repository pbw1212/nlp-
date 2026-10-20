"""deterministic test rescore of Phase 2F Round-1 predictions against corrected v2 gold."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.task2_interpretation_eval_support import evaluate


ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint-dir", type=Path, default=Path("/tmp/phase2f-checkpoint"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    reports = {}
    for name, counts in (("eval", (30, 30, 10)), ("hard", (12, 12, 6))):
        root = ROOT / "benchmarks/task2" / f"context_interpretation_{name}_v2"
        report = evaluate(
            "checkpoint",
            dataset_root=root,
            dataset_version=root.name,
            expected_counts=counts,
            prediction_checkpoint=args.checkpoint_dir / ("frozen.json" if name == "eval" else "hard.json"),
        )
        reports[name] = report.to_dict()
    if args.json:
        print(json.dumps(reports, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    else:
        for name, report in reports.items():
            print(name, "context_f1=", report["context_metrics"]["f1"],
                  "false_known=", report["context_metrics"]["false_known_rate"],
                  "scope_state=", report["scope_metrics"]["scope_state_accuracy"],
                  "scope_axis_f1=", report["diagnostics"]["scope_field_selection_accuracy"],
                  "D=", report["attribution"]["predicted_predicted"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
