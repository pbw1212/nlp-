import time

import pytest

from src.memory.api.memory_manager import LongTermMemoryManager
from src.memory.core.config import MemoryConfig
from src.memory.core.schema import (
    ActiveStatus,
    IngestionState,
    MemoryItem,
    MemoryType,
    RetentionDisposition,
    SemanticValidity,
)
from src.memory.llm.errors import ProviderRequestError
from src.memory.llm.llm_client import LLMClientBase
from src.memory.lifecycle.nemori_distiller import NemoriDistiller
from src.memory.llm.errors import ProviderResponseError


def make_manager(tmp_path, filename="lifecycle.db", **overrides):
    return LongTermMemoryManager(
        config=MemoryConfig(sqlite_db_path=str(tmp_path / filename), **overrides)
    )


def test_public_router_and_recycle_restore(tmp_path):
    manager = make_manager(tmp_path)
    result = manager.process_user_interaction(
        "请记住：项目架构严禁依赖外部 Docker。",
        is_explicit=True,
        source_event_id="evt-explicit",
    )
    memory_id = result["memory_id"]
    assert result["route"] == "FAST_PATH"
    assert manager.get_current(memory_id) is not None

    original_history = manager.get_history(memory_id)
    manager.soft_delete_memory(memory_id)
    assert manager.get_current(memory_id) is None
    assert manager.get_recycle_bin_memories()[0].retention_disposition is RetentionDisposition.RECYCLED
    restored = manager.restore_memory_from_recycle_bin(memory_id)
    assert restored is not None
    assert restored.retention_disposition is RetentionDisposition.ACTIVE
    assert manager.get_history(memory_id) == original_history


def test_foreground_journal_does_not_implicitly_run_pipeline(tmp_path):
    manager = make_manager(tmp_path)
    result = manager.record_foreground_interaction("普通对话", "回复")
    assert result["updated"] is False
    assert result["pipeline_deferred"] is True
    assert manager.pending_dialogue_buffer == [{"role": "user", "content": "普通对话"}, {"role": "assistant", "content": "回复"}]
    assert manager.list_current() == []


def test_consolidation_has_no_turn_or_wall_clock_schedule(tmp_path):
    db_path = str(tmp_path / "restart-time.db")
    config = MemoryConfig(sqlite_db_path=db_path)
    first = LongTermMemoryManager(config=config)
    restarted = LongTermMemoryManager(config=config)

    assert not hasattr(restarted.consolidation, "turn_counter")
    assert not hasattr(restarted.consolidation, "last_consolidation_time")
    assert not hasattr(config, "consolidation_turn_threshold")
    assert not hasattr(config, "consolidation_time_threshold_hours")


def test_pending_events_survive_app_restart_without_hidden_worker(tmp_path):
    from src.memory.app.console import PersonalAgentApp

    class EmptyConsolidationClient(LLMClientBase):
        def generate_json(self, system_prompt, user_prompt, *, response_schema=None, schema_name="structured_output"):
            if "partition them into semantically coherent" in system_prompt:
                return {"episodes": []}
            return {"proposals": [], "warnings": []}

        def generate_chat_json(self, messages, enable_web_search=False):
            return {"response": "unused"}

    data_dir = tmp_path / "global-agent-data"
    config = MemoryConfig(sqlite_db_path=str(data_dir / "memory.db"))
    first = PersonalAgentApp(
        data_dir=str(data_dir), config=config, llm_client=EmptyConsolidationClient()
    )
    try:
        first.long_term_memory_mgr.record_foreground_interaction(
            "跨终端重启后仍应沉淀", "测试回复", defer_consolidation=True
        )
    finally:
        first.close()

    restarted = PersonalAgentApp(
        data_dir=str(data_dir), config=config, llm_client=EmptyConsolidationClient()
    )
    try:
        status = restarted.get_memory_update_status()
        assert status["mode"] == "agent_tool"
        assert status["pending_events"] == 1
        assert status["threshold_trigger_enabled"] is False
        assert status["time_trigger_enabled"] is False
    finally:
        restarted.close()


class AlwaysFailingClient(LLMClientBase):
    def generate_json(self, system_prompt, user_prompt, *, response_schema=None, schema_name="structured_output"):
        raise ProviderRequestError(
            "synthetic provider failure", provider="test", operation="generate_json"
        )

    def generate_chat_json(self, messages, enable_web_search=False):
        return {"response": "unused"}


def test_provider_failure_keeps_durable_pending_event(tmp_path):
    db_path = str(tmp_path / "failure.db")
    manager = LongTermMemoryManager(
        config=MemoryConfig(sqlite_db_path=db_path),
        llm_client=AlwaysFailingClient(),
    )
    manager.record_foreground_interaction(
        "必须保留的输入", "助手响应", source_event_id="evt-fail"
    )
    with pytest.raises(ProviderRequestError):
        manager.run_autonomous_dream_pipeline()

    event = manager.sqlite_mgr.get_ingestion_event("evt-fail")
    assert event is not None
    assert event.state is IngestionState.PENDING
    assert event.raw_text == "必须保留的输入"
    assert manager.list_current() == []

    restarted = LongTermMemoryManager(
        config=MemoryConfig(sqlite_db_path=db_path), llm_client=AlwaysFailingClient()
    )
    assert [event.event_id for event in restarted.sqlite_mgr.list_pending_events()] == ["evt-fail"]


def test_slow_path_reports_buffered_state(tmp_path):
    manager = make_manager(tmp_path)
    result = manager.process_user_interaction("普通的当前对话", source_event_id="evt-buffer")
    assert result["action"] == "BUFFERED_FOR_UPDATE"
    assert result["updated"] is False
    assert manager.sqlite_mgr.get_ingestion_event("evt-buffer").state is IngestionState.PENDING


class LifecycleResponseClient(LLMClientBase):
    def __init__(self, response):
        self.response = response

    def generate_json(self, system_prompt, user_prompt, *, response_schema=None, schema_name="structured_output"):
        return self.response

    def generate_chat_json(self, messages, enable_web_search=False):
        return {"response": "unused"}


@pytest.mark.parametrize(
    "response",
    [
        {"operation": "CORRECT", "target_memory_id": None, "content": "new", "reason": "missing target"},
        {"operation": "ADD", "target_memory_id": "mem_existing", "content": "new", "reason": "wrong target"},
    ],
)
def test_explicit_lifecycle_rejects_ambiguous_target_contract(response):
    distiller = NemoriDistiller(
        LifecycleResponseClient(response),
        MemoryConfig(sqlite_db_path=":memory:"),
    )

    with pytest.raises(ProviderResponseError):
        distiller.propose_explicit_lifecycle(
            [{"memory_id": "mem_existing", "content": "old"}],
            "new",
            "evt-contract",
        )
