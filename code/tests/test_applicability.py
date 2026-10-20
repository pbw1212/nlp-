from __future__ import annotations

import socket

import pytest

from src.memory.core.schema import Granularity, MemoryType, ProvenanceAuthority
from src.memory.retrieval.applicability import ApplicabilityEngine
from src.memory.retrieval.applicability_models import (
    ApplicabilityContext,
    ApplicabilityDecision,
    ApplicabilityDimension,
    ApplicabilityReasonCode,
    ApplicabilityStatus,
    CandidateScopeRule,
    CandidateScopeState,
    ConstraintOperator,
    ContextKnowledgeState,
    ContextValue,
    MemoryApplicabilityProfile,
)
from src.memory.retrieval.recall import MemoryRecallEngine
from src.memory.storage.sqlite_manager import SQLiteMemoryManager


def _candidate(repository, memory_id="memory", content="旅行酒店住宿方便"):
    event, _ = repository.register_ingestion_event(content, event_id=f"event-{memory_id}")
    repository.create_memory(
        memory_id=memory_id,
        content=content,
        memory_type=MemoryType.PREFERENCE_RULES,
        granularity=Granularity.SEMANTIC_INSIGHT,
        event_ids=(event.event_id,),
        authority=ProvenanceAuthority.EXPLICIT_USER,
    )
    return MemoryRecallEngine(repository).recall(content, top_k=10).candidates[0]


@pytest.fixture
def candidate(tmp_path):
    return _candidate(SQLiteMemoryManager(str(tmp_path / "applicability.db")))


def _context(dimension, field, state, values=()):
    return ApplicabilityContext((ContextValue(dimension, field, state, tuple(values)),))


def _profile(candidate, dimension, field, scope, values=(), operator=ConstraintOperator.REQUIRES):
    return MemoryApplicabilityProfile(candidate.snapshot.memory_id, (
        CandidateScopeRule(dimension, field, scope, tuple(values), operator),
    ))


def test_exact_match_and_explicit_mismatch(candidate):
    profile = _profile(candidate, ApplicabilityDimension.LOCATION, "location", CandidateScopeState.EXPLICIT_CONSTRAINT, (" Shanghai ",))
    matched = ApplicabilityEngine().assess(
        candidate,
        _context(ApplicabilityDimension.LOCATION, "location", ContextKnowledgeState.KNOWN, ("ＳＨＡＮＧＨＡＩ",)),
        profile,
    )
    mismatched = ApplicabilityEngine().assess(
        candidate,
        _context(ApplicabilityDimension.LOCATION, "location", ContextKnowledgeState.KNOWN, ("mountain",)),
        profile,
    )
    assert matched.overall is ApplicabilityStatus.MATCH
    assert matched.decision is ApplicabilityDecision.RETAIN
    assert mismatched.overall is ApplicabilityStatus.MISMATCH
    assert mismatched.decision is ApplicabilityDecision.REJECT
    assert mismatched.dimensions[0].reason_code is ApplicabilityReasonCode.EXPLICIT_CONFLICT


def test_unknown_context_and_unspecified_scope_are_preserved(candidate):
    constrained = _profile(candidate, ApplicabilityDimension.CONSTRAINT, "payment", CandidateScopeState.EXPLICIT_CONSTRAINT, ("company",))
    unknown_context = ApplicabilityEngine().assess(
        candidate,
        _context(ApplicabilityDimension.CONSTRAINT, "payment", ContextKnowledgeState.UNKNOWN),
        constrained,
    )
    unspecified = ApplicabilityEngine().assess(
        candidate,
        _context(ApplicabilityDimension.LOCATION, "location", ContextKnowledgeState.KNOWN, ("shanghai",)),
        MemoryApplicabilityProfile(candidate.snapshot.memory_id, ()),
    )
    assert unknown_context.overall is ApplicabilityStatus.UNKNOWN
    assert unknown_context.decision is ApplicabilityDecision.RETAIN_WITH_UNCERTAINTY
    assert unknown_context.dimensions[0].reason_code is ApplicabilityReasonCode.CONTEXT_UNKNOWN
    assert unspecified.overall is ApplicabilityStatus.UNKNOWN
    assert unspecified.dimensions == ()


def test_sparse_profile_ignores_absent_axis_and_explicit_unspecified_is_uncertain(candidate):
    engine = ApplicabilityEngine()
    context = _context(
        ApplicabilityDimension.LOCATION, "location", ContextKnowledgeState.KNOWN, ("shanghai",)
    )
    absent = engine.assess(
        candidate, context,
        MemoryApplicabilityProfile(candidate.snapshot.memory_id, ()),
    )
    unresolved = engine.assess(
        candidate, context,
        _profile(candidate, ApplicabilityDimension.LOCATION, "location", CandidateScopeState.UNSPECIFIED),
    )
    assert absent.dimensions == ()
    assert absent.overall is ApplicabilityStatus.UNKNOWN
    assert unresolved.dimensions[0].status is ApplicabilityStatus.UNKNOWN
    assert unresolved.decision is ApplicabilityDecision.RETAIN_WITH_UNCERTAINTY


def test_ambiguous_context_and_explicit_general(candidate):
    constrained = _profile(candidate, ApplicabilityDimension.TASK, "task", CandidateScopeState.EXPLICIT_CONSTRAINT, ("business",))
    ambiguous = ApplicabilityEngine().assess(
        candidate,
        _context(ApplicabilityDimension.TASK, "task", ContextKnowledgeState.AMBIGUOUS, ("business", "personal")),
        constrained,
    )
    general = ApplicabilityEngine().assess(
        candidate,
        _context(ApplicabilityDimension.LOCATION, "location", ContextKnowledgeState.AMBIGUOUS, ("shanghai", "mountain")),
        _profile(candidate, ApplicabilityDimension.LOCATION, "location", CandidateScopeState.EXPLICIT_GENERAL),
    )
    assert ambiguous.overall is ApplicabilityStatus.AMBIGUOUS
    assert ambiguous.decision is ApplicabilityDecision.RETAIN_WITH_UNCERTAINTY
    assert general.overall is ApplicabilityStatus.MATCH
    assert general.dimensions[0].reason_code is ApplicabilityReasonCode.EXPLICIT_GENERAL_SCOPE


def test_requires_and_excludes_constraints(candidate):
    requires = _profile(candidate, ApplicabilityDimension.CONSTRAINT, "payment", CandidateScopeState.EXPLICIT_CONSTRAINT, ("company",))
    excludes = _profile(
        candidate, ApplicabilityDimension.CONSTRAINT, "ingredient",
        CandidateScopeState.EXPLICIT_CONSTRAINT, ("peanuts",), ConstraintOperator.EXCLUDES,
    )
    engine = ApplicabilityEngine()
    assert engine.assess(candidate, _context(ApplicabilityDimension.CONSTRAINT, "payment", ContextKnowledgeState.KNOWN, ("self",)), requires).overall is ApplicabilityStatus.MISMATCH
    assert engine.assess(candidate, _context(ApplicabilityDimension.CONSTRAINT, "ingredient", ContextKnowledgeState.KNOWN, ("peanuts",)), excludes).overall is ApplicabilityStatus.MISMATCH
    assert engine.assess(candidate, _context(ApplicabilityDimension.CONSTRAINT, "ingredient", ContextKnowledgeState.KNOWN, ("soy",)), excludes).overall is ApplicabilityStatus.MATCH


def test_multi_dimension_aggregation_precedence(candidate):
    profile = MemoryApplicabilityProfile(candidate.snapshot.memory_id, (
        CandidateScopeRule(ApplicabilityDimension.LOCATION, "location", CandidateScopeState.EXPLICIT_CONSTRAINT, ("shanghai",)),
        CandidateScopeRule(ApplicabilityDimension.TASK, "task", CandidateScopeState.EXPLICIT_CONSTRAINT, ("business",)),
        CandidateScopeRule(ApplicabilityDimension.CONSTRAINT, "payment", CandidateScopeState.EXPLICIT_CONSTRAINT, ("company",)),
    ))
    context = ApplicabilityContext((
        ContextValue(ApplicabilityDimension.LOCATION, "location", ContextKnowledgeState.KNOWN, ("mountain",)),
        ContextValue(ApplicabilityDimension.TASK, "task", ContextKnowledgeState.AMBIGUOUS, ("business", "personal")),
        ContextValue(ApplicabilityDimension.CONSTRAINT, "payment", ContextKnowledgeState.UNKNOWN),
    ))
    assessment = ApplicabilityEngine().assess(candidate, context, profile)
    assert [item.status for item in assessment.dimensions] == [
        ApplicabilityStatus.MISMATCH, ApplicabilityStatus.AMBIGUOUS, ApplicabilityStatus.UNKNOWN,
    ]
    assert assessment.overall is ApplicabilityStatus.MISMATCH
    assert assessment.decision is ApplicabilityDecision.REJECT


def test_time_dimension_uses_same_explicit_symbolic_semantics(candidate):
    profile = _profile(
        candidate,
        ApplicabilityDimension.TIME,
        "travel_season",
        CandidateScopeState.EXPLICIT_CONSTRAINT,
        ("winter",),
    )
    assessment = ApplicabilityEngine().assess(
        candidate,
        _context(
            ApplicabilityDimension.TIME,
            "travel_season",
            ContextKnowledgeState.KNOWN,
            ("winter",),
        ),
        profile,
    )
    assert assessment.overall is ApplicabilityStatus.MATCH
    assert assessment.dimensions[0].dimension is ApplicabilityDimension.TIME


def test_original_rank_score_and_provenance_are_unchanged(tmp_path):
    repository = SQLiteMemoryManager(str(tmp_path / "integration.db"))
    candidate = _candidate(repository)
    before_score = candidate.recall_score
    before_rank = candidate.rank
    before_provenance = candidate.snapshot.provenance
    result = ApplicabilityEngine().assess(
        candidate,
        _context(ApplicabilityDimension.LOCATION, "location", ContextKnowledgeState.KNOWN, ("mountain",)),
        _profile(candidate, ApplicabilityDimension.LOCATION, "location", CandidateScopeState.EXPLICIT_CONSTRAINT, ("shanghai",)),
    )
    assert result.candidate is candidate
    assert result.candidate.recall_score == before_score
    assert result.candidate.rank == before_rank
    assert result.candidate.snapshot.provenance == before_provenance
    assert any(ref.startswith("revision:") for ref in result.dimensions[0].evidence_refs)
    assert any(ref.startswith("provenance:") for ref in result.dimensions[0].evidence_refs)


def test_batch_is_read_only_deterministic_and_order_preserving(tmp_path, monkeypatch):
    repository = SQLiteMemoryManager(str(tmp_path / "batch.db"))
    first = _candidate(repository, "memory-a", "旅行住宿方便")
    _candidate(repository, "memory-b", "旅行酒店方便")
    recall = MemoryRecallEngine(repository).recall("旅行住宿方便", top_k=10)
    profiles = {item.snapshot.memory_id: MemoryApplicabilityProfile(item.snapshot.memory_id, ()) for item in recall.candidates}
    context = _context(ApplicabilityDimension.LOCATION, "location", ContextKnowledgeState.KNOWN, ("shanghai",))
    with repository._get_connection() as connection:
        before = tuple(connection.iterdump())

    def forbidden_network(*args, **kwargs):
        raise AssertionError("network attempted")

    monkeypatch.setattr(socket, "create_connection", forbidden_network)
    result = ApplicabilityEngine().assess_recall_result(recall, context, profiles)
    with repository._get_connection() as connection:
        after = tuple(connection.iterdump())
    assert before == after
    assert result.recall_result is recall
    assert [item.candidate.rank for item in result.assessments] == [item.rank for item in recall.candidates]
    assert all(item.decision is ApplicabilityDecision.RETAIN_WITH_UNCERTAINTY for item in result.assessments)


def test_typed_loaders_reject_unknown_fields_and_normalize_order():
    context = ApplicabilityContext.from_dict({"values": [
        {"dimension": "TASK", "field": " task ", "state": "KNOWN", "values": ["Ｂ", "a"]},
        {"dimension": "LOCATION", "field": "location", "state": "KNOWN", "values": ["Shanghai"]},
    ]})
    assert [item.dimension for item in context.values] == [ApplicabilityDimension.LOCATION, ApplicabilityDimension.TASK]
    assert context.values[1].values == ("a", "b")
    with pytest.raises(ValueError, match="未知字段"):
        ApplicabilityContext.from_dict({"values": [], "query": "not allowed"})

