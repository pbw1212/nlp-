from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.task2_interpretation_eval_support import evaluate, smoke


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 2E semantic interpretation evaluation")
    parser.add_argument("--provider", choices=("oracle-fixture", "real"), default="oracle-fixture")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.smoke:
        report = smoke(args.provider)
        if args.json:
            print(json.dumps(report.__dict__, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            return 0 if report.context_passed and report.scope_passed and report.end_to_end_passed else 1
        print("TASK 2E REAL SEMANTIC SMOKE")
        print(f"Provider: {report.provider['provider']} / {report.provider['model']}")
        print(f"Context: {'PASS' if report.context_passed else 'FAIL'}")
        print(f"Scope: {'PASS' if report.scope_passed else 'FAIL'}")
        print(f"End-to-end: {'PASS' if report.end_to_end_passed else 'FAIL'} ({report.predicted_status})")
        print(f"Usage: requests={report.usage.get('request_count', 0)} input={report.usage.get('input_tokens', 0)} output={report.usage.get('output_tokens', 0)}")
        return 0 if report.context_passed and report.scope_passed and report.end_to_end_passed else 1
    report = evaluate(args.provider)
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return 0
    print("TASK 2E SEMANTIC INTERPRETATION EVALUATION")
    print(f"Provider: {report.provider['provider']} / {report.provider['model']}")
    if not report.semantic_performance_claim_allowed:
        print("Mode: ORACLE ORCHESTRATION (NOT SEMANTIC PERFORMANCE)")
    context, scope, attr = report.context_metrics, report.scope_metrics, report.attribution
    print(f"Context P/R/F1: {context.precision:.4f}/{context.recall:.4f}/{context.f1:.4f}")
    print(f"Context state accuracy: {context.state_accuracy:.4f}; false-known: {context.false_known_rate:.4f}")
    print(f"Scope state/value/operator: {scope.scope_state_accuracy:.4f}/{scope.value_accuracy:.4f}/{scope.operator_accuracy:.4f}")
    print(f"Scope constraint/general/unspecified: {scope.explicit_constraint_accuracy:.4f}/{scope.explicit_general_accuracy:.4f}/{scope.unspecified_accuracy:.4f}")
    print("Oracle attribution:")
    print(f"  A oracle/oracle:       {attr.oracle_oracle:.4f}")
    print(f"  B predicted/oracle:    {attr.predicted_context_oracle_scope:.4f}")
    print(f"  C oracle/predicted:    {attr.oracle_context_predicted_scope:.4f}")
    print(f"  D predicted/predicted: {attr.predicted_predicted:.4f}")
    print(f"End-to-end applicability: {report.end_to_end_accuracy:.4f}")
    print(f"Usage: requests={report.usage.get('request_count', 0)} input={report.usage.get('input_tokens', 0)} output={report.usage.get('output_tokens', 0)}")
    print(f"Validation failures: context={len(report.errors['context_validation'])} scope={len(report.errors['scope_validation'])}")
    print("Result: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
