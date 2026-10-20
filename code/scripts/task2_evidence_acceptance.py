"""deterministic test acceptance for the Phase 2G derived evidence pipeline."""

from __future__ import annotations

import tempfile
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.memory.core.schema import Granularity, MemoryType, ProvenanceAuthority
from src.memory.retrieval.applicability import ApplicabilityEngine
from src.memory.retrieval.applicability_models import (
    ApplicabilityContext,
    ApplicabilityDimension,
    CandidateScopeRule,
    CandidateScopeState,
    ConstraintOperator,
    MemoryApplicabilityProfile,
)
from src.memory.retrieval.evidence import (
    ConflictAnnotation,
    DeterministicEvidencePipeline,
    EvidenceSufficiencyStatus,
)
from src.memory.retrieval.recall import MemoryRecallEngine
from src.memory.storage.sqlite_manager import SQLiteMemoryManager


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="task2-evidence-") as directory:
        repository = SQLiteMemoryManager(str(Path(directory) / "acceptance.db"))
        for memory_id, content in (("evidence-a", "旅行时喜欢安静酒店"), ("evidence-b", "旅行时偏好早班机")):
            event, _ = repository.register_ingestion_event(content, event_id=f"event-{memory_id}")
            repository.create_memory(
                memory_id=memory_id,
                content=content,
                memory_type=MemoryType.PREFERENCE_RULES,
                granularity=Granularity.SEMANTIC_INSIGHT,
                event_ids=(event.event_id,),
                authority=ProvenanceAuthority.EXPLICIT_USER,
            )
        recall = MemoryRecallEngine(repository).recall("旅行", top_k=8)
        profiles = {
            "evidence-a": MemoryApplicabilityProfile(
                "evidence-a",
                (CandidateScopeRule(ApplicabilityDimension.CONSTRAINT, "payment", CandidateScopeState.EXPLICIT_CONSTRAINT, ("company",)),),
            ),
            "evidence-b": MemoryApplicabilityProfile(
                "evidence-b",
                (CandidateScopeRule(ApplicabilityDimension.CONSTRAINT, "payment", CandidateScopeState.EXPLICIT_CONSTRAINT, ("company",), operator=ConstraintOperator.EXCLUDES),),
            ),
        }
        applicability = ApplicabilityEngine().assess_recall_result(
            recall, ApplicabilityContext(()), profiles
        )
        pipeline = DeterministicEvidencePipeline()
        automatic = pipeline.run_typed(applicability, request_id="automatic-conflict")
        assert any("unresolved_conflict:structured:" in warning for warning in automatic.warnings)
        resolved = pipeline.run_typed(
            applicability,
            request_id="acceptance",
            conflicts=(ConflictAnnotation("travel-preference", ("evidence-a", "evidence-b"), winner_memory_id="evidence-a"),),
        )
        assert resolved.selected
        assert resolved.sufficiency.status is EvidenceSufficiencyStatus.PARTIAL
        assert all(item.provenance_refs for item in resolved.selected)
        assert resolved.to_json()
        natural = pipeline.run_natural_language(
            SimpleNamespace(run=lambda query, **_kwargs: SimpleNamespace(applicability_result=applicability)),
            "旅行时怎么付款",
            query_id="natural-acceptance",
        )
        assert natural.request_id == "natural-acceptance"
        print("Automatic conflict baseline: PASS")
        print("Natural-language → EvidenceBundle contract: PASS")
    print("Task 2G evidence acceptance: PASS (deterministic test, typed input, no provider)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

