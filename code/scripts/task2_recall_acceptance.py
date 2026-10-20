from __future__ import annotations

import argparse
import json
import socket
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, Tuple
from unittest.mock import patch

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.memory.core.schema import (
    ChangeKind,
    Granularity,
    MemoryType,
    ProvenanceAuthority,
    RetentionDisposition,
    SemanticMetadata,
)
from src.memory.retrieval.metrics import mean_recall_at_k, mean_reciprocal_rank
from src.memory.retrieval.recall import MemoryRecallEngine
from src.memory.storage.sqlite_manager import SQLiteMemoryManager


FIXTURE_PATH = Path(__file__).resolve().parents[2] / "benchmarks/task2/recall_engineering_v1.json"


@dataclass(frozen=True)
class Task2RecallCheck:
    name: str
    passed: bool


@dataclass(frozen=True)
class Task2RecallAcceptanceReport:
    passed: bool
    checks: Tuple[Task2RecallCheck, ...]
    recall_at_4: float
    mrr: float

    def to_dict(self) -> Dict[str, object]:
        return {
            "passed": self.passed,
            "checks": [asdict(check) for check in self.checks],
            "recall_at_4": self.recall_at_4,
            "mrr": self.mrr,
        }


def _register_and_create(repository: SQLiteMemoryManager, memory: Dict[str, object]) -> None:
    memory_id = str(memory["memory_id"])
    authority = ProvenanceAuthority(str(memory["authority"]))
    metadata = SemanticMetadata.from_dict(memory.get("metadata"))
    initial_content = memory.get("initial_content")
    if initial_content:
        old_event, _ = repository.register_ingestion_event(
            str(initial_content), event_id=f"{memory_id}-old"
        )
        repository.create_memory(
            memory_id=memory_id,
            content=str(initial_content),
            memory_type=MemoryType.PREFERENCE_RULES,
            granularity=Granularity.SEMANTIC_INSIGHT,
            semantic_metadata=metadata,
            event_ids=(old_event.event_id,),
            authority=authority,
        )
        current_event, _ = repository.register_ingestion_event(
            str(memory["content"]), event_id=f"{memory_id}-current"
        )
        repository.apply_revision(
            memory_id,
            str(memory["content"]),
            change_kind=ChangeKind.UPDATE,
            event_ids=(current_event.event_id,),
            authority=authority,
            semantic_metadata=metadata,
        )
        return
    event, _ = repository.register_ingestion_event(
        str(memory["content"]), event_id=f"{memory_id}-event"
    )
    repository.create_memory(
        memory_id=memory_id,
        content=str(memory["content"]),
        memory_type=MemoryType.PREFERENCE_RULES,
        granularity=Granularity.SEMANTIC_INSIGHT,
        semantic_metadata=metadata,
        event_ids=(event.event_id,),
        authority=authority,
    )


def load_engineering_fixture(repository: SQLiteMemoryManager) -> Dict[str, object]:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    for memory in fixture["memories"]:
        _register_and_create(repository, memory)
    return fixture


def _database_dump(repository: SQLiteMemoryManager) -> Tuple[str, ...]:
    with repository._get_connection() as connection:
        return tuple(connection.iterdump())


def _rankings(engine: MemoryRecallEngine, queries: Iterable[Dict[str, object]]) -> Dict[str, Tuple[str, ...]]:
    return {
        str(query["query_id"]): tuple(
            candidate.snapshot.memory_id
            for candidate in engine.recall(str(query["query"]), top_k=8).candidates
        )
        for query in queries
    }


def run_acceptance() -> Task2RecallAcceptanceReport:
    with tempfile.TemporaryDirectory(prefix="task2a-acceptance-") as temp_dir:
        repository = SQLiteMemoryManager(str(Path(temp_dir) / "recall.db"))
        fixture = load_engineering_fixture(repository)
        engine = MemoryRecallEngine(repository)

        evolution_history = repository.get_history("bench_hotel_evolution")
        corpus = repository.list_current()
        checks = []
        checks.append(Task2RecallCheck(
            "Current-safe corpus",
            len(corpus) == len(fixture["memories"]) and all(s.revision.semantic_validity.value == "CURRENT" for s in corpus),
        ))
        historical_result = engine.recall("我以前更喜欢安静的酒店", top_k=8)
        historical_candidate = next(
            candidate for candidate in historical_result.candidates
            if candidate.snapshot.memory_id == "bench_hotel_evolution"
        )
        checks.append(Task2RecallCheck(
            "Historical revision exclusion",
            historical_candidate.snapshot.revision.revision_id == evolution_history[1].revision_id
            and historical_candidate.snapshot.revision.content != evolution_history[0].content,
        ))

        repository.set_retention("bench_spicy_food", RetentionDisposition.RECYCLED)
        recycled = engine.recall("吃辣饮食偏好", top_k=8)
        checks.append(Task2RecallCheck(
            "Recycle exclusion",
            all(candidate.snapshot.memory_id != "bench_spicy_food" for candidate in recycled.candidates),
        ))
        repository.set_retention("bench_spicy_food", RetentionDisposition.ACTIVE)

        first = engine.recall("旅行酒店住宿", top_k=8)
        second = engine.recall("旅行酒店住宿", top_k=8)
        checks.append(Task2RecallCheck("Deterministic ranking", first == second))
        checks.append(Task2RecallCheck(
            "Zero-score exclusion", not engine.recall("zzzzqwertynotincorpus", top_k=8).candidates
        ))
        metadata = engine.recall("上海", top_k=8)
        shanghai = next(c for c in metadata.candidates if c.snapshot.memory_id == "bench_shanghai_solo")
        checks.append(Task2RecallCheck(
            "Metadata lexical trace",
            shanghai.score_breakdown.metadata_overlap > 0
            and any(field == "locations" for field, _ in shanghai.matched_metadata_fields),
        ))
        explicit = engine.recall("吃辣", top_k=8).candidates[0]
        inferred = engine.recall("花生过敏", top_k=8).candidates[0]
        checks.append(Task2RecallCheck(
            "Provenance preservation",
            explicit.snapshot.provenance[0].authority is ProvenanceAuthority.EXPLICIT_USER
            and inferred.snapshot.provenance[0].authority is ProvenanceAuthority.INFERRED,
        ))

        before = _database_dump(repository)
        for query in fixture["queries"]:
            engine.recall(str(query["query"]), top_k=4)
        after = _database_dump(repository)
        checks.append(Task2RecallCheck("Read-only query", before == after))

        def forbidden_network(*args, **kwargs):
            raise AssertionError("recall baseline 不允许网络访问")

        with patch.object(socket, "create_connection", side_effect=forbidden_network):
            test_result = engine.recall("上海住宿", top_k=4)
        checks.append(Task2RecallCheck("deterministic-test/network guard", bool(test_result.candidates)))

        rankings = _rankings(engine, fixture["queries"])
        expected = {
            str(query["query_id"]): tuple(str(item) for item in query["expected"])
            for query in fixture["queries"]
        }
        recall_4 = mean_recall_at_k(rankings, expected, 4)
        mrr = mean_reciprocal_rank(rankings, expected)
        return Task2RecallAcceptanceReport(
            passed=all(check.passed for check in checks),
            checks=tuple(checks),
            recall_at_4=recall_4,
            mrr=mrr,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Task 2A deterministic recall acceptance")
    parser.add_argument("--json", action="store_true", help="输出 machine-readable JSON")
    args = parser.parse_args()
    report = run_acceptance()
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print("TASK 2A RECALL ACCEPTANCE\n")
        for check in report.checks:
            print(f"{check.name:<31} {'PASS' if check.passed else 'FAIL'}")
        print(f"\nEngineering Recall@4: {report.recall_at_4:.6f}")
        print(f"Engineering MRR:      {report.mrr:.6f}")
        print(f"\nResult:\n{'PASS' if report.passed else 'FAIL'}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
