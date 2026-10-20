#!/usr/bin/env python3
"""CLI entry point for the MAPAS product trial harness."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

# Ensure repository root is on Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.memory.product_trial.runner import ProductTrialRunner
from src.memory.core.diagnostics import log_boundary_failure


logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="MAPAS Personal Agent Product Trial Harness")
    parser.add_argument("--suite", type=str, default="core", help="Scenario suite to run (core/all)")
    parser.add_argument("--category", type=str, default=None, help="Filter by category (conversation/memory/task/grounding/tools/safety/cross)")
    parser.add_argument("--seed", type=int, default=42, help="Seed for deterministic fuzzing")
    parser.add_argument("--explore", action="store_true", help="Run exploratory self-play mode")
    parser.add_argument("--output-json", type=str, default=".runtime/artifacts/product_trial/latest.json", help="Path to save output JSON report")
    args = parser.parse_args()

    print("=" * 60)
    print("MAPAS PERSONAL AGENT PRODUCT TRIAL HARNESS")
    print("Isolation Mode : 100% Temporary Directory (Real ~/.agent_data Untouched)")
    print("Provider Mode  : explicit test client")
    print("Fixer Status   : DISABLED (Harness Execution & Calibration Phase)")
    print("=" * 60)

    runner = ProductTrialRunner()

    # 1. Run Predefined Scenarios
    results = runner.run_suite(category_filter=args.category)

    passed_cnt = sum(1 for r in results if r.status == "PASS")
    failed_cnt = sum(1 for r in results if r.status == "FAIL")
    uncertain_cnt = sum(1 for r in results if r.status == "UNCERTAIN")

    print("\n--- SCENARIO RESULTS ---")
    for r in results:
        status_symbol = "[PASS]" if r.status == "PASS" else "[FAIL]"
        print(f"[{r.scenario_id}] {r.category.upper():<12} {status_symbol} | {r.description}")
        if r.status == "FAIL":
            for reason in r.failed_reasons:
                print(f"   -> Violation: {reason}")

    # 2. Run Seeded Fuzzing
    fuzz_passed, fuzz_violations = runner.run_seeded_fuzz(seed=args.seed, steps_count=50)

    # 3. Print Dashboard Summary
    print("\n" + "=" * 60)
    print("PRODUCT TRIAL SUMMARY")
    print("=" * 60)
    print(f"Total Scenarios Run : {len(results)}")
    print(f"PASS                : {passed_cnt}")
    print(f"FAIL                : {failed_cnt}")
    print(f"UNCERTAIN           : {uncertain_cnt}")
    print(f"Deterministic Fuzz  : {'PASS (50 steps)' if fuzz_passed else 'FAIL'}")
    if fuzz_violations:
        print(f"Fuzz Violations     : {fuzz_violations}")
    print("-" * 60)
    print("Calibration Checks:")
    c001_res = next((r for r in results if r.scenario_id == "C001"), None)
    m003_res = next((r for r in results if r.scenario_id == "M003"), None)
    g001_res = next((r for r in results if r.scenario_id == "G001"), None)
    print(f" - C001 (Previous Question Recall) : {'PASS' if c001_res and c001_res.status == 'PASS' else 'FAIL'}")
    print(f" - M003 (Conflicting /remember)   : {'FAIL (Expected Calibration Failure)' if m003_res and m003_res.status == 'FAIL' else 'PASS'}")
    print(f" - G001 (Dynamic Weather Grounding): {'PASS' if g001_res and g001_res.status == 'PASS' else 'FAIL'}")
    print("=" * 60)

    # Save JSON Report
    report_data = {
        "summary": {
            "total": len(results),
            "pass": passed_cnt,
            "fail": failed_cnt,
            "uncertain": uncertain_cnt,
            "fuzz_passed": fuzz_passed,
            "fuzz_violations": fuzz_violations,
        },
        "scenarios": [
            {
                "scenario_id": r.scenario_id,
                "category": r.category,
                "description": r.description,
                "status": r.status,
                "failed_reasons": r.failed_reasons,
            }
            for r in results
        ],
    }

    try:
        os.makedirs(os.path.dirname(args.output_json), exist_ok=True)
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=2, ensure_ascii=False)
        print(f"JSON Report saved to: {args.output_json}")
    except Exception as exc:
        metadata = log_boundary_failure(logger, "product_trial_report_write_failed", exc)
        print(f"Could not save JSON report ({metadata['error_type']}).")


if __name__ == "__main__":
    main()
