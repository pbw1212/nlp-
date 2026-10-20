from __future__ import annotations

from typing import Any, Mapping, Sequence, Tuple

from src.memory.core.schema import MemorySnapshot
from src.memory.llm.errors import ProviderError, ProviderResponseError
from src.memory.retrieval.applicability_models import (
    ApplicabilityContext,
    ApplicabilityDimension,
    CandidateScopeRule,
    ContextValue,
    MemoryApplicabilityProfile,
)
from src.memory.retrieval.applicability_ontology import (
    DEFAULT_APPLICABILITY_FIELD_REGISTRY,
    ApplicabilityFieldRegistry,
)
from src.memory.retrieval.interpretation_models import (
    CONTEXT_PROMPT_VERSION,
    INTERPRETATION_SCHEMA_VERSION,
    SCOPE_PROMPT_VERSION,
    ContextFactTrace,
    ContextInterpretation,
    ContextSource,
    EvidenceKind,
    InterpretationEvidence,
    InterpretationProviderIdentity,
    InterpretationTaskKind,
    MemoryScopeInterpretation,
    ScopeRuleTrace,
    SelectedField,
    SelectionTrace,
    SemanticInterpretationTask,
)
from src.memory.retrieval.interpretation_provider import SemanticInterpretationProvider


CONTEXT_SYSTEM_PROMPT = """You extract only evidence-grounded current-context facts that can change whether a personal memory applies.
Return exactly one JSON object with schema_version=1, values, and warnings.
Each values item must contain: dimension, field, state, values, evidence.
Each evidence item must contain evidence_ref, kind (EXPLICIT or INFERRED), reason.
Use only an exact (dimension, field_id) pair from the supplied registry. Never invent or rename a field.
Allowed states: KNOWN, UNKNOWN, AMBIGUOUS. Never turn missing information into KNOWN.
Missing means not mentioned or not relevant: omit the row. UNKNOWN means the field matters to the current decision but its value is explicitly unavailable; emit no values. AMBIGUOUS means at least two evidence-supported alternatives remain; emit all alternatives.
Query intent is not automatically an applicability fact. For example, 'find a hotel' does not create TASK.hotel_search. It may make destination, travel_party and payment_source relevant-but-UNKNOWN when those facts are explicitly undecided or the request is an underspecified accommodation decision.
Do not infer defaults such as solo, self-paid, current city, driving, or business purpose.
Normalize only through supplied allowed values/alias guidance. Open text values must stay close to explicit source wording.
Evidence refs must point to supplied source IDs, optionally with #chars=start-end; do not copy source text.
Counterexamples: 'I like rich hotel breakfasts' does not prove any LOCATION; 'company or self may pay' is AMBIGUOUS, not UNKNOWN; 'help me find a hotel' has no KNOWN applicability fact.
Unknown fields or enums are forbidden. If no supported field fits, omit it and add a warning."""


SCOPE_SYSTEM_PROMPT = """You infer applicability scope for one current memory revision, conservatively.
Return exactly one JSON object with schema_version=1, memory_id, rules, and warnings.
Each rule must contain: dimension, field, scope_state, values, operator, evidence.
Each evidence item must contain evidence_ref, kind (EXPLICIT or INFERRED), reason.
Use only an exact (dimension, field_id) pair from the supplied registry. Never invent or rename a field.
Allowed scope_state: EXPLICIT_CONSTRAINT, EXPLICIT_GENERAL, UNSPECIFIED.
Allowed operator: REQUIRES, EXCLUDES. EXCLUDES is only for CONSTRAINT.
Absence of a restriction is UNSPECIFIED, never EXPLICIT_GENERAL. EXPLICIT_GENERAL requires direct universal evidence such as any/always/regardless/no matter where/who.
Emit explicit constraints/general rules and only those UNSPECIFIED rules that represent a preference-relevant applicability axis genuinely considered by the statement. Do not enumerate every registry field.
Metadata mentions are hints, not proof of scope. Prefer revision content and provenance-backed evidence.
Do not flatten complex conditional logic that this schema cannot represent; emit UNSPECIFIED plus a warning.
Evidence refs must point to revision, metadata field, or provenance IDs; do not copy source text.
Counterexample: 'I like hotels with rich breakfast' may have LOCATION.destination and TASK.trip_type UNSPECIFIED; it does not make either EXPLICIT_GENERAL. A location metadata mention alone is not a location constraint.
Unknown fields or enums are forbidden. If no supported field fits, omit it and add a warning."""


_EVIDENCE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "evidence_ref": {"type": "string"},
        "kind": {"type": "string", "enum": ["EXPLICIT", "INFERRED"]},
        "reason": {"type": "string"},
    },
    "required": ["evidence_ref", "kind", "reason"],
}

def _context_output_schema(registry: ApplicabilityFieldRegistry) -> Mapping[str, Any]:
    return {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "schema_version": {"type": "integer", "enum": [1]},
        "values": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "dimension": {"type": "string", "enum": [item.value for item in ApplicabilityDimension]},
                    "field": {"type": "string", "enum": list(registry.output_field_names)},
                    "state": {"type": "string", "enum": ["KNOWN", "UNKNOWN", "AMBIGUOUS"]},
                    "values": {"type": "array", "items": {"type": "string"}},
                    "evidence": {"type": "array", "items": _EVIDENCE_SCHEMA},
                },
                "required": ["dimension", "field", "state", "values", "evidence"],
            },
        },
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["schema_version", "values", "warnings"],
    }

def _scope_output_schema(registry: ApplicabilityFieldRegistry) -> Mapping[str, Any]:
    return {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "schema_version": {"type": "integer", "enum": [1]},
        "memory_id": {"type": "string"},
        "rules": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "dimension": {"type": "string", "enum": [item.value for item in ApplicabilityDimension]},
                    "field": {"type": "string", "enum": list(registry.output_field_names)},
                    "scope_state": {
                        "type": "string",
                        "enum": ["EXPLICIT_CONSTRAINT", "EXPLICIT_GENERAL", "UNSPECIFIED"],
                    },
                    "values": {"type": "array", "items": {"type": "string"}},
                    "operator": {"type": "string", "enum": ["REQUIRES", "EXCLUDES"]},
                    "evidence": {"type": "array", "items": _EVIDENCE_SCHEMA},
                },
                "required": ["dimension", "field", "scope_state", "values", "operator", "evidence"],
            },
        },
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["schema_version", "memory_id", "rules", "warnings"],
    }


CONTEXT_OUTPUT_SCHEMA = _context_output_schema(DEFAULT_APPLICABILITY_FIELD_REGISTRY)
SCOPE_OUTPUT_SCHEMA = _scope_output_schema(DEFAULT_APPLICABILITY_FIELD_REGISTRY)


def _selection_schema(registry: ApplicabilityFieldRegistry) -> Mapping[str, Any]:
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            "schema_version": {"type": "integer", "enum": [1]},
            "selected": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "dimension": {"type": "string", "enum": [item.value for item in ApplicabilityDimension]},
                    "field": {"type": "string", "enum": list(registry.output_field_names)},
                    "evidence": {"type": "array", "items": _EVIDENCE_SCHEMA},
                },
                "required": ["dimension", "field", "evidence"],
            }},
            "warnings": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["schema_version", "selected", "warnings"],
    }


def _conditioned_context_schema(registry: ApplicabilityFieldRegistry) -> Mapping[str, Any]:
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            "schema_version": {"type": "integer", "enum": [1]},
            "dimension": {"type": "string", "enum": [item.value for item in ApplicabilityDimension]},
            "field": {"type": "string", "enum": list(registry.output_field_names)},
            "state": {"type": "string", "enum": ["KNOWN", "UNKNOWN", "AMBIGUOUS"]},
            "values": {"type": "array", "items": {"type": "string"}},
            "evidence": {"type": "array", "items": _EVIDENCE_SCHEMA},
            "warnings": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["schema_version", "dimension", "field", "state", "values", "evidence", "warnings"],
    }


def _conditioned_scope_schema(registry: ApplicabilityFieldRegistry) -> Mapping[str, Any]:
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            "schema_version": {"type": "integer", "enum": [1]},
            "dimension": {"type": "string", "enum": [item.value for item in ApplicabilityDimension]},
            "field": {"type": "string", "enum": list(registry.output_field_names)},
            "scope_state": {"type": "string", "enum": ["EXPLICIT_CONSTRAINT", "EXPLICIT_GENERAL", "UNSPECIFIED"]},
            "values": {"type": "array", "items": {"type": "string"}},
            "operator": {"type": "string", "enum": ["REQUIRES", "EXCLUDES"]},
            "evidence": {"type": "array", "items": _EVIDENCE_SCHEMA},
            "warnings": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["schema_version", "dimension", "field", "scope_state", "values", "operator", "evidence", "warnings"],
    }


class _InterpreterBase:
    def __init__(
        self,
        provider: SemanticInterpretationProvider,
        *,
        registry: ApplicabilityFieldRegistry = DEFAULT_APPLICABILITY_FIELD_REGISTRY,
        validation_attempts: int = 2,
    ) -> None:
        if validation_attempts <= 0:
            raise ValueError("validation_attempts 必须大于 0")
        self._provider = provider
        self._registry = registry
        self._validation_attempts = validation_attempts

    @property
    def provider_identity(self) -> InterpretationProviderIdentity:
        return self._provider.identity

    def _validated_call(self, task: SemanticInterpretationTask, validator):
        last_error: Exception | None = None
        for _attempt in range(self._validation_attempts):
            try:
                output = self._provider.interpret(task)
                if not isinstance(output, Mapping):
                    raise TypeError("semantic provider output 必须是 object")
                return validator(output)
            except ProviderResponseError as exc:
                last_error = exc
            except ProviderError:
                raise
            except (TypeError, ValueError, KeyError) as exc:
                last_error = exc
        raise ProviderResponseError(
            "semantic interpretation output schema validation failed",
            provider=self._provider.identity.provider,
            operation=task.kind.value.lower(),
        ) from last_error


class ContextFieldSelector(_InterpreterBase):
    """Select only registry fields with source evidence; values are deferred."""

    def select(self, query: str, *, query_id: str = "current-query", recent_context: Sequence[ContextSource] = ()) -> SelectionTrace:
        sources = (ContextSource(query_id, query), *tuple(recent_context))
        source_ids = {source.source_id for source in sources}
        task = SemanticInterpretationTask(
            InterpretationTaskKind.CONTEXT_FIELD_SELECTION, "context_field_selection_v1", 1,
            "Select only applicability-relevant registry fields supported by explicit source evidence. Omit query intent and unsupported fields.",
            {"ontology_version": self._registry.version, "field_registry": self._registry.prompt_specs(),
             "sources": [{"source_id": s.source_id, "text": s.text} for s in sources]},
            _selection_schema(self._registry), "context_field_selection_v1",
        )
        return self._validated_call(task, lambda data: self._parse_selection(data, source_ids, "context_field_selection_v1"))

    def _parse_selection(self, data: Mapping[str, Any], source_ids: set[str], prompt: str) -> SelectionTrace:
        _exact_keys(data, {"schema_version", "selected", "warnings"}, "context selection")
        if data["schema_version"] != 1:
            raise ValueError("selection schema_version invalid")
        selected = []
        seen = set()
        for item in _list(data, "selected"):
            _exact_keys(item, {"dimension", "field", "evidence"}, "selected field")
            dimension = ApplicabilityDimension(item["dimension"])
            definition = self._registry.resolve(dimension, _nonempty_string(item, "field"))
            evidence = _evidence(item, source_ids=source_ids)
            if not evidence:
                raise ValueError("selected field 必须有 evidence")
            if definition.key in seen:
                raise ValueError("duplicate selected field")
            seen.add(definition.key)
            selected.append(SelectedField(dimension.value, definition.field_id, evidence))
        return SelectionTrace(tuple(selected), _strings(data, "warnings"), self._provider.identity, prompt)


class ContextFactInterpreter(_InterpreterBase):
    """Interpret one immutable selected context field."""

    def interpret_one(self, source: str, selected: SelectedField, *, source_id: str = "current-query") -> ContextFactTrace:
        dimension = ApplicabilityDimension(selected.dimension)
        definition = self._registry.resolve(dimension, selected.field)
        task = SemanticInterpretationTask(
            InterpretationTaskKind.CONTEXT_FACT_INTERPRETATION, "context_fact_interpretation_v1", 1,
            "Interpret only the supplied field. Do not create or rename fields. UNKNOWN needs explicit relevance; AMBIGUOUS needs at least two supported values.",
            {"field": definition.prompt_spec(), "source": {"source_id": source_id, "text": source},
             "selected_evidence": [e.evidence_ref for e in selected.evidence]},
            _conditioned_context_schema(self._registry), "context_fact_interpretation_v1",
        )
        return self._validated_call(task, lambda data: self._parse_fact(data, selected, {source_id}))

    def _parse_fact(self, data: Mapping[str, Any], selected: SelectedField, source_ids: set[str]) -> ContextFactTrace:
        _exact_keys(data, {"schema_version", "dimension", "field", "state", "values", "evidence", "warnings"}, "conditioned context")
        if data["dimension"] != selected.dimension or data["field"] != selected.field:
            raise ValueError("conditioned context field immutable")
        item = {k: data[k] for k in ("dimension", "field", "state", "values", "evidence")}
        value = _context_value(item, self._registry)
        evidence = _evidence(item, source_ids=source_ids)
        if value.state.value == "KNOWN" and not evidence:
            raise ValueError("KNOWN requires evidence")
        if value.state.value == "AMBIGUOUS" and len(value.values) < 2:
            raise ValueError("AMBIGUOUS requires two values")
        return ContextFactTrace(value, evidence, tuple(_string_list(item, "values")))


class ScopeFieldSelector(_InterpreterBase):
    """Select qualifier axes, never preference targets or scope states."""

    def select(self, snapshot: MemorySnapshot) -> SelectionTrace:
        revision = snapshot.revision
        allowed_refs = {f"revision:{revision.revision_id}", *(f"metadata:{n}" for n in revision.semantic_metadata.to_dict()), *(f"provenance:{p.provenance_id}" for p in snapshot.provenance)}
        task = SemanticInterpretationTask(
            InterpretationTaskKind.SCOPE_FIELD_SELECTION, "memory_scope_field_selection_v1", 1,
            "Select only evidence-backed applicability qualifier axes. Preference targets and metadata mentions alone are not axes; bare preferences produce zero rules.",
            {"ontology_version": self._registry.version, "field_registry": self._registry.prompt_specs(), "memory_id": snapshot.memory_id, "content": revision.content, "semantic_metadata": revision.semantic_metadata.to_dict(), "allowed_evidence_refs": sorted(allowed_refs)},
            _selection_schema(self._registry), "memory_scope_field_selection_v1",
        )
        return self._validated_call(task, lambda data: self._parse_scope_selection(data, allowed_refs))

    def _parse_scope_selection(self, data: Mapping[str, Any], allowed_refs: set[str]) -> SelectionTrace:
        _exact_keys(data, {"schema_version", "selected", "warnings"}, "scope selection")
        if data["schema_version"] != 1:
            raise ValueError("selection schema_version invalid")
        selected, seen = [], set()
        for item in _list(data, "selected"):
            _exact_keys(item, {"dimension", "field", "evidence"}, "selected scope field")
            dimension = ApplicabilityDimension(item["dimension"])
            definition = self._registry.resolve(dimension, _nonempty_string(item, "field"))
            evidence = _evidence(item, allowed_refs=allowed_refs)
            if not evidence or definition.key in seen:
                raise ValueError("scope selection evidence/duplicate invalid")
            seen.add(definition.key)
            selected.append(SelectedField(dimension.value, definition.field_id, evidence))
        return SelectionTrace(tuple(selected), _strings(data, "warnings"), self._provider.identity, "memory_scope_field_selection_v1")


class ScopeRuleInterpreter(_InterpreterBase):
    """Interpret state/value only for an immutable selected qualifier axis."""

    def interpret_one(self, snapshot: MemorySnapshot, selected: SelectedField) -> ScopeRuleTrace:
        revision = snapshot.revision
        allowed_refs = {f"revision:{revision.revision_id}", *(f"metadata:{n}" for n in revision.semantic_metadata.to_dict()), *(f"provenance:{p.provenance_id}" for p in snapshot.provenance)}
        task = SemanticInterpretationTask(
            InterpretationTaskKind.SCOPE_RULE_INTERPRETATION, "memory_scope_rule_interpretation_v1", 1,
            "Interpret only the supplied qualifier axis. Do not switch fields. Distinguish constraint, explicit general, and evidence-backed unspecified.",
            {"field": self._registry.resolve(ApplicabilityDimension(selected.dimension), selected.field).prompt_spec(), "memory_id": snapshot.memory_id, "content": revision.content, "selected_evidence": [e.evidence_ref for e in selected.evidence]},
            _conditioned_scope_schema(self._registry), "memory_scope_rule_interpretation_v1",
        )
        return self._validated_call(task, lambda data: self._parse_scope_rule(data, selected, snapshot.memory_id, allowed_refs, revision.content))

    def _parse_scope_rule(self, data: Mapping[str, Any], selected: SelectedField, memory_id: str, allowed_refs: set[str], content: str) -> ScopeRuleTrace:
        _exact_keys(data, {"schema_version", "dimension", "field", "scope_state", "values", "operator", "evidence", "warnings"}, "conditioned scope")
        if data["dimension"] != selected.dimension or data["field"] != selected.field:
            raise ValueError("conditioned scope field immutable")
        item = {k: data[k] for k in ("dimension", "field", "scope_state", "values", "operator", "evidence")}
        evidence = _evidence(item, allowed_refs=allowed_refs)
        rule = _scope_rule(item, tuple(e.evidence_ref for e in evidence), self._registry)
        if not evidence:
            raise ValueError("scope rule requires evidence")
        if rule.scope_state.value == "EXPLICIT_GENERAL" and not _has_explicit_general_evidence(content):
            raise ValueError("general scope requires explicit evidence")
        return ScopeRuleTrace(rule, evidence, tuple(_string_list(item, "values")))


class ContextInterpreter(_InterpreterBase):
    def __init__(self, provider: SemanticInterpretationProvider, *, registry: ApplicabilityFieldRegistry = DEFAULT_APPLICABILITY_FIELD_REGISTRY, validation_attempts: int = 2, staged: bool = False) -> None:
        super().__init__(provider, registry=registry, validation_attempts=validation_attempts)
        self._staged = staged

    def interpret(
        self,
        query: str,
        *,
        query_id: str = "current-query",
        recent_context: Sequence[ContextSource] = (),
    ) -> ContextInterpretation:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query 不能为空")
        if self._staged:
            selector = ContextFieldSelector(self._provider, registry=self._registry, validation_attempts=self._validation_attempts)
            selected = selector.select(query, query_id=query_id, recent_context=recent_context)
            fact_interpreter = ContextFactInterpreter(self._provider, registry=self._registry, validation_attempts=self._validation_attempts)
            source_text_by_id = {
                query_id: query,
                **{item.source_id: item.text for item in recent_context},
            }
            facts = tuple(
                fact_interpreter.interpret_one(
                    source_text_by_id[
                        item.evidence[0].evidence_ref.removeprefix("source:").split("#", 1)[0]
                    ],
                    item,
                    source_id=item.evidence[0].evidence_ref.removeprefix("source:").split("#", 1)[0],
                )
                for item in selected.selected
            )
            return ContextInterpretation(ApplicabilityContext(tuple(item.value for item in facts)), facts, selected.warnings, self._provider.identity, "context_field_selection_v1+context_fact_interpretation_v1", selection_trace=selected)
        sources = (ContextSource(query_id, query), *tuple(recent_context))
        source_ids = [source.source_id for source in sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("context source_id 不能重复")
        task = SemanticInterpretationTask(
            kind=InterpretationTaskKind.CURRENT_CONTEXT,
            prompt_version=CONTEXT_PROMPT_VERSION,
            schema_version=INTERPRETATION_SCHEMA_VERSION,
            system_prompt=CONTEXT_SYSTEM_PROMPT,
            payload={
                "ontology_version": self._registry.version,
                "field_registry": self._registry.prompt_specs(),
                "sources": [{"source_id": source.source_id, "text": source.text} for source in sources],
            },
            output_schema=_context_output_schema(self._registry),
            schema_name="context_interpretation_v2",
        )
        return self._validated_call(task, lambda data: self._parse(data, {item.source_id for item in sources}))

    def _parse(self, data: Mapping[str, Any], source_ids: set[str]) -> ContextInterpretation:
        _exact_keys(data, {"schema_version", "values", "warnings"}, "context output")
        _schema_version(data)
        facts = []
        for item in _list(data, "values"):
            _exact_keys(item, {"dimension", "field", "state", "values", "evidence"}, "context fact")
            raw_values = _string_list(item, "values")
            value = _context_value(item, self._registry)
            evidence = _evidence(item, source_ids=source_ids)
            if value.state.value != "UNKNOWN" and not evidence:
                raise ValueError("KNOWN/AMBIGUOUS context fact 必须有 evidence")
            facts.append(ContextFactTrace(value, evidence, raw_values))
        context = ApplicabilityContext(tuple(fact.value for fact in facts))
        return ContextInterpretation(
            context=context,
            facts=tuple(facts),
            warnings=_strings(data, "warnings"),
            provider=self._provider.identity,
        )


class MemoryScopeInterpreter(_InterpreterBase):
    def __init__(self, provider: SemanticInterpretationProvider, *, registry: ApplicabilityFieldRegistry = DEFAULT_APPLICABILITY_FIELD_REGISTRY, validation_attempts: int = 2, staged: bool = False) -> None:
        super().__init__(provider, registry=registry, validation_attempts=validation_attempts)
        self._staged = staged

    def interpret(self, snapshot: MemorySnapshot) -> MemoryScopeInterpretation:
        revision = snapshot.revision
        if self._staged:
            selected = ScopeFieldSelector(self._provider, registry=self._registry, validation_attempts=self._validation_attempts).select(snapshot)
            rule_interpreter = ScopeRuleInterpreter(self._provider, registry=self._registry, validation_attempts=self._validation_attempts)
            rules = tuple(rule_interpreter.interpret_one(snapshot, item) for item in selected.selected)
            return MemoryScopeInterpretation(MemoryApplicabilityProfile(snapshot.memory_id, tuple(item.rule for item in rules)), rules, selected.warnings, self._provider.identity, "memory_scope_field_selection_v1+memory_scope_rule_interpretation_v1", selection_trace=selected)
        allowed_refs = {
            f"revision:{revision.revision_id}",
            *(f"metadata:{name}" for name in revision.semantic_metadata.to_dict()),
            *(f"provenance:{item.provenance_id}" for item in snapshot.provenance),
        }
        task = SemanticInterpretationTask(
            kind=InterpretationTaskKind.MEMORY_SCOPE,
            prompt_version=SCOPE_PROMPT_VERSION,
            schema_version=INTERPRETATION_SCHEMA_VERSION,
            system_prompt=SCOPE_SYSTEM_PROMPT,
            payload={
                "ontology_version": self._registry.version,
                "field_registry": self._registry.prompt_specs(),
                "memory_id": snapshot.memory_id,
                "revision_id": revision.revision_id,
                "content": revision.content,
                "semantic_metadata": revision.semantic_metadata.to_dict(),
                "provenance": [
                    {"provenance_id": item.provenance_id, "authority": item.authority.value}
                    for item in snapshot.provenance
                ],
            },
            output_schema=_scope_output_schema(self._registry),
            schema_name="memory_scope_interpretation_v2",
        )
        return self._validated_call(
            task,
            lambda data: self._parse(
                data, snapshot.memory_id, allowed_refs, revision.content
            ),
        )

    def _parse(
        self,
        data: Mapping[str, Any],
        memory_id: str,
        allowed_refs: set[str],
        revision_content: str,
    ) -> MemoryScopeInterpretation:
        _exact_keys(data, {"schema_version", "memory_id", "rules", "warnings"}, "scope output")
        _schema_version(data)
        if not isinstance(data["memory_id"], str) or data["memory_id"] != memory_id:
            raise ValueError("scope output memory_id mismatch")
        traces = []
        for item in _list(data, "rules"):
            _exact_keys(
                item,
                {"dimension", "field", "scope_state", "values", "operator", "evidence"},
                "scope rule",
            )
            evidence = _evidence(item, allowed_refs=allowed_refs)
            raw_values = _string_list(item, "values")
            rule = _scope_rule(
                item, tuple(entry.evidence_ref for entry in evidence), self._registry
            )
            if rule.scope_state.value != "UNSPECIFIED" and not evidence:
                raise ValueError("explicit scope rule 必须有 evidence")
            if (
                rule.scope_state.value == "EXPLICIT_GENERAL"
                and not _has_explicit_general_evidence(revision_content)
            ):
                raise ValueError("EXPLICIT_GENERAL 缺少明确 universal/general 文本证据")
            traces.append(ScopeRuleTrace(rule, evidence, raw_values))
        profile = MemoryApplicabilityProfile(memory_id, tuple(trace.rule for trace in traces))
        return MemoryScopeInterpretation(
            profile=profile,
            rules=tuple(traces),
            warnings=_strings(data, "warnings"),
            provider=self._provider.identity,
        )


def _exact_keys(data: Mapping[str, Any], expected: set[str], label: str) -> None:
    if not isinstance(data, Mapping) or set(data) != expected:
        raise ValueError(f"{label} fields invalid")


def _schema_version(data: Mapping[str, Any]) -> None:
    if data["schema_version"] != INTERPRETATION_SCHEMA_VERSION:
        raise ValueError("interpretation schema_version invalid")


def _list(data: Mapping[str, Any], key: str) -> list[Any]:
    value = data[key]
    if not isinstance(value, list):
        raise ValueError(f"{key} 必须是数组")
    return value


def _strings(data: Mapping[str, Any], key: str) -> Tuple[str, ...]:
    values = _list(data, key)
    if not all(isinstance(value, str) for value in values):
        raise ValueError(f"{key} 必须是字符串数组")
    return tuple(values)


def _context_value(
    item: Mapping[str, Any], registry: ApplicabilityFieldRegistry
) -> ContextValue:
    from src.memory.retrieval.applicability_models import ApplicabilityDimension, ContextKnowledgeState

    dimension = ApplicabilityDimension(item["dimension"])
    definition, values = registry.normalize_values(
        dimension, _nonempty_string(item, "field"), _string_list(item, "values")
    )
    return ContextValue(
        dimension=dimension,
        field=definition.field_id,
        state=ContextKnowledgeState(item["state"]),
        values=values,
    )


def _scope_rule(
    item: Mapping[str, Any],
    evidence_refs: Tuple[str, ...],
    registry: ApplicabilityFieldRegistry,
) -> CandidateScopeRule:
    from src.memory.retrieval.applicability_models import (
        ApplicabilityDimension,
        CandidateScopeState,
        ConstraintOperator,
    )

    dimension = ApplicabilityDimension(item["dimension"])
    definition, values = registry.normalize_values(
        dimension, _nonempty_string(item, "field"), _string_list(item, "values")
    )
    return CandidateScopeRule(
        dimension=dimension,
        field=definition.field_id,
        scope_state=CandidateScopeState(item["scope_state"]),
        values=values,
        operator=ConstraintOperator(item["operator"]),
        evidence_refs=evidence_refs,
    )


def _evidence(
    item: Mapping[str, Any], *, source_ids: set[str] | None = None, allowed_refs: set[str] | None = None
) -> Tuple[InterpretationEvidence, ...]:
    entries = _list(item, "evidence")
    result = []
    for entry in entries:
        _exact_keys(entry, {"evidence_ref", "kind", "reason"}, "evidence")
        ref = entry["evidence_ref"]
        if not isinstance(ref, str) or not ref:
            raise ValueError("evidence_ref 必须是非空字符串")
        if source_ids is not None:
            raw_source_ref = ref.removeprefix("source:")
            source_id = raw_source_ref.split("#", 1)[0]
            if source_id not in source_ids or (
                not ref.startswith("source:") and not any(ref.startswith(f"{item}#") or ref == item for item in source_ids)
            ):
                raise ValueError("context evidence_ref 未指向 supplied source")
            ref = f"source:{raw_source_ref}"
        if allowed_refs is not None:
            if ref not in allowed_refs:
                suffix_matches = [item for item in allowed_refs if item.split(":", 1)[1] == ref]
                if len(suffix_matches) != 1:
                    raise ValueError("scope evidence_ref 未指向 supplied memory evidence")
                ref = suffix_matches[0]
        reason = _nonempty_string(entry, "reason")
        result.append(InterpretationEvidence(ref, EvidenceKind(entry["kind"]), reason))
    return tuple(result)


def _nonempty_string(data: Mapping[str, Any], key: str) -> str:
    value = data[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} 必须是非空字符串")
    return value


def _string_list(data: Mapping[str, Any], key: str) -> Tuple[str, ...]:
    values = _list(data, key)
    if not all(isinstance(value, str) and value.strip() for value in values):
        raise ValueError(f"{key} 必须是非空字符串数组")
    return tuple(values)


def _has_explicit_general_evidence(content: str) -> bool:
    normalized = content.casefold()
    markers = (
        "regardless", "no matter", "anywhere", "anyone", "every ", "always",
        "不管", "无论", "任何", "所有", "每个", "总是",
    )
    return any(marker in normalized for marker in markers)
