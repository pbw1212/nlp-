from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.task2_interpretation_eval_support import evaluate


ROOT = Path(__file__).resolve().parents[2]
HARD_ROOT = ROOT / "benchmarks/task2/context_interpretation_hard_v1"
BEFORE = {
    "context_f1": 0.1026,
    "false_known": 0.7619,
    "scope_state": 0.0141,
    "end_to_end_d": 0.4000,
}


def run(provider: str, *, checkpoint_dir: Path | None = None):
    frozen = evaluate(
        provider,
        prediction_checkpoint=(checkpoint_dir / "frozen.json") if checkpoint_dir else None,
    )
    hard = evaluate(
        provider,
        dataset_root=HARD_ROOT,
        dataset_version="context_interpretation_hard_v1",
        expected_counts=(12, 12, 6),
        prediction_checkpoint=(checkpoint_dir / "hard.json") if checkpoint_dir else None,
    )
    validation_failures = (
        len(frozen.errors["context_validation"])
        + len(frozen.errors["scope_validation"])
    )
    validation_total = frozen.context_count + frozen.memory_count
    gate = {
        "context_f1": frozen.context_metrics.f1 >= 0.75,
        "false_known": frozen.context_metrics.false_known_rate <= 0.15,
        "scope_state": frozen.scope_metrics.scope_state_accuracy >= 0.70,
        "validation_failure": (validation_failures / validation_total) <= 0.05,
        "end_to_end_d": frozen.attribution.predicted_predicted >= 0.70,
        "hard_no_catastrophic_collapse": (
            hard.context_metrics.f1 >= 0.60
            and hard.scope_metrics.scope_state_accuracy >= 0.60
            and hard.attribution.predicted_predicted >= 0.60
        ),
    }
    return frozen, hard, gate


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 2F semantic hardening evaluation")
    parser.add_argument("--provider", choices=("oracle-fixture", "real"), default="oracle-fixture")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--checkpoint-dir", type=Path)
    args = parser.parse_args()
    if args.checkpoint_dir is not None:
        args.checkpoint_dir.mkdir(parents=True, exist_ok=True)
    frozen, hard, gate = run(args.provider, checkpoint_dir=args.checkpoint_dir)
    if args.json:
        print(json.dumps({
            "before": BEFORE,
            "frozen": frozen.to_dict(),
            "hard": hard.to_dict(),
            "promotion_gate": gate,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return 0 if all(gate.values()) else 1

    print("TASK 2F SEMANTIC INTERPRETATION HARDENING")
    print(f"Provider: {frozen.provider['provider']} / {frozen.provider['model']}")
    print("\nFrozen-v1 before -> after")
    print(f"Context F1:       {BEFORE['context_f1']:.4f} -> {frozen.context_metrics.f1:.4f}")
    print(f"False-known:      {BEFORE['false_known']:.4f} -> {frozen.context_metrics.false_known_rate:.4f}")
    print(f"Scope state:      {BEFORE['scope_state']:.4f} -> {frozen.scope_metrics.scope_state_accuracy:.4f}")
    print(f"End-to-end D:     {BEFORE['end_to_end_d']:.4f} -> {frozen.attribution.predicted_predicted:.4f}")
    print("\nFrozen diagnostics")
    for key, value in frozen.diagnostics.items():
        print(f"{key}: {'N/A' if value is None else f'{value:.4f}'}")
    print("\nHard-v1")
    print(f"Context F1:       {hard.context_metrics.f1:.4f}")
    print(f"False-known:      {hard.context_metrics.false_known_rate:.4f}")
    print(f"Scope state:      {hard.scope_metrics.scope_state_accuracy:.4f}")
    print(f"End-to-end D:     {hard.attribution.predicted_predicted:.4f}")
    print("\nPromotion gate")
    for key, passed in gate.items():
        print(f"{key:30} {'PASS' if passed else 'FAIL'}")
    print(f"\nResult: {'PASS' if all(gate.values()) else 'FAIL'}")
    return 0 if all(gate.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
