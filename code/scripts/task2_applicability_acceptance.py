from __future__ import annotations

import argparse
import json
import socket
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Mapping, Tuple
from unittest.mock import patch

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.memory.core.schema import Granularity, MemoryType, ProvenanceAuthority
from src.memory.retrieval.applicability import ApplicabilityEngine
from src.memory.retrieval.applicability_metrics import (
    applicability_accuracy,
    mismatch_contamination_after,
    mismatch_contamination_before,
    mismatch_rejection_accuracy,
    unknown_preservation_accuracy,
)
from src.memory.retrieval.applicability_models import (
    ApplicabilityContext,
    ApplicabilityDecision,
    ApplicabilityStatus,
    MemoryApplicabilityProfile,
)
from src.memory.retrieval.recall import MemoryRecallEngine
from src.memory.storage.sqlite_manager import SQLiteMemoryManager


ROOT = Path(__file__).resolve().parents[2]
PROFILE_FIXTURE = ROOT / "benchmarks/task2/applicability_profiles_v1.json"
CONTEXT_FIXTURE = ROOT / "benchmarks/task2/applicability_contexts_v1.json"

MEMORIES = {
    "app_solo_shanghai": "独自去上海旅行时，住宿喜欢住市中心，出行方便。",
    "app_friends_hiking": "和朋友去山里徒步旅行时，住宿看重停车方便。",
    "app_business_hotel": "公司报销的商务出差，可以接受价格更高且位置方便的酒店。",
    "app_general_transport": "不管去哪旅行，住宿通常优先考虑交通方便。",
    "app_breakfast": "旅行住酒店时，我喜欢早餐丰富的住宿。",
    "app_no_peanuts": "旅行用餐时需要避免花生食材。",
}


@dataclass(frozen=True)
class ApplicabilityAcceptanceCheck:
    name: str
    passed: bool


@dataclass(frozen=True)
class RecallApplicabilityTrace:
    memory_id: str
    original_rank: int
    original_recall_score: float
    applicability: str
    decision: str


@dataclass(frozen=True)
class Task2ApplicabilityAcceptanceReport:
    passed: bool
    checks: Tuple[ApplicabilityAcceptanceCheck, ...]
    applicability_accuracy: float
    mismatch_rejection_accuracy: float
    unknown_preservation_accuracy: float
    contamination_before: float
    contamination_after: float
    recall_trace: Tuple[RecallApplicabilityTrace, ...]

    def to_dict(self) -> Dict[str, object]:
        return {
            "passed": self.passed,
            "checks": [asdict(check) for check in self.checks],
            "applicability_accuracy": self.applicability_accuracy,
            "mismatch_rejection_accuracy": self.mismatch_rejection_accuracy,
            "unknown_preservation_accuracy": self.unknown_preservation_accuracy,
            "contamination_before": self.contamination_before,
            "contamination_after": self.contamination_after,
            "recall_trace": [asdict(trace) for trace in self.recall_trace],
        }


def _load_profiles() -> Dict[str, MemoryApplicabilityProfile]:
    payload = json.loads(PROFILE_FIXTURE.read_text(encoding="utf-8"))
    if set(payload) != {"label", "profiles"} or not isinstance(payload["profiles"], list):
        raise ValueError("invalid applicability profile fixture")
    profiles = tuple(MemoryApplicabilityProfile.from_dict(item) for item in payload["profiles"])
    return {profile.memory_id: profile for profile in profiles}


def _load_contexts() -> Tuple[Mapping[str, object], ...]:
    payload = json.loads(CONTEXT_FIXTURE.read_text(encoding="utf-8"))
    if set(payload) != {"label", "contexts"} or not isinstance(payload["contexts"], list):
        raise ValueError("invalid applicability context fixture")
    required = {"scenario_id", "memory_id", "expected", "values"}
    for item in payload["contexts"]:
        if not isinstance(item, dict) or set(item) != required:
            raise ValueError("invalid context scenario shape")
    return tuple(payload["contexts"])


def _populate(repository: SQLiteMemoryManager) -> None:
    for memory_id, content in MEMORIES.items():
        event, _ = repository.register_ingestion_event(content, event_id=f"event-{memory_id}")
        repository.create_memory(
            memory_id=memory_id,
            content=content,
            memory_type=MemoryType.PREFERENCE_RULES,
            granularity=Granularity.SEMANTIC_INSIGHT,
            event_ids=(event.event_id,),
            authority=ProvenanceAuthority.EXPLICIT_USER,
        )


def _dump(repository: SQLiteMemoryManager) -> Tuple[str, ...]:
    with repository._get_connection() as connection:
        return tuple(connection.iterdump())


def run_acceptance() -> Task2ApplicabilityAcceptanceReport:
    with tempfile.TemporaryDirectory(prefix="task2b-acceptance-") as temp_dir:
        repository = SQLiteMemoryManager(str(Path(temp_dir) / "applicability.db"))
        _populate(repository)
        profiles = _load_profiles()
        contexts = _load_contexts()
        recall_engine = MemoryRecallEngine(repository)
        applicability_engine = ApplicabilityEngine()

        candidates = {}
        for memory_id, content in MEMORIES.items():
            candidates[memory_id] = next(
                candidate
                for candidate in recall_engine.recall(content, top_k=len(MEMORIES)).candidates
                if candidate.snapshot.memory_id == memory_id
            )

        scenario_assessments = {}
        scenario_passes = {}
        for scenario in contexts:
            context = ApplicabilityContext.from_dict({"values": scenario["values"]})
            memory_id = str(scenario["memory_id"])
            assessment = applicability_engine.assess(
                candidates[memory_id], context, profiles[memory_id]
            )
            expected = ApplicabilityStatus(str(scenario["expected"]))
            scenario_id = str(scenario["scenario_id"])
            scenario_assessments[scenario_id] = assessment
            scenario_passes[scenario_id] = assessment.overall is expected

        checks = [
            ApplicabilityAcceptanceCheck("Exact scoped match", scenario_passes["A_solo_shanghai_match"]),
            ApplicabilityAcceptanceCheck("Explicit mismatch rejection", scenario_passes["B_friends_hiking_mismatch"] and scenario_assessments["B_friends_hiking_mismatch"].decision is ApplicabilityDecision.REJECT),
            ApplicabilityAcceptanceCheck("Company-paid business match", scenario_passes["C_company_business_match"]),
            ApplicabilityAcceptanceCheck("Unknown context preservation", scenario_passes["E_payment_unknown"] and scenario_assessments["E_payment_unknown"].decision is ApplicabilityDecision.RETAIN_WITH_UNCERTAINTY),
            ApplicabilityAcceptanceCheck("Unspecified scope != match", scenario_passes["G_unspecified_scope"]),
            ApplicabilityAcceptanceCheck("Explicit general scope", scenario_passes["F_explicit_general"]),
            ApplicabilityAcceptanceCheck("Ambiguous context preservation", scenario_passes["H_ambiguous_task"] and scenario_assessments["H_ambiguous_task"].decision is ApplicabilityDecision.RETAIN_WITH_UNCERTAINTY),
            ApplicabilityAcceptanceCheck("Constraint mismatch", scenario_passes["D_self_paid_mismatch"] and scenario_passes["I_excluded_constraint"]),
        ]

        integration_recall = recall_engine.recall(
            "朋友去山里徒步旅行，酒店住宿要方便并有早餐", top_k=4
        )
        integration_context = ApplicabilityContext.from_dict({"values": [
            {"dimension": "PERSON", "field": "travel_party", "state": "KNOWN", "values": ["friends"]},
            {"dimension": "LOCATION", "field": "location", "state": "KNOWN", "values": ["mountain"]},
            {"dimension": "SCENARIO", "field": "trip_scenario", "state": "KNOWN", "values": ["hiking_trip"]},
        ]})
        integration = applicability_engine.assess_recall_result(
            integration_recall, integration_context, profiles
        )
        integration_expected = {
            "app_friends_hiking": ApplicabilityStatus.MATCH,
            "app_solo_shanghai": ApplicabilityStatus.MISMATCH,
            "app_breakfast": ApplicabilityStatus.UNKNOWN,
            "app_general_transport": ApplicabilityStatus.MATCH,
        }
        actual_ids = {assessment.candidate.snapshot.memory_id for assessment in integration.assessments}
        if actual_ids != set(integration_expected):
            raise AssertionError(f"integration fixture ranking drift: {sorted(actual_ids)}")
        before_scores = {
            candidate.snapshot.memory_id: (candidate.rank, candidate.recall_score)
            for candidate in integration_recall.candidates
        }
        checks.append(ApplicabilityAcceptanceCheck(
            "Recall score unchanged",
            all(
                before_scores[assessment.candidate.snapshot.memory_id]
                == (assessment.candidate.rank, assessment.candidate.recall_score)
                for assessment in integration.assessments
            ),
        ))

        before_db = _dump(repository)
        with patch.object(socket, "create_connection", side_effect=AssertionError("network attempted")):
            repeated = applicability_engine.assess_recall_result(
                integration_recall, integration_context, profiles
            )
        after_db = _dump(repository)
        checks.append(ApplicabilityAcceptanceCheck("Read-only", before_db == after_db))
        checks.append(ApplicabilityAcceptanceCheck("deterministic test/network guard", repeated == integration))

        accuracy = applicability_accuracy(integration.assessments, integration_expected)
        rejection = mismatch_rejection_accuracy(integration.assessments, integration_expected)
        unknown = unknown_preservation_accuracy(integration.assessments, integration_expected)
        contamination_before = mismatch_contamination_before(integration_expected)
        contamination_after = mismatch_contamination_after(integration.assessments, integration_expected)
        checks.append(ApplicabilityAcceptanceCheck(
            "Contamination decreases",
            contamination_before > 0 and contamination_after < contamination_before,
        ))
        trace = tuple(
            RecallApplicabilityTrace(
                memory_id=assessment.candidate.snapshot.memory_id,
                original_rank=assessment.candidate.rank,
                original_recall_score=round(assessment.candidate.recall_score, 6),
                applicability=assessment.overall.value,
                decision=assessment.decision.value,
            )
            for assessment in integration.assessments
        )
        return Task2ApplicabilityAcceptanceReport(
            passed=all(check.passed for check in checks)
            and all(scenario_passes.values())
            and accuracy == rejection == unknown == 1.0,
            checks=tuple(checks),
            applicability_accuracy=accuracy,
            mismatch_rejection_accuracy=rejection,
            unknown_preservation_accuracy=unknown,
            contamination_before=contamination_before,
            contamination_after=contamination_after,
            recall_trace=trace,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Task 2B deterministic applicability acceptance")
    parser.add_argument("--json", action="store_true", help="输出 machine-readable JSON")
    args = parser.parse_args()
    report = run_acceptance()
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print("TASK 2B APPLICABILITY ACCEPTANCE\n")
        for check in report.checks:
            print(f"{check.name:<34} {'PASS' if check.passed else 'FAIL'}")
        print(f"\nApplicability accuracy:              {report.applicability_accuracy:.6f}")
        print(f"Mismatch rejection accuracy:         {report.mismatch_rejection_accuracy:.6f}")
        print(f"Unknown preservation accuracy:       {report.unknown_preservation_accuracy:.6f}")
        print(f"Recall-only mismatch contamination:  {report.contamination_before:.6f}")
        print(f"After applicability:                 {report.contamination_after:.6f}")
        print("\nRecall → applicability trace:")
        for trace in report.recall_trace:
            print(
                f"  rank={trace.original_rank} score={trace.original_recall_score:.6f} "
                f"memory={trace.memory_id} → {trace.applicability}/{trace.decision}"
            )
        print(f"\nResult:\n{'PASS' if report.passed else 'FAIL'}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
