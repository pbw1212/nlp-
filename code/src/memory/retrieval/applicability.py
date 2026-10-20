from __future__ import annotations

from typing import Mapping, Optional, Sequence

from src.memory.retrieval.applicability_models import (
    ApplicabilityAssessment,
    ApplicabilityContext,
    ApplicabilityDecision,
    ApplicabilityDimension,
    ApplicabilityReasonCode,
    ApplicabilityResult,
    ApplicabilityStatus,
    CandidateScopeRule,
    CandidateScopeState,
    ConstraintOperator,
    ContextKnowledgeState,
    ContextValue,
    DimensionAssessment,
    MemoryApplicabilityProfile,
)
from src.memory.retrieval.models import RecallCandidate, RecallResult


class ApplicabilityEngine:
    """Structured symbolic comparison only；不解释自然语言或写回 memory。"""

    def __init__(self, *, sparse_profiles: bool = True) -> None:
        # Sparse profiles are the canonical Task 2 semantics.  The explicit
        # legacy mode exists only to reproduce historical v1 attribution
        # numbers without rewriting that benchmark's meaning.
        self.sparse_profiles = sparse_profiles

    def assess(
        self,
        candidate: RecallCandidate,
        context: ApplicabilityContext,
        profile: MemoryApplicabilityProfile,
    ) -> ApplicabilityAssessment:
        if candidate.snapshot.memory_id != profile.memory_id:
            raise ValueError("profile memory_id 与 candidate 不一致")
        context_by_key = {value.key: value for value in context.values}
        rules_by_key = {rule.key: rule for rule in profile.rules}
        # A sparse profile owns the applicability axes. Context facts without
        # a candidate-side qualifier must not manufacture an implicit
        # UNSPECIFIED rule; that would conflate absence with uncertainty.
        keys = sorted(
            set(rules_by_key)
            if self.sparse_profiles
            else set(rules_by_key) | set(context_by_key),
            key=lambda key: (list(ApplicabilityDimension).index(key[0]), key[1]),
        )
        dimensions = tuple(
            self._assess_dimension(
                candidate,
                rules_by_key.get(key),
                context_by_key.get(key),
                dimension=key[0],
                field=key[1],
            )
            for key in keys
        )
        overall, decision = self._aggregate(dimensions)
        return ApplicabilityAssessment(candidate, dimensions, overall, decision)

    def assess_recall_result(
        self,
        recall_result: RecallResult,
        context: ApplicabilityContext,
        profiles: Mapping[str, MemoryApplicabilityProfile],
    ) -> ApplicabilityResult:
        assessments = []
        warnings = []
        for candidate in recall_result.candidates:
            profile = profiles.get(candidate.snapshot.memory_id)
            if profile is None:
                profile = MemoryApplicabilityProfile(candidate.snapshot.memory_id, ())
                warnings.append(f"missing_profile:{candidate.snapshot.memory_id}")
            assessments.append(self.assess(candidate, context, profile))
        return ApplicabilityResult(
            query=recall_result.query,
            recall_result=recall_result,
            assessments=tuple(assessments),
            warnings=tuple(warnings),
        )

    @staticmethod
    def _assess_dimension(
        candidate: RecallCandidate,
        rule: Optional[CandidateScopeRule],
        context_value: Optional[ContextValue],
        *,
        dimension: ApplicabilityDimension,
        field: str,
    ) -> DimensionAssessment:
        rule = rule or CandidateScopeRule(
            dimension=dimension,
            field=field,
            scope_state=CandidateScopeState.UNSPECIFIED,
        )
        context_value = context_value or ContextValue(
            dimension=dimension,
            field=field,
            state=ContextKnowledgeState.UNKNOWN,
        )
        base_refs = (
            f"memory:{candidate.snapshot.memory_id}",
            f"revision:{candidate.snapshot.revision.revision_id}",
            f"context:{field}",
            *tuple(f"provenance:{ref.provenance_id}" for ref in candidate.snapshot.provenance),
            *rule.evidence_refs,
        )
        evidence_refs = tuple(dict.fromkeys(base_refs))

        if rule.scope_state is CandidateScopeState.EXPLICIT_GENERAL:
            status = ApplicabilityStatus.MATCH
            reason = ApplicabilityReasonCode.EXPLICIT_GENERAL_SCOPE
            explanation = f"{field} 已明确声明为通用 scope"
        elif rule.scope_state is CandidateScopeState.UNSPECIFIED:
            status = ApplicabilityStatus.UNKNOWN
            reason = ApplicabilityReasonCode.CANDIDATE_SCOPE_UNSPECIFIED
            explanation = f"candidate 未声明 {field} 的适用 scope"
        elif context_value.state is ContextKnowledgeState.UNKNOWN:
            status = ApplicabilityStatus.UNKNOWN
            reason = ApplicabilityReasonCode.CONTEXT_UNKNOWN
            explanation = f"current context 未知 {field}"
        elif context_value.state is ContextKnowledgeState.AMBIGUOUS:
            status = ApplicabilityStatus.AMBIGUOUS
            reason = ApplicabilityReasonCode.CONTEXT_AMBIGUOUS
            explanation = f"current context 的 {field} 存在多个候选值"
        else:
            candidate_values = set(rule.values)
            context_values = set(context_value.values)
            overlap = bool(candidate_values & context_values)
            if rule.operator is ConstraintOperator.EXCLUDES:
                matched = not overlap
            else:
                matched = overlap
            if matched:
                status = ApplicabilityStatus.MATCH
                reason = ApplicabilityReasonCode.EXACT_MATCH
                explanation = f"{field} 满足明确 scope/constraint"
            else:
                status = ApplicabilityStatus.MISMATCH
                reason = (
                    ApplicabilityReasonCode.REQUIRED_CONSTRAINT_MISSING
                    if dimension is ApplicabilityDimension.CONSTRAINT
                    and rule.operator is ConstraintOperator.REQUIRES
                    else ApplicabilityReasonCode.EXPLICIT_CONFLICT
                )
                explanation = f"{field} 与明确 scope/constraint 冲突"

        return DimensionAssessment(
            dimension=dimension,
            field=field,
            candidate_scope_state=rule.scope_state,
            candidate_values=rule.values,
            context_state=context_value.state,
            context_values=context_value.values,
            status=status,
            reason_code=reason,
            evidence_refs=evidence_refs,
            explanation=explanation,
            operator=rule.operator,
        )

    @staticmethod
    def _aggregate(
        dimensions: Sequence[DimensionAssessment],
    ) -> tuple[ApplicabilityStatus, ApplicabilityDecision]:
        statuses = {assessment.status for assessment in dimensions}
        if ApplicabilityStatus.MISMATCH in statuses:
            return ApplicabilityStatus.MISMATCH, ApplicabilityDecision.REJECT
        if ApplicabilityStatus.AMBIGUOUS in statuses:
            return ApplicabilityStatus.AMBIGUOUS, ApplicabilityDecision.RETAIN_WITH_UNCERTAINTY
        if ApplicabilityStatus.UNKNOWN in statuses or not dimensions:
            return ApplicabilityStatus.UNKNOWN, ApplicabilityDecision.RETAIN_WITH_UNCERTAINTY
        return ApplicabilityStatus.MATCH, ApplicabilityDecision.RETAIN
