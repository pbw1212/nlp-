import sqlite3

import pytest

from src.memory.api.memory_manager import LongTermMemoryManager
from src.memory.core.config import MemoryConfig
from src.memory.core.errors import DuplicateEventConflictError
from src.memory.core.schema import (
    ChangeKind,
    IngestionState,
    MemoryItem,
    MemoryMetadata,
    MemoryType,
    SemanticValidity,
)


def manager_for(tmp_path, excerpt=32):
    return LongTermMemoryManager(
        config=MemoryConfig(
            sqlite_db_path=str(tmp_path / "revisions.db"),
            provenance_excerpt_max_chars=excerpt,
        )
    )


def test_add_creates_stable_identity_revision_and_provenance(tmp_path):
    manager = manager_for(tmp_path)
    result = manager.process_user_interaction(
        "请记住：我喜欢安静的酒店",
        is_explicit=True,
        source_event_id="evt-add",
        session_id="session-1",
        message_id="message-1",
    )
    memory_id = result["memory_id"]
    history = manager.get_history(memory_id)
    snapshot = manager.get_current(memory_id)

    assert len(history) == 1
    assert history[0].revision_no == 1
    assert history[0].semantic_validity is SemanticValidity.CURRENT
    assert snapshot.memory_id == memory_id
    assert snapshot.revision.revision_id == history[0].revision_id
    assert snapshot.provenance[0].source_event_id == "evt-add"
    assert snapshot.provenance[0].session_id == "session-1"
    assert snapshot.provenance[0].authority.value == "EXPLICIT_USER"
    assert len(snapshot.provenance[0].content_hash) == 64


def test_update_supersedes_and_preserves_identity(tmp_path):
    manager = manager_for(tmp_path)
    memory_id = manager.process_explicit_instruction("我喜欢安静的酒店", source_event_id="evt-1")
    original_revision_id = manager.get_history(memory_id)[0].revision_id

    updated = manager.update_memory_item(
        memory_id, "最好靠近地铁", source_event_id="evt-2"
    )
    history = manager.get_history(memory_id)

    assert updated.memory_id == memory_id
    assert [revision.semantic_validity for revision in history] == [
        SemanticValidity.SUPERSEDED,
        SemanticValidity.CURRENT,
    ]
    assert history[1].revision_id != original_revision_id
    assert history[1].supersedes_revision_id == original_revision_id
    assert history[0].valid_until == history[1].valid_from
    assert manager.sqlite_mgr.get_revision_provenance(history[1].revision_id)[0].source_event_id == "evt-2"


def test_correct_contradicts_old_revision_without_new_identity(tmp_path):
    manager = manager_for(tmp_path)
    memory_id = manager.process_explicit_instruction("我不能住热闹区域", source_event_id="evt-old")
    corrected = manager.correct_memory_item(
        memory_id,
        "现在热闹区域也可以，只要隔音好",
        source_event_id="evt-correct",
    )
    history = manager.get_history(memory_id)
    assert corrected.memory_id == memory_id
    assert history[0].semantic_validity is SemanticValidity.CONTRADICTED
    assert history[1].semantic_validity is SemanticValidity.CURRENT
    assert history[1].change_kind is ChangeKind.CORRECT


def test_restore_does_not_revive_contradicted_or_superseded_revision(tmp_path):
    manager = manager_for(tmp_path)
    memory_id = manager.process_explicit_instruction("旧偏好", source_event_id="evt-old")
    manager.correct_memory_item(memory_id, "新偏好", source_event_id="evt-new")
    before = manager.get_history(memory_id)

    manager.soft_delete_memory(memory_id)
    manager.restore_memory_from_recycle_bin(memory_id)
    after = manager.get_history(memory_id)

    assert [r.semantic_validity for r in before] == [r.semantic_validity for r in after]
    assert after[0].semantic_validity is SemanticValidity.CONTRADICTED
    assert after[1].semantic_validity is SemanticValidity.CURRENT


def test_noop_and_operational_actions_do_not_change_history(tmp_path):
    manager = manager_for(tmp_path)
    memory_id = manager.process_explicit_instruction("稳定偏好", source_event_id="evt-base")
    before = manager.get_history(memory_id)
    manager.crud.promote_memory(memory_id)
    manager.crud.demote_memory(memory_id)
    manager.pin_memory(memory_id)
    manager.rise_abstract_memories([memory_id], "抽象规律", MemoryType.PREFERENCE_RULES)
    assert manager.get_history(memory_id) == before

    event, _ = manager.sqlite_mgr.register_ingestion_event("没有长期价值", event_id="evt-noop")
    manager.sqlite_mgr.apply_noop([event.event_id])
    assert manager.get_history(memory_id) == before


def test_bounded_excerpt_and_raw_text_cleared_after_commit(tmp_path):
    manager = manager_for(tmp_path, excerpt=12)
    long_text = "请记住：" + "非常私密且很长的内容" * 20
    result = manager.process_user_interaction(long_text, is_explicit=True, source_event_id="evt-long")
    event = manager.sqlite_mgr.get_ingestion_event("evt-long")
    snapshot = manager.get_current(result["memory_id"])

    assert event.state is IngestionState.APPLIED
    assert event.raw_text is None
    assert len(event.controlled_excerpt) <= 12
    assert len(snapshot.provenance[0].controlled_excerpt) <= 12
    assert snapshot.provenance[0].controlled_excerpt != long_text


def test_applied_event_replay_is_idempotent_and_payload_conflict_is_explicit(tmp_path):
    manager = manager_for(tmp_path)
    first = manager.process_user_interaction("请记住：稳定规则", is_explicit=True, source_event_id="evt-same")
    replay = manager.process_user_interaction("请记住：稳定规则", is_explicit=True, source_event_id="evt-same")

    assert replay["idempotent_replay"] is True
    assert replay["memory_id"] == first["memory_id"]
    assert len(manager.get_history(first["memory_id"])) == 1
    with pytest.raises(DuplicateEventConflictError):
        manager.process_user_interaction("请记住：不同规则", is_explicit=True, source_event_id="evt-same")


def test_partial_unique_index_rejects_two_current_revisions(tmp_path):
    manager = manager_for(tmp_path)
    memory_id = manager.process_explicit_instruction("唯一当前版本", source_event_id="evt-unique")
    with manager.sqlite_mgr._get_connection() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO memory_revisions
                   SELECT 'rev-illegal', memory_id, 2, 'illegal', 'CURRENT', created_at,
                          event_time, valid_from, NULL, confidence, 'UPDATE', revision_id,
                          semantic_metadata_json
                   FROM memory_revisions WHERE memory_id=?""",
                (memory_id,),
            )


def test_list_current_only_returns_active_current(tmp_path):
    manager = manager_for(tmp_path)
    active_id = manager.process_explicit_instruction("active", source_event_id="evt-active")
    recycled_id = manager.process_explicit_instruction("recycled", source_event_id="evt-recycled")
    manager.soft_delete_memory(recycled_id)
    assert [snapshot.memory_id for snapshot in manager.list_current()] == [active_id]
    assert [revision.revision_no for revision in manager.get_history(recycled_id)] == [1]
