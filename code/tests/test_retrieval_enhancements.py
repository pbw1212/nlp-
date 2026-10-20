from __future__ import annotations

from src.memory.core.schema import (
    GraphRelation,
    Granularity,
    LifecycleOperation,
    LifecycleProposal,
    MemoryType,
    RelationType,
)
from src.memory.core.config import MemoryConfig
from src.memory.api.memory_manager import LongTermMemoryManager
from src.memory.llm.llm_client import LLMClientBase
from src.memory.retrieval.graph import PersonalizedGraphMemoryRetriever
from src.memory.retrieval.models import RecallRequest
from src.memory.retrieval.recall import MemoryRecallEngine
from src.memory.retrieval.rerank import OpenAICompatibleCandidateReranker
from src.memory.storage.sqlite_manager import SQLiteMemoryManager


def _add(repository: SQLiteMemoryManager, memory_id: str, content: str) -> None:
    event, _ = repository.register_ingestion_event(content, event_id=f"event-{memory_id}")
    repository.create_memory(
        memory_id=memory_id,
        content=content,
        memory_type=MemoryType.PREFERENCE_RULES,
        granularity=Granularity.SEMANTIC_INSIGHT,
        event_ids=(event.event_id,),
    )


def test_multi_query_recall_keeps_expansion_request_local(tmp_path):
    repository = SQLiteMemoryManager(str(tmp_path / "multi-query.db"))
    _add(repository, "memory", "偏好靠窗座位")

    result = MemoryRecallEngine(repository).recall_request(
        RecallRequest("完全不相关", expanded_queries=("靠窗",))
    )

    assert [item.snapshot.memory_id for item in result.candidates] == ["memory"]
    assert result.trace.query_variants == ("完全不相关", "靠窗")
    assert "multi_query_union" in result.trace.stages


def test_personalized_graph_walk_recalls_linked_current_memory(tmp_path):
    repository = SQLiteMemoryManager(str(tmp_path / "graph.db"))
    _add(repository, "source", "上海住宿偏好")
    _add(repository, "linked", "靠窗且安静")
    source = repository.get_memory("source")
    assert source is not None
    source.graph_relations.append(GraphRelation("linked", RelationType.DERIVED_TO, 1.0))
    repository.save_operational_state(source)

    result = PersonalizedGraphMemoryRetriever(
        MemoryRecallEngine(repository), repository, iterations=8
    ).recall("上海住宿", top_k=2)

    assert {item.snapshot.memory_id for item in result.candidates} == {"source", "linked"}
    assert "personalized_graph_walk" in result.trace.stages
    assert result.candidates[-1].score_breakdown.graph_random_walk is not None


class _RerankClient(LLMClientBase):
    def generate_json(self, system_prompt, user_prompt, **kwargs):
        import json

        payload = json.loads(user_prompt)
        ids = [item["memory_id"] for item in payload["candidates"]]
        return {"ranked_memory_ids": list(reversed(ids)), "warnings": []}

    def generate_chat_json(self, messages, enable_web_search=False):
        raise AssertionError("chat path is not used")


def test_model_reranker_can_only_reorder_existing_candidates(tmp_path):
    repository = SQLiteMemoryManager(str(tmp_path / "rerank.db"))
    _add(repository, "a", "上海住宿")
    _add(repository, "b", "上海交通")
    base = MemoryRecallEngine(repository).recall("上海", top_k=2)

    result = OpenAICompatibleCandidateReranker(
        _RerankClient(), provider="fake", model="fixture"
    ).rerank("上海", base)

    assert [item.snapshot.memory_id for item in result.candidates] == ["b", "a"]
    assert result.method.endswith("+model_rerank_v1")
    assert "model_rerank" in result.trace.stages


def test_forgetting_is_disabled_by_default(tmp_path):
    manager = LongTermMemoryManager(
        config=MemoryConfig(sqlite_db_path=str(tmp_path / "forgetting.db"))
    )
    manager.sqlite_mgr.create_memory(
        memory_id="old",
        content="长期保留",
        memory_type=MemoryType.PREFERENCE_RULES,
        granularity=Granularity.SEMANTIC_INSIGHT,
        last_accessed=1.0,
    )

    result = manager.consolidation.run_periodic_consolidation()
    item = manager.sqlite_mgr.get_memory("old")

    assert result["forgetting_enabled"] is False
    assert item is not None
    assert item.retention_disposition.value == "ACTIVE"
    assert item.decay_strength == 1.0


def test_provider_lifecycle_can_explicitly_adjust_importance_without_time_decay(tmp_path):
    repository = SQLiteMemoryManager(str(tmp_path / "importance.db"))
    _add(repository, "memory", "只在项目中使用")
    event, _ = repository.register_ingestion_event("项目重要性变化", event_id="importance-event")

    repository.apply_proposals_atomic(
        [
            LifecycleProposal(
                operation=LifecycleOperation.UPDATE,
                target_memory_id="memory",
                content="只在当前项目中使用",
                importance_score=0.2,
                provenance_event_ids=(event.event_id,),
            )
        ],
        [event.event_id],
        consolidation_run_id="importance-run",
    )

    item = repository.get_memory("memory")
    assert item is not None
    assert item.importance_score == 0.2
