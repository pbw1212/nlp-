"""Maintenance regressions: persistence, provenance and provider boundaries."""
import gc
import json
import sqlite3

import pytest

from src.memory.api.memory_manager import LongTermMemoryManager
from src.memory.core.config import MemoryConfig
from src.memory.llm.llm_client import LLMClientBase, OpenAILLMClient
from src.memory.llm.errors import ProviderResponseError
from src.memory.runtime.events import AgentEventStore


class ProposalClient(LLMClientBase):
    def __init__(self):
        self.proposal = {"operation": "ADD", "target_memory_id": None, "content": "长期使用 Python"}
        self.episodes = []

    def generate_json(self, system_prompt, user_prompt, **kwargs):
        if kwargs.get("schema_name") == "nemori_partition":
            return {"episodes": self.episodes}
        return self.proposal

    def generate_chat_json(self, messages, enable_web_search=False):
        raise AssertionError("not used")


def test_explicit_write_correct_and_remove_keep_index_current(tmp_path):
    client = ProposalClient()
    manager = LongTermMemoryManager(config=MemoryConfig(sqlite_db_path=str(tmp_path / "m.db")), llm_client=client)
    memory_id = manager.process_user_interaction("长期使用 Python", is_explicit=True, source_event_id="add")["memory_id"]
    assert manager.vector_store.search("Python")[0][0] == memory_id
    client.proposal = {"operation": "CORRECT", "target_memory_id": memory_id, "content": "长期使用 Julia"}
    manager.process_user_interaction("改为 Julia", is_explicit=True, source_event_id="correct")
    assert manager.vector_store.search("Python") == []
    assert manager.vector_store.search("Julia")[0][0] == memory_id
    client.proposal = {"operation": "REMOVE", "target_memory_id": memory_id, "content": ""}
    manager.process_user_interaction("撤回", is_explicit=True, source_event_id="remove")
    assert manager.get_current(memory_id) is None
    assert manager.vector_store.search("Julia") == []
    restarted = LongTermMemoryManager(config=manager.config, llm_client=client)
    assert restarted.vector_store.search("Julia") == []
    assert len(restarted.get_history(memory_id)) == 2


def test_noop_does_not_invent_target_or_claim_write(tmp_path):
    client = ProposalClient()
    manager = LongTermMemoryManager(config=MemoryConfig(sqlite_db_path=str(tmp_path / "m.db")), llm_client=client)
    memory_id = manager.process_explicit_instruction("Python", source_event_id="first")
    client.proposal = {"operation": "NOOP", "target_memory_id": memory_id, "content": ""}
    result = manager.process_user_interaction("没有长期内容", is_explicit=True, source_event_id="noop")
    assert result["memory_id"] == memory_id
    assert result["updated"] is False
    assert len(manager.get_history(memory_id)) == 1
    replay = manager.process_user_interaction("没有长期内容", is_explicit=True, source_event_id="noop")
    assert replay["updated"] is False


def test_episode_provenance_is_limited_to_actual_sources(tmp_path):
    client = ProposalClient()
    manager = LongTermMemoryManager(config=MemoryConfig(sqlite_db_path=str(tmp_path / "m.db")), llm_client=client)
    manager.record_foreground_interaction("天气不错", "", source_event_id="transient")
    manager.record_foreground_interaction("长期使用 Python", "", source_event_id="durable")
    client.episodes = [{"message_indices": [2], "cue": "偏好", "narrative": "长期使用 Python", "durability": "DURABLE"}]
    manager.run_autonomous_dream_pipeline()
    snapshot = manager.list_current()[0]
    assert {ref.source_event_id for ref in snapshot.provenance} == {"durable"}
    assert manager.sqlite_mgr.list_pending_events() == []


def test_out_of_range_episode_does_not_apply_memory(tmp_path):
    client = ProposalClient()
    manager = LongTermMemoryManager(config=MemoryConfig(sqlite_db_path=str(tmp_path / "m.db")), llm_client=client)
    manager.record_foreground_interaction("输入", "", source_event_id="source")
    client.episodes = [{"message_indices": [2], "cue": "cue", "narrative": "fact", "durability": "DURABLE"}]
    with pytest.raises(ProviderResponseError):
        manager.run_autonomous_dream_pipeline()
    assert manager.list_current() == []
    assert len(manager.sqlite_mgr.list_pending_events()) == 1


def test_event_store_closes_connection_at_context_exit(tmp_path):
    store = AgentEventStore(str(tmp_path / "events.db"))
    with store._connection() as connection:
        connection.execute("SELECT 1")
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")
    # No garbage collection is required to release Windows file handles.
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        store.append("test", payload={})
        store.list_events()
        (tmp_path / "events.db").unlink()
    finally:
        if was_enabled:
            gc.enable()


@pytest.mark.parametrize("content,finish,schema", [
    ("", "stop", None),
    ("not-json", "stop", None),
    ('{"ok":true}', "length", None),
    ('{"ok":"wrong"}', "stop", {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}),
])
def test_invalid_provider_results_never_reach_write_boundary(monkeypatch, content, finish, schema):
    client = OpenAILLMClient("test-key", max_retries=1)
    response = {"choices": [{"finish_reason": finish, "message": {"content": content}}]}
    monkeypatch.setattr(client, "_read_response_with_deadline", lambda req: json.dumps(response).encode())
    with pytest.raises(ProviderResponseError):
        client.generate_json("Return JSON", "test", response_schema=schema)
    assert client.last_usage == {}


@pytest.mark.parametrize("existing", [False, True])
def test_trial_side_effects_do_not_touch_invoking_workspace(tmp_path, monkeypatch, existing):
    from src.memory.product_trial.runner import ProductTrialRunner
    from src.memory.product_trial.scenarios import get_all_scenarios
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "test.txt"
    if existing:
        target.write_bytes(b"user-owned sentinel")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    scenario = next(s for s in get_all_scenarios() if s.scenario_id == "S001")
    result = ProductTrialRunner().run_scenario(scenario)
    assert result.status == "PASS"
    after = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    assert after == before


def test_unbound_turn_retrieves_memory_without_creating_task(tmp_path):
    from src.memory.runtime.agent_runtime import AgentRuntime
    from src.memory.runtime.task_router import TaskBindingAction, TaskBindingDecision
    from src.memory.runtime.models import TurnRequest
    from src.memory.task_state.repository import TaskStateRepository
    from src.memory.task_state.service import TaskStateService
    from src.memory.retrieval.evidence import EvidenceBundle, EvidenceSufficiency, EvidenceSufficiencyStatus

    class Resolver:
        def resolve(self, **kwargs):
            return TaskBindingDecision(action=TaskBindingAction.UNBOUND)

    class Retrieval:
        def __init__(self):
            self.queries = []
            self.bundle = EvidenceBundle(schema_version="evidence_bundle_v1", request_id="unbound-test",
                                         query="长期偏好", selected=(),
                                         sufficiency=EvidenceSufficiency(EvidenceSufficiencyStatus.INSUFFICIENT))
        def retrieve_evidence(self, query, **kwargs):
            self.queries.append(query)
            return self.bundle

    repository = TaskStateRepository(str(tmp_path / "task.db"))
    retrieval = Retrieval()
    runtime = AgentRuntime(task_service=TaskStateService(repository),
                           task_binding_resolver=Resolver(), retrieval_facade=retrieval)
    result = runtime.process_user_turn(TurnRequest(turn_id="unbound", user_input="请参考长期偏好"))
    assert result.error_info is None
    assert retrieval.queries == ["请参考长期偏好"]
    assert result.context.evidence_bundle is retrieval.bundle
    assert repository.list_tasks() == ()
