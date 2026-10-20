"""Phase 2G request-local evidence pipeline.

This module deliberately sits after recall and applicability.  Every object is
derived, bounded, and rebuildable; nothing here is a second canonical memory
store and no method writes to Task 1.
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple

from src.memory.retrieval.applicability_models import (
    ApplicabilityAssessment,
    ApplicabilityDecision,
    ApplicabilityResult,
    ApplicabilityStatus,
)
from src.memory.retrieval.semantic_pipeline import SemanticRetrievalPipeline


class ConflictStatus(str, Enum):
    NO_CONFLICT = "NO_CONFLICT"
    RESOLVED = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"


class EvidenceSufficiencyStatus(str, Enum):
    SUFFICIENT = "SUFFICIENT"
    PARTIAL = "PARTIAL"
    INSUFFICIENT = "INSUFFICIENT"


@dataclass(frozen=True)
class ConflictAnnotation:
    """A bounded, caller-supplied conflict hint for the deterministic baseline.

    A future semantic resolver may produce the same DTO.  Keeping the hint
    explicit avoids inventing conflicts from lexical similarity alone.
    """

    conflict_key: str
    memory_ids: Tuple[str, ...]
    winner_memory_id: Optional[str] = None
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.conflict_key or len(self.memory_ids) < 2:
            raise ValueError("conflict annotation requires a key and two memories")
        if len(set(self.memory_ids)) != len(self.memory_ids):
            raise ValueError("conflict memory_ids must be unique")
        if self.winner_memory_id is not None and self.winner_memory_id not in self.memory_ids:
            raise ValueError("conflict winner must belong to memory_ids")


@dataclass(frozen=True)
class ConflictAssessment:
    status: ConflictStatus
    conflict_key: Optional[str] = None
    reason: str = ""
    losing_memory_ids: Tuple[str, ...] = ()
    is_loser: bool = False


@dataclass(frozen=True)
class ScopeRuleEvidence:
    """Minimal structured scope evidence used by the conservative detector."""

    dimension: str
    field: str
    scope_state: str
    values: Tuple[str, ...]
    operator: str
    evidence_refs: Tuple[str, ...]


@dataclass(frozen=True)
class CandidateEvidence:
    """Derived evidence for one current-safe recall candidate."""

    memory_id: str
    revision_id: str
    content: str
    original_rank: int
    original_score: float
    score_breakdown: Mapping[str, Optional[float]]
    applicability_status: ApplicabilityStatus
    applicability_decision: ApplicabilityDecision
    applicability_reasons: Tuple[str, ...]
    provenance_refs: Tuple[str, ...]
    aspect_keys: Tuple[str, ...]
    conflict: ConflictAssessment
    scope_rules: Tuple[ScopeRuleEvidence, ...]
    semantic_validity: str
    retention_disposition: str
    current_safe: bool = True
    selection_trace: Tuple[str, ...] = ()

    @property
    def uncertain(self) -> bool:
        return (
            self.applicability_status in {ApplicabilityStatus.UNKNOWN, ApplicabilityStatus.AMBIGUOUS}
            or self.conflict.status is ConflictStatus.UNRESOLVED
        )


@dataclass(frozen=True)
class EvidenceSufficiency:
    status: EvidenceSufficiencyStatus
    reason_codes: Tuple[str, ...] = ()
    missing_aspects: Tuple[str, ...] = ()
    uncertainties: Tuple[str, ...] = ()
    unresolved_conflicts: Tuple[str, ...] = ()


@dataclass(frozen=True)
class DistilledEvidence:
    memory_id: str
    revision_id: str
    text: str
    original_rank: int
    original_score: float
    score_breakdown: Mapping[str, Optional[float]]
    provenance_refs: Tuple[str, ...]
    applicability_status: ApplicabilityStatus
    applicability_decision: ApplicabilityDecision
    applicability_reasons: Tuple[str, ...]
    conflict_status: ConflictStatus
    conflict_key: Optional[str]
    conflict_losing_memory_ids: Tuple[str, ...]
    source_trace: Tuple[str, ...]
    aspect_keys: Tuple[str, ...] = ()
    current_safe: bool = True


@dataclass(frozen=True)
class EvidenceBundle:
    schema_version: str
    request_id: str
    query: str
    selected: Tuple[DistilledEvidence, ...]
    sufficiency: EvidenceSufficiency
    rejected_or_omitted: Tuple[str, ...] = ()
    warnings: Tuple[str, ...] = ()
    pipeline_identity: Tuple[str, ...] = ("task2_evidence_pipeline_v1",)
    retrieval_trace: Mapping[str, Any] = field(default_factory=dict)
    max_items: int = 8
    max_chars_per_item: int = 600
    max_total_chars: int = 3000

    def to_dict(self) -> dict:
        """Stable JSON-compatible representation for callers and tests."""
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "query": self.query,
            "selected": [
                {
                    "memory_id": item.memory_id,
                    "revision_id": item.revision_id,
                    "text": item.text,
                    "original_rank": item.original_rank,
                    "original_score": item.original_score,
                    "score_breakdown": dict(item.score_breakdown),
                    "provenance_refs": list(item.provenance_refs),
                    "applicability_status": item.applicability_status.value,
                    "applicability_decision": item.applicability_decision.value,
                    "applicability_reasons": list(item.applicability_reasons),
                    "conflict_status": item.conflict_status.value,
                    "conflict_key": item.conflict_key,
                    "conflict_losing_memory_ids": list(item.conflict_losing_memory_ids),
                    "source_trace": list(item.source_trace),
                    "current_safe": item.current_safe,
                }
                for item in self.selected
            ],
            "sufficiency": {
                "status": self.sufficiency.status.value,
                "reason_codes": list(self.sufficiency.reason_codes),
                "missing_aspects": list(self.sufficiency.missing_aspects),
                "uncertainties": list(self.sufficiency.uncertainties),
                "unresolved_conflicts": list(self.sufficiency.unresolved_conflicts),
            },
            "rejected_or_omitted": list(self.rejected_or_omitted),
            "warnings": list(self.warnings),
            "pipeline_identity": list(self.pipeline_identity),
            "retrieval_trace": dict(self.retrieval_trace),
            "budgets": {
                "max_items": self.max_items,
                "max_chars_per_item": self.max_chars_per_item,
                "max_total_chars": self.max_total_chars,
            },
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True)
class EvidencePipelineConfig:
    max_items: int = 8
    max_chars_per_item: int = 600
    max_total_chars: int = 3000
    schema_version: str = "evidence_bundle_v1"

    def __post_init__(self) -> None:
        if self.max_items <= 0 or self.max_chars_per_item <= 0 or self.max_total_chars <= 0:
            raise ValueError("evidence budgets must be positive")


def _normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _aspects(assessment: ApplicabilityAssessment) -> Tuple[str, ...]:
    metadata = assessment.candidate.snapshot.revision.semantic_metadata
    values = (*metadata.topics, *metadata.scopes, *metadata.conditions, *metadata.keywords)
    normalized = tuple(dict.fromkeys(_normalize_text(value) for value in values if value.strip()))
    return normalized or ("__unclassified__",)


def _conflict_for(memory_id: str, annotations: Sequence[ConflictAnnotation]) -> ConflictAssessment:
    for annotation in annotations:
        if memory_id not in annotation.memory_ids:
            continue
        if annotation.winner_memory_id is None:
            return ConflictAssessment(
                ConflictStatus.UNRESOLVED,
                annotation.conflict_key,
                annotation.reason,
                tuple(item for item in annotation.memory_ids if item != memory_id),
            )
        if memory_id == annotation.winner_memory_id:
            return ConflictAssessment(
                ConflictStatus.RESOLVED,
                annotation.conflict_key,
                annotation.reason,
                tuple(item for item in annotation.memory_ids if item != memory_id),
            )
        return ConflictAssessment(
            ConflictStatus.RESOLVED,
            annotation.conflict_key,
            annotation.reason,
            (annotation.winner_memory_id,),
            True,
        )
    return ConflictAssessment(ConflictStatus.NO_CONFLICT)


class ConflictDetector:
    """Precision-first detection of explicit structured contradictions.

    The detector intentionally does not infer natural-language opposition from
    text similarity.  It only flags two current memories that assert the same
    exact scope value with opposite structured operators (REQUIRES vs
    EXCLUDES).  Anything less explicit remains unclassified and can still be
    supplied through ``ConflictAnnotation`` when an upstream component has
    stronger evidence.
    """

    def detect(self, candidates: Sequence[CandidateEvidence]) -> Tuple[ConflictAnnotation, ...]:
        annotations: list[ConflictAnnotation] = []
        eligible = [
            item for item in candidates
            if item.current_safe and item.applicability_decision is not ApplicabilityDecision.REJECT
        ]
        for index, left in enumerate(eligible):
            for right in eligible[index + 1 :]:
                if self._opposite_rule(left, right):
                    key = self._conflict_key(left, right)
                    annotations.append(
                        ConflictAnnotation(
                            conflict_key=key,
                            memory_ids=(left.memory_id, right.memory_id),
                            reason=(
                                "same structured applicability axis/value has "
                                "opposite REQUIRES/EXCLUDES operators"
                            ),
                        )
                    )
        return tuple(annotations)

    @staticmethod
    def _opposite_rule(left: CandidateEvidence, right: CandidateEvidence) -> bool:
        for first in left.scope_rules:
            for second in right.scope_rules:
                if (
                    first.dimension == second.dimension
                    and first.field == second.field
                    and first.scope_state == "EXPLICIT_CONSTRAINT"
                    and second.scope_state == "EXPLICIT_CONSTRAINT"
                    and first.values == second.values
                    and {first.operator, second.operator} == {"REQUIRES", "EXCLUDES"}
                ):
                    return True
        return False

    @staticmethod
    def _conflict_key(left: CandidateEvidence, right: CandidateEvidence) -> str:
        return "structured:" + ":".join(sorted((left.memory_id, right.memory_id)))


class DeterministicEvidencePipeline:
    """Applicability -> bounded, traceable evidence using no provider."""

    def __init__(self, config: EvidencePipelineConfig | None = None) -> None:
        self.config = config or EvidencePipelineConfig()

    def build(
        self,
        applicability: ApplicabilityResult,
        *,
        request_id: str = "current-query",
        required_aspects: Iterable[str] = (),
        conflicts: Optional[Sequence[ConflictAnnotation]] = None,
    ) -> EvidenceBundle:
        candidates = tuple(
            self._candidate_evidence(assessment, ())
            for assessment in applicability.assessments
        )
        effective_conflicts = tuple(conflicts) if conflicts is not None else ConflictDetector().detect(candidates)
        if effective_conflicts:
            candidates = tuple(
                self._candidate_evidence(assessment, effective_conflicts)
                for assessment in applicability.assessments
            )
        rejected: list[str] = []
        eligible: list[CandidateEvidence] = []
        for item in candidates:
            if not item.current_safe:
                rejected.append(f"not_current_safe:{item.memory_id}")
                continue
            if item.applicability_decision is ApplicabilityDecision.REJECT:
                rejected.append(f"mismatch:{item.memory_id}")
                continue
            if item.conflict.status is ConflictStatus.RESOLVED and item.conflict.is_loser:
                # A losing evidence item remains in the trace, but is omitted
                # from the answer-facing bounded selection.
                rejected.append(
                    f"conflict_loser:{item.memory_id}:{item.revision_id}:"
                    f"{','.join(item.provenance_refs)}"
                )
                continue
            eligible.append(item)

        selected = self._select(eligible)
        distilled = self._distill(selected)
        required = tuple(dict.fromkeys(_normalize_text(value) for value in required_aspects))
        sufficiency = self._sufficiency(distilled, required, rejected)
        warnings = tuple(dict.fromkeys((*applicability.warnings, *(
            f"unresolved_conflict:{item.conflict.conflict_key}"
            for item in eligible
            if item.conflict.status is ConflictStatus.UNRESOLVED and item.conflict.conflict_key
        ))))
        return EvidenceBundle(
            schema_version=self.config.schema_version,
            request_id=request_id,
            query=applicability.query,
            selected=distilled,
            sufficiency=sufficiency,
            rejected_or_omitted=tuple(rejected),
            warnings=warnings,
            retrieval_trace={
                "recall": applicability.recall_result.trace.to_dict(),
                "applicability": {
                    "assessment_count": len(applicability.assessments),
                    "rejected_count": len(applicability.rejected_candidates),
                    "warning_count": len(applicability.warnings),
                },
            },
            max_items=self.config.max_items,
            max_chars_per_item=self.config.max_chars_per_item,
            max_total_chars=self.config.max_total_chars,
        )

    def run_typed(self, applicability: ApplicabilityResult, **kwargs: Any) -> EvidenceBundle:
        return self.build(applicability, **kwargs)

    def run_natural_language(
        self,
        semantic_pipeline: SemanticRetrievalPipeline,
        query: str,
        *,
        top_k: int = 8,
        query_id: str = "current-query",
        recent_context: tuple = (),
        **kwargs: Any,
    ) -> EvidenceBundle:
        """Convenience path; provider failures remain visible to the caller."""
        result = semantic_pipeline.run(
            query,
            top_k=top_k,
            query_id=query_id,
            recent_context=recent_context,
        )
        return self.build(result.applicability_result, request_id=query_id, **kwargs)

    def _candidate_evidence(
        self,
        assessment: ApplicabilityAssessment,
        conflicts: Sequence[ConflictAnnotation],
    ) -> CandidateEvidence:
        candidate = assessment.candidate
        breakdown = candidate.score_breakdown
        reasons = tuple(dict.fromkeys(item.reason_code.value for item in assessment.dimensions))
        refs = tuple(
            dict.fromkeys(
                f"provenance:{ref.provenance_id}" for ref in candidate.snapshot.provenance
            )
        )
        return CandidateEvidence(
            memory_id=candidate.snapshot.memory_id,
            revision_id=candidate.snapshot.revision.revision_id,
            content=candidate.snapshot.revision.content,
            original_rank=candidate.rank,
            original_score=candidate.recall_score,
            score_breakdown={
                "content_bm25": breakdown.content_bm25,
                "metadata_overlap": breakdown.metadata_overlap,
                "dense_similarity": breakdown.dense_similarity,
                "fusion_rrf": breakdown.fusion_rrf,
            },
            applicability_status=assessment.overall,
            applicability_decision=assessment.decision,
            applicability_reasons=reasons,
            provenance_refs=refs,
            aspect_keys=_aspects(assessment),
            conflict=_conflict_for(candidate.snapshot.memory_id, conflicts),
            scope_rules=tuple(
                ScopeRuleEvidence(
                    dimension=item.dimension.value,
                    field=item.field,
                    scope_state=item.candidate_scope_state.value,
                    values=item.candidate_values,
                    operator=item.operator.value,
                    evidence_refs=item.evidence_refs,
                )
                for item in assessment.dimensions
                if item.candidate_scope_state.value == "EXPLICIT_CONSTRAINT"
            ),
            semantic_validity=candidate.snapshot.revision.semantic_validity.value,
            retention_disposition=candidate.snapshot.retention_disposition.value,
            current_safe=(candidate.snapshot.revision.semantic_validity.value == "CURRENT" and candidate.snapshot.retention_disposition.value == "ACTIVE"),
            selection_trace=(f"recall_rank:{candidate.rank}", "applicability_gate"),
        )

    def _select(self, eligible: Sequence[CandidateEvidence]) -> Tuple[CandidateEvidence, ...]:
        # First occurrence wins for same current identity/content.  Scope
        # differences are preserved because applicability status/reasons are
        # part of the deduplication signature.
        unique: list[CandidateEvidence] = []
        seen: set[tuple[str, str, str, str]] = set()
        for item in sorted(eligible, key=lambda value: (value.original_rank, -value.original_score, value.memory_id)):
            signature = (
                item.memory_id,
                item.revision_id,
                _normalize_text(item.content),
                item.applicability_status.value,
            )
            if signature in seen:
                continue
            seen.add(signature)
            unique.append(item)

        # Deterministic round-robin over aspects prevents one aspect from
        # filling the whole budget while retaining ranked order within each.
        buckets: dict[str, list[CandidateEvidence]] = {}
        for item in unique:
            buckets.setdefault(item.aspect_keys[0], []).append(item)
        ordered_aspects = sorted(buckets)
        selected: list[CandidateEvidence] = []
        index = 0
        while len(selected) < self.config.max_items and ordered_aspects:
            progressed = False
            for aspect in ordered_aspects:
                bucket = buckets[aspect]
                if index < len(bucket):
                    selected.append(bucket[index])
                    progressed = True
                    if len(selected) >= self.config.max_items:
                        break
            if not progressed:
                break
            index += 1
        return tuple(selected)

    def _distill(self, selected: Sequence[CandidateEvidence]) -> Tuple[DistilledEvidence, ...]:
        remaining = self.config.max_total_chars
        output: list[DistilledEvidence] = []
        for item in selected:
            if remaining <= 0:
                break
            text = " ".join(item.content.split())[: min(self.config.max_chars_per_item, remaining)]
            if not text:
                continue
            output.append(
                DistilledEvidence(
                    memory_id=item.memory_id,
                    revision_id=item.revision_id,
                    text=text,
                    original_rank=item.original_rank,
                    original_score=item.original_score,
                    score_breakdown=item.score_breakdown,
                    provenance_refs=item.provenance_refs,
                    applicability_status=item.applicability_status,
                    applicability_decision=item.applicability_decision,
                    applicability_reasons=item.applicability_reasons,
                    conflict_status=item.conflict.status,
                    conflict_key=item.conflict.conflict_key,
                    conflict_losing_memory_ids=item.conflict.losing_memory_ids,
                    source_trace=item.selection_trace,
                    aspect_keys=item.aspect_keys,
                    current_safe=item.current_safe,
                )
            )
            remaining -= len(text)
        return tuple(output)

    @staticmethod
    def _sufficiency(
        selected: Sequence[DistilledEvidence],
        required: Sequence[str],
        rejected: Sequence[str],
    ) -> EvidenceSufficiency:
        aspects = {aspect for item in selected for aspect in item.aspect_keys}
        missing = tuple(aspect for aspect in required if aspect not in aspects)
        uncertainties = tuple(
            item.memory_id
            for item in selected
            if item.applicability_status in {ApplicabilityStatus.UNKNOWN, ApplicabilityStatus.AMBIGUOUS}
            or item.conflict_status is ConflictStatus.UNRESOLVED
        )
        unresolved = tuple(
            item.memory_id for item in selected if item.conflict_status is ConflictStatus.UNRESOLVED
        )
        if not selected:
            return EvidenceSufficiency(
                EvidenceSufficiencyStatus.INSUFFICIENT,
                ("NO_ELIGIBLE_EVIDENCE",),
                missing,
                uncertainties,
                unresolved,
            )
        if missing:
            return EvidenceSufficiency(
                EvidenceSufficiencyStatus.INSUFFICIENT,
                ("MISSING_REQUIRED_ASPECTS",),
                missing,
                uncertainties,
                unresolved,
            )
        # Ordinary MISMATCH rejection is an expected applicability gate
        # outcome, not by itself an evidence insufficiency.  Uncertainty and
        # unresolved conflicts are what lower a complete selection to PARTIAL.
        if uncertainties:
            return EvidenceSufficiency(
                EvidenceSufficiencyStatus.PARTIAL,
                ("UNCERTAINTY_OR_OMISSION_PRESENT",),
                missing,
                uncertainties,
                unresolved,
            )
        return EvidenceSufficiency(EvidenceSufficiencyStatus.SUFFICIENT, (), (), (), ())


# Concise aliases for callers that prefer the generic production names.
EvidencePipeline = DeterministicEvidencePipeline
