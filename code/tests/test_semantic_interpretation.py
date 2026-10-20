from __future__ import annotations

import socket

import pytest

from src.memory.core.config import MemoryConfig, ProviderMode
from src.memory.core.schema import Granularity, MemoryType, SemanticMetadata
from src.memory.llm.errors import ProviderConfigurationError, ProviderRequestError, ProviderResponseError
from src.memory.retrieval.applicability import ApplicabilityEngine
from src.memory.retrieval.applicability_models import ApplicabilityStatus, CandidateScopeState, ContextKnowledgeState
from src.memory.retrieval.interpretation_models import InterpretationProviderIdentity
from src.memory.retrieval.interpretation_provider import build_semantic_interpretation_provider
from src.memory.retrieval.interpreters import (
    ContextInterpreter, ContextFieldSelector, ContextFactInterpreter,
    MemoryScopeInterpreter, ScopeFieldSelector, ScopeRuleInterpreter,
)
from src.memory.retrieval.dense import DenseEmbeddingRetriever
from src.memory.retrieval.embedding import EmbeddingProviderIdentity
from src.memory.retrieval.hybrid import HybridRRFMemoryRetriever
from src.memory.retrieval.recall import MemoryRecallEngine
from src.memory.retrieval.semantic_pipeline import SemanticRetrievalPipeline
from src.memory.storage.sqlite_manager import SQLiteMemoryManager


class FakeSemanticInterpretationProvider:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.tasks = []
        self._identity = InterpretationProviderIdentity("fake-test-only", "fixture-v1")

    @property
    def identity(self):
        return self._identity

    def interpret(self, task):
        self.tasks.append(task)
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return output


class FakeEmbeddingProvider:
    identity = EmbeddingProviderIdentity(
        provider="fake-test-only", provider_version="1", model="fixture-v1",
        dimension=2, normalization="l2", batch_size=8,
    )

    def embed_query(self, _text):
        return (1.0, 0.0)

    def embed_documents(self, texts):
        return tuple((1.0, 0.0) for _ in texts)


def context_output(*values, warnings=()):
    return {"schema_version": 1, "values": list(values), "warnings": list(warnings)}


def context_fact(dimension, field, state, values=(), *, source="q", kind="EXPLICIT"):
    evidence = [] if state == "UNKNOWN" else [{
        "evidence_ref": f"source:{source}", "kind": kind, "reason": "supported by source"
    }]
    return {
        "dimension": dimension, "field": field, "state": state,
        "values": list(values), "evidence": evidence,
    }


def scope_output(memory_id, *rules, warnings=()):
    return {
        "schema_version": 1, "memory_id": memory_id,
        "rules": list(rules), "warnings": list(warnings),
    }


def scope_rule(dimension, field, state, values=(), *, operator="REQUIRES", revision="rev"):
    evidence = [] if state == "UNSPECIFIED" else [{
        "evidence_ref": f"revision:{revision}", "kind": "EXPLICIT", "reason": "explicit phrase"
    }]
    return {
        "dimension": dimension, "field": field, "scope_state": state,
        "values": list(values), "operator": operator, "evidence": evidence,
    }


def selected_field(dimension, field, *, ref="q"):
    return {"dimension": dimension, "field": field, "evidence": [{
        "evidence_ref": f"source:{ref}", "kind": "EXPLICIT", "reason": "qualifier evidence"
    }]}


def conditioned_context(dimension, field, state, values=(), *, ref="q"):
    return {"schema_version": 1, "dimension": dimension, "field": field,
            "state": state, "values": list(values),
            "evidence": [] if state == "UNKNOWN" else [{"evidence_ref": f"source:{ref}", "kind": "EXPLICIT", "reason": "fact evidence"}],
            "warnings": []}


def conditioned_scope(dimension, field, state, values=(), *, revision="rev"):
    return {"schema_version": 1, "dimension": dimension, "field": field,
            "scope_state": state, "values": list(values), "operator": "REQUIRES",
            "evidence": [{"evidence_ref": f"revision:{revision}", "kind": "EXPLICIT", "reason": "scope evidence"}],
            "warnings": []}


def _snapshot(tmp_path, memory_id="hotel", content="独自去上海时喜欢住市中心", metadata=None):
    repository = SQLiteMemoryManager(str(tmp_path / f"{memory_id}.db"))
    event, _ = repository.register_ingestion_event(content, event_id=f"event-{memory_id}")
    repository.create_memory(
        memory_id=memory_id, content=content,
        memory_type=MemoryType.PREFERENCE_RULES,
        granularity=Granularity.SEMANTIC_INSIGHT,
        semantic_metadata=metadata or SemanticMetadata(), event_ids=(event.event_id,),
    )
    return repository, repository.get_current(memory_id)


def test_context_explicit_unknown_ambiguous_multifield_and_evidence():
    provider = FakeSemanticInterpretationProvider([context_output(
        context_fact("PERSON", "travel_party", "KNOWN", ("friends",)),
        context_fact("LOCATION", "destination", "UNKNOWN"),
        context_fact("TASK", "trip_type", "AMBIGUOUS", ("business", "personal")),
    )])
    result = ContextInterpreter(provider).interpret("I will travel with friends for a mixed trip", query_id="q")
    assert [item.value.state for item in result.facts] == [
        ContextKnowledgeState.KNOWN, ContextKnowledgeState.UNKNOWN, ContextKnowledgeState.AMBIGUOUS
    ]
    assert result.facts[0].evidence[0].evidence_ref == "source:q"
    assert result.prompt_version == "context_interpretation_v2"


def test_staged_context_selection_and_conditioned_interpretation_are_attributable():
    provider = FakeSemanticInterpretationProvider([
        {"schema_version": 1, "selected": [selected_field("PERSON", "travel_party")], "warnings": []},
        conditioned_context("PERSON", "travel_party", "KNOWN", ("friends",)),
    ])
    result = ContextInterpreter(provider, staged=True).interpret("我和朋友去徒步", query_id="q")
    assert result.context.values[0].field == "travel_party"
    assert result.selection_trace is not None
    assert [task.kind.value for task in provider.tasks] == ["CONTEXT_FIELD_SELECTION", "CONTEXT_FACT_INTERPRETATION"]


def test_staged_scope_selector_separates_axis_from_rule(tmp_path):
    _repository, snapshot = _snapshot(tmp_path, content="我喜欢安静的房间，出差时更在意隔音")
    revision = snapshot.revision.revision_id
    provider = FakeSemanticInterpretationProvider([
        {"schema_version": 1, "selected": [selected_field("TASK", "trip_type", ref=f"revision:{revision}")], "warnings": []},
        conditioned_scope("TASK", "trip_type", "EXPLICIT_CONSTRAINT", ("business_trip",), revision=revision),
    ])
    # Scope evidence uses revision refs, so the selector fixture is adapted to
    # the same typed contract by replacing the source ref in the fake output.
    provider.outputs[0]["selected"][0]["evidence"][0]["evidence_ref"] = f"revision:{revision}"
    result = MemoryScopeInterpreter(provider, staged=True).interpret(snapshot)
    assert result.profile.rules[0].field == "trip_type"
    assert result.selection_trace is not None
    assert [task.kind.value for task in provider.tasks] == ["SCOPE_FIELD_SELECTION", "SCOPE_RULE_INTERPRETATION"]


@pytest.mark.parametrize("query", [
    "周末我和几个朋友去山里徒步", "I am traveling with colleagues", "去 Shanghai 出差，公司付款",
])
def test_context_accepts_chinese_english_and_mixed_sources(query):
    provider = FakeSemanticInterpretationProvider([context_output(
        context_fact("SCENARIO", "activity", "KNOWN", ("hiking",))
    )])
    assert ContextInterpreter(provider).interpret(query, query_id="q").context.values


def test_negation_is_preserved_as_explicit_context_value():
    provider = FakeSemanticInterpretationProvider([context_output(
        context_fact("CONSTRAINT", "parking", "KNOWN", ("not_required",))
    )])
    result = ContextInterpreter(provider).interpret("这次不开车，不需要停车位", query_id="q")
    assert result.context.values[0].values == ("not_required",)


def test_scope_constraint_general_unspecified_requires_excludes(tmp_path):
    repository, snapshot = _snapshot(
        tmp_path,
        content="无论和谁同行，在上海且公司付款时都不能有花生",
    )
    revision = snapshot.revision.revision_id
    provider = FakeSemanticInterpretationProvider([scope_output(
        "hotel",
        scope_rule("LOCATION", "destination", "EXPLICIT_CONSTRAINT", ("上海",), revision=revision),
        scope_rule("PERSON", "travel_party", "EXPLICIT_GENERAL", revision=revision),
        scope_rule("TIME", "season", "UNSPECIFIED", revision=revision),
        scope_rule("CONSTRAINT", "payment_source", "EXPLICIT_CONSTRAINT", ("company",), revision=revision),
        scope_rule("CONSTRAINT", "allergen", "EXPLICIT_CONSTRAINT", ("peanut",), operator="EXCLUDES", revision=revision),
    )])
    result = MemoryScopeInterpreter(provider).interpret(snapshot)
    states = {rule.rule.field: rule.rule.scope_state for rule in result.rules}
    assert states == {
        "destination": CandidateScopeState.EXPLICIT_CONSTRAINT,
        "travel_party": CandidateScopeState.EXPLICIT_GENERAL,
        "season": CandidateScopeState.UNSPECIFIED,
        "payment_source": CandidateScopeState.EXPLICIT_CONSTRAINT,
        "allergen": CandidateScopeState.EXPLICIT_CONSTRAINT,
    }
    assert provider.tasks[0].payload["semantic_metadata"] == snapshot.revision.semantic_metadata.to_dict()


def test_explicit_general_without_universal_evidence_is_rejected(tmp_path):
    _repository, snapshot = _snapshot(tmp_path, content="我喜欢早餐丰富的酒店")
    revision = snapshot.revision.revision_id
    output = scope_output(
        "hotel",
        scope_rule("LOCATION", "destination", "EXPLICIT_GENERAL", revision=revision),
    )
    provider = FakeSemanticInterpretationProvider([output, output])
    with pytest.raises(ProviderResponseError):
        MemoryScopeInterpreter(provider).interpret(snapshot)


def test_query_intent_does_not_require_an_invented_context_fact():
    provider = FakeSemanticInterpretationProvider([context_output()])
    result = ContextInterpreter(provider).interpret("帮我找酒店", query_id="q")
    assert result.context.values == ()
    field_schema = provider.tasks[0].output_schema["properties"]["values"]["items"]["properties"]["field"]
    assert "hotel_search" not in field_schema["enum"]


def test_provider_request_error_keeps_sanitized_rate_limit_diagnostics():
    from src.memory.llm.errors import ProviderRequestError
    error = ProviderRequestError("request failed", provider="groq", operation="chat.completions", status_code=429)
    error.error_type = "tokens"
    error.error_code = "rate_limit_exceeded"
    error.retry_after = "558"
    data = error.as_dict()
    assert data["status_code"] == 429
    assert "api_key" not in str(data).lower()


def test_metadata_mention_is_not_automatically_scope_constraint(tmp_path):
    _repository, snapshot = _snapshot(
        tmp_path,
        content="我曾在上海吃过一家餐厅",
        metadata=SemanticMetadata(locations=("上海",)),
    )
    provider = FakeSemanticInterpretationProvider([scope_output("hotel")])
    result = MemoryScopeInterpreter(provider).interpret(snapshot)
    assert result.profile.rules == ()


@pytest.mark.parametrize("bad", [
    {"schema_version": 1, "values": [], "warnings": [], "extra": True},
    context_output(context_fact("UNKNOWN_DIM", "x", "KNOWN", ("y",))),
    {"schema_version": 1, "values": []},
])
def test_malformed_unknown_enum_or_missing_field_retries_then_typed_failure(bad):
    provider = FakeSemanticInterpretationProvider([bad, bad])
    with pytest.raises(ProviderResponseError):
        ContextInterpreter(provider, validation_attempts=2).interpret("query", query_id="q")
    assert len(provider.tasks) == 2


@pytest.mark.parametrize("bad_values", ["friends", ["friends", 2], [""]])
def test_non_array_or_non_string_context_values_are_rejected(bad_values):
    bad = context_output({
        "dimension": "PERSON", "field": "travel_party", "state": "KNOWN",
        "values": bad_values,
        "evidence": [{"evidence_ref": "source:q", "kind": "EXPLICIT", "reason": "source"}],
    })
    provider = FakeSemanticInterpretationProvider([bad, bad])
    with pytest.raises(ProviderResponseError):
        ContextInterpreter(provider).interpret("query", query_id="q")


def test_duplicate_context_source_ids_are_rejected_before_provider_call():
    from src.memory.retrieval.interpretation_models import ContextSource

    provider = FakeSemanticInterpretationProvider([])
    with pytest.raises(ValueError, match="source_id"):
        ContextInterpreter(provider).interpret(
            "query", query_id="same", recent_context=(ContextSource("same", "older"),)
        )
    assert not provider.tasks


def test_unambiguous_bare_source_id_is_canonicalized():
    fact = context_fact("PERSON", "travel_party", "KNOWN", ("friends",))
    fact["evidence"][0]["evidence_ref"] = "q"
    result = ContextInterpreter(FakeSemanticInterpretationProvider([context_output(fact)])).interpret(
        "friends", query_id="q"
    )
    assert result.facts[0].evidence[0].evidence_ref == "source:q"


def test_provider_error_is_visible_and_never_falls_back():
    error = ProviderRequestError("deterministic test", provider="fake", operation="interpret")
    provider = FakeSemanticInterpretationProvider([error])
    with pytest.raises(ProviderRequestError):
        ContextInterpreter(provider).interpret("query")
    assert len(provider.tasks) == 1


def test_malformed_provider_response_is_retried_then_typed_failure():
    error = ProviderResponseError("malformed", provider="fake", operation="interpret")
    provider = FakeSemanticInterpretationProvider([error, error])
    with pytest.raises(ProviderResponseError):
        ContextInterpreter(provider, validation_attempts=2).interpret("query")
    assert len(provider.tasks) == 2


def test_real_provider_requires_explicit_required_mode_and_key():
    with pytest.raises(ProviderConfigurationError):
        build_semantic_interpretation_provider(MemoryConfig(provider_mode=ProviderMode.REQUIRED))


def test_real_provider_uses_safe_default_structured_completion_budget(monkeypatch):
    captured = {}

    class CapturingClient:
        def generate_json(self, *_args, **_kwargs):
            return {}

    def capture(config):
        captured["max_completion_tokens"] = config.llm_max_completion_tokens
        return CapturingClient()

    monkeypatch.setattr(
        "src.memory.retrieval.interpretation_provider.build_llm_client", capture
    )
    build_semantic_interpretation_provider(
        MemoryConfig(
            provider_mode=ProviderMode.REQUIRED,
            llm_api_key="test-only-key",
        )
    )
    assert captured["max_completion_tokens"] == 2048


def test_real_provider_preserves_explicit_structured_completion_budget(monkeypatch):
    captured = {}

    class CapturingClient:
        def generate_json(self, *_args, **_kwargs):
            return {}

    def capture(config):
        captured["max_completion_tokens"] = config.llm_max_completion_tokens
        return CapturingClient()

    monkeypatch.setattr(
        "src.memory.retrieval.interpretation_provider.build_llm_client", capture
    )
    build_semantic_interpretation_provider(
        MemoryConfig(
            provider_mode=ProviderMode.REQUIRED,
            llm_api_key="test-only-key",
            llm_max_completion_tokens=4096,
        )
    )
    assert captured["max_completion_tokens"] == 4096


def test_pipeline_retrieval_interpretation_applicability_is_read_only_and_score_stable(tmp_path, monkeypatch):
    repository, snapshot = _snapshot(tmp_path, content="独自去上海时喜欢住市中心")
    revision = snapshot.revision.revision_id
    provider = FakeSemanticInterpretationProvider([
        context_output(context_fact("PERSON", "travel_party", "KNOWN", ("friends",))),
        scope_output("hotel", scope_rule(
            "PERSON", "travel_party", "EXPLICIT_CONSTRAINT", ("solo",), revision=revision
        )),
    ])
    with repository._get_connection() as connection:
        before = tuple(connection.iterdump())
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    retriever = HybridRRFMemoryRetriever(
        MemoryRecallEngine(repository), DenseEmbeddingRetriever(repository, FakeEmbeddingProvider())
    )
    pipeline = SemanticRetrievalPipeline(
        retriever, ContextInterpreter(provider), MemoryScopeInterpreter(provider), ApplicabilityEngine()
    )
    result = pipeline.run("上海朋友住宿", query_id="q", top_k=1)
    assert result.recall_result.method == "hybrid_rrf_v1"
    assert result.recall_result.candidates[0].recall_score > 0
    assert result.applicability_result.assessments[0].overall is ApplicabilityStatus.MISMATCH
    assert result.applicability_result.rejected_candidates
    assert result.recall_result.candidates[0].recall_score == result.applicability_result.assessments[0].candidate.recall_score
    with repository._get_connection() as connection:
        assert before == tuple(connection.iterdump())


def test_pipeline_skips_context_api_when_recall_has_no_candidates(tmp_path):
    repository = SQLiteMemoryManager(str(tmp_path / "empty.db"))
    provider = FakeSemanticInterpretationProvider([])
    pipeline = SemanticRetrievalPipeline(
        MemoryRecallEngine(repository),
        ContextInterpreter(provider),
        MemoryScopeInterpreter(provider),
        ApplicabilityEngine(),
    )

    result = pipeline.run("东京住宿推荐", query_id="q", top_k=8)

    assert result.recall_result.candidates == ()
    assert result.scope_interpretations == ()
    assert result.applicability_result.assessments == ()
    assert provider.tasks == []
    assert result.context_interpretation.warnings == (
        "context_interpretation_skipped_no_recall_candidates",
    )


def test_pipeline_keeps_recalled_memory_when_scope_provider_response_is_invalid(tmp_path):
    repository, _snapshot_value = _snapshot(tmp_path, content="我喜欢安静的房间")

    class ScopeProviderFailure(FakeSemanticInterpretationProvider):
        def interpret(self, task):
            if task.schema_name == "memory_scope_interpretation_v2":
                raise ProviderResponseError(
                    "malformed scope response",
                    provider="fake-test-only",
                    operation="memory_scope",
                )
            return super().interpret(task)

    provider = ScopeProviderFailure([context_output()])
    pipeline = SemanticRetrievalPipeline(
        MemoryRecallEngine(repository),
        ContextInterpreter(provider),
        MemoryScopeInterpreter(provider),
        ApplicabilityEngine(),
    )

    result = pipeline.run("安静的房间", query_id="q", top_k=1)

    assert len(result.recall_result.candidates) == 1
    assert len(result.scope_interpretations) == 1
    assert result.scope_interpretations[0].profile.rules == ()
    assert result.scope_interpretations[0].warnings == (
        "scope_interpretation_unavailable",
    )
    assert any(
        warning.startswith("scope_interpretation_unavailable:")
        for warning in result.applicability_result.warnings
    )
    assert result.applicability_result.assessments


