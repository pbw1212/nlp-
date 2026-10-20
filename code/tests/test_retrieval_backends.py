from __future__ import annotations

import socket
from dataclasses import asdict

import pytest

from src.memory.core.schema import (
    ChangeKind,
    Granularity,
    MemoryType,
    RetentionDisposition,
)
from src.memory.llm.errors import ProviderResponseError
from src.memory.retrieval.dense import DenseEmbeddingRetriever
from src.memory.retrieval.embedding import EmbeddingProviderIdentity, FastEmbedEmbeddingProvider
from src.memory.retrieval.hybrid import HybridRRFMemoryRetriever
from src.memory.retrieval.models import RecallCandidate, RecallRequest, RecallResult, RecallScoreBreakdown
from src.memory.retrieval.recall import MemoryRecallEngine
from src.memory.storage.sqlite_manager import SQLiteMemoryManager


class FakeEmbeddingProvider:
    def __init__(self, vectors, *, dimension=3):
        self.vectors = vectors
        self._identity = EmbeddingProviderIdentity(
            provider="fake-test-only",
            provider_version="1",
            model="fixture-v1",
            dimension=dimension,
            normalization="l2",
            batch_size=8,
        )

    @property
    def identity(self):
        return self._identity

    def embed_query(self, text):
        return tuple(self.vectors[text])

    def embed_documents(self, texts):
        return tuple(tuple(self.vectors[text]) for text in texts)


def _add(repository, memory_id, content):
    event, _ = repository.register_ingestion_event(content, event_id=f"event-{memory_id}")
    repository.create_memory(
        memory_id=memory_id,
        content=content,
        memory_type=MemoryType.PREFERENCE_RULES,
        granularity=Granularity.SEMANTIC_INSIGHT,
        event_ids=(event.event_id,),
    )


@pytest.fixture
def repository(tmp_path):
    return SQLiteMemoryManager(str(tmp_path / "dense.db"))


def test_dense_cosine_similarity_order_zero_exclusion_and_stable_tie(repository):
    _add(repository, "memory-b", "same-b")
    _add(repository, "memory-a", "same-a")
    _add(repository, "orthogonal", "orthogonal")
    provider = FakeEmbeddingProvider({
        "query": (1, 0, 0),
        "same-a": (1, 0, 0),
        "same-b": (2, 0, 0),
        "orthogonal": (0, 1, 0),
    })
    result = DenseEmbeddingRetriever(repository, provider).recall("query", top_k=8)
    assert [candidate.snapshot.memory_id for candidate in result.candidates] == ["memory-a", "memory-b"]
    assert result.candidates[0].recall_score == pytest.approx(1.0)
    assert result.candidates[0].score_breakdown.dense_similarity == pytest.approx(1.0)
    assert result.candidates[0].score_breakdown.content_bm25 == 0


@pytest.mark.parametrize(
    "bad_vector,dimension",
    [((0, 0, 0), 3), ((1, 2), 3), ((float("nan"), 0, 1), 3)],
)
def test_dense_rejects_zero_invalid_or_dimension_mismatched_vectors(repository, bad_vector, dimension):
    _add(repository, "memory", "document")
    provider = FakeEmbeddingProvider({"query": (1, 0, 0), "document": bad_vector}, dimension=dimension)
    with pytest.raises(ProviderResponseError):
        DenseEmbeddingRetriever(repository, provider).recall("query")


def test_dense_current_safe_excludes_recycled_and_historical(repository):
    _add(repository, "active", "active-current")
    _add(repository, "recycled", "recycled-current")
    repository.set_retention("recycled", RetentionDisposition.RECYCLED)
    _add(repository, "evolved", "historical-only")
    event, _ = repository.register_ingestion_event("evolved-current", event_id="event-evolved-v2")
    repository.apply_revision("evolved", "evolved-current", change_kind=ChangeKind.UPDATE, event_ids=(event.event_id,))
    provider = FakeEmbeddingProvider({
        "query": (1, 0, 0),
        "active-current": (1, 0, 0),
        "evolved-current": (1, 0, 0),
    })
    result = DenseEmbeddingRetriever(repository, provider).recall("query")
    assert [candidate.snapshot.memory_id for candidate in result.candidates] == ["active", "evolved"]
    assert all(candidate.snapshot.revision.content != "historical-only" for candidate in result.candidates)


def test_dense_is_read_only_and_default_path_has_no_network(repository, monkeypatch):
    _add(repository, "memory", "document")
    provider = FakeEmbeddingProvider({"query": (1, 0, 0), "document": (1, 0, 0)})
    with repository._get_connection() as connection:
        before = tuple(connection.iterdump())
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    DenseEmbeddingRetriever(repository, provider).recall("query")
    with repository._get_connection() as connection:
        assert before == tuple(connection.iterdump())


def _candidate(snapshot, rank, score, *, dense=False):
    return RecallCandidate(
        snapshot=snapshot,
        rank=rank,
        recall_score=score,
        score_breakdown=RecallScoreBreakdown(
            0.0 if dense else score,
            0.0,
            dense_similarity=score if dense else None,
        ),
        matched_terms=() if dense else ("term",),
        matched_metadata_fields=(),
    )


class StubRetriever:
    def __init__(self, method, candidates):
        self.method = method
        self.candidates = candidates

    def recall_request(self, request):
        candidates = tuple(self.candidates[: request.top_k])
        return RecallResult(request.query, self.method, len(self.candidates), candidates)


def test_rrf_formula_duplicate_fusion_and_deterministic_ranking(repository):
    _add(repository, "a", "alpha")
    _add(repository, "b", "beta")
    snapshots = {item.memory_id: item for item in repository.list_current()}
    lexical = StubRetriever("lexical", (_candidate(snapshots["a"], 1, 9), _candidate(snapshots["b"], 2, 4)))
    dense = StubRetriever("dense", (_candidate(snapshots["b"], 1, .9, dense=True), _candidate(snapshots["a"], 2, .8, dense=True)))
    result = HybridRRFMemoryRetriever(lexical, dense, rrf_k=60, candidate_limit=2).recall_request(
        RecallRequest("query", 2)
    )
    assert [candidate.snapshot.memory_id for candidate in result.candidates] == ["a", "b"]
    assert result.candidates[0].recall_score == pytest.approx(1 / 61 + 1 / 62)
    assert result.candidates[0].score_breakdown.content_bm25 == 9
    assert result.candidates[0].score_breakdown.dense_similarity == pytest.approx(.8)
    assert result.candidates[0].score_breakdown.fusion_rrf == result.candidates[0].recall_score


def test_hybrid_does_not_mutate_child_scores_or_apply_applicability(repository):
    _add(repository, "memory", "alpha")
    snapshot = repository.list_current()[0]
    lexical_candidate = _candidate(snapshot, 1, 2.5)
    dense_candidate = _candidate(snapshot, 1, .75, dense=True)
    result = HybridRRFMemoryRetriever(
        StubRetriever("lexical", (lexical_candidate,)),
        StubRetriever("dense", (dense_candidate,)),
        candidate_limit=1,
    ).recall_request(RecallRequest("query", 1))
    assert lexical_candidate.recall_score == 2.5
    assert dense_candidate.recall_score == .75
    assert result.candidates[0].recall_score != 2.5 * .75


def test_phase2a_score_breakdown_constructor_and_serialization_remain_compatible():
    breakdown = RecallScoreBreakdown(1.25, .35)
    assert breakdown.total == pytest.approx(1.6)
    payload = asdict(breakdown)
    assert payload["content_bm25"] == 1.25
    assert payload["dense_similarity"] is None
    assert payload["fusion_rrf"] is None


def test_fastembed_missing_dependency_is_typed(monkeypatch):
    import builtins

    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "fastembed":
            raise ImportError("blocked in test")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    from src.memory.llm.errors import ProviderConfigurationError

    with pytest.raises(ProviderConfigurationError):
        FastEmbedEmbeddingProvider()


def test_lexical_baseline_method_and_scores_are_unchanged(repository):
    _add(repository, "memory", "上海住宿")
    result = MemoryRecallEngine(repository).recall("上海")
    assert result.method == "lexical_bm25_v1"
    assert result.candidates[0].score_breakdown.dense_similarity is None
    assert result.candidates[0].score_breakdown.fusion_rrf is None
