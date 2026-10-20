from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from typing import Iterator
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.memory.core.errors import (
    DuplicateEventConflictError,
    InvalidLifecycleTransitionError,
    LegacySchemaDetectedError,
    MemoryNotFoundError,
    MigrationError,
)
from src.memory.core.schema import (
    ActiveStatus,
    ChangeKind,
    GraphRelation,
    IngestionEvent,
    IngestionState,
    MemoryItem,
    MemoryMetadata,
    MemoryRevision,
    MemorySnapshot,
    MemoryType,
    Granularity,
    ProvenanceAuthority,
    ProvenanceRef,
    RelationType,
    RetentionDisposition,
    SemanticMetadata,
    SemanticValidity,
    bounded_excerpt,
    new_event_id,
    new_memory_id,
    new_provenance_id,
    new_revision_id,
    payload_hash,
)
from src.memory.storage.sqlite_utils import ClosingSQLiteConnection


SCHEMA_VERSION = 2


class SQLiteMemoryManager:
    """Task 1 v2 repository。普通打开 legacy DB 时绝不自动迁移。"""

    def __init__(self, db_path: str = "memory_store.db", *, provenance_excerpt_max_chars: int = 240):
        self.db_path = db_path
        self.provenance_excerpt_max_chars = provenance_excerpt_max_chars
        self._open_or_initialize()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.db_path,
            timeout=10.0,
            factory=ClosingSQLiteConnection,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = self._get_connection()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _table_names(conn: sqlite3.Connection) -> set[str]:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    def _open_or_initialize(self) -> None:
        with self._connection() as conn:
            tables = self._table_names(conn)
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version not in {0, SCHEMA_VERSION}:
                raise LegacySchemaDetectedError(
                    f"不支持的 schema version {version}，期望 0 或 {SCHEMA_VERSION}"
                )
            if not tables:
                self._create_v2_schema(conn)
                conn.commit()
                return
            if version == 0 and "memories" in tables:
                raise LegacySchemaDetectedError(
                    f"检测到 Phase 1A legacy schema：{self.db_path}；必须显式迁移"
                )
            if version != SCHEMA_VERSION:
                raise LegacySchemaDetectedError(
                    f"不支持的 schema version {version}，期望 {SCHEMA_VERSION}"
                )
            required = {"memory_items", "memory_revisions", "memory_revision_provenance", "ingestion_events"}
            if not required.issubset(tables):
                raise LegacySchemaDetectedError("schema version=2 但缺少 canonical tables")
            # Additive v2 migration: older v2 databases did not retain the
            # assistant half of a foreground turn.  Keep the schema version
            # stable because this is nullable journal metadata, not a change
            # to the canonical memory lifecycle.
            ingestion_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(ingestion_events)")
            }
            if "assistant_text" not in ingestion_columns:
                conn.execute("ALTER TABLE ingestion_events ADD COLUMN assistant_text TEXT")

    @staticmethod
    def _create_v2_schema(conn: sqlite3.Connection) -> None:
        # 逐条 execute 保持调用方 transaction；executescript 会隐式 COMMIT。
        statements = (
            """CREATE TABLE IF NOT EXISTS memory_items (
                memory_id TEXT PRIMARY KEY,
                memory_type TEXT NOT NULL,
                granularity TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                current_revision_id TEXT REFERENCES memory_revisions(revision_id)
                    ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED,
                importance_score REAL NOT NULL CHECK (importance_score BETWEEN 0 AND 1),
                access_count INTEGER NOT NULL CHECK (access_count >= 0),
                last_accessed REAL NOT NULL,
                decay_strength REAL NOT NULL CHECK (decay_strength BETWEEN 0 AND 1),
                retention_disposition TEXT NOT NULL,
                is_pinned INTEGER NOT NULL,
                enable_time_decay INTEGER NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS memory_revisions (
                revision_id TEXT PRIMARY KEY,
                memory_id TEXT NOT NULL REFERENCES memory_items(memory_id) ON DELETE CASCADE,
                revision_no INTEGER NOT NULL,
                content TEXT NOT NULL,
                semantic_validity TEXT NOT NULL,
                created_at REAL NOT NULL,
                event_time REAL,
                valid_from REAL NOT NULL,
                valid_until REAL,
                confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
                change_kind TEXT NOT NULL,
                supersedes_revision_id TEXT REFERENCES memory_revisions(revision_id),
                semantic_metadata_json TEXT NOT NULL,
                UNIQUE(memory_id, revision_no)
            )""",
            """CREATE UNIQUE INDEX IF NOT EXISTS one_current_revision_per_memory
                ON memory_revisions(memory_id)
                WHERE semantic_validity = 'CURRENT'""",
            """CREATE TABLE IF NOT EXISTS provenance_refs (
                provenance_id TEXT PRIMARY KEY,
                source_event_id TEXT NOT NULL,
                session_id TEXT,
                message_id TEXT,
                source_type TEXT NOT NULL,
                observed_at REAL NOT NULL,
                authority TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                controlled_excerpt TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS memory_revision_provenance (
                revision_id TEXT NOT NULL REFERENCES memory_revisions(revision_id) ON DELETE CASCADE,
                provenance_id TEXT NOT NULL REFERENCES provenance_refs(provenance_id) ON DELETE CASCADE,
                PRIMARY KEY (revision_id, provenance_id)
            )""",
            """CREATE TABLE IF NOT EXISTS ingestion_events (
                event_id TEXT PRIMARY KEY,
                session_id TEXT,
                message_id TEXT,
                payload_hash TEXT NOT NULL,
                raw_text TEXT,
                assistant_text TEXT,
                controlled_excerpt TEXT NOT NULL,
                state TEXT NOT NULL,
                created_at REAL NOT NULL,
                applied_at REAL,
                result_metadata_json TEXT,
                consolidation_run_id TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS graph_edges (
                source_id TEXT NOT NULL REFERENCES memory_items(memory_id) ON DELETE CASCADE,
                target_id TEXT NOT NULL,
                relation_type TEXT NOT NULL,
                weight REAL NOT NULL,
                PRIMARY KEY (source_id, target_id, relation_type)
            )""",
            """CREATE TABLE IF NOT EXISTS core_profiles (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at REAL NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS system_status (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at REAL NOT NULL
            )""",
            """CREATE TRIGGER IF NOT EXISTS current_revision_matches_memory_on_insert
                BEFORE INSERT ON memory_items
                WHEN NEW.current_revision_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM memory_revisions
                      WHERE revision_id=NEW.current_revision_id
                        AND memory_id=NEW.memory_id
                        AND semantic_validity='CURRENT'
                  )
                BEGIN
                    SELECT RAISE(ABORT, 'current revision must be CURRENT and belong to memory item');
                END""",
            """CREATE TRIGGER IF NOT EXISTS current_revision_matches_memory_on_update
                BEFORE UPDATE OF current_revision_id ON memory_items
                WHEN NEW.current_revision_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM memory_revisions
                      WHERE revision_id=NEW.current_revision_id
                        AND memory_id=NEW.memory_id
                        AND semantic_validity='CURRENT'
                  )
                BEGIN
                    SELECT RAISE(ABORT, 'current revision must be CURRENT and belong to memory item');
                END""",
        )
        for statement in statements:
            conn.execute(statement)
        conn.execute("PRAGMA user_version=2")

    @classmethod
    def migrate_legacy_database(cls, db_path: str, *, provenance_excerpt_max_chars: int = 240) -> Dict[str, int]:
        """显式、单事务 migration；失败时抛 MigrationError 并保持 legacy schema。"""
        conn = sqlite3.connect(db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("BEGIN IMMEDIATE")
            tables = cls._table_names(conn)
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version != 0 or "memories" not in tables:
                raise MigrationError("输入不是可识别的 Phase 1A legacy schema")

            legacy_rows = conn.execute("SELECT * FROM memories ORDER BY memory_id").fetchall()
            legacy_edges = conn.execute("SELECT * FROM graph_edges").fetchall() if "graph_edges" in tables else []
            conn.execute("ALTER TABLE memories RENAME TO legacy_memories")
            if "graph_edges" in tables:
                conn.execute("ALTER TABLE graph_edges RENAME TO legacy_graph_edges")
            cls._create_v2_schema(conn)
            cls._copy_legacy_rows(conn, legacy_rows, legacy_edges)
            conn.execute("DROP TABLE legacy_memories")
            if "legacy_graph_edges" in cls._table_names(conn):
                conn.execute("DROP TABLE legacy_graph_edges")
            conn.commit()
            return {"memory_items": len(legacy_rows), "revisions": len(legacy_rows)}
        except Exception as exc:
            conn.rollback()
            if isinstance(exc, MigrationError):
                raise
            raise MigrationError("legacy migration 失败，transaction 已回滚") from exc
        finally:
            conn.close()

    @staticmethod
    def _copy_legacy_rows(conn: sqlite3.Connection, rows: Sequence[sqlite3.Row], edges: Sequence[sqlite3.Row]) -> None:
        now = time.time()
        for row in rows:
            metadata = json.loads(row["metadata_json"])
            legacy_status = row["status"]
            if legacy_status == ActiveStatus.ACTIVE.value:
                retention, validity = RetentionDisposition.ACTIVE, SemanticValidity.CURRENT
            elif legacy_status == ActiveStatus.IN_RECYCLE_BIN.value:
                retention, validity = RetentionDisposition.RECYCLED, SemanticValidity.CURRENT
            elif legacy_status == ActiveStatus.DEPRECATED.value:
                retention, validity = RetentionDisposition.ARCHIVED, SemanticValidity.SUPERSEDED
            elif legacy_status == ActiveStatus.CONTRADICTED.value:
                retention, validity = RetentionDisposition.RECYCLED, SemanticValidity.CONTRADICTED
            else:
                raise MigrationError(f"无法映射 legacy status: {legacy_status}")
            revision_id = new_revision_id()
            created_at = float(row["created_at"])
            scopes = metadata.get("valid_scope", [])
            semantic_metadata = SemanticMetadata.from_dict({"scopes": scopes})
            conn.execute(
                """INSERT INTO memory_items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (row["memory_id"], row["memory_type"], row["granularity"], created_at, now,
                 None,
                 float(metadata.get("importance_score", 0.5)), int(metadata.get("access_count", 1)),
                 float(metadata.get("last_accessed", row["last_accessed"])),
                 float(metadata.get("decay_strength", 1.0)), retention.value,
                 int(bool(metadata.get("is_pinned", False))), int(bool(metadata.get("enable_time_decay", False))))
            )
            conn.execute(
                """INSERT INTO memory_revisions VALUES (?, ?, 1, ?, ?, ?, NULL, ?, NULL, ?, ?, NULL, ?)""",
                (revision_id, row["memory_id"], row["content"], validity.value, created_at, created_at,
                 float(metadata.get("confidence", 0.8)), ChangeKind.LEGACY_IMPORT.value,
                 json.dumps(semantic_metadata.to_dict(), ensure_ascii=False))
            )
            if validity is SemanticValidity.CURRENT:
                conn.execute(
                    "UPDATE memory_items SET current_revision_id=? WHERE memory_id=?",
                    (revision_id, row["memory_id"]),
                )
        for edge in edges:
            conn.execute(
                "INSERT INTO graph_edges VALUES (?, ?, ?, ?)",
                (edge["source_id"], edge["target_id"], edge["relation_type"], edge["weight"]),
            )

    def set_system_status(self, key: str, value: str) -> None:
        with self._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO system_status VALUES (?, ?, ?)",
                (key, str(value), time.time()),
            )

    def get_system_status(self, key: str, default_val: str = "") -> str:
        with self._connection() as conn:
            row = conn.execute("SELECT value FROM system_status WHERE key=?", (key,)).fetchone()
            return row["value"] if row else default_val

    def register_ingestion_event(
        self,
        raw_text: str,
        *,
        assistant_text: Optional[str] = None,
        event_id: Optional[str] = None,
        session_id: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> Tuple[IngestionEvent, bool]:
        event_id = event_id or new_event_id()
        normalized_assistant = assistant_text if assistant_text else None
        digest = payload_hash(
            raw_text if normalized_assistant is None else f"{raw_text}\x00{normalized_assistant}"
        )
        with self._connection() as conn:
            existing = conn.execute("SELECT * FROM ingestion_events WHERE event_id=?", (event_id,)).fetchone()
            if existing:
                if (
                    existing["payload_hash"] != digest
                    or existing["session_id"] != session_id
                    or existing["message_id"] != message_id
                ):
                    raise DuplicateEventConflictError(
                        f"event_id {event_id} 已绑定不同 payload/source identity"
                    )
                return self._row_to_event(existing), True
            conn.execute(
                """INSERT INTO ingestion_events
                   (event_id, session_id, message_id, payload_hash, raw_text, assistant_text, controlled_excerpt,
                    state, created_at, applied_at, result_metadata_json, consolidation_run_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)""",
                (event_id, session_id, message_id, digest, raw_text, normalized_assistant,
                 bounded_excerpt(raw_text, self.provenance_excerpt_max_chars),
                 IngestionState.PENDING.value, time.time()),
            )
            row = conn.execute("SELECT * FROM ingestion_events WHERE event_id=?", (event_id,)).fetchone()
            return self._row_to_event(row), False

    def get_ingestion_event(self, event_id: str) -> Optional[IngestionEvent]:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM ingestion_events WHERE event_id=?", (event_id,)).fetchone()
            return self._row_to_event(row) if row else None

    def list_pending_events(self) -> List[IngestionEvent]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM ingestion_events WHERE state=? ORDER BY created_at, event_id",
                (IngestionState.PENDING.value,),
            ).fetchall()
            return [self._row_to_event(row) for row in rows]

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> IngestionEvent:
        return IngestionEvent(
            event_id=row["event_id"], session_id=row["session_id"], message_id=row["message_id"],
            payload_hash=row["payload_hash"], raw_text=row["raw_text"],
            assistant_text=row["assistant_text"] if "assistant_text" in row.keys() else None,
            controlled_excerpt=row["controlled_excerpt"], state=IngestionState(row["state"]),
            created_at=row["created_at"], applied_at=row["applied_at"],
            result_metadata=json.loads(row["result_metadata_json"]) if row["result_metadata_json"] else None,
            consolidation_run_id=row["consolidation_run_id"],
        )

    def get_memory(self, memory_id: str) -> Optional[MemoryItem]:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM memory_items WHERE memory_id=?", (memory_id,)).fetchone()
            return self._row_to_item(conn, row) if row else None

    def list_memories(self, status: Optional[ActiveStatus] = None) -> List[MemoryItem]:
        retention = None
        if status is not None:
            retention = {
                ActiveStatus.ACTIVE: RetentionDisposition.ACTIVE,
                ActiveStatus.IN_RECYCLE_BIN: RetentionDisposition.RECYCLED,
                ActiveStatus.DEPRECATED: RetentionDisposition.ARCHIVED,
                ActiveStatus.CONTRADICTED: RetentionDisposition.RECYCLED,
            }[status]
        with self._connection() as conn:
            if retention:
                rows = conn.execute("SELECT * FROM memory_items WHERE retention_disposition=?", (retention.value,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM memory_items").fetchall()
            return [self._row_to_item(conn, row) for row in rows]

    def _row_to_item(self, conn: sqlite3.Connection, row: sqlite3.Row) -> MemoryItem:
        current = None
        if row["current_revision_id"]:
            rev_row = conn.execute("SELECT * FROM memory_revisions WHERE revision_id=?", (row["current_revision_id"],)).fetchone()
            current = self._row_to_revision(rev_row) if rev_row else None
        edges = conn.execute("SELECT * FROM graph_edges WHERE source_id=?", (row["memory_id"],)).fetchall()
        item = MemoryItem(
            memory_id=row["memory_id"], memory_type=MemoryType(row["memory_type"]),
            granularity=Granularity(row["granularity"]), created_at=row["created_at"],
            updated_at=row["updated_at"], current_revision_id=row["current_revision_id"],
            importance_score=row["importance_score"], access_count=row["access_count"],
            last_accessed=row["last_accessed"], decay_strength=row["decay_strength"],
            retention_disposition=RetentionDisposition(row["retention_disposition"]),
            is_pinned=bool(row["is_pinned"]), enable_time_decay=bool(row["enable_time_decay"]),
            graph_relations=[GraphRelation(e["target_id"], RelationType(e["relation_type"]), e["weight"]) for e in edges],
            metadata=MemoryMetadata(
                timestamp=row["created_at"], last_accessed=row["last_accessed"],
                importance_score=row["importance_score"], access_count=row["access_count"],
                decay_strength=row["decay_strength"], is_pinned=bool(row["is_pinned"]),
                enable_time_decay=bool(row["enable_time_decay"]),
            ),
        )
        item.attach_current_revision(current)
        return item

    @staticmethod
    def _row_to_revision(row: sqlite3.Row) -> MemoryRevision:
        revision = MemoryRevision(
            revision_id=row["revision_id"], memory_id=row["memory_id"], revision_no=row["revision_no"],
            content=row["content"], semantic_validity=SemanticValidity(row["semantic_validity"]),
            created_at=row["created_at"], event_time=row["event_time"], valid_from=row["valid_from"],
            valid_until=row["valid_until"], confidence=row["confidence"], change_kind=ChangeKind(row["change_kind"]),
            supersedes_revision_id=row["supersedes_revision_id"],
            semantic_metadata=SemanticMetadata.from_dict(json.loads(row["semantic_metadata_json"])),
        )
        revision.validate()
        return revision

    def get_history(self, memory_id: str) -> List[MemoryRevision]:
        with self._connection() as conn:
            exists = conn.execute("SELECT 1 FROM memory_items WHERE memory_id=?", (memory_id,)).fetchone()
            if not exists:
                raise MemoryNotFoundError(memory_id)
            rows = conn.execute(
                "SELECT * FROM memory_revisions WHERE memory_id=? ORDER BY revision_no", (memory_id,)
            ).fetchall()
            return [self._row_to_revision(row) for row in rows]

    def get_current(self, memory_id: str) -> Optional[MemorySnapshot]:
        with self._connection() as conn:
            row = conn.execute(
                """SELECT * FROM memory_items
                   WHERE memory_id=? AND retention_disposition=? AND current_revision_id IS NOT NULL""",
                (memory_id, RetentionDisposition.ACTIVE.value),
            ).fetchone()
            return self._snapshot(conn, row) if row else None

    def list_current(self) -> List[MemorySnapshot]:
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT * FROM memory_items
                   WHERE retention_disposition=? AND current_revision_id IS NOT NULL ORDER BY created_at""",
                (RetentionDisposition.ACTIVE.value,),
            ).fetchall()
            return [self._snapshot(conn, row) for row in rows]

    def _snapshot(self, conn: sqlite3.Connection, item_row: sqlite3.Row) -> MemorySnapshot:
        rev_row = conn.execute(
            "SELECT * FROM memory_revisions WHERE revision_id=? AND semantic_validity=?",
            (item_row["current_revision_id"], SemanticValidity.CURRENT.value),
        ).fetchone()
        if not rev_row:
            raise InvalidLifecycleTransitionError("current_revision_id 未指向 CURRENT revision")
        revision = self._row_to_revision(rev_row)
        prov_rows = conn.execute(
            """SELECT p.* FROM provenance_refs p JOIN memory_revision_provenance rp
               ON p.provenance_id=rp.provenance_id WHERE rp.revision_id=? ORDER BY p.observed_at""",
            (revision.revision_id,),
        ).fetchall()
        provenance = tuple(self._row_to_provenance(row) for row in prov_rows)
        return MemorySnapshot(
            memory_id=item_row["memory_id"], memory_type=MemoryType(item_row["memory_type"]),
            granularity=Granularity(item_row["granularity"]),
            retention_disposition=RetentionDisposition(item_row["retention_disposition"]),
            revision=revision, provenance=provenance, importance_score=item_row["importance_score"],
            access_count=item_row["access_count"], last_accessed=item_row["last_accessed"],
            decay_strength=item_row["decay_strength"], is_pinned=bool(item_row["is_pinned"]),
        )

    @staticmethod
    def _row_to_provenance(row: sqlite3.Row) -> ProvenanceRef:
        return ProvenanceRef(
            provenance_id=row["provenance_id"], source_event_id=row["source_event_id"],
            session_id=row["session_id"], message_id=row["message_id"], source_type=row["source_type"],
            observed_at=row["observed_at"], authority=ProvenanceAuthority(row["authority"]),
            content_hash=row["content_hash"], controlled_excerpt=row["controlled_excerpt"],
        )

    def get_revision_provenance(self, revision_id: str) -> List[ProvenanceRef]:
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT p.* FROM provenance_refs p JOIN memory_revision_provenance rp
                   ON p.provenance_id=rp.provenance_id WHERE rp.revision_id=?""", (revision_id,)
            ).fetchall()
            return [self._row_to_provenance(row) for row in rows]

    def save_operational_state(self, item: MemoryItem) -> None:
        with self._connection() as conn:
            conn.execute(
                """UPDATE memory_items SET updated_at=?, importance_score=?, access_count=?, last_accessed=?,
                   decay_strength=?, retention_disposition=?, is_pinned=?, enable_time_decay=? WHERE memory_id=?""",
                (time.time(), item.importance_score, item.access_count, item.last_accessed, item.decay_strength,
                 item.retention_disposition.value, int(item.is_pinned), int(item.enable_time_decay), item.memory_id),
            )
            self._replace_edges(conn, item)

    def save_memory(self, item: MemoryItem) -> None:
        """DEPRECATED Phase 1A adapter；canonical lifecycle 不调用此方法。"""
        if self.get_memory(item.memory_id):
            self.save_operational_state(item)
            return
        self.apply_add(item)

    def apply_add(
        self, item: MemoryItem, *, event_ids: Sequence[str] = (), authority: ProvenanceAuthority = ProvenanceAuthority.UNKNOWN,
        change_kind: ChangeKind = ChangeKind.ADD,
    ) -> MemoryRevision:
        """DEPRECATED Phase 1A MemoryItem facade adapter。"""
        return self.create_memory(
            memory_id=item.memory_id,
            memory_type=item.memory_type,
            granularity=item.granularity,
            content=item.content,
            created_at=item.metadata.timestamp,
            importance_score=item.metadata.importance_score,
            access_count=item.metadata.access_count,
            last_accessed=item.metadata.last_accessed,
            decay_strength=item.metadata.decay_strength,
            retention_disposition=item.retention_disposition,
            is_pinned=item.metadata.is_pinned,
            enable_time_decay=item.metadata.enable_time_decay,
            confidence=item.metadata.confidence,
            semantic_metadata=SemanticMetadata.from_dict({"scopes": item.metadata.valid_scope}),
            graph_relations=item.graph_relations,
            event_ids=event_ids,
            authority=authority,
            change_kind=change_kind,
        )

    def create_memory(
        self,
        *,
        content: str,
        memory_type: MemoryType,
        granularity: Granularity,
        memory_id: Optional[str] = None,
        created_at: Optional[float] = None,
        importance_score: float = 0.5,
        access_count: int = 1,
        last_accessed: Optional[float] = None,
        decay_strength: float = 1.0,
        retention_disposition: RetentionDisposition = RetentionDisposition.ACTIVE,
        is_pinned: bool = False,
        enable_time_decay: bool = False,
        confidence: float = 0.8,
        semantic_metadata: Optional[SemanticMetadata] = None,
        graph_relations: Sequence[GraphRelation] = (),
        event_ids: Sequence[str] = (),
        authority: ProvenanceAuthority = ProvenanceAuthority.UNKNOWN,
        change_kind: ChangeKind = ChangeKind.ADD,
    ) -> MemoryRevision:
        """Canonical typed ADD path；不读取 compatibility projections。"""
        memory_id = memory_id or new_memory_id()
        now = time.time()
        content = content.strip()
        if not content:
            raise InvalidLifecycleTransitionError("ADD content 不能为空")
        if not 0.0 <= importance_score <= 1.0 or not 0.0 <= decay_strength <= 1.0:
            raise InvalidLifecycleTransitionError("operational score 必须位于 [0,1]")
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            revision = self._insert_new_identity_and_revision(
                conn,
                memory_id=memory_id,
                memory_type=memory_type,
                granularity=granularity,
                content=content,
                created_at=created_at if created_at is not None else now,
                importance_score=importance_score,
                access_count=access_count,
                last_accessed=last_accessed if last_accessed is not None else now,
                decay_strength=decay_strength,
                retention_disposition=retention_disposition,
                is_pinned=is_pinned,
                enable_time_decay=enable_time_decay,
                semantic_metadata=semantic_metadata or SemanticMetadata(),
                confidence=confidence,
                change_kind=change_kind,
                now=now,
            )
            self._attach_event_provenance(conn, revision.revision_id, event_ids, authority)
            self._mark_events_applied(conn, event_ids, {"memory_id": memory_id, "revision_id": revision.revision_id})
            self._replace_edges_values(conn, memory_id, graph_relations)
            conn.commit()
            return revision

    def apply_revision(
        self, memory_id: str, content: str, *, change_kind: ChangeKind,
        event_ids: Sequence[str] = (), authority: ProvenanceAuthority = ProvenanceAuthority.UNKNOWN,
        confidence: Optional[float] = None, semantic_metadata: Optional[SemanticMetadata] = None,
    ) -> MemoryRevision:
        if change_kind not in {ChangeKind.UPDATE, ChangeKind.CORRECT}:
            raise InvalidLifecycleTransitionError("apply_revision 仅支持 UPDATE/CORRECT")
        now = time.time()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            item_row = conn.execute("SELECT * FROM memory_items WHERE memory_id=?", (memory_id,)).fetchone()
            if not item_row:
                raise MemoryNotFoundError(memory_id)
            old_row = conn.execute(
                "SELECT * FROM memory_revisions WHERE revision_id=? AND semantic_validity=?",
                (item_row["current_revision_id"], SemanticValidity.CURRENT.value),
            ).fetchone()
            if not old_row:
                raise InvalidLifecycleTransitionError("target 没有 CURRENT revision")
            old = self._row_to_revision(old_row)
            old_state = SemanticValidity.SUPERSEDED if change_kind is ChangeKind.UPDATE else SemanticValidity.CONTRADICTED
            conn.execute(
                "UPDATE memory_revisions SET semantic_validity=?, valid_until=? WHERE revision_id=?",
                (old_state.value, now, old.revision_id),
            )
            new_revision = MemoryRevision(
                revision_id=new_revision_id(), memory_id=memory_id, revision_no=old.revision_no + 1,
                content=content.strip(), semantic_validity=SemanticValidity.CURRENT, created_at=now,
                event_time=now, valid_from=now, valid_until=None,
                confidence=old.confidence if confidence is None else confidence,
                change_kind=change_kind, supersedes_revision_id=old.revision_id,
                semantic_metadata=semantic_metadata or old.semantic_metadata,
            )
            new_revision.validate()
            self._insert_revision(conn, new_revision)
            conn.execute(
                "UPDATE memory_items SET current_revision_id=?, updated_at=? WHERE memory_id=?",
                (new_revision.revision_id, now, memory_id),
            )
            self._attach_event_provenance(conn, new_revision.revision_id, event_ids, authority)
            self._mark_events_applied(conn, event_ids, {"memory_id": memory_id, "revision_id": new_revision.revision_id})
            conn.commit()
            return new_revision

    def apply_noop(
        self,
        event_ids: Sequence[str],
        *,
        consolidation_run_id: Optional[str] = None,
        result_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            result = {"operation": "NOOP"}
            if result_metadata:
                result.update(result_metadata)
            self._mark_events_applied(conn, event_ids, result, consolidation_run_id)
            conn.commit()

    def apply_proposals_atomic(
        self,
        proposals: Sequence[Any],
        event_ids: Sequence[str],
        *,
        consolidation_run_id: str,
        failure_hook: Optional[Any] = None,
        authority: Optional[ProvenanceAuthority] = None,
        enable_time_decay: bool = False,
    ) -> List[Tuple[str, str]]:
        """Validated proposal batch 的单 transaction apply；供 Slow Path 与 Explicit Fast Path 使用。"""
        from src.memory.core.schema import LifecycleOperation

        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            results: List[Tuple[str, str]] = []
            for proposal in proposals:
                if proposal.operation is LifecycleOperation.NOOP:
                    continue
                if proposal.operation is LifecycleOperation.ADD:
                    memory_id = new_memory_id()
                    now = time.time()
                    revision = self._insert_new_identity_and_revision(
                        conn,
                        memory_id=memory_id,
                        memory_type=proposal.memory_type,
                        granularity=proposal.granularity,
                        content=proposal.content or "",
                        created_at=now,
                        importance_score=(
                            proposal.importance_score
                            if proposal.importance_score is not None
                            else 0.5
                        ),
                        access_count=1,
                        last_accessed=now,
                        decay_strength=1.0,
                        retention_disposition=RetentionDisposition.ACTIVE,
                        is_pinned=False,
                        enable_time_decay=enable_time_decay,
                        semantic_metadata=proposal.semantic_metadata,
                        confidence=proposal.confidence,
                        change_kind=ChangeKind.ADD,
                        now=now,
                    )
                    eff_authority = authority or ProvenanceAuthority.INFERRED
                    self._attach_event_provenance(conn, revision.revision_id, proposal.provenance_event_ids, eff_authority)
                    results.append((memory_id, revision.revision_id))
                elif proposal.operation in {LifecycleOperation.UPDATE, LifecycleOperation.CORRECT}:
                    memory_id = proposal.target_memory_id or ""
                    item_row = conn.execute(
                        "SELECT * FROM memory_items WHERE memory_id=?", (memory_id,)
                    ).fetchone()
                    if not item_row:
                        raise MemoryNotFoundError(memory_id)
                    old_row = conn.execute(
                        "SELECT * FROM memory_revisions WHERE revision_id=? AND semantic_validity=?",
                        (item_row["current_revision_id"], SemanticValidity.CURRENT.value),
                    ).fetchone()
                    if not old_row:
                        raise InvalidLifecycleTransitionError("proposal target 没有 CURRENT revision")
                    old = self._row_to_revision(old_row)
                    change_kind = (
                        ChangeKind.UPDATE
                        if proposal.operation is LifecycleOperation.UPDATE
                        else ChangeKind.CORRECT
                    )
                    old_state = (
                        SemanticValidity.SUPERSEDED
                        if change_kind is ChangeKind.UPDATE
                        else SemanticValidity.CONTRADICTED
                    )
                    transition_time = time.time()
                    conn.execute(
                        "UPDATE memory_revisions SET semantic_validity=?, valid_until=? WHERE revision_id=?",
                        (old_state.value, transition_time, old.revision_id),
                    )
                    revision = MemoryRevision(
                        revision_id=new_revision_id(), memory_id=memory_id,
                        revision_no=old.revision_no + 1, content=proposal.content or "",
                        semantic_validity=SemanticValidity.CURRENT, created_at=transition_time,
                        event_time=transition_time, valid_from=transition_time, valid_until=None,
                        confidence=proposal.confidence, change_kind=change_kind,
                        supersedes_revision_id=old.revision_id,
                        semantic_metadata=proposal.semantic_metadata,
                    )
                    revision.validate()
                    self._insert_revision(conn, revision)
                    conn.execute(
                        "UPDATE memory_items SET current_revision_id=?, updated_at=?, importance_score=? WHERE memory_id=?",
                        (
                            revision.revision_id,
                            transition_time,
                            proposal.importance_score
                            if proposal.importance_score is not None
                            else item_row["importance_score"],
                            memory_id,
                        ),
                    )
                    eff_authority = (
                        authority
                        if authority is not None
                        else (
                            ProvenanceAuthority.EXPLICIT_USER
                            if change_kind is ChangeKind.CORRECT
                            else ProvenanceAuthority.INFERRED
                        )
                    )
                    self._attach_event_provenance(
                        conn, revision.revision_id, proposal.provenance_event_ids, eff_authority
                    )
                    results.append((memory_id, revision.revision_id))
                elif proposal.operation is LifecycleOperation.REMOVE:
                    memory_id = proposal.target_memory_id or ""
                    item_row = conn.execute(
                        "SELECT * FROM memory_items WHERE memory_id=? AND retention_disposition=?",
                        (memory_id, RetentionDisposition.ACTIVE.value),
                    ).fetchone()
                    if not item_row:
                        raise InvalidLifecycleTransitionError(
                            "REMOVE target 不存在或已经不在 ACTIVE 状态"
                        )
                    if bool(item_row["is_pinned"]):
                        raise InvalidLifecycleTransitionError("REMOVE 不得作用于已锁定记忆")
                    if item_row["memory_type"] not in {
                        MemoryType.CORE_PROFILE.value,
                        MemoryType.PREFERENCE_RULES.value,
                    }:
                        raise InvalidLifecycleTransitionError("REMOVE 只能作用于用户特征记忆")
                    transition_time = time.time()
                    conn.execute(
                        """UPDATE memory_items
                           SET retention_disposition=?, updated_at=?, last_accessed=?
                           WHERE memory_id=?""",
                        (
                            RetentionDisposition.RECYCLED.value,
                            transition_time,
                            transition_time,
                            memory_id,
                        ),
                    )
                    results.append((memory_id, RetentionDisposition.RECYCLED.value))
                else:
                    raise InvalidLifecycleTransitionError(
                        f"不支持的 lifecycle proposal: {proposal.operation.value}"
                    )
            if failure_hook:
                failure_hook()
            if results:
                first_memory_id, first_result_id = results[0]
                res_meta = {"memory_id": first_memory_id, "results": results}
                if str(first_result_id).startswith("rev_"):
                    res_meta["revision_id"] = first_result_id
                else:
                    res_meta["retention"] = first_result_id
            else:
                res_meta = {"results": []}
            self._mark_events_applied(conn, event_ids, res_meta, consolidation_run_id)
            conn.commit()
            return results

    def _insert_new_identity_and_revision(
        self,
        conn: sqlite3.Connection,
        *,
        memory_id: str,
        memory_type: MemoryType,
        granularity: Granularity,
        content: str,
        created_at: float,
        importance_score: float,
        access_count: int,
        last_accessed: float,
        decay_strength: float,
        retention_disposition: RetentionDisposition,
        is_pinned: bool,
        enable_time_decay: bool,
        semantic_metadata: SemanticMetadata,
        confidence: float,
        change_kind: ChangeKind,
        now: float,
    ) -> MemoryRevision:
        revision = MemoryRevision(
            revision_id=new_revision_id(), memory_id=memory_id, revision_no=1, content=content,
            semantic_validity=SemanticValidity.CURRENT, created_at=now,
            event_time=now, valid_from=now, valid_until=None, confidence=confidence,
            change_kind=change_kind, semantic_metadata=semantic_metadata,
        )
        revision.validate()
        conn.execute(
            """INSERT INTO memory_items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (memory_id, memory_type.value, granularity.value, created_at,
             now, None, importance_score, access_count, last_accessed, decay_strength,
             retention_disposition.value, int(is_pinned), int(enable_time_decay)),
        )
        self._insert_revision(conn, revision)
        conn.execute(
            "UPDATE memory_items SET current_revision_id=? WHERE memory_id=?",
            (revision.revision_id, memory_id),
        )
        return revision

    @staticmethod
    def _insert_revision(conn: sqlite3.Connection, revision: MemoryRevision) -> None:
        conn.execute(
            """INSERT INTO memory_revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (revision.revision_id, revision.memory_id, revision.revision_no, revision.content,
             revision.semantic_validity.value, revision.created_at, revision.event_time,
             revision.valid_from, revision.valid_until, revision.confidence, revision.change_kind.value,
             revision.supersedes_revision_id,
             json.dumps(revision.semantic_metadata.to_dict(), ensure_ascii=False)),
        )

    def _attach_event_provenance(
        self, conn: sqlite3.Connection, revision_id: str, event_ids: Sequence[str], authority: ProvenanceAuthority,
    ) -> None:
        for event_id in event_ids:
            event = conn.execute("SELECT * FROM ingestion_events WHERE event_id=?", (event_id,)).fetchone()
            if not event:
                raise InvalidLifecycleTransitionError(f"provenance event 不存在: {event_id}")
            provenance_id = new_provenance_id()
            conn.execute(
                """INSERT INTO provenance_refs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (provenance_id, event_id, event["session_id"], event["message_id"], "INGESTION_EVENT",
                 event["created_at"], authority.value, event["payload_hash"], event["controlled_excerpt"]),
            )
            conn.execute("INSERT INTO memory_revision_provenance VALUES (?, ?)", (revision_id, provenance_id))

    @staticmethod
    def _mark_events_applied(
        conn: sqlite3.Connection, event_ids: Sequence[str], result: Dict[str, Any],
        consolidation_run_id: Optional[str] = None,
    ) -> None:
        now = time.time()
        for event_id in event_ids:
            row = conn.execute("SELECT state FROM ingestion_events WHERE event_id=?", (event_id,)).fetchone()
            if not row:
                raise InvalidLifecycleTransitionError(f"event 不存在: {event_id}")
            if row["state"] == IngestionState.APPLIED.value:
                continue
            conn.execute(
                """UPDATE ingestion_events SET state=?, raw_text=NULL, assistant_text=NULL, applied_at=?,
                   result_metadata_json=?, consolidation_run_id=? WHERE event_id=?""",
                (IngestionState.APPLIED.value, now, json.dumps(result, ensure_ascii=False), consolidation_run_id, event_id),
            )

    @staticmethod
    def _replace_edges(conn: sqlite3.Connection, item: MemoryItem) -> None:
        """DEPRECATED Phase 1A adapter。"""
        SQLiteMemoryManager._replace_edges_values(conn, item.memory_id, item.graph_relations)

    @staticmethod
    def _replace_edges_values(
        conn: sqlite3.Connection, memory_id: str, relations: Sequence[GraphRelation]
    ) -> None:
        conn.execute("DELETE FROM graph_edges WHERE source_id=?", (memory_id,))
        for relation in relations:
            conn.execute(
                "INSERT OR REPLACE INTO graph_edges VALUES (?, ?, ?, ?)",
                (memory_id, relation.target_id, relation.relation_type.value, relation.weight),
            )

    def set_retention(self, memory_id: str, disposition: RetentionDisposition) -> bool:
        with self._connection() as conn:
            row = conn.execute("SELECT retention_disposition FROM memory_items WHERE memory_id=?", (memory_id,)).fetchone()
            if not row:
                return False
            if row["retention_disposition"] == disposition.value:
                return False
            conn.execute(
                "UPDATE memory_items SET retention_disposition=?, updated_at=?, last_accessed=? WHERE memory_id=?",
                (disposition.value, time.time(), time.time(), memory_id),
            )
            return True

    def cleanup_recycle_bin(self, retention_days: int = 30) -> int:
        cutoff = time.time() - retention_days * 86400
        with self._connection() as conn:
            ids = [r[0] for r in conn.execute(
                "SELECT memory_id FROM memory_items WHERE retention_disposition=? AND last_accessed<?",
                (RetentionDisposition.RECYCLED.value, cutoff),
            )]
            for memory_id in ids:
                conn.execute("DELETE FROM memory_items WHERE memory_id=?", (memory_id,))
            return len(ids)

    def set_core_profile(self, key: str, value: str) -> None:
        with self._connection() as conn:
            conn.execute("INSERT OR REPLACE INTO core_profiles VALUES (?, ?, ?)", (key, value, time.time()))

    def get_all_core_profiles(self) -> Dict[str, str]:
        with self._connection() as conn:
            return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM core_profiles")}
