import pytest
import re

from src.memory.api.memory_manager import LongTermMemoryManager
from src.memory.core.config import MemoryConfig
from src.memory.core.schema import IngestionState
from src.memory.llm.llm_client import LLMClientBase
from src.memory.core.schema import SemanticValidity
from src.memory.core.errors import InvalidLifecycleTransitionError


class NovelMemoryClient(LLMClientBase):
    def generate_json(self, system_prompt, user_prompt, *, response_schema=None, schema_name="structured_output"):
        if "partition them into semantically coherent" in system_prompt:
            return {"episodes": [{"message_indices": [1], "cue": "hotel", "narrative": "用户喜欢安静酒店。", "durability": "DURABLE"}]}
        if "Prediction Error" in system_prompt:
            return {
                "predicted_content": "",
                "prediction_error_residual": "用户喜欢安静酒店。",
                "is_novel_info": True,
                "novelty_score": 0.9,
            }
        if schema_name == "automatic_lifecycle" or "lifecycle proposer for automatic" in system_prompt:
            return {
                "operation": "ADD",
                "target_memory_id": None,
                "content": "用户喜欢安静酒店。",
                "memory_type": "PREFERENCE_RULES",
                "granularity": "SEMANTIC_INSIGHT",
                "confidence": 0.9,
                "importance_score": None,
                "semantic_metadata": {},
            }
        return {"abstract_insight": "unused", "memory_type": "PREFERENCE_RULES"}

    def generate_chat_json(self, messages, enable_web_search=False):
        return {"response": "unused"}


class AutomaticCorrectionClient(LLMClientBase):
    """Provider double for the provider-proposed inferred lifecycle path."""

    def generate_json(self, system_prompt, user_prompt, *, response_schema=None, schema_name="structured_output"):
        if "Dialogue Window:" in user_prompt:
            narrative = (
                "用户将个人项目数据库改为 PostgreSQL，不再使用 SQLite。"
                if "PostgreSQL" in user_prompt
                else "用户长期偏好使用 Python 和 SQLite 做个人项目。"
            )
            return {"episodes": [{"message_indices": [1], "cue": "database preference", "narrative": narrative, "durability": "DURABLE"}]}
        if schema_name == "automatic_lifecycle":
            target = re.search(r"mem_[a-z0-9]{12}", user_prompt)
            if not target:
                return {
                    "operation": "ADD",
                    "target_memory_id": None,
                    "content": "用户长期偏好使用 Python 和 SQLite 做个人项目。",
                    "memory_type": "PREFERENCE_RULES",
                    "granularity": "SEMANTIC_INSIGHT",
                    "confidence": 0.92,
                    "importance_score": None,
                    "semantic_metadata": {},
                }
            return {
                "operation": "CORRECT",
                "target_memory_id": target.group(0),
                "content": "用户将个人项目数据库改为 PostgreSQL，不再使用 SQLite。",
                "memory_type": "PREFERENCE_RULES",
                "granularity": "SEMANTIC_INSIGHT",
                "confidence": 0.95,
                "importance_score": None,
                "semantic_metadata": {},
            }
        return {"response": "unused"}

    def generate_chat_json(self, messages, enable_web_search=False):
        return {"response": "unused"}


def test_automatic_lifecycle_provider_none_alias_is_canonical_noop(tmp_path):
    class NoneAliasClient(AutomaticCorrectionClient):
        def generate_json(self, system_prompt, user_prompt, *, response_schema=None, schema_name="structured_output"):
            if schema_name == "automatic_lifecycle":
                return {
                    "operation": "NONE",
                    "target_memory_id": None,
                    "content": "",
                    "memory_type": "EPISODIC_GRAPH",
                    "granularity": "FINE_EPISODE",
                    "confidence": 0.8,
                }
            return super().generate_json(system_prompt, user_prompt)

    manager = LongTermMemoryManager(
        config=MemoryConfig(sqlite_db_path=str(tmp_path / "none-alias.db")),
        llm_client=NoneAliasClient(),
    )
    proposal = manager.distiller.propose_inferred_lifecycle(
        [{"memory_id": "mem_existing", "content": "旧事实"}],
        "没有新增事实",
        "evt-none",
    )
    assert proposal.operation.value == "NOOP"


def test_automatic_lifecycle_null_canonical_fields_fall_back_to_compatibility_aliases(tmp_path):
    class NullCanonicalClient(AutomaticCorrectionClient):
        def generate_json(self, system_prompt, user_prompt, *, response_schema=None, schema_name="structured_output"):
            if schema_name == "automatic_lifecycle":
                return {
                    "operation": None,
                    "action": "CORRECT",
                    "target_memory_id": None,
                    "memory_id": "mem_123456789012",
                    "content": "新事实",
                    "memory_type": "EPISODIC_GRAPH",
                    "granularity": "FINE_EPISODE",
                    "confidence": 0.8,
                }
            return super().generate_json(system_prompt, user_prompt)

    manager = LongTermMemoryManager(
        config=MemoryConfig(sqlite_db_path=str(tmp_path / "null-canonical.db")),
        llm_client=NullCanonicalClient(),
    )
    proposal = manager.distiller.propose_inferred_lifecycle(
        [{"memory_id": "mem_123456789012", "content": "旧事实"}],
        "新事实",
        "evt-null-canonical",
    )
    assert proposal.operation.value == "CORRECT"
    assert proposal.target_memory_id == "mem_123456789012"


def test_automatic_lifecycle_ignores_provider_transport_metadata(tmp_path):
    class ProviderMetadataClient(AutomaticCorrectionClient):
        def generate_json(self, system_prompt, user_prompt, *, response_schema=None, schema_name="structured_output"):
            if schema_name == "automatic_lifecycle":
                return {
                    "operation": "ADD",
                    "target_memory_id": None,
                    "content": "用户偏好安静酒店。",
                    "memory_type": "PREFERENCE_RULES",
                    "granularity": "SEMANTIC_INSIGHT",
                    "confidence": 0.9,
                    "importance_score": None,
                    "semantic_metadata": {
                        "topics": ["住宿"],
                        "source": "provider_lifecycle",
                        "supersedes": None,
                        "active_version": 1,
                        "history_retained": True,
                    },
                }
            return super().generate_json(
                system_prompt,
                user_prompt,
                response_schema=response_schema,
                schema_name=schema_name,
            )

    manager = LongTermMemoryManager(
        config=MemoryConfig(sqlite_db_path=str(tmp_path / "provider-metadata.db")),
        llm_client=ProviderMetadataClient(),
    )

    proposal = manager.distiller.propose_inferred_lifecycle(
        [],
        "用户偏好安静酒店。",
        "evt-provider-metadata",
    )

    assert proposal.semantic_metadata.topics == ("住宿",)


def make_manager(tmp_path):
    return LongTermMemoryManager(
        config=MemoryConfig(sqlite_db_path=str(tmp_path / "journal.db")),
        llm_client=NovelMemoryClient(),
    )


def test_successful_consolidation_commits_memory_and_scrubs_raw_text(tmp_path):
    manager = make_manager(tmp_path)
    manager.record_foreground_interaction(
        "我喜欢安静酒店", "了解", source_event_id="evt-pending"
    )
    result = manager.run_autonomous_dream_pipeline()
    event = manager.sqlite_mgr.get_ingestion_event("evt-pending")

    assert result["new_memories_created"] == 1
    assert event.state is IngestionState.APPLIED
    assert event.raw_text is None
    assert event.consolidation_run_id == result["consolidation_run_id"]
    assert len(manager.list_current()) == 1
    assert manager.list_current()[0].memory_type.value == "PREFERENCE_RULES"


def test_transient_or_uncertain_episode_is_not_persisted(tmp_path):
    class TransientClient(NovelMemoryClient):
        def generate_json(self, system_prompt, user_prompt, *, response_schema=None, schema_name="structured_output"):
            if "partition them into semantically coherent" in system_prompt:
                return {
                    "episodes": [{
                        "message_indices": [1],
                        "cue": "temporary discussion",
                        "narrative": "用户只是临时考虑一个方案，尚未形成长期偏好。",
                        "durability": "TRANSIENT",
                    }]
                }
            return super().generate_json(
                system_prompt,
                user_prompt,
                response_schema=response_schema,
                schema_name=schema_name,
            )

    manager = LongTermMemoryManager(
        config=MemoryConfig(sqlite_db_path=str(tmp_path / "transient.db")),
        llm_client=TransientClient(),
    )
    manager.record_foreground_interaction("这只是临时讨论", "回复", source_event_id="evt-transient")

    result = manager.run_autonomous_dream_pipeline()

    assert result["episodes_processed"] == 0
    assert result["new_memories_created"] == 0
    assert manager.list_current() == []
    assert manager.sqlite_mgr.get_ingestion_event("evt-transient").state is IngestionState.APPLIED


def test_automatic_memory_distillation_does_not_send_assistant_reply_as_fact(tmp_path):
    class CaptureUserOnlyClient(NovelMemoryClient):
        def __init__(self):
            self.partition_prompt = ""

        def generate_json(self, system_prompt, user_prompt, *, response_schema=None, schema_name="structured_output"):
            if "partition them into semantically coherent" in system_prompt:
                self.partition_prompt = user_prompt
            return super().generate_json(system_prompt, user_prompt)

    client = CaptureUserOnlyClient()
    manager = LongTermMemoryManager(
        config=MemoryConfig(sqlite_db_path=str(tmp_path / "user-only.db")),
        llm_client=client,
    )
    manager.record_foreground_interaction(
        "用户长期使用 Python。",
        "助手确认：未来一定会遵循这个背景。",
        source_event_id="evt-user-only",
    )
    manager.run_autonomous_dream_pipeline()
    assert "助手确认" not in client.partition_prompt


def test_forced_db_failure_rolls_back_all_writes_and_keeps_retryable(tmp_path):
    manager = make_manager(tmp_path)
    manager.record_foreground_interaction("待重试输入", "回复", source_event_id="evt-rollback")
    expected_run_id = manager._consolidation_run_id(["evt-rollback"])

    def fail_inside_transaction():
        raise RuntimeError("forced DB apply failure")

    with pytest.raises(RuntimeError):
        manager.run_autonomous_dream_pipeline(failure_hook=fail_inside_transaction)

    event = manager.sqlite_mgr.get_ingestion_event("evt-rollback")
    assert event.state is IngestionState.PENDING
    assert event.raw_text == "待重试输入"
    assert manager.list_current() == []

    success = manager.run_autonomous_dream_pipeline()
    assert success["consolidation_run_id"] == expected_run_id
    assert len(manager.list_current()) == 1
    assert manager.run_autonomous_dream_pipeline()["new_memories_created"] == 0
    assert len(manager.list_current()) == 1


def test_automatic_lifecycle_corrects_existing_identity_instead_of_adding_conflict(tmp_path):
    manager = LongTermMemoryManager(
        config=MemoryConfig(sqlite_db_path=str(tmp_path / "automatic-correction.db")),
        llm_client=AutomaticCorrectionClient(),
    )
    manager.record_foreground_interaction(
        "我长期偏好用 Python 和 SQLite 做个人项目。",
        "已记录。",
        source_event_id="evt-old-preference",
    )
    manager.run_autonomous_dream_pipeline()
    old_id = manager.list_current()[0].memory_id

    manager.record_foreground_interaction(
        "更新长期偏好：数据库改用 PostgreSQL，不再使用 SQLite。",
        "已理解这是更正。",
        source_event_id="evt-new-preference",
    )
    manager.run_autonomous_dream_pipeline()

    current = manager.list_current()
    assert len(current) == 1
    assert current[0].memory_id == old_id
    assert "PostgreSQL" in current[0].revision.content
    assert [revision.semantic_validity for revision in manager.get_history(old_id)] == [
        SemanticValidity.CONTRADICTED,
        SemanticValidity.CURRENT,
    ]


class AutomaticRemovalClient(LLMClientBase):
    def generate_json(self, system_prompt, user_prompt, *, response_schema=None, schema_name="structured_output"):
        if "Dialogue Window:" in user_prompt:
            narrative = "用户撤回此前的安静酒店偏好。" if "撤回" in user_prompt else "用户偏好安静酒店。"
            return {"episodes": [{"message_indices": [1], "cue": "hotel preference", "narrative": narrative, "durability": "DURABLE"}]}
        if schema_name == "automatic_lifecycle":
            target = re.search(r"mem_[a-z0-9]{12}", user_prompt)
            if target:
                return {
                    "operation": "REMOVE",
                    "target_memory_id": target.group(0),
                    "content": "",
                    "memory_type": "PREFERENCE_RULES",
                    "granularity": "SEMANTIC_INSIGHT",
                    "confidence": 0.96,
                    "importance_score": None,
                    "semantic_metadata": {},
                }
            return {
                "operation": "ADD",
                "target_memory_id": None,
                "content": "用户偏好安静酒店。",
                "memory_type": "PREFERENCE_RULES",
                "granularity": "SEMANTIC_INSIGHT",
                "confidence": 0.92,
                "importance_score": None,
                "semantic_metadata": {},
            }
        return {"response": "unused"}

    def generate_chat_json(self, messages, enable_web_search=False):
        return {"response": "unused"}


def _seed_automatic_profile(manager):
    manager.record_foreground_interaction("我偏好安静酒店。", "收到", source_event_id="evt-profile-add")
    manager.run_autonomous_dream_pipeline()
    return manager.list_current()[0].memory_id


def test_automatic_profile_removal_moves_unpinned_trait_to_recycle_bin(tmp_path):
    manager = LongTermMemoryManager(
        config=MemoryConfig(sqlite_db_path=str(tmp_path / "automatic-removal.db")),
        llm_client=AutomaticRemovalClient(),
    )
    memory_id = _seed_automatic_profile(manager)
    manager.record_foreground_interaction("我撤回这个偏好。", "收到", source_event_id="evt-profile-remove")

    result = manager.run_autonomous_dream_pipeline()

    assert result["memories_removed"] == 1
    assert manager.list_current() == []
    assert [item.memory_id for item in manager.get_recycle_bin_memories()] == [memory_id]


def test_automatic_profile_removal_never_removes_pinned_trait(tmp_path):
    manager = LongTermMemoryManager(
        config=MemoryConfig(sqlite_db_path=str(tmp_path / "automatic-removal-pinned.db")),
        llm_client=AutomaticRemovalClient(),
    )
    memory_id = _seed_automatic_profile(manager)
    manager.pin_memory(memory_id, True)
    manager.record_foreground_interaction("我撤回这个偏好。", "收到", source_event_id="evt-profile-protected")

    result = manager.run_autonomous_dream_pipeline()

    assert result["memories_removed"] == 0
    assert [snapshot.memory_id for snapshot in manager.list_current()] == [memory_id]
    assert manager.get_recycle_bin_memories() == []


class BatchConflictClient(LLMClientBase):
    def __init__(self) -> None:
        self.conflicting_batch = False

    def generate_json(self, system_prompt, user_prompt, *, response_schema=None, schema_name="structured_output"):
        if "Dialogue Window:" in user_prompt:
            narratives = (
                ["批次第一条更正。", "批次第二条更正。"]
                if self.conflicting_batch
                else ["初始长期事实。"]
            )
            return {"episodes": [{"message_indices": [1], "cue": "test", "narrative": item, "durability": "DURABLE"} for item in narratives]}
        if schema_name == "automatic_lifecycle":
            target = re.search(r"mem_[a-z0-9]{12}", user_prompt)
            if not target:
                return {
                    "operation": "ADD",
                    "target_memory_id": None,
                    "content": "初始长期事实。",
                    "memory_type": "EPISODIC_GRAPH",
                    "granularity": "FINE_EPISODE",
                    "confidence": 0.9,
                    "importance_score": None,
                    "semantic_metadata": {},
                }
            return {
                "operation": "CORRECT",
                "target_memory_id": target.group(0),
                "content": "批次更正内容。",
                "memory_type": "EPISODIC_GRAPH",
                "granularity": "FINE_EPISODE",
                "confidence": 0.9,
                "importance_score": None,
                "semantic_metadata": {},
            }
        return {"response": "unused"}

    def generate_chat_json(self, messages, enable_web_search=False):
        return {"response": "unused"}


def test_automatic_batch_rejects_competing_mutations_before_atomic_apply(tmp_path):
    client = BatchConflictClient()
    manager = LongTermMemoryManager(
        config=MemoryConfig(sqlite_db_path=str(tmp_path / "batch-conflict.db")),
        llm_client=client,
    )
    manager.record_foreground_interaction("建立初始事实", "收到", source_event_id="evt-seed")
    manager.run_autonomous_dream_pipeline()
    assert len(manager.list_current()) == 1

    client.conflicting_batch = True
    manager.record_foreground_interaction("产生批内冲突", "收到", source_event_id="evt-conflict")
    with pytest.raises(InvalidLifecycleTransitionError, match="多个互相竞争"):
        manager.run_autonomous_dream_pipeline()

    event = manager.sqlite_mgr.get_ingestion_event("evt-conflict")
    assert event.state is IngestionState.PENDING
    assert len(manager.list_current()) == 1
