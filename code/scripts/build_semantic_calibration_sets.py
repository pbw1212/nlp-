"""Create the reviewed sparse-contract DEV/HOLDOUT calibration fixtures."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "benchmarks/task2"

CASES = [
    ("friends_hike", "zh", "周末我和几个朋友开车去山里徒步，必须方便停车。", [("PERSON", "travel_party", "KNOWN", ["friends"]), ("SCENARIO", "activity", "KNOWN", ["hiking"]), ("CONSTRAINT", "parking", "KNOWN", ["required"])]),
    ("company_trip", "en", "This is a company-paid business trip to London.", [("TASK", "trip_type", "KNOWN", ["business_trip"]), ("CONSTRAINT", "payment_source", "KNOWN", ["company"]), ("LOCATION", "destination", "KNOWN", ["london"])]),
    ("intent_only", "zh", "帮我找一家酒店。", []),
    ("ambiguous_payment", "mixed", "费用可能 company paid，也可能 self-paid。", [("CONSTRAINT", "payment_source", "AMBIGUOUS", ["company", "self"])]),
    ("unknown_payment", "en", "The payment source is relevant, but I do not know who will pay yet.", [("CONSTRAINT", "payment_source", "UNKNOWN", [])]),
    ("family_pet", "en", "I am travelling with my family and need a pet-friendly hotel.", [("PERSON", "travel_party", "KNOWN", ["family"]), ("CONSTRAINT", "pet", "KNOWN", ["required"])]),
    ("mixed_destination", "mixed", "和 colleagues 去 Shanghai 出差，酒店由公司付款。", [("PERSON", "travel_party", "KNOWN", ["colleagues"]), ("LOCATION", "destination", "KNOWN", ["shanghai"]), ("CONSTRAINT", "payment_source", "KNOWN", ["company"])]),
    ("no_drive", "zh", "这次不开车，不需要停车位。", [("SCENARIO", "transport_mode", "KNOWN", ["not_driving"]), ("CONSTRAINT", "parking", "KNOWN", ["not_required"])]),
    ("solo_budget", "zh", "我独自去上海，预算由自己承担。", [("PERSON", "travel_party", "KNOWN", ["solo"]), ("LOCATION", "destination", "KNOWN", ["shanghai"]), ("CONSTRAINT", "payment_source", "KNOWN", ["self"])]),
    ("weekend_morning", "en", "We need to leave early on the weekend; the exact time is unknown.", [("TIME", "day_type", "KNOWN", ["weekend"]), ("TIME", "departure", "UNKNOWN", [])]),
    ("breakfast_bare", "zh", "我喜欢早餐丰富的酒店。", []),
    ("breakfast_work", "en", "I like rich hotel breakfasts when traveling for work.", [("TASK", "trip_type", "KNOWN", ["business_trip"])]),
    ("breakfast_general", "mixed", "不管去哪，breakfast 都要丰富。", [("LOCATION", "destination", "KNOWN", ["anywhere"])]),
    ("correction", "zh", "原本和朋友去，后来改成我自己去。", [("PERSON", "travel_party", "KNOWN", ["solo"])]),
    ("negation", "en", "No peanuts are allowed, and parking is not required.", [("CONSTRAINT", "dietary_restriction", "KNOWN", ["peanut"]), ("CONSTRAINT", "parking", "KNOWN", ["not_required"])]),
    ("target_not_scope", "zh", "我喜欢安静的房间。", []),
    ("scope_axis", "en", "I prefer quiet rooms when traveling for work.", [("TASK", "trip_type", "KNOWN", ["business_trip"])]),
    ("shopping", "mixed", "周末去买 running shoes，不限品牌。", [("TASK", "shopping_category", "KNOWN", ["running shoes"])]),
    ("schedule", "zh", "工作日早上开会，会议是 online。", [("TIME", "day_type", "KNOWN", ["workday"]), ("TASK", "meeting_mode", "KNOWN", ["online"])]),
    ("missing", "en", "I need a place to stay, but the destination and party are undecided.", [("LOCATION", "destination", "UNKNOWN", []), ("PERSON", "travel_party", "UNKNOWN", [])]),
]

def _runtime(prefix: str):
    contexts, memories, context_gold, scope_gold = [], [], [], []
    for cid, lang, text, values in CASES:
        contexts.append({"case_id": f"{prefix}c_{cid}", "language": lang, "text": text})
        mid = f"{prefix}m_{cid}"
        memories.append({"case_id": mid, "language": lang, "memory_id": mid, "text": text})
        context_gold.append({"case_id": f"{prefix}c_{cid}", "values": [list(v) for v in values]})
        rules = []
        for dimension, field, state, vals in values:
            if state == "KNOWN":
                rules.append([dimension, field, "EXPLICIT_CONSTRAINT", vals, "REQUIRES"])
        scope_gold.append({"case_id": mid, "rules": rules})
    pairs = []
    pair_gold = []
    for i, (cid, *_rest) in enumerate(CASES[:10]):
        pid = f"{prefix}p_{i:02d}"
        pairs.append({"pair_id": pid, "context_case_id": f"{prefix}c_{cid}", "memory_case_id": f"{prefix}m_{cid}"})
        pair_gold.append([pid, "MATCH"])
    return {"schema_version": 2, "contexts": contexts, "memories": memories, "pairs": []}, {"schema_version": 2, "context_gold": context_gold, "scope_gold": scope_gold, "pair_gold": pair_gold}, pairs

def main() -> None:
    for name, prefix in (("semantic_calibration_dev_v1", "d"), ("semantic_calibration_holdout_v1", "h")):
        runtime, gold, pairs = _runtime(prefix)
        runtime["pairs"] = pairs
        target = ROOT / name
        target.mkdir(parents=True, exist_ok=True)
        for filename, data in (("runtime.json", runtime), ("gold.json", gold)):
            (target / filename).write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        (target / "README.md").write_text(
            f"# {name}\n\nSparse-contract calibration fixture; engineering evaluation only. Two-pass internal review; holdout predictions are not inspected during development.\n\nSHA-256: "
            + hashlib.sha256((target / "gold.json").read_bytes()).hexdigest() + " (gold.json)\n",
            encoding="utf-8",
        )

if __name__ == "__main__":
    main()
