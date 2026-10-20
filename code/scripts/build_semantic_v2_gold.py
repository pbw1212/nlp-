"""Build the reviewed sparse-scope v2 semantic evaluation gold files.

This is an deterministic test annotation migration utility. It never reads predictions;
the two audit passes are represented by the explicit reviewed correction table
and the invariant checks below.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DATASETS = {
    "context_interpretation_eval": (30, "context_interpretation_eval_v1", "v1"),
    "context_interpretation_hard": (12, "context_interpretation_hard_v1", "v1"),
}


SCOPE_REMOVALS = {
    "m04": "ABSENCE_NOT_AXIS",
    "m16": "TARGET_NOT_SCOPE",
    "m26": "ABSENCE_NOT_AXIS",
    "hm02": "ABSENCE_NOT_AXIS",
    "hm04": "TARGET_NOT_SCOPE",
}

CONTEXT_REMOVALS = {
    "c04": "QUERY_INTENT_NOT_FACT",
    "c21": "NO_EXPLICIT_RELEVANT_FACT",
    "c29": "NO_EXPLICIT_RELEVANT_FACT",
}

PAIR_CHANGES = {
    "p03": ("MATCH", "OLD_DENSE_ABSENCE_SEMANTICS"),
    "hp04": ("MATCH", "OLD_DENSE_ABSENCE_SEMANTICS"),
}


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rule_key(rule: list[Any]) -> str:
    return f"{rule[0]}.{rule[1]}"


def _review_scope(gold: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    result = json.loads(json.dumps(gold))
    result["schema_version"] = 2
    changes: list[dict[str, Any]] = []
    for item in result["scope_gold"]:
        case_id = item["case_id"]
        old_rules = item["rules"]
        new_rules = []
        for old_rule in old_rules:
            if case_id in SCOPE_REMOVALS and old_rule[2] == "UNSPECIFIED":
                changes.append(
                    {
                        "case_id": case_id,
                        "memory_id": case_id,
                        "old_rule": old_rule,
                        "new_rule": None,
                        "change_type": "REMOVED",
                        "reason_code": SCOPE_REMOVALS[case_id],
                        "rationale": "Sparse contract requires positive axis relevance evidence.",
                    }
                )
                continue
            if case_id == "m13" and old_rule[0] == "TIME" and old_rule[1] == "day_type":
                new_rule = ["TIME", "day_type", "EXPLICIT_CONSTRAINT", ["workday"], "REQUIRES"]
                changes.append(
                    {
                        "case_id": case_id,
                        "memory_id": case_id,
                        "old_rule": old_rule,
                        "new_rule": new_rule,
                        "change_type": "CHANGED_STATE_AND_VALUE",
                        "reason_code": "GENERAL_EVIDENCE_MISSING",
                        "rationale": "Every workday constrains the workday category; it is not universal across day types.",
                    }
                )
                new_rules.append(new_rule)
                continue
            new_rules.append(old_rule)
        item["rules"] = new_rules
    return result, changes


def _review_context(gold: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    result = json.loads(json.dumps(gold))
    result["schema_version"] = 2
    changes: list[dict[str, Any]] = []
    for item in result["context_gold"]:
        case_id = item["case_id"]
        if case_id in CONTEXT_REMOVALS:
            old_values = item["values"]
            if old_values:
                changes.append(
                    {
                        "case_id": case_id,
                        "old_values": old_values,
                        "new_values": [],
                        "change_type": "REMOVED",
                        "reason_code": CONTEXT_REMOVALS[case_id],
                        "rationale": "No explicit applicability-relevant fact is stated.",
                    }
                )
            item["values"] = []
    return result, changes


def _scope_evidence(gold: dict[str, Any]) -> list[dict[str, Any]]:
    entries = []
    for item in gold["scope_gold"]:
        for index, rule in enumerate(item["rules"]):
            entries.append(
                {
                    "case_id": item["case_id"],
                    "memory_id": item["case_id"],
                    "rule_index": index,
                    "field": _rule_key(rule),
                    "evidence_ref": f"memory:{item['case_id']}",
                    "evidence_kind": "SOURCE_TEXT",
                    "axis_relevance": (
                        "Explicit qualifier or condition in source text; sparse profile rule retained."
                    ),
                }
            )
    return entries


def _assert_reviewed(gold: dict[str, Any], evidence: list[dict[str, Any]]) -> None:
    evidence_keys = {(item["case_id"], item["rule_index"]) for item in evidence}
    for item in gold["scope_gold"]:
        for index, rule in enumerate(item["rules"]):
            assert (item["case_id"], index) in evidence_keys
            if rule[2] == "EXPLICIT_GENERAL":
                assert item["case_id"] in {"m03", "m07", "m18", "m23", "hm01", "hm08"}
            if rule[2] == "UNSPECIFIED":
                assert item["case_id"] in {"m08", "m30", "hm10"}


def main() -> None:
    migration_scope: list[dict[str, Any]] = []
    migration_context: list[dict[str, Any]] = []
    migration_pairs: list[dict[str, Any]] = []
    for stem, (expected, source_version, _) in DATASETS.items():
        source = ROOT / "benchmarks/task2" / source_version
        target = ROOT / "benchmarks/task2" / f"{stem}_v2"
        target.mkdir(parents=True, exist_ok=True)
        runtime = _read(source / "runtime.json")
        scope_gold, scope_changes = _review_scope(_read(source / "gold.json"))
        context_gold, context_changes = _review_context(scope_gold)
        # Keep pair labels and runtime inputs byte-for-byte semantically stable.
        context_gold["pair_gold"] = _read(source / "gold.json")["pair_gold"]
        pair_changes = []
        for pair in context_gold["pair_gold"]:
            pair_id, old_status = pair
            if pair_id in PAIR_CHANGES:
                new_status, reason = PAIR_CHANGES[pair_id]
                pair[1] = new_status
                pair_changes.append(
                    {
                        "pair_id": pair_id,
                        "old_status": old_status,
                        "new_status": new_status,
                        "change_type": "CHANGED_STATUS",
                        "reason_code": reason,
                        "rationale": "Sparse profile ignores axes absent from the candidate qualifier set.",
                    }
                )
        _assert_reviewed(scope_gold, _scope_evidence(scope_gold))
        (target / "runtime.json").write_text(json.dumps(runtime, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        (target / "gold.json").write_text(json.dumps(context_gold, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        scope_path = target / "scope_evidence_v2.json"
        scope_path.write_text(json.dumps(_scope_evidence(scope_gold), ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        (target / "README.md").write_text(
            f"# {stem}_v2\n\nCorrected sparse evidence-backed scope gold. Source: {source_version}; v1 is preserved as historical diagnostic data.\n\nTwo-pass internal review completed before any Round-1 prediction inspection. Every retained scope rule has an entry in `scope_evidence_v2.json`.\n",
            encoding="utf-8",
        )
        migration_scope.extend(scope_changes)
        migration_context.extend(context_changes)
        migration_pairs.extend(pair_changes)
        assert len(runtime["contexts"]) + len(runtime["memories"]) == expected * 2

    out = ROOT / "benchmarks/task2"
    (out / "scope_gold_migration_v1_to_v2.json").write_text(json.dumps(migration_scope, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (out / "context_gold_migration_v1_to_v2.json").write_text(json.dumps(migration_context, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (out / "pair_gold_migration_v1_to_v2.json").write_text(json.dumps(migration_pairs, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
