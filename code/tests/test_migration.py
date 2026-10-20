import hashlib
import json
import sqlite3

import pytest

from src.memory.core.errors import LegacySchemaDetectedError, MigrationError
from src.memory.core.schema import RetentionDisposition, SemanticValidity
from src.memory.storage.sqlite_manager import SQLiteMemoryManager


def create_legacy_db(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE memories (
            memory_id TEXT PRIMARY KEY, content TEXT NOT NULL, memory_type TEXT NOT NULL,
            granularity TEXT NOT NULL, status TEXT NOT NULL, metadata_json TEXT NOT NULL,
            created_at REAL NOT NULL, last_accessed REAL NOT NULL
        );
        CREATE TABLE graph_edges (
            source_id TEXT NOT NULL, target_id TEXT NOT NULL, relation_type TEXT NOT NULL,
            weight REAL NOT NULL, PRIMARY KEY (source_id, target_id, relation_type)
        );
        CREATE TABLE core_profiles (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at REAL NOT NULL);
        CREATE TABLE system_status (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at REAL NOT NULL);
    """)
    metadata = json.dumps({
        "importance_score": 0.7, "confidence": 0.8, "access_count": 2,
        "decay_strength": 0.9, "last_accessed": 100.0, "valid_scope": ["travel"],
        "is_pinned": False, "enable_time_decay": True,
    })
    conn.execute(
        "INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("mem-active", "active content", "PREFERENCE_RULES", "SEMANTIC_INSIGHT", "ACTIVE", metadata, 50.0, 100.0),
    )
    conn.execute(
        "INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("mem-recycled", "recycled content", "EPISODIC_GRAPH", "FINE_EPISODE", "IN_RECYCLE_BIN", metadata, 60.0, 100.0),
    )
    conn.commit()
    conn.close()


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_legacy_normal_startup_refuses_without_mutation(tmp_path):
    path = tmp_path / "legacy.db"
    create_legacy_db(path)
    before = file_hash(path)
    with pytest.raises(LegacySchemaDetectedError):
        SQLiteMemoryManager(str(path))
    assert file_hash(path) == before
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM memories").fetchone()[0] == 2


def test_explicit_legacy_migration_preserves_ids_and_maps_initial_revisions(tmp_path):
    path = tmp_path / "legacy.db"
    create_legacy_db(path)
    report = SQLiteMemoryManager.migrate_legacy_database(str(path))
    repository = SQLiteMemoryManager(str(path))

    assert report == {"memory_items": 2, "revisions": 2}
    active = repository.get_memory("mem-active")
    recycled = repository.get_memory("mem-recycled")
    assert active.retention_disposition is RetentionDisposition.ACTIVE
    assert repository.get_history("mem-active")[0].semantic_validity is SemanticValidity.CURRENT
    assert recycled.retention_disposition is RetentionDisposition.RECYCLED
    assert repository.get_revision_provenance(repository.get_history("mem-active")[0].revision_id) == []


def test_migration_failure_rolls_back_to_usable_legacy_schema(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    create_legacy_db(path)
    before = file_hash(path)

    def fail_copy(conn, rows, edges):
        raise RuntimeError("synthetic migration failure")

    monkeypatch.setattr(SQLiteMemoryManager, "_copy_legacy_rows", staticmethod(fail_copy))
    with pytest.raises(MigrationError):
        SQLiteMemoryManager.migrate_legacy_database(str(path))

    assert file_hash(path) == before
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM memories").fetchone()[0] == 2
        assert "memory_items" not in {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
