from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from typing import Iterator, Optional, Sequence, Tuple

from src.memory.task_state.models import (
    ContinuationCheckpoint,
    EntryKind,
    EntryStatus,
    EvidenceRef,
    LedgerEntry,
    Priority,
    ProposalBasis,
    RelationKind,
    SourceKind,
    TaskEvent,
    TaskAnalysis,
    TaskLedger,
    TaskStateProposal,
    TaskStateProposalBatch,
    TaskStateUpdateReceipt,
    TaskStatus,
    EventProcessStatus,
    ProposalAction,
    TaskRelation,
)
from src.memory.storage.sqlite_utils import ClosingSQLiteConnection


TASK_STATE_SCHEMA_VERSION = 5


class TaskStateRepository:
    """Durable Task3 store; intentionally separate from Task1 SQLite tables."""

    def __init__(self, db_path: str = "task_state.db") -> None:
        self.db_path = db_path
        self._open_or_initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.db_path,
            timeout=10.0,
            factory=ClosingSQLiteConnection,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _open_or_initialize(self) -> None:
        with self._connection() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if not tables:
                self._create_schema(connection)
                connection.commit()
                return
            if version == 1:
                self._migrate_v1_to_v2(connection)
                version = 2
            if version == 2:
                self._migrate_v2_to_v3(connection)
                version = 3
            if version == 3:
                self._migrate_v3_to_v4(connection)
                version = 4
            if version == 4:
                self._migrate_v4_to_v5(connection)
                version = 5
            if version != TASK_STATE_SCHEMA_VERSION:
                raise ValueError(
                    f"unsupported task-state schema {version}; expected {TASK_STATE_SCHEMA_VERSION}"
                )
            self._backfill_legacy_tool_payloads(connection)
            connection.commit()
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            required = {"task_ledgers", "task_entries", "task_relations", "task_checkpoints", "task_operations", "task_events"}
            if not required.issubset(tables):
                raise ValueError("task-state schema is incomplete")

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        statements = (
            """CREATE TABLE task_ledgers (
                task_id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )""",
            """CREATE TABLE task_entries (
                entry_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES task_ledgers(task_id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                content TEXT NOT NULL,
                status TEXT NOT NULL,
                priority INTEGER NOT NULL CHECK(priority BETWEEN 1 AND 4),
                source_kind TEXT NOT NULL DEFAULT 'USER',
                basis TEXT NOT NULL DEFAULT 'EXPLICIT',
                source_ref TEXT,
                evidence_refs_json TEXT NOT NULL,
                supersedes_entry_id TEXT REFERENCES task_entries(entry_id),
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )""",
            """CREATE INDEX task_entries_by_task_status
                ON task_entries(task_id, status, priority DESC, updated_at DESC)""",
            """CREATE TABLE task_relations (
                task_id TEXT NOT NULL REFERENCES task_ledgers(task_id) ON DELETE CASCADE,
                from_entry_id TEXT NOT NULL REFERENCES task_entries(entry_id) ON DELETE CASCADE,
                to_entry_id TEXT NOT NULL REFERENCES task_entries(entry_id) ON DELETE CASCADE,
                relation_kind TEXT NOT NULL,
                PRIMARY KEY(task_id, from_entry_id, to_entry_id, relation_kind)
            )""",
            """CREATE TABLE task_checkpoints (
                checkpoint_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES task_ledgers(task_id) ON DELETE CASCADE,
                last_processed_event TEXT,
                goal_entry_ids_json TEXT NOT NULL,
                current_step TEXT,
                focus TEXT,
                next_action TEXT,
                world_state TEXT,
                continuation_note TEXT NOT NULL,
                critical_constraint_ids_json TEXT NOT NULL,
                open_question_ids_json TEXT NOT NULL,
                decision_ids_json TEXT NOT NULL,
                evidence_refs_json TEXT NOT NULL,
                created_at REAL NOT NULL
            )""",
            """CREATE INDEX task_checkpoints_latest
                ON task_checkpoints(task_id, created_at DESC)""",
            """CREATE TABLE task_operations (
                operation_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES task_ledgers(task_id) ON DELETE CASCADE,
                operation_kind TEXT NOT NULL,
                entry_id TEXT,
                created_at REAL NOT NULL
            )""",
            """CREATE TABLE task_events (
                event_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES task_ledgers(task_id) ON DELETE CASCADE,
                source_kind TEXT NOT NULL,
                content TEXT NOT NULL,
                structured_payload_json TEXT NOT NULL,
                occurred_at REAL NOT NULL,
                source_ref TEXT,
                turn_id TEXT,
                tool_call_id TEXT,
                status TEXT NOT NULL,
                receipt_json TEXT,
                error_text TEXT,
                created_at REAL NOT NULL
            )""",
        )
        for statement in statements:
            connection.execute(statement)
        connection.execute(f"PRAGMA user_version={TASK_STATE_SCHEMA_VERSION}")

    @staticmethod
    def _migrate_v1_to_v2(connection: sqlite3.Connection) -> None:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "task_entries" not in tables:
            raise ValueError("cannot migrate incomplete task-state v1 schema")
        connection.execute("ALTER TABLE task_entries ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'USER'")
        connection.execute(
            """CREATE TABLE IF NOT EXISTS task_events (
                event_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES task_ledgers(task_id) ON DELETE CASCADE,
                source_kind TEXT NOT NULL,
                content TEXT NOT NULL,
                structured_payload_json TEXT NOT NULL,
                occurred_at REAL NOT NULL,
                source_ref TEXT,
                turn_id TEXT,
                tool_call_id TEXT,
                status TEXT NOT NULL,
                receipt_json TEXT,
                error_text TEXT,
                created_at REAL NOT NULL
            )"""
        )
        connection.execute("PRAGMA user_version=2")

    @staticmethod
    def _migrate_v2_to_v3(connection: sqlite3.Connection) -> None:
        connection.execute("ALTER TABLE task_entries ADD COLUMN basis TEXT NOT NULL DEFAULT 'EXPLICIT'")
        connection.execute("PRAGMA user_version=3")

    @staticmethod
    def _migrate_v3_to_v4(connection: sqlite3.Connection) -> None:
        connection.execute("ALTER TABLE task_ledgers ADD COLUMN status TEXT NOT NULL DEFAULT 'ACTIVE'")
        connection.execute("PRAGMA user_version=4")

    @staticmethod
    def _migrate_v4_to_v5(connection: sqlite3.Connection) -> None:
        for column in ("focus", "next_action", "world_state"):
            connection.execute(f"ALTER TABLE task_checkpoints ADD COLUMN {column} TEXT")
        connection.execute("PRAGMA user_version=5")

    @staticmethod
    def _backfill_legacy_tool_payloads(connection: sqlite3.Connection) -> None:
        """Derive truthful status metadata for pre-contract tool events.

        Older v4 rows persisted an empty payload even though the canonical
        content already encoded the tool name and terminal status.  Backfilling
        on open keeps replay identity strict without making those durable rows
        unrecoverable after an upgrade.
        """
        rows = connection.execute(
            """SELECT event_id, content FROM task_events
            WHERE source_kind=? AND structured_payload_json='{}'""",
            (SourceKind.TOOL.value,),
        ).fetchall()
        pattern = re.compile(r"^Tool '([^']+)' \((SUCCESS|FAILED)\):")
        for row in rows:
            match = pattern.match(row["content"])
            if match is None:
                continue
            tool_name, status = match.groups()
            payload = {
                "success": status == "SUCCESS",
                "status": status,
                "tool_name": tool_name,
            }
            connection.execute(
                "UPDATE task_events SET structured_payload_json=? WHERE event_id=?",
                (json.dumps(payload, ensure_ascii=False, sort_keys=True), row["event_id"]),
            )

    @staticmethod
    def _refs_to_json(refs: Sequence[EvidenceRef]) -> str:
        return json.dumps([ref.to_dict() for ref in refs], ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _refs_from_json(raw: str) -> Tuple[EvidenceRef, ...]:
        return tuple(
            EvidenceRef(
                bundle_id=item["bundle_id"],
                memory_id=item["memory_id"],
                revision_id=item["revision_id"],
                evidence_item_id=item.get("evidence_item_id"),
            )
            for item in json.loads(raw)
        )

    @staticmethod
    def _entry_from_row(row: sqlite3.Row) -> LedgerEntry:
        return LedgerEntry(
            entry_id=row["entry_id"],
            task_id=row["task_id"],
            kind=EntryKind(row["kind"]),
            content=row["content"],
            status=EntryStatus(row["status"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            priority=Priority(row["priority"]),
            source_kind=SourceKind(row["source_kind"]),
            basis=ProposalBasis(row["basis"]),
            source_ref=row["source_ref"],
            evidence_refs=TaskStateRepository._refs_from_json(row["evidence_refs_json"]),
            supersedes_entry_id=row["supersedes_entry_id"],
        )

    def create_task(self, task_id: str) -> TaskLedger:
        if not task_id:
            raise ValueError("task_id must be non-empty")
        now = time.time()
        with self._transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO task_ledgers(task_id, created_at, updated_at) VALUES(?,?,?)",
                (task_id, now, now),
            )
        return self.get_ledger(task_id)

    def get_ledger(self, task_id: str) -> TaskLedger:
        with self._connection() as connection:
            ledger = connection.execute(
                "SELECT task_id, status, created_at, updated_at FROM task_ledgers WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if ledger is None:
                raise KeyError(f"unknown task: {task_id}")
            rows = connection.execute(
                "SELECT * FROM task_entries WHERE task_id=? ORDER BY created_at, entry_id",
                (task_id,),
            ).fetchall()
        return TaskLedger(task_id, tuple(self._entry_from_row(row) for row in rows), ledger["created_at"], ledger["updated_at"], TaskStatus(ledger["status"]))

    def get_entry(self, entry_id: str) -> LedgerEntry:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM task_entries WHERE entry_id=?", (entry_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown entry: {entry_id}")
        return self._entry_from_row(row)

    def add_entry(
        self,
        task_id: str,
        kind: EntryKind,
        content: str,
        *,
        priority: Priority = Priority.NORMAL,
        source_kind: SourceKind = SourceKind.USER,
        basis: ProposalBasis = ProposalBasis.EXPLICIT,
        source_ref: Optional[str] = None,
        evidence_refs: Sequence[EvidenceRef] = (),
        operation_id: Optional[str] = None,
        entry_id: Optional[str] = None,
    ) -> LedgerEntry:
        if not content.strip():
            raise ValueError("entry content must be non-empty")
        entry_id = entry_id or f"entry_{uuid.uuid4().hex}"
        now = time.time()
        with self._transaction() as connection:
            self._ensure_task(connection, task_id)
            existing = self._operation_entry(connection, operation_id, task_id)
            if existing is not None:
                return self._entry_from_connection(connection, existing)
            connection.execute(
                """INSERT INTO task_entries
                (entry_id, task_id, kind, content, status, priority, source_kind, basis, source_ref,
                 evidence_refs_json, supersedes_entry_id, created_at, updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (entry_id, task_id, kind.value, content, EntryStatus.ACTIVE.value, int(priority), source_kind.value, basis.value, source_ref,
                 self._refs_to_json(evidence_refs), None, now, now),
            )
            if operation_id:
                connection.execute(
                    "INSERT INTO task_operations(operation_id, task_id, operation_kind, entry_id, created_at) VALUES(?,?,?,?,?)",
                    (operation_id, task_id, "ADD", entry_id, now),
                )
            connection.execute("UPDATE task_ledgers SET updated_at=? WHERE task_id=?", (now, task_id))
            return self._entry_from_connection(connection, entry_id)

    def _ensure_task(self, connection: sqlite3.Connection, task_id: str) -> None:
        if connection.execute("SELECT 1 FROM task_ledgers WHERE task_id=?", (task_id,)).fetchone() is None:
            raise KeyError(f"unknown task: {task_id}")

    @staticmethod
    def _operation_entry(connection: sqlite3.Connection, operation_id: Optional[str], task_id: str) -> Optional[str]:
        if operation_id is None:
            return None
        row = connection.execute(
            "SELECT task_id, entry_id FROM task_operations WHERE operation_id=?", (operation_id,)
        ).fetchone()
        if row is None:
            return None
        if row["task_id"] != task_id:
            raise ValueError("operation_id already belongs to another task")
        return row["entry_id"]

    @staticmethod
    def _entry_from_connection(connection: sqlite3.Connection, entry_id: Optional[str]) -> LedgerEntry:
        if entry_id is None:
            raise ValueError("operation has no entry result")
        row = connection.execute("SELECT * FROM task_entries WHERE entry_id=?", (entry_id,)).fetchone()
        if row is None:
            raise KeyError(f"operation entry missing: {entry_id}")
        return TaskStateRepository._entry_from_row(row)

    def update_status(self, entry_id: str, status: EntryStatus, *, operation_id: Optional[str] = None) -> LedgerEntry:
        now = time.time()
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM task_entries WHERE entry_id=?", (entry_id,)).fetchone()
            if row is None:
                raise KeyError(f"unknown entry: {entry_id}")
            if operation_id and self._operation_entry(connection, operation_id, row["task_id"]):
                return self._entry_from_connection(connection, entry_id)
            current = EntryStatus(row["status"])
            if current is not status and current is not EntryStatus.ACTIVE:
                raise ValueError(f"cannot transition {current.value} to {status.value}")
            connection.execute("UPDATE task_entries SET status=?, updated_at=? WHERE entry_id=?", (status.value, now, entry_id))
            if operation_id:
                connection.execute(
                    "INSERT INTO task_operations(operation_id, task_id, operation_kind, entry_id, created_at) VALUES(?,?,?,?,?)",
                    (operation_id, row["task_id"], status.value, entry_id, now),
                )
            connection.execute("UPDATE task_ledgers SET updated_at=? WHERE task_id=?", (now, row["task_id"]))
            return self._entry_from_connection(connection, entry_id)

    def resolve_entry(self, entry_id: str, *, operation_id: Optional[str] = None) -> LedgerEntry:
        return self.update_status(entry_id, EntryStatus.RESOLVED, operation_id=operation_id)

    def supersede_entry(
        self,
        entry_id: str,
        content: str,
        *,
        priority: Optional[Priority] = None,
        source_kind: Optional[SourceKind] = None,
        basis: Optional[ProposalBasis] = None,
        source_ref: Optional[str] = None,
        evidence_refs: Sequence[EvidenceRef] = (),
        operation_id: Optional[str] = None,
    ) -> LedgerEntry:
        now = time.time()
        new_id = f"entry_{uuid.uuid4().hex}"
        with self._transaction() as connection:
            old = connection.execute("SELECT * FROM task_entries WHERE entry_id=?", (entry_id,)).fetchone()
            if old is None:
                raise KeyError(f"unknown entry: {entry_id}")
            existing = self._operation_entry(connection, operation_id, old["task_id"])
            if existing is not None:
                return self._entry_from_connection(connection, existing)
            if EntryStatus(old["status"]) is not EntryStatus.ACTIVE:
                raise ValueError("only ACTIVE entries can be superseded")
            connection.execute(
                """INSERT INTO task_entries
                (entry_id, task_id, kind, content, status, priority, source_kind, basis, source_ref,
                 evidence_refs_json, supersedes_entry_id, created_at, updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (new_id, old["task_id"], old["kind"], content, EntryStatus.ACTIVE.value,
                 int(priority or Priority(old["priority"])), (source_kind or SourceKind(old["source_kind"])).value, (basis or ProposalBasis(old["basis"])).value,
                 source_ref or old["source_ref"],
                 self._refs_to_json(evidence_refs), entry_id, now, now),
            )
            connection.execute("UPDATE task_entries SET status=?, updated_at=? WHERE entry_id=?", (EntryStatus.SUPERSEDED.value, now, entry_id))
            connection.execute(
                "INSERT INTO task_relations(task_id, from_entry_id, to_entry_id, relation_kind) VALUES(?,?,?,?)",
                (old["task_id"], new_id, entry_id, RelationKind.SUPERSEDES.value),
            )
            if operation_id:
                connection.execute(
                    "INSERT INTO task_operations(operation_id, task_id, operation_kind, entry_id, created_at) VALUES(?,?,?,?,?)",
                    (operation_id, old["task_id"], "SUPERSEDE", new_id, now),
                )
            connection.execute("UPDATE task_ledgers SET updated_at=? WHERE task_id=?", (now, old["task_id"]))
            return self._entry_from_connection(connection, new_id)

    def add_relation(self, relation: TaskRelation) -> None:
        with self._transaction() as connection:
            self._ensure_task(connection, relation.task_id)
            for entry_id in (relation.from_entry_id, relation.to_entry_id):
                row = connection.execute("SELECT task_id FROM task_entries WHERE entry_id=?", (entry_id,)).fetchone()
                if row is None or row["task_id"] != relation.task_id:
                    raise ValueError("relation entries must belong to the same task")
            connection.execute(
                "INSERT OR IGNORE INTO task_relations(task_id, from_entry_id, to_entry_id, relation_kind) VALUES(?,?,?,?)",
                (relation.task_id, relation.from_entry_id, relation.to_entry_id, relation.kind.value),
            )

    def register_event(self, event: TaskEvent) -> EventProcessStatus:
        now = time.time()
        with self._transaction() as connection:
            self._ensure_task(connection, event.task_id)
            existing = connection.execute("SELECT * FROM task_events WHERE event_id=?", (event.event_id,)).fetchone()
            if existing is not None:
                structured_payload_json = json.dumps(
                    dict(event.structured_payload), ensure_ascii=False, sort_keys=True
                )
                if (
                    existing["task_id"] != event.task_id
                    or existing["source_kind"] != event.source_kind.value
                    or existing["content"] != event.content
                    or existing["structured_payload_json"] != structured_payload_json
                    or existing["source_ref"] != event.source_ref
                    or existing["turn_id"] != event.turn_id
                    or existing["tool_call_id"] != event.tool_call_id
                ):
                    raise ValueError("event_id already belongs to a different event identity")
                return EventProcessStatus(existing["status"])
            connection.execute(
                """INSERT INTO task_events
                (event_id, task_id, source_kind, content, structured_payload_json,
                 occurred_at, source_ref, turn_id, tool_call_id, status, created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (event.event_id, event.task_id, event.source_kind.value, event.content,
                 json.dumps(dict(event.structured_payload), ensure_ascii=False, sort_keys=True),
                 event.occurred_at or now, event.source_ref, event.turn_id, event.tool_call_id,
                 EventProcessStatus.RECEIVED.value, now),
            )
            return EventProcessStatus.RECEIVED

    def get_event(self, event_id: str) -> Optional[TaskEvent]:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM task_events WHERE event_id=?", (event_id,)).fetchone()
        if row is None:
            return None
        return TaskEvent(
            task_id=row["task_id"], event_id=row["event_id"], source_kind=SourceKind(row["source_kind"]),
            content=row["content"], structured_payload=json.loads(row["structured_payload_json"]),
            occurred_at=row["occurred_at"], source_ref=row["source_ref"], turn_id=row["turn_id"],
            tool_call_id=row["tool_call_id"],
        )

    def get_event_status(self, event_id: str) -> Optional[EventProcessStatus]:
        with self._connection() as connection:
            row = connection.execute("SELECT status FROM task_events WHERE event_id=?", (event_id,)).fetchone()
        return None if row is None else EventProcessStatus(row["status"])

    def get_recent_events(self, task_id: str, limit: int = 4) -> Tuple[TaskEvent, ...]:
        if limit <= 0:
            return ()
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM task_events WHERE task_id=? ORDER BY occurred_at DESC, event_id DESC LIMIT ?",
                (task_id, limit),
            ).fetchall()
        events = tuple(
            TaskEvent(
                task_id=row["task_id"], event_id=row["event_id"], source_kind=SourceKind(row["source_kind"]),
                content=row["content"], structured_payload=json.loads(row["structured_payload_json"]),
                occurred_at=row["occurred_at"], source_ref=row["source_ref"], turn_id=row["turn_id"],
                tool_call_id=row["tool_call_id"],
            )
            for row in rows
        )
        return tuple(reversed(events))

    def get_event_receipt(self, event_id: str) -> Optional[TaskStateUpdateReceipt]:
        with self._connection() as connection:
            row = connection.execute("SELECT receipt_json FROM task_events WHERE event_id=?", (event_id,)).fetchone()
        if row is None or row["receipt_json"] is None:
            return None
        return self._receipt_from_json(row["receipt_json"])

    @staticmethod
    def _receipt_from_json(raw: str) -> TaskStateUpdateReceipt:
        data = json.loads(raw)
        return TaskStateUpdateReceipt(
            task_id=data["task_id"], event_id=data["event_id"], status=EventProcessStatus(data["status"]),
            batch_id=data["batch_id"], proposal_ids=tuple(data.get("proposal_ids", ())),
            applied_entry_ids=tuple(data.get("applied_entry_ids", ())),
            superseded_entry_ids=tuple(data.get("superseded_entry_ids", ())),
            resolved_entry_ids=tuple(data.get("resolved_entry_ids", ())),
            relation_keys=tuple(data.get("relation_keys", ())), warnings=tuple(data.get("warnings", ())),
            ledger_updated_at=data.get("ledger_updated_at", 0.0),
            analysis=TaskAnalysis.from_dict(data.get("analysis")),
        )

    def mark_event_failed(self, event_id: str, error_text: str) -> None:
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE task_events SET status=?, error_text=? WHERE event_id=?",
                (EventProcessStatus.FAILED.value, error_text[:1000], event_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"task event not found: {event_id}")

    def mark_event_rejected(self, event_id: str, receipt: TaskStateUpdateReceipt) -> None:
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE task_events SET status=?, receipt_json=? WHERE event_id=?",
                (EventProcessStatus.REJECTED.value, json.dumps(receipt.to_dict(), sort_keys=True), event_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"task event not found: {event_id}")

    def mark_event_pending_confirmation(self, event_id: str, receipt: TaskStateUpdateReceipt) -> None:
        if receipt.status is not EventProcessStatus.PENDING_CONFIRMATION:
            raise ValueError("pending confirmation receipt has an invalid status")
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE task_events SET status=?, receipt_json=?, error_text=NULL WHERE event_id=?",
                (
                    EventProcessStatus.PENDING_CONFIRMATION.value,
                    json.dumps(receipt.to_dict(), sort_keys=True),
                    event_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"task event not found: {event_id}")

    def apply_proposal_batch(self, event: TaskEvent, batch: TaskStateProposalBatch) -> TaskStateUpdateReceipt:
        now = time.time()
        batch_id = f"batch:{event.event_id}"
        with self._transaction() as connection:
            row = connection.execute("SELECT status, receipt_json FROM task_events WHERE event_id=?", (event.event_id,)).fetchone()
            if row is None:
                raise KeyError("event must be registered before apply")
            if EventProcessStatus(row["status"]) is EventProcessStatus.APPLIED:
                receipt = self._receipt_from_json(row["receipt_json"])
                return receipt
            applied: list[str] = []
            superseded: list[str] = []
            resolved: list[str] = []
            relation_keys: list[str] = []
            for proposal in batch.proposals:
                result = self._apply_proposal_connection(connection, event, proposal, now)
                applied.extend(result[0])
                superseded.extend(result[1])
                resolved.extend(result[2])
                relation_keys.extend(result[3])
            connection.execute("UPDATE task_ledgers SET updated_at=? WHERE task_id=?", (now, event.task_id))
            receipt = TaskStateUpdateReceipt(
                task_id=event.task_id,
                event_id=event.event_id,
                status=EventProcessStatus.APPLIED,
                batch_id=batch_id,
                proposal_ids=tuple(item.proposal_id for item in batch.proposals),
                applied_entry_ids=tuple(applied),
                superseded_entry_ids=tuple(superseded),
                resolved_entry_ids=tuple(resolved),
                relation_keys=tuple(relation_keys),
                warnings=tuple(batch.warnings),
                ledger_updated_at=now,
                analysis=batch.analysis,
            )
            connection.execute(
                "UPDATE task_events SET status=?, receipt_json=? WHERE event_id=?",
                (EventProcessStatus.APPLIED.value, json.dumps(receipt.to_dict(), sort_keys=True), event.event_id),
            )
            return receipt

    def _apply_proposal_connection(self, connection: sqlite3.Connection, event: TaskEvent, proposal: TaskStateProposal, now: float) -> tuple[list[str], list[str], list[str], list[str]]:
        applied: list[str] = []
        superseded: list[str] = []
        resolved: list[str] = []
        relations: list[str] = []
        operation_id = f"{event.event_id}:{proposal.proposal_id}"
        if proposal.action is ProposalAction.ADD:
            if proposal.entry_kind is None or proposal.content is None or proposal.basis is None:
                raise ValueError("ADD proposal is missing validated fields")
            entry_id = f"entry_{event.event_id}_{proposal.proposal_id}"
            connection.execute(
                """INSERT INTO task_entries
                (entry_id, task_id, kind, content, status, priority, source_kind, basis, source_ref,
                 evidence_refs_json, supersedes_entry_id, created_at, updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (entry_id, event.task_id, proposal.entry_kind.value, proposal.content,
                 EntryStatus.ACTIVE.value, int(proposal.priority or Priority.NORMAL), event.source_kind.value, proposal.basis.value,
                 event.source_ref or f"event:{event.event_id}", self._refs_to_json(proposal.evidence_refs),
                 None, now, now),
            )
            connection.execute(
                "INSERT INTO task_operations(operation_id, task_id, operation_kind, entry_id, created_at) VALUES(?,?,?,?,?)",
                (operation_id, event.task_id, ProposalAction.ADD.value, entry_id, now),
            )
            applied.append(entry_id)
        elif proposal.action is ProposalAction.RESOLVE:
            if proposal.target_entry_id is None:
                raise ValueError("RESOLVE proposal is missing target_entry_id")
            connection.execute("UPDATE task_entries SET status=?, updated_at=? WHERE entry_id=?", (EntryStatus.RESOLVED.value, now, proposal.target_entry_id))
            connection.execute(
                "INSERT INTO task_operations(operation_id, task_id, operation_kind, entry_id, created_at) VALUES(?,?,?,?,?)",
                (operation_id, event.task_id, ProposalAction.RESOLVE.value, proposal.target_entry_id, now),
            )
            resolved.append(proposal.target_entry_id)
        elif proposal.action is ProposalAction.REMOVE:
            if proposal.target_entry_id is None:
                raise ValueError("REMOVE proposal is missing target_entry_id")
            connection.execute("UPDATE task_entries SET status=?, updated_at=? WHERE entry_id=?", (EntryStatus.REMOVED.value, now, proposal.target_entry_id))
            connection.execute(
                "INSERT INTO task_operations(operation_id, task_id, operation_kind, entry_id, created_at) VALUES(?,?,?,?,?)",
                (operation_id, event.task_id, ProposalAction.REMOVE.value, proposal.target_entry_id, now),
            )
            applied.append(proposal.target_entry_id)
        elif proposal.action is ProposalAction.SET_PRIORITY:
            if proposal.target_entry_id is None or proposal.priority is None:
                raise ValueError("SET_PRIORITY proposal is missing validated fields")
            connection.execute("UPDATE task_entries SET priority=?, updated_at=? WHERE entry_id=?", (int(proposal.priority), now, proposal.target_entry_id))
            connection.execute(
                "INSERT INTO task_operations(operation_id, task_id, operation_kind, entry_id, created_at) VALUES(?,?,?,?,?)",
                (operation_id, event.task_id, ProposalAction.SET_PRIORITY.value, proposal.target_entry_id, now),
            )
            applied.append(proposal.target_entry_id)
        elif proposal.action is ProposalAction.LINK_RELATION:
            relation = proposal.relation
            if relation is None:
                raise ValueError("LINK_RELATION proposal is missing relation")
            connection.execute(
                "INSERT OR IGNORE INTO task_relations(task_id, from_entry_id, to_entry_id, relation_kind) VALUES(?,?,?,?)",
                (event.task_id, relation.from_entry_id, relation.to_entry_id, relation.kind.value),
            )
            relation_key = f"{relation.from_entry_id}:{relation.kind.value}:{relation.to_entry_id}"
            relations.append(relation_key)
        elif proposal.action is ProposalAction.SUPERSEDE:
            if (
                proposal.target_entry_id is None
                or proposal.replacement_content is None
                or proposal.basis is None
            ):
                raise ValueError("SUPERSEDE proposal is missing validated fields")
            old = connection.execute("SELECT * FROM task_entries WHERE entry_id=?", (proposal.target_entry_id,)).fetchone()
            new_id = f"entry_{event.event_id}_{proposal.proposal_id}"
            connection.execute(
                """INSERT INTO task_entries
                (entry_id, task_id, kind, content, status, priority, source_kind, basis, source_ref,
                 evidence_refs_json, supersedes_entry_id, created_at, updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (new_id, event.task_id, old["kind"], proposal.replacement_content,
                 EntryStatus.ACTIVE.value, int(proposal.priority or Priority(old["priority"])), event.source_kind.value, proposal.basis.value,
                 event.source_ref or f"event:{event.event_id}", self._refs_to_json(proposal.evidence_refs),
                 old["entry_id"], now, now),
            )
            connection.execute("UPDATE task_entries SET status=?, updated_at=? WHERE entry_id=?", (EntryStatus.SUPERSEDED.value, now, old["entry_id"]))
            connection.execute(
                "INSERT INTO task_relations(task_id, from_entry_id, to_entry_id, relation_kind) VALUES(?,?,?,?)",
                (event.task_id, new_id, old["entry_id"], RelationKind.SUPERSEDES.value),
            )
            connection.execute(
                "INSERT INTO task_operations(operation_id, task_id, operation_kind, entry_id, created_at) VALUES(?,?,?,?,?)",
                (operation_id, event.task_id, ProposalAction.SUPERSEDE.value, new_id, now),
            )
            applied.append(new_id)
            superseded.append(old["entry_id"])
        return applied, superseded, resolved, relations

    def list_active_entries(self, task_id: str) -> Tuple[LedgerEntry, ...]:
        ledger = self.get_ledger(task_id)
        return ledger.active_entries

    def get_history(self, task_id: str) -> Tuple[LedgerEntry, ...]:
        return self.get_ledger(task_id).entries

    def get_task_status(self, task_id: str) -> TaskStatus:
        with self._connection() as connection:
            row = connection.execute("SELECT status FROM task_ledgers WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown task: {task_id}")
        return TaskStatus(row["status"])

    def set_task_status(self, task_id: str, status: TaskStatus) -> TaskLedger:
        now = time.time()
        with self._transaction() as connection:
            self._ensure_task(connection, task_id)
            connection.execute("UPDATE task_ledgers SET status=?, updated_at=? WHERE task_id=?", (status.value, now, task_id))
        return self.get_ledger(task_id)

    def list_tasks(self, *, status: Optional[TaskStatus] = None) -> Tuple[TaskLedger, ...]:
        with self._connection() as connection:
            if status is None:
                rows = connection.execute("SELECT task_id FROM task_ledgers ORDER BY created_at, task_id").fetchall()
            else:
                rows = connection.execute("SELECT task_id FROM task_ledgers WHERE status=? ORDER BY created_at, task_id", (status.value,)).fetchall()
        return tuple(self.get_ledger(row["task_id"]) for row in rows)

    def list_active_tasks(self) -> Tuple[TaskLedger, ...]:
        return self.list_tasks(status=TaskStatus.ACTIVE)

    def write_checkpoint(self, checkpoint: ContinuationCheckpoint) -> ContinuationCheckpoint:
        created_at = checkpoint.created_at or time.time()
        if checkpoint.created_at == 0.0:
            checkpoint = ContinuationCheckpoint(
                task_id=checkpoint.task_id,
                checkpoint_id=checkpoint.checkpoint_id,
                last_processed_event=checkpoint.last_processed_event,
                goal_entry_ids=checkpoint.goal_entry_ids,
                current_step=checkpoint.current_step,
                focus=checkpoint.focus,
                next_action=checkpoint.next_action,
                world_state=checkpoint.world_state,
                continuation_note=checkpoint.continuation_note,
                critical_constraint_ids=checkpoint.critical_constraint_ids,
                open_question_ids=checkpoint.open_question_ids,
                decision_ids=checkpoint.decision_ids,
                evidence_refs=checkpoint.evidence_refs,
                created_at=created_at,
            )
        with self._transaction() as connection:
            self._ensure_task(connection, checkpoint.task_id)
            connection.execute(
                """INSERT INTO task_checkpoints
                (checkpoint_id, task_id, last_processed_event, goal_entry_ids_json,
                 current_step, focus, next_action, world_state, continuation_note,
                 critical_constraint_ids_json, open_question_ids_json, decision_ids_json,
                 evidence_refs_json, created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (checkpoint.checkpoint_id, checkpoint.task_id, checkpoint.last_processed_event,
                 json.dumps(checkpoint.goal_entry_ids), checkpoint.current_step, checkpoint.focus,
                 checkpoint.next_action, checkpoint.world_state, checkpoint.continuation_note,
                 json.dumps(checkpoint.critical_constraint_ids), json.dumps(checkpoint.open_question_ids),
                 json.dumps(checkpoint.decision_ids), self._refs_to_json(checkpoint.evidence_refs),
                 created_at),
            )
        return checkpoint

    def get_latest_checkpoint(self, task_id: str) -> Optional[ContinuationCheckpoint]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM task_checkpoints WHERE task_id=? ORDER BY created_at DESC, checkpoint_id DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        return ContinuationCheckpoint(
            task_id=row["task_id"], checkpoint_id=row["checkpoint_id"],
            last_processed_event=row["last_processed_event"],
            goal_entry_ids=tuple(json.loads(row["goal_entry_ids_json"])),
            current_step=row["current_step"], continuation_note=row["continuation_note"],
            focus=row["focus"], next_action=row["next_action"], world_state=row["world_state"],
            critical_constraint_ids=tuple(json.loads(row["critical_constraint_ids_json"])),
            open_question_ids=tuple(json.loads(row["open_question_ids_json"])),
            decision_ids=tuple(json.loads(row["decision_ids_json"])),
            evidence_refs=self._refs_from_json(row["evidence_refs_json"]),
            created_at=row["created_at"],
        )
