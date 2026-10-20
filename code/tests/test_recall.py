from __future__ import annotations

import socket

import pytest

from src.memory.core.schema import (
    ChangeKind,
    Granularity,
    MemoryType,
    ProvenanceAuthority,
    RetentionDisposition,
    SemanticMetadata,
)
from src.memory.retrieval.metrics import recall_at_k, reciprocal_rank
from src.memory.retrieval.recall import MemoryRecallEngine
from src.memory.retrieval.tokenizer import tokenize
from src.memory.storage.sqlite_manager import SQLiteMemoryManager


def _add(repository, memory_id, content, *, metadata=None, authority=ProvenanceAuthority.EXPLICIT_USER):
    event, _ = repository.register_ingestion_event(content, event_id=f"event-{memory_id}")
    repository.create_memory(
        memory_id=memory_id,
        content=content,
        memory_type=MemoryType.PREFERENCE_RULES,
        granularity=Granularity.SEMANTIC_INSIGHT,
        semantic_metadata=metadata or SemanticMetadata(),
        event_ids=(event.event_id,),
        authority=authority,
    )


@pytest.fixture
def repository(tmp_path):
    return SQLiteMemoryManager(str(tmp_path / "recall.db"))


def test_multilingual_tokenizer_is_normalized_and_deterministic():
    assert tokenize("ＡＰＩ Hotel 123") == ("api", "hotel", "123")
    assert tokenize("上海") == ("上", "海", "上海")
    assert tokenize("上海") == tokenize("上海")


def test_current_safe_recall_excludes_recycled_and_historical_text(repository):
    _add(repository, "active", "当前酒店需要隔音好")
    _add(repository, "recycled", "唯一回收关键词")
    repository.set_retention("recycled", RetentionDisposition.RECYCLED)
    _add(repository, "evolved", "旧版只喜欢非常安静的郊区酒店")
    event, _ = repository.register_ingestion_event("新版热闹区域也可以", event_id="event-evolved-new")
    repository.apply_revision(
        "evolved", "新版热闹区域也可以", change_kind=ChangeKind.UPDATE,
        event_ids=(event.event_id,), authority=ProvenanceAuthority.EXPLICIT_USER,
    )
    engine = MemoryRecallEngine(repository)

    assert not engine.recall("唯一回收关键词").candidates
    result = engine.recall("非常安静的郊区酒店")
    assert all(candidate.snapshot.revision.revision_no == 2 for candidate in result.candidates if candidate.snapshot.memory_id == "evolved")
    assert all("旧版只喜欢" not in candidate.snapshot.revision.content for candidate in result.candidates)


def test_strong_match_beats_weak_match_and_zero_scores_are_excluded(repository):
    _add(repository, "strong", "上海上海住宿出行方便")
    _add(repository, "weak", "上海饮食")
    _add(repository, "none", "周末跑步")
    result = MemoryRecallEngine(repository).recall("上海住宿出行", top_k=10)
    ids = [candidate.snapshot.memory_id for candidate in result.candidates]
    assert ids[0] == "strong"
    assert "none" not in ids
    assert result.candidates[0].score_breakdown.content_bm25 > 0


def test_metadata_overlap_is_separate_and_traceable(repository):
    _add(
        repository,
        "metadata-only",
        "我通常喜欢住市中心",
        metadata=SemanticMetadata(locations=("上海",), topics=("住宿",)),
    )
    candidate = MemoryRecallEngine(repository).recall("上海", top_k=3).candidates[0]
    assert candidate.score_breakdown.content_bm25 == 0
    assert candidate.score_breakdown.metadata_overlap > 0
    assert candidate.matched_metadata_fields == (("locations", ("上", "海", "上海")),)


def test_ties_use_memory_id_and_top_k_is_bounded(repository):
    _add(repository, "memory-b", "相同文本")
    _add(repository, "memory-a", "相同文本")
    result = MemoryRecallEngine(repository).recall("相同文本", top_k=1)
    assert [candidate.snapshot.memory_id for candidate in result.candidates] == ["memory-a"]
    assert result.candidates[0].rank == 1


@pytest.mark.parametrize("query,top_k,warning", [
    ("", 8, "empty_query"),
    ("   ", 8, "empty_query"),
    ("query", 0, "non_positive_top_k"),
    ("query", -1, "non_positive_top_k"),
])
def test_edge_inputs_return_empty(repository, query, top_k, warning):
    _add(repository, "memory", "query")
    result = MemoryRecallEngine(repository).recall(query, top_k=top_k)
    assert not result.candidates
    assert warning in result.warnings


def test_empty_current_corpus_returns_explicit_warning(repository):
    result = MemoryRecallEngine(repository).recall("query")
    assert result.corpus_size == 0
    assert not result.candidates
    assert result.warnings == ("empty_current_corpus",)


def test_recall_preserves_provenance_and_does_not_rank_by_authority(repository):
    _add(repository, "explicit", "共同关键词", authority=ProvenanceAuthority.EXPLICIT_USER)
    _add(repository, "inferred", "共同关键词", authority=ProvenanceAuthority.INFERRED)
    result = MemoryRecallEngine(repository).recall("共同关键词")
    assert [candidate.snapshot.memory_id for candidate in result.candidates] == ["explicit", "inferred"]
    assert result.candidates[0].recall_score == result.candidates[1].recall_score
    assert result.candidates[0].snapshot.provenance[0].authority is ProvenanceAuthority.EXPLICIT_USER
    assert result.candidates[1].snapshot.provenance[0].authority is ProvenanceAuthority.INFERRED


def test_recall_is_read_only_and_has_no_network_path(repository, monkeypatch):
    _add(repository, "memory", "上海住宿")
    with repository._get_connection() as connection:
        before = tuple(connection.iterdump())

    def forbidden_network(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "create_connection", forbidden_network)
    engine = MemoryRecallEngine(repository)
    for _ in range(3):
        assert engine.recall("上海").candidates
    with repository._get_connection() as connection:
        after = tuple(connection.iterdump())
    assert before == after


def test_memory_type_filter_is_explicit_recall_filter(repository):
    _add(repository, "preference", "共同词")
    event, _ = repository.register_ingestion_event("共同词", event_id="event-profile")
    repository.create_memory(
        memory_id="profile", content="共同词", memory_type=MemoryType.CORE_PROFILE,
        granularity=Granularity.SEMANTIC_INSIGHT, event_ids=(event.event_id,),
    )
    result = MemoryRecallEngine(repository).recall(
        "共同词", memory_types=(MemoryType.CORE_PROFILE,)
    )
    assert [candidate.snapshot.memory_id for candidate in result.candidates] == ["profile"]
    assert result.corpus_size == 1


def test_metric_formulas_cover_multiple_targets():
    ranked = ("a", "x", "b", "c")
    assert recall_at_k(ranked, {"a", "b", "missing"}, 3) == pytest.approx(2 / 3)
    assert reciprocal_rank(ranked, {"b", "c"}) == pytest.approx(1 / 3)
