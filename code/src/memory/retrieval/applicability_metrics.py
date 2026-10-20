from __future__ import annotations

from typing import Mapping, Sequence

from src.memory.retrieval.applicability_models import (
    ApplicabilityAssessment,
    ApplicabilityDecision,
    ApplicabilityStatus,
)


def applicability_accuracy(
    assessments: Sequence[ApplicabilityAssessment],
    expected: Mapping[str, ApplicabilityStatus],
) -> float:
    if not expected:
        raise ValueError("expected applicability labels 不能为空")
    actual = {assessment.candidate.snapshot.memory_id: assessment.overall for assessment in assessments}
    return sum(actual.get(memory_id) is status for memory_id, status in expected.items()) / len(expected)


def mismatch_rejection_accuracy(
    assessments: Sequence[ApplicabilityAssessment],
    expected: Mapping[str, ApplicabilityStatus],
) -> float:
    mismatch_ids = {memory_id for memory_id, status in expected.items() if status is ApplicabilityStatus.MISMATCH}
    if not mismatch_ids:
        return 1.0
    rejected = {
        assessment.candidate.snapshot.memory_id
        for assessment in assessments
        if assessment.decision is ApplicabilityDecision.REJECT
    }
    return len(mismatch_ids & rejected) / len(mismatch_ids)


def unknown_preservation_accuracy(
    assessments: Sequence[ApplicabilityAssessment],
    expected: Mapping[str, ApplicabilityStatus],
) -> float:
    unknown_ids = {memory_id for memory_id, status in expected.items() if status is ApplicabilityStatus.UNKNOWN}
    if not unknown_ids:
        return 1.0
    preserved = {
        assessment.candidate.snapshot.memory_id
        for assessment in assessments
        if assessment.overall is ApplicabilityStatus.UNKNOWN
        and assessment.decision is ApplicabilityDecision.RETAIN_WITH_UNCERTAINTY
    }
    return len(unknown_ids & preserved) / len(unknown_ids)


def mismatch_contamination_before(expected: Mapping[str, ApplicabilityStatus]) -> float:
    if not expected:
        return 0.0
    return sum(status is ApplicabilityStatus.MISMATCH for status in expected.values()) / len(expected)


def mismatch_contamination_after(
    assessments: Sequence[ApplicabilityAssessment],
    expected: Mapping[str, ApplicabilityStatus],
) -> float:
    retained = [
        assessment
        for assessment in assessments
        if assessment.decision is not ApplicabilityDecision.REJECT
    ]
    if not retained:
        return 0.0
    mismatch_count = sum(
        expected.get(assessment.candidate.snapshot.memory_id) is ApplicabilityStatus.MISMATCH
        for assessment in retained
    )
    return mismatch_count / len(retained)
