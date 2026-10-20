from __future__ import annotations

from types import SimpleNamespace

from src.memory.core.schema import Granularity, MemoryType, ProvenanceAuthority, SemanticMetadata
from src.memory.retrieval.applicability import ApplicabilityEngine
from src.memory.retrieval.applicability_models import (
    ApplicabilityContext,
    ApplicabilityDimension,
    CandidateScopeRule,
    CandidateScopeState,
    ConstraintOperator,
    ContextKnowledgeState,
    ContextValue,
    MemoryApplicabilityProfile,
)
from src.memory.retrieval.evidence import (
    ConflictAnnotation,
    ConflictDetector,
    ConflictStatus,
    DeterministicEvidencePipeline,
    EvidencePipelineConfig,
    EvidenceSufficiencyStatus,
)
from src.memory.retrieval.recall import MemoryRecallEngine
from src.memory.storage.sqlite_manager import SQLiteMemoryManager


def _recall(tmp_path, items):
    repository = SQLiteMemoryManager(str(tmp_path / "evidence.db"))
    for memory_id, content, topics in items:
        event, _ = repository.register_ingestion_event(content, event_id=f"event-{memory_id}")
        repository.create_memory(
            memory_id=memory_id,
            content=content,
            memory_type=MemoryType.PREFERENCE_RULES,
            granularity=Granularity.SEMANTIC_INSIGHT,
            semantic_metadata=SemanticMetadata(topics=tuple(topics)),
            event_ids=(event.event_id,),
            authority=ProvenanceAuthority.EXPLICIT_USER,
        )
    return MemoryRecallEngine(repository).recall("旅行", top_k=20)


def _applicability(recall, profiles=()):
    context = ApplicabilityContext(
        (ContextValue(ApplicabilityDimension.TASK, "trip", ContextKnowledgeState.KNOWN, ("travel",)),)
    )
    profile_map = {profile.memory_id: profile for profile in profiles}
    return ApplicabilityEngine().assess_recall_result(recall, context, profile_map)


def test_mismatch_rejected_and_uncertain_retained(tmp_path):
    recall = _recall(tmp_path, [("a", "旅行时喜欢安静酒店", ("hotel",)), ("b", "旅行时公司报销", ("payment",))])
    constrained = MemoryApplicabilityProfile(
        "a",
        (CandidateScopeRule(ApplicabilityDimension.TASK, "trip", CandidateScopeState.EXPLICIT_CONSTRAINT, ("business",)),),
    )
    result = _applicability(recall, (constrained,))
    bundle = DeterministicEvidencePipeline().run_typed(result)
    assert "mismatch:a" in bundle.rejected_or_omitted
    assert any(item.memory_id == "b" for item in bundle.selected)
    assert bundle.sufficiency.status is EvidenceSufficiencyStatus.PARTIAL


def test_conflict_resolution_keeps_loser_trace_and_unresolved_retains_both(tmp_path):
    recall = _recall(tmp_path, [("a", "旅行早班机", ("flight",)), ("b", "旅行下午航班", ("flight",))])
    result = _applicability(recall)
    pipeline = DeterministicEvidencePipeline()
    resolved = pipeline.run_typed(
        result,
        conflicts=(ConflictAnnotation("flight-time", ("a", "b"), winner_memory_id="a"),),
    )
    assert [item.memory_id for item in resolved.selected] == ["a"]
    assert any(item.startswith("conflict_loser:b:") for item in resolved.rejected_or_omitted)
    unresolved = pipeline.run_typed(
        result,
        conflicts=(ConflictAnnotation("flight-time", ("a", "b")),),
    )
    assert {item.memory_id for item in unresolved.selected} == {"a", "b"}
    assert unresolved.sufficiency.status is EvidenceSufficiencyStatus.PARTIAL
    assert "unresolved_conflict:flight-time" in unresolved.warnings


def test_dedup_diversity_and_bounded_distillation_are_deterministic(tmp_path):
    recall = _recall(
        tmp_path,
        [("a", "旅行酒店隔音很好", ("hotel",)), ("b", "旅行酒店早餐丰富", ("food",)), ("c", "旅行交通方便", ("transport",))],
    )
    result = _applicability(recall)
    pipeline = DeterministicEvidencePipeline(EvidencePipelineConfig(max_items=2, max_chars_per_item=5, max_total_chars=8))
    first = pipeline.run_typed(result)
    second = pipeline.run_typed(result)
    assert first.to_json() == second.to_json()
    assert len(first.selected) <= 2
    assert sum(len(item.text) for item in first.selected) <= 8
    assert all(len(item.text) <= 5 for item in first.selected)
    assert all(item.provenance_refs for item in first.selected)
    assert first.to_dict()["budgets"] == {"max_items": 2, "max_chars_per_item": 5, "max_total_chars": 8}
    assert all(item.current_safe for item in first.selected)


def test_empty_input_is_insufficient_and_bundle_is_serializable(tmp_path):
    recall = _recall(tmp_path, [("a", "旅行酒店", ("hotel",))]
    )
    result = _applicability(recall, ())
    # Force all candidates to mismatch with an impossible explicit rule.
    profile = MemoryApplicabilityProfile(
        "a",
        (CandidateScopeRule(ApplicabilityDimension.TASK, "trip", CandidateScopeState.EXPLICIT_CONSTRAINT, ("business",)),),
    )
    empty = _applicability(recall, (profile,))
    bundle = DeterministicEvidencePipeline().run_typed(empty, required_aspects=("hotel",))
    assert bundle.selected == ()
    assert bundle.sufficiency.status is EvidenceSufficiencyStatus.INSUFFICIENT
    assert bundle.to_dict()["schema_version"] == "evidence_bundle_v1"


def test_typed_path_does_not_need_provider(tmp_path):
    recall = _recall(tmp_path, [("a", "旅行酒店", ("hotel",))])
    result = _applicability(recall)
    bundle = DeterministicEvidencePipeline().run_typed(result, request_id="typed")
    assert bundle.request_id == "typed"
    assert bundle.selected[0].memory_id == "a"


def test_detect_clear_cross_memory_conflict_without_annotation(tmp_path):
    recall = _recall(tmp_path, [("a", "旅行公司付款", ("payment",)), ("b", "旅行公司付款", ("payment",))])
    context = ApplicabilityContext(())
    profiles = {
        "a": MemoryApplicabilityProfile(
            "a", (CandidateScopeRule(ApplicabilityDimension.CONSTRAINT, "payment", CandidateScopeState.EXPLICIT_CONSTRAINT, ("company",)),)
        ),
        "b": MemoryApplicabilityProfile(
            "b", (CandidateScopeRule(ApplicabilityDimension.CONSTRAINT, "payment", CandidateScopeState.EXPLICIT_CONSTRAINT, ("company",), operator=ConstraintOperator.EXCLUDES),)
        ),
    }
    result = ApplicabilityEngine().assess_recall_result(recall, context, profiles)
    pipeline = DeterministicEvidencePipeline()
    candidates = tuple(pipeline._candidate_evidence(item, ()) for item in result.assessments)
    detected = ConflictDetector().detect(candidates)
    assert len(detected) == 1
    assert detected[0].winner_memory_id is None
    bundle = pipeline.run_typed(result)
    assert bundle.sufficiency.status is EvidenceSufficiencyStatus.PARTIAL
    assert any("unresolved_conflict:structured:" in warning for warning in bundle.warnings)


def test_no_conflict_for_different_aspects_or_non_overlapping_scope(tmp_path):
    recall = _recall(tmp_path, [("a", "旅行公司付款", ("payment",)), ("b", "旅行公司付款", ("payment",))])
    context = ApplicabilityContext(())
    profiles = {
        "a": MemoryApplicabilityProfile(
            "a", (CandidateScopeRule(ApplicabilityDimension.CONSTRAINT, "payment", CandidateScopeState.EXPLICIT_CONSTRAINT, ("company",)),)
        ),
        "b": MemoryApplicabilityProfile(
            "b", (CandidateScopeRule(ApplicabilityDimension.LOCATION, "destination", CandidateScopeState.EXPLICIT_CONSTRAINT, ("shanghai",)),)
        ),
    }
    result = ApplicabilityEngine().assess_recall_result(recall, context, profiles)
    pipeline = DeterministicEvidencePipeline()
    candidates = tuple(pipeline._candidate_evidence(item, ()) for item in result.assessments)
    assert ConflictDetector().detect(candidates) == ()


def test_no_conflict_when_structured_evidence_is_insufficient(tmp_path):
    recall = _recall(tmp_path, [("a", "旅行偏好", ("travel",)), ("b", "旅行偏好", ("travel",))])
    result = _applicability(recall)
    pipeline = DeterministicEvidencePipeline()
    candidates = tuple(pipeline._candidate_evidence(item, ()) for item in result.assessments)
    assert ConflictDetector().detect(candidates) == ()


def test_natural_language_convenience_entry_terminates_in_shared_bundle(tmp_path):
    recall = _recall(tmp_path, [("a", "旅行酒店", ("hotel",))])
    result = _applicability(recall)

    class FakeSemanticPath:
        def run(self, query, **_kwargs):
            return SimpleNamespace(applicability_result=type(result)(query, result.recall_result, result.assessments, result.warnings))

    bundle = DeterministicEvidencePipeline().run_natural_language(
        FakeSemanticPath(), "帮我找旅行酒店", query_id="nl-test"
    )
    assert bundle.request_id == "nl-test"
    assert bundle.query == "帮我找旅行酒店"
    assert bundle.selected
