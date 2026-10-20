from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Tuple

from src.memory.retrieval.applicability_models import (
    ApplicabilityContext,
    CandidateScopeRule,
    ContextValue,
    MemoryApplicabilityProfile,
)


CONTEXT_PROMPT_VERSION = "context_interpretation_v2"
SCOPE_PROMPT_VERSION = "memory_scope_interpretation_v2"
INTERPRETATION_SCHEMA_VERSION = 1


class InterpretationTaskKind(str, Enum):
    CURRENT_CONTEXT = "CURRENT_CONTEXT"
    MEMORY_SCOPE = "MEMORY_SCOPE"
    CONTEXT_FIELD_SELECTION = "CONTEXT_FIELD_SELECTION"
    CONTEXT_FACT_INTERPRETATION = "CONTEXT_FACT_INTERPRETATION"
    SCOPE_FIELD_SELECTION = "SCOPE_FIELD_SELECTION"
    SCOPE_RULE_INTERPRETATION = "SCOPE_RULE_INTERPRETATION"


class EvidenceKind(str, Enum):
    EXPLICIT = "EXPLICIT"
    INFERRED = "INFERRED"


@dataclass(frozen=True)
class InterpretationProviderIdentity:
    provider: str
    model: str

    def __post_init__(self) -> None:
        if not self.provider or not self.model:
            raise ValueError("semantic provider/model 不能为空")


@dataclass(frozen=True)
class ContextSource:
    source_id: str
    text: str

    def __post_init__(self) -> None:
        if not self.source_id or not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("context source 需要 source_id 和非空 text")


@dataclass(frozen=True)
class InterpretationEvidence:
    evidence_ref: str
    kind: EvidenceKind
    reason: str

    def __post_init__(self) -> None:
        if not self.evidence_ref or not self.reason:
            raise ValueError("interpretation evidence ref/reason 不能为空")


@dataclass(frozen=True)
class ContextFactTrace:
    value: ContextValue
    evidence: Tuple[InterpretationEvidence, ...]
    raw_values: Tuple[str, ...] = ()


@dataclass(frozen=True)
class SelectedField:
    dimension: str
    field: str
    evidence: Tuple[InterpretationEvidence, ...]


@dataclass(frozen=True)
class SelectionTrace:
    selected: Tuple[SelectedField, ...]
    warnings: Tuple[str, ...]
    provider: InterpretationProviderIdentity
    prompt_version: str


@dataclass(frozen=True)
class ScopeRuleTrace:
    rule: CandidateScopeRule
    evidence: Tuple[InterpretationEvidence, ...]
    raw_values: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ContextInterpretation:
    context: ApplicabilityContext
    facts: Tuple[ContextFactTrace, ...]
    warnings: Tuple[str, ...]
    provider: InterpretationProviderIdentity
    prompt_version: str = CONTEXT_PROMPT_VERSION
    schema_version: int = INTERPRETATION_SCHEMA_VERSION
    selection_trace: SelectionTrace | None = None


@dataclass(frozen=True)
class MemoryScopeInterpretation:
    profile: MemoryApplicabilityProfile
    rules: Tuple[ScopeRuleTrace, ...]
    warnings: Tuple[str, ...]
    provider: InterpretationProviderIdentity
    prompt_version: str = SCOPE_PROMPT_VERSION
    schema_version: int = INTERPRETATION_SCHEMA_VERSION
    selection_trace: SelectionTrace | None = None


@dataclass(frozen=True)
class SemanticInterpretationTask:
    kind: InterpretationTaskKind
    prompt_version: str
    schema_version: int
    system_prompt: str
    payload: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    schema_name: str


def bounded_source_ref(source_id: str, start: int | None = None, end: int | None = None) -> str:
    if not source_id:
        raise ValueError("source_id 不能为空")
    if start is None or end is None:
        return f"source:{source_id}"
    if start < 0 or end < start:
        raise ValueError("source span 非法")
    return f"source:{source_id}#chars={start}-{end}"
