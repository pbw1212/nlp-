from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Iterable, Mapping, Tuple

from src.memory.retrieval.models import RecallCandidate, RecallResult


class ApplicabilityDimension(str, Enum):
    PERSON = "PERSON"
    LOCATION = "LOCATION"
    TIME = "TIME"
    TASK = "TASK"
    SCENARIO = "SCENARIO"
    CONSTRAINT = "CONSTRAINT"


class ContextKnowledgeState(str, Enum):
    KNOWN = "KNOWN"
    UNKNOWN = "UNKNOWN"
    AMBIGUOUS = "AMBIGUOUS"


class CandidateScopeState(str, Enum):
    EXPLICIT_CONSTRAINT = "EXPLICIT_CONSTRAINT"
    EXPLICIT_GENERAL = "EXPLICIT_GENERAL"
    UNSPECIFIED = "UNSPECIFIED"


class ConstraintOperator(str, Enum):
    REQUIRES = "REQUIRES"
    EXCLUDES = "EXCLUDES"


class ApplicabilityStatus(str, Enum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    UNKNOWN = "UNKNOWN"
    AMBIGUOUS = "AMBIGUOUS"


class ApplicabilityDecision(str, Enum):
    RETAIN = "RETAIN"
    REJECT = "REJECT"
    RETAIN_WITH_UNCERTAINTY = "RETAIN_WITH_UNCERTAINTY"


class ApplicabilityReasonCode(str, Enum):
    EXACT_MATCH = "EXACT_MATCH"
    EXPLICIT_CONFLICT = "EXPLICIT_CONFLICT"
    CONTEXT_UNKNOWN = "CONTEXT_UNKNOWN"
    CONTEXT_AMBIGUOUS = "CONTEXT_AMBIGUOUS"
    CANDIDATE_SCOPE_UNSPECIFIED = "CANDIDATE_SCOPE_UNSPECIFIED"
    EXPLICIT_GENERAL_SCOPE = "EXPLICIT_GENERAL_SCOPE"
    REQUIRED_CONSTRAINT_MISSING = "REQUIRED_CONSTRAINT_MISSING"


def normalize_symbolic_value(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("applicability value 必须是字符串")
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    if not normalized:
        raise ValueError("applicability value 不能为空")
    return normalized


def _normalized_values(values: Iterable[str]) -> Tuple[str, ...]:
    return tuple(sorted(set(normalize_symbolic_value(value) for value in values)))


def _normalized_field(field: str) -> str:
    return normalize_symbolic_value(field)


@dataclass(frozen=True)
class ContextValue:
    dimension: ApplicabilityDimension
    field: str
    state: ContextKnowledgeState
    values: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "field", _normalized_field(self.field))
        object.__setattr__(self, "values", _normalized_values(self.values))
        if self.state is ContextKnowledgeState.KNOWN and not self.values:
            raise ValueError("KNOWN context 必须包含 value")
        if self.state is ContextKnowledgeState.UNKNOWN and self.values:
            raise ValueError("UNKNOWN context 不能包含 value")
        if self.state is ContextKnowledgeState.AMBIGUOUS and len(self.values) < 2:
            raise ValueError("AMBIGUOUS context 必须保留至少两个候选 value")

    @property
    def key(self) -> tuple[ApplicabilityDimension, str]:
        return self.dimension, self.field


@dataclass(frozen=True)
class ApplicabilityContext:
    values: Tuple[ContextValue, ...]

    def __post_init__(self) -> None:
        keys = [value.key for value in self.values]
        if len(keys) != len(set(keys)):
            raise ValueError("context 不能重复定义同一 dimension/field")
        object.__setattr__(self, "values", tuple(sorted(self.values, key=_value_sort_key)))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ApplicabilityContext":
        _validate_keys(data, {"values"}, "context")
        return cls(tuple(_context_value_from_dict(item) for item in _require_list(data, "values")))


@dataclass(frozen=True)
class CandidateScopeRule:
    dimension: ApplicabilityDimension
    field: str
    scope_state: CandidateScopeState
    values: Tuple[str, ...] = ()
    operator: ConstraintOperator = ConstraintOperator.REQUIRES
    evidence_refs: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "field", _normalized_field(self.field))
        object.__setattr__(self, "values", _normalized_values(self.values))
        object.__setattr__(self, "evidence_refs", tuple(sorted(set(self.evidence_refs))))
        if self.scope_state is CandidateScopeState.EXPLICIT_CONSTRAINT and not self.values:
            raise ValueError("EXPLICIT_CONSTRAINT 必须包含 value")
        if self.scope_state is not CandidateScopeState.EXPLICIT_CONSTRAINT and self.values:
            raise ValueError("只有 EXPLICIT_CONSTRAINT 可以包含 value")
        if self.dimension is not ApplicabilityDimension.CONSTRAINT and self.operator is ConstraintOperator.EXCLUDES:
            raise ValueError("EXCLUDES 只用于 CONSTRAINT dimension")

    @property
    def key(self) -> tuple[ApplicabilityDimension, str]:
        return self.dimension, self.field


@dataclass(frozen=True)
class MemoryApplicabilityProfile:
    memory_id: str
    rules: Tuple[CandidateScopeRule, ...]

    def __post_init__(self) -> None:
        if not self.memory_id:
            raise ValueError("profile memory_id 不能为空")
        keys = [rule.key for rule in self.rules]
        if len(keys) != len(set(keys)):
            raise ValueError("profile 不能重复定义同一 dimension/field")
        object.__setattr__(self, "rules", tuple(sorted(self.rules, key=_rule_sort_key)))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MemoryApplicabilityProfile":
        _validate_keys(data, {"memory_id", "rules"}, "profile")
        return cls(
            memory_id=_require_string(data, "memory_id"),
            rules=tuple(_scope_rule_from_dict(item) for item in _require_list(data, "rules")),
        )


@dataclass(frozen=True)
class DimensionAssessment:
    dimension: ApplicabilityDimension
    field: str
    candidate_scope_state: CandidateScopeState
    candidate_values: Tuple[str, ...]
    context_state: ContextKnowledgeState
    context_values: Tuple[str, ...]
    status: ApplicabilityStatus
    reason_code: ApplicabilityReasonCode
    evidence_refs: Tuple[str, ...]
    explanation: str
    operator: ConstraintOperator = ConstraintOperator.REQUIRES


@dataclass(frozen=True)
class ApplicabilityAssessment:
    candidate: RecallCandidate
    dimensions: Tuple[DimensionAssessment, ...]
    overall: ApplicabilityStatus
    decision: ApplicabilityDecision


@dataclass(frozen=True)
class ApplicabilityResult:
    query: str
    recall_result: RecallResult
    assessments: Tuple[ApplicabilityAssessment, ...]
    warnings: Tuple[str, ...] = ()

    @property
    def retained_candidates(self) -> Tuple[RecallCandidate, ...]:
        return tuple(
            assessment.candidate
            for assessment in self.assessments
            if assessment.decision is not ApplicabilityDecision.REJECT
        )

    @property
    def rejected_candidates(self) -> Tuple[RecallCandidate, ...]:
        return tuple(
            assessment.candidate
            for assessment in self.assessments
            if assessment.decision is ApplicabilityDecision.REJECT
        )


_DIMENSION_ORDER = {dimension: index for index, dimension in enumerate(ApplicabilityDimension)}


def _value_sort_key(value: ContextValue) -> tuple[int, str]:
    return _DIMENSION_ORDER[value.dimension], value.field


def _rule_sort_key(rule: CandidateScopeRule) -> tuple[int, str]:
    return _DIMENSION_ORDER[rule.dimension], rule.field


def _validate_keys(data: Mapping[str, Any], allowed: set[str], label: str) -> None:
    if not isinstance(data, Mapping):
        raise TypeError(f"{label} 必须是 object")
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"{label} 包含未知字段: {sorted(unknown)}")


def _require_string(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} 必须是非空字符串")
    return value


def _require_list(data: Mapping[str, Any], key: str) -> list[Any]:
    value = data.get(key)
    if not isinstance(value, list):
        raise ValueError(f"{key} 必须是数组")
    return value


def _string_tuple(data: Mapping[str, Any], key: str) -> Tuple[str, ...]:
    values = data.get(key, [])
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise ValueError(f"{key} 必须是字符串数组")
    return tuple(values)


def _context_value_from_dict(data: Mapping[str, Any]) -> ContextValue:
    _validate_keys(data, {"dimension", "field", "state", "values"}, "context value")
    return ContextValue(
        dimension=ApplicabilityDimension(_require_string(data, "dimension")),
        field=_require_string(data, "field"),
        state=ContextKnowledgeState(_require_string(data, "state")),
        values=_string_tuple(data, "values"),
    )


def _scope_rule_from_dict(data: Mapping[str, Any]) -> CandidateScopeRule:
    _validate_keys(
        data,
        {"dimension", "field", "scope_state", "values", "operator", "evidence_refs"},
        "profile rule",
    )
    return CandidateScopeRule(
        dimension=ApplicabilityDimension(_require_string(data, "dimension")),
        field=_require_string(data, "field"),
        scope_state=CandidateScopeState(_require_string(data, "scope_state")),
        values=_string_tuple(data, "values"),
        operator=ConstraintOperator(str(data.get("operator", ConstraintOperator.REQUIRES.value))),
        evidence_refs=_string_tuple(data, "evidence_refs"),
    )
