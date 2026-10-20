from src.memory.core.schema import (
    ActiveStatus,
    Granularity,
    MemoryItem,
    MemoryMetadata,
    MemoryType,
)
from src.memory.storage.sqlite_manager import SQLiteMemoryManager
from src.memory.storage.vector_store import InMemoryVectorStore


def test_sqlite_and_lexical_placeholder(tmp_path):
    sqlite_mgr = SQLiteMemoryManager(str(tmp_path / "storage.db"))
    lexical_store = InMemoryVectorStore()
    item = MemoryItem(
        content="User prefers Python for rapid prototyping.",
        memory_type=MemoryType.PREFERENCE_RULES,
        granularity=Granularity.SEMANTIC_INSIGHT,
        status=ActiveStatus.ACTIVE,
        metadata=MemoryMetadata(importance_score=0.9, valid_scope=["Software"]),
    )

    sqlite_mgr.save_memory(item)
    lexical_store.add_or_update(item.memory_id, item.content)

    fetched = sqlite_mgr.get_memory(item.memory_id)
    assert fetched is not None
    assert fetched.content == item.content
    assert fetched.metadata.importance_score == 0.9
    assert lexical_store.retrieval_kind == "lexical_character_2gram_placeholder"
    assert lexical_store.search("Python prototyping")[0][0] == item.memory_id


def test_lexical_placeholder_excludes_zero_score_candidates():
    store = InMemoryVectorStore()
    store.add_or_update("unrelated", "abcdef")

    assert store.search("中文") == []
    assert store.search("a") == []
    assert store.search("abcdef", top_k=0) == []
