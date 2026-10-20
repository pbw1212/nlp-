#!/usr/bin/env python3
"""Extended CLI Entry Point: runs canonical (14) + extended (18) trial scenarios.

Place at code/scripts/personal_agent_trial_extended.py
Requires extended_scenarios.py placed at code/src/memory/product_trial/extended_scenarios.py

Windows note: SQLite handles may linger after scenario completion, causing
PermissionError when TemporaryDirectory cleans up. We patch TemporaryDirectory
to ignore cleanup errors (harmless leftover files in %TEMP%).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

# --- Windows cleanup-error tolerance patch (must run before runner import) ---
_OrigTemporaryDirectory = tempfile.TemporaryDirectory


class _TolerantTemporaryDirectory(_OrigTemporaryDirectory):
    def __init__(self, *args, **kwargs):
        kwargs["ignore_cleanup_errors"] = True
        super().__init__(*args, **kwargs)


tempfile.TemporaryDirectory = _TolerantTemporaryDirectory
# --- end patch ---

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.memory.product_trial.runner import ProductTrialRunner
from src.memory.product_trial.scenarios import get_all_scenarios
from src.memory.product_trial.extended_scenarios import get_extended_scenarios


def main() -> None:
    parser = argparse.ArgumentParser(description="MAPAS Extended Product Trial (canonical + extended suites)")
    parser.add_argument("--category", type=str, default=None, help="Filter by category")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-json", type=str, default=".runtime/artifacts/product_trial/extended.json")
    args = parser.parse_args()

    print("=" * 64)
    print("MAPAS EXTENDED PRODUCT TRIAL  (canonical 14 + extended 18 = 32 scenarios)")
    print("=" * 64)

    runner = ProductTrialRunner()
    scenarios = list(get_all_scenarios()) + list(get_extended_scenarios())

    results = []
    for sc in scenarios:
        if args.category and sc.category != args.category:
            continue
        try:
            results.append(runner.run_scenario(sc))
        except Exception as exc:  # noqa: BLE001 - report and continue
            print(f"[{sc.scenario_id:<7}] {sc.category.upper():<13} [ERROR] | {sc.description}")
            print(f"           -> runner exception: {type(exc).__name__}: {exc}")
            continue

    passed = sum(1 for r in results if r.status == "PASS")
    failed = sum(1 for r in results if r.status == "FAIL")

    print("\n--- SCENARIO RESULTS ---")
    for r in results:
        mark = "[PASS]" if r.status == "PASS" else "[FAIL]"
        print(f"[{r.scenario_id:<7}] {r.category.upper():<13} {mark} | {r.description}")
        if r.status == "FAIL":
            for reason in r.failed_reasons:
                print(f"           -> {reason}")

    try:
        fuzz_ok, fuzz_violations = runner.run_seeded_fuzz(seed=args.seed, steps_count=50)
    except Exception as exc:  # noqa: BLE001
        fuzz_ok, fuzz_violations = False, [f"fuzz exception: {exc}"]

    # Per-category summary
    cats = {}
    for r in results:
        c = cats.setdefault(r.category, [0, 0])
        c[0] += 1
        c[1] += 1 if r.status == "PASS" else 0

    print("\n" + "=" * 64)
    print("EXTENDED TRIAL SUMMARY")
    print("=" * 64)
    for c, (total, ok) in sorted(cats.items()):
        print(f"  {c:<15} {ok}/{total} PASS")
    print("-" * 64)
    print(f"  TOTAL           {passed}/{len(results)} PASS")
    print(f"  Fuzz (50 steps) {'PASS' if fuzz_ok else 'FAIL'}")
    print("=" * 64)

    os.makedirs(os.path.dirname(args.output_json), exist_ok=True)
    with open(args.output_json, "w", encoding="utf-8") as f:
        json.dump(
            {
                "summary": {"total": len(results), "pass": passed, "fail": failed, "fuzz_passed": fuzz_ok},
                "categories": {c: {"total": t, "pass": p} for c, (t, p) in cats.items()},
                "scenarios": [
                    {"id": r.scenario_id, "category": r.category, "status": r.status,
                     "description": r.description, "failed_reasons": list(r.failed_reasons)}
                    for r in results
                ],
            },
            f, indent=2, ensure_ascii=False,
        )
    print(f"JSON report: {args.output_json}")


if __name__ == "__main__":
    main()
