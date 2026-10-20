from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.memory.agent.models import ReasonerInput
from src.memory.agent.prompt_composer import PromptComposer
from src.memory.api.memory_manager import LongTermMemoryManager
from src.memory.conversation import ConversationRepository, ConversationRole
from src.memory.core.config import MemoryConfig, ProviderMode
from src.memory.core.schema import (
    ChangeKind,
    Granularity,
    MemoryItem,
    MemoryMetadata,
    MemoryRevision,
    MemoryType,
    SemanticValidity,
)
from src.memory.llm.llm_client import LLMClientBase
from src.memory.retrieval.applicability_models import (
    ApplicabilityDecision,
    ApplicabilityStatus,
)
from src.memory.retrieval.evidence import (
    ConflictStatus,
    DistilledEvidence,
    EvidenceBundle,
    EvidenceSufficiency,
    EvidenceSufficiencyStatus,
)
from src.memory.runtime.models import AgentTurnContext, TurnRequest


class _LifecycleClient(LLMClientBase):
    def __init__(self) -> None:
        self.target_memory_id: str | None = None
        self.explicit_calls = 0

    def generate_json(self, system_prompt, user_prompt, **kwargs):
        if kwargs.get("schema_name") == "explicit_proposal":
            self.explicit_calls += 1
            if self.target_memory_id is not None:
                return {
                    "operation": "NOOP",
                    "target_memory_id": self.target_memory_id,
                    "content": "我长期使用机械键盘",
                }
            return {"operation": "ADD", "content": "我长期使用机械键盘"}
        raise AssertionError("unexpected provider call")

    def generate_chat_json(self, messages, enable_web_search=False):
        raise AssertionError("unexpected provider call")


def _memory_manager(tmp_path, client: _LifecycleClient) -> LongTermMemoryManager:
    config = MemoryConfig(
        sqlite_db_path=str(tmp_path / "memory.db"),
        provider_mode=ProviderMode.REQUIRED,
    )
    return LongTermMemoryManager(config=config, llm_client=client)


def test_prompt_composer_uses_distilled_text_and_keeps_current_request_last():
    item = DistilledEvidence(
        memory_id="mem_1",
        revision_id="rev_1",
        text="偏好 <安静> & 靠窗",
        original_rank=1,
        original_score=1.0,
        score_breakdown={},
        provenance_refs=(),
        applicability_status=ApplicabilityStatus.MATCH,
        applicability_decision=ApplicabilityDecision.RETAIN,
        applicability_reasons=(),
        conflict_status=ConflictStatus.NO_CONFLICT,
        conflict_key=None,
        conflict_losing_memory_ids=(),
        source_trace=(),
    )
    bundle = EvidenceBundle(
        schema_version="v1",
        request_id="q1",
        query="酒店",
        selected=(item,),
        sufficiency=EvidenceSufficiency(EvidenceSufficiencyStatus.SUFFICIENT),
    )
    context = AgentTurnContext(
        turn_id="turn_1",
        task_id="task_1",
        receipt=None,
        maintenance=None,
        evidence_bundle=bundle,
        working_context=None,  # type: ignore[arg-type]
        structured_task_context=None,  # type: ignore[arg-type]
        warnings=("low-confidence",),
    )

    _, prompt = PromptComposer().compose(
        ReasonerInput(turn_context=context, user_input="请推荐酒店")
    )

    assert "偏好 &lt;安静&gt; &amp; 靠窗" in prompt
    assert prompt.index("### RUNTIME WARNINGS") < prompt.index("### CURRENT USER REQUEST")
    assert prompt.endswith("### CURRENT USER REQUEST\n请推荐酒店")


def test_agent_prompt_delegates_side_effect_confirmation_to_application():
    from src.memory.agent.tooling import ToolEffect, ToolSpec

    prompt = PromptComposer()._build_system_prompt((ToolSpec(
        name="create_file",
        description="创建文件",
        parameters_schema={"type": "object"},
        effect=ToolEffect.SIDE_EFFECT,
    ),))

    assert "emit the" in prompt
    assert "Do not ask for confirmation in a" in prompt
    assert "FINAL_RESPONSE and do not execute" in prompt


def test_noop_event_replay_returns_same_identity_without_provider_recall(tmp_path):
    client = _LifecycleClient()
    manager = _memory_manager(tmp_path, client)
    first_id = manager.process_explicit_instruction(
        "我长期使用机械键盘", source_event_id="event_1"
    )
    client.target_memory_id = first_id

    noop_id = manager.process_explicit_instruction(
        "我长期使用机械键盘", source_event_id="event_2"
    )
    replay_id = manager.process_explicit_instruction(
        "我长期使用机械键盘", source_event_id="event_2"
    )

    assert noop_id == replay_id == first_id
    assert client.explicit_calls == 2


def test_conversation_append_is_atomic_and_detects_identity_conflict(tmp_path):
    repository = ConversationRepository(str(tmp_path / "conversation.db"))
    session = repository.create_session(session_id="session_1")

    def append_once(_index: int) -> str:
        return repository.append_message(
            session.session_id,
            "turn_1",
            ConversationRole.USER,
            "同一条消息",
        ).message_id

    with ThreadPoolExecutor(max_workers=8) as pool:
        message_ids = tuple(pool.map(append_once, range(16)))

    assert len(set(message_ids)) == 1
    assert len(repository.list_recent_messages(session.session_id, limit=20)) == 1
    with pytest.raises(ValueError, match="已绑定不同"):
        repository.append_message(
            session.session_id,
            "turn_1",
            ConversationRole.USER,
            "不同内容",
        )


def test_conversation_context_honors_zero_and_small_character_budgets(tmp_path):
    repository = ConversationRepository(str(tmp_path / "conversation.db"))
    session = repository.create_session(session_id="session_1")
    repository.append_message(
        session.session_id,
        "turn_1",
        ConversationRole.USER,
        "一" * 100,
    )

    empty = repository.build_conversation_context(
        session.session_id, max_messages=0, max_chars=10
    )
    bounded = repository.build_conversation_context(
        session.session_id, max_messages=1, max_chars=10
    )

    assert empty.recent_messages == ()
    assert empty.omitted_message_count == 1
    assert len(bounded.recent_messages[0].content) <= 10
    with pytest.raises(ValueError, match="不能为负数"):
        repository.build_conversation_context(session.session_id, max_chars=-1)


@pytest.mark.parametrize(
    "override",
    [
        {"vector_dim": 0},
        {"s0_initial_strength": 1.1},
        {"decay_gamma": -0.1},
        {"decay_threshold": 1.1},
        {"time_unit_hours": 0},
        {"recycle_bin_retention_days": -1},
    ],
)
def test_memory_config_rejects_invalid_algorithm_parameters(override):
    with pytest.raises(ValueError):
        MemoryConfig(**override)


def test_decay_is_bounded_when_clock_moves_backwards(tmp_path):
    client = _LifecycleClient()
    manager = _memory_manager(tmp_path, client)
    now = time.time()
    item = MemoryItem(
        memory_type=MemoryType.PREFERENCE_RULES,
        granularity=Granularity.SEMANTIC_INSIGHT,
        last_accessed=now + 3600,
        metadata=MemoryMetadata(last_accessed=now + 3600),
    )
    item.attach_current_revision(
        MemoryRevision(
            revision_id="rev_1",
            memory_id=item.memory_id,
            revision_no=1,
            content="future timestamp fixture",
            semantic_validity=SemanticValidity.CURRENT,
            created_at=now,
            event_time=now,
            valid_from=now,
            valid_until=None,
            confidence=0.8,
            change_kind=ChangeKind.ADD,
        )
    )

    strength = manager.consolidation.calculate_ebbinghaus_decay(item, current_time=now)

    assert strength == manager.config.s0_initial_strength


@pytest.mark.parametrize("value", [None, 123, "", "   "])
def test_turn_request_rejects_non_string_or_blank_user_input(value):
    with pytest.raises(ValueError, match="user_input"):
        TurnRequest(turn_id="turn_1", user_input=value)  # type: ignore[arg-type]
