import sqlite3

import pytest

from src.memory.api.memory_manager import LongTermMemoryManager
from src.memory.core.config import MemoryConfig
from src.memory.core.errors import LegacySchemaDetectedError
from src.memory.core.schema import (
    LifecycleOperation,
    LifecycleProposal,
    SemanticMetadata,
)
from src.memory.storage.sqlite_manager import SQLiteMemoryManager


def test_future_schema_is_rejected_without_mutation(tmp_path):
    path = tmp_path / "future.db"
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version=999")

    with pytest.raises(LegacySchemaDetectedError):
        SQLiteMemoryManager(str(path))

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 999
        assert conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0] == 0


def test_current_revision_pointer_has_fk_and_rejects_cross_memory_reference(tmp_path):
    manager = LongTermMemoryManager(
        config=MemoryConfig(sqlite_db_path=str(tmp_path / "integrity.db"))
    )
    first = manager.process_explicit_instruction("first", source_event_id="fk-first")
    second = manager.process_explicit_instruction("second", source_event_id="fk-second")
    first_revision = manager.get_current(first).revision
    second_revision = manager.get_current(second).revision

    with manager.sqlite_mgr._get_connection() as conn:
        foreign_keys = conn.execute("PRAGMA foreign_key_list(memory_items)").fetchall()
        assert any(row["from"] == "current_revision_id" for row in foreign_keys)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE memory_items SET current_revision_id=? WHERE memory_id=?",
                (second_revision.revision_id, first),
            )

    assert manager.get_current(first).revision.revision_id == first_revision.revision_id


def test_semantic_metadata_validation_and_round_trip_before_write(tmp_path):
    manager = LongTermMemoryManager(
        config=MemoryConfig(sqlite_db_path=str(tmp_path / "metadata.db"))
    )
    metadata = SemanticMetadata.from_dict({
        "persons": ["person-a"],
        "entities": ["entity-a"],
        "locations": ["location-a"],
        "topics": ["topic-a"],
        "keywords": ["keyword-a"],
        "scopes": ["scope-a"],
        "conditions": ["condition-a"],
    })
    event, _ = manager.sqlite_mgr.register_ingestion_event("structured", event_id="metadata-event")
    proposal = LifecycleProposal(
        operation=LifecycleOperation.ADD,
        content="structured content",
        semantic_metadata=metadata,
        provenance_event_ids=(event.event_id,),
    )
    memory_id, _ = manager.sqlite_mgr.apply_proposals_atomic(
        [proposal], [event.event_id], consolidation_run_id="metadata-run"
    )[0]
    assert manager.get_current(memory_id).revision.semantic_metadata == metadata

    before = len(manager.list_current())
    with pytest.raises(ValueError):
        SemanticMetadata.from_dict({"unknown": ["value"]})
    with pytest.raises(ValueError):
        SemanticMetadata.from_dict({"persons": "not-an-array"})
    with pytest.raises(ValueError):
        SemanticMetadata.from_dict(["not-an-object"])
    assert len(manager.list_current()) == before


def test_current_safe_read_matrix(tmp_path):
    manager = LongTermMemoryManager(
        config=MemoryConfig(sqlite_db_path=str(tmp_path / "read-matrix.db"))
    )
    active = manager.process_explicit_instruction("active", source_event_id="matrix-1")
    recycled = manager.process_explicit_instruction("recycled", source_event_id="matrix-2")
    archived = manager.process_explicit_instruction("archived", source_event_id="matrix-3")
    evolved = manager.process_explicit_instruction("evolved-v1", source_event_id="matrix-4")
    manager.soft_delete_memory(recycled)
    manager.archive_memory(archived)
    manager.update_memory_item(evolved, "evolved-v2", source_event_id="matrix-5")

    snapshots = manager.list_current()
    assert [snapshot.memory_id for snapshot in snapshots] == [active, evolved]
    assert snapshots[1].revision.revision_no == 2
    assert len(manager.get_history(evolved)) == 2
    assert manager.get_current(recycled) is None
    assert manager.get_current(archived) is None
