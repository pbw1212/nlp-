"""Durable, client-neutral Agent lifecycle events.

This is the MAPAS equivalent of the Codex app-server event boundary.  SQLite
is authoritative; subscribers are only a delivery convenience.  Events are
append-only and can be replayed by sequence after a client reconnects.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from src.memory.storage.sqlite_utils import ClosingSQLiteConnection


EVENT_SCHEMA_VERSION = 1
MAX_EVENT_PAYLOAD_BYTES = 256 * 1024
MAX_REPLAY_LIMIT = 1000


@dataclass(frozen=True)
class AgentEvent:
    """A replayable lifecycle event emitted by the Agent host."""

    event_id: str
    sequence: int
    event_type: str
    session_id: Optional[str]
    turn_id: Optional[str]
    payload: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    schema_version: int = EVENT_SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AgentEventStore:
    """SQLite-backed append-only event store with per-session replay."""

    def __init__(self, db_path: str) -> None:
        self.db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.db_path, timeout=30.0, factory=ClosingSQLiteConnection
        )
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    session_id TEXT,
                    turn_id TEXT,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    schema_version INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_events_session_sequence "
                "ON agent_events(session_id, sequence)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_events_turn_sequence "
                "ON agent_events(turn_id, sequence)"
            )
            connection.commit()

    @staticmethod
    def _validate_payload(payload: Mapping[str, Any]) -> str:
        if not isinstance(payload, Mapping):
            raise ValueError("event payload must be a JSON object")
        encoded = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_EVENT_PAYLOAD_BYTES:
            raise ValueError("event payload exceeds bounded event storage limit")
        return encoded

    @staticmethod
    def _from_row(row: sqlite3.Row) -> AgentEvent:
        payload = json.loads(row["payload_json"])
        if not isinstance(payload, dict):
            raise ValueError("stored event payload is not a JSON object")
        return AgentEvent(
            event_id=row["event_id"],
            sequence=int(row["sequence"]),
            event_type=row["event_type"],
            session_id=row["session_id"],
            turn_id=row["turn_id"],
            payload=payload,
            created_at=float(row["created_at"]),
            schema_version=int(row["schema_version"]),
        )

    def append(
        self,
        event_type: str,
        *,
        session_id: Optional[str] = None,
        turn_id: Optional[str] = None,
        payload: Optional[Mapping[str, Any]] = None,
        event_id: Optional[str] = None,
        created_at: Optional[float] = None,
    ) -> AgentEvent:
        if not isinstance(event_type, str) or not event_type.strip():
            raise ValueError("event_type must be a non-empty string")
        encoded_payload = self._validate_payload(payload or {})
        assigned_id = event_id or f"evt_{uuid.uuid4().hex[:16]}"
        timestamp = created_at if created_at is not None else time.time()
        with self._lock, self._connection() as connection:
            existing = connection.execute(
                "SELECT * FROM agent_events WHERE event_id=?", (assigned_id,)
            ).fetchone()
            if existing is not None:
                existing_event = self._from_row(existing)
                if (
                    existing_event.event_type != event_type
                    or existing_event.session_id != session_id
                    or existing_event.turn_id != turn_id
                    or json.dumps(existing_event.payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    != encoded_payload
                ):
                    raise ValueError(f"event identity mismatch for event_id: {assigned_id}")
                return existing_event
            cursor = connection.execute(
                """
                INSERT INTO agent_events
                    (event_id, event_type, session_id, turn_id, payload_json, created_at, schema_version)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (assigned_id, event_type, session_id, turn_id, encoded_payload, timestamp, EVENT_SCHEMA_VERSION),
            )
            connection.commit()
            return AgentEvent(
                event_id=assigned_id,
                sequence=int(cursor.lastrowid),
                event_type=event_type,
                session_id=session_id,
                turn_id=turn_id,
                payload=dict(payload or {}),
                created_at=timestamp,
            )

    def list_events(
        self,
        *,
        after_sequence: int = 0,
        session_id: Optional[str] = None,
        turn_id: Optional[str] = None,
        limit: int = MAX_REPLAY_LIMIT,
    ) -> Tuple[AgentEvent, ...]:
        if after_sequence < 0:
            raise ValueError("after_sequence cannot be negative")
        if limit <= 0 or limit > MAX_REPLAY_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_REPLAY_LIMIT}")
        clauses = ["sequence > ?"]
        values: List[Any] = [after_sequence]
        if session_id is not None:
            clauses.append("session_id = ?")
            values.append(session_id)
        if turn_id is not None:
            clauses.append("turn_id = ?")
            values.append(turn_id)
        query = (
            "SELECT * FROM agent_events WHERE "
            + " AND ".join(clauses)
            + " ORDER BY sequence ASC LIMIT ?"
        )
        values.append(limit)
        with self._lock, self._connection() as connection:
            rows = connection.execute(query, values).fetchall()
        return tuple(self._from_row(row) for row in rows)

    def latest_sequence(self) -> int:
        with self._lock, self._connection() as connection:
            row = connection.execute("SELECT MAX(sequence) AS latest FROM agent_events").fetchone()
        return int(row["latest"] or 0)

    def list_unfinished_turns(self, *, session_id: Optional[str] = None) -> Tuple[AgentEvent, ...]:
        """Find turns with a committed start but no terminal lifecycle event."""
        clauses = [
            "event_type = 'turn.started'",
            "turn_id IS NOT NULL",
            "NOT EXISTS ("
            "SELECT 1 FROM agent_events terminal "
            "WHERE terminal.turn_id = agent_events.turn_id "
            "AND terminal.event_type IN ('turn.completed', 'turn.failed', 'turn.interrupted')"
            ")",
        ]
        values: List[Any] = []
        if session_id is not None:
            clauses.append("session_id = ?")
            values.append(session_id)
        query = "SELECT * FROM agent_events WHERE " + " AND ".join(clauses) + " ORDER BY sequence ASC"
        with self._lock, self._connection() as connection:
            rows = connection.execute(query, values).fetchall()
        # A resumed turn can have more than one start marker. Return one
        # descriptor per logical turn while keeping the earliest marker.
        seen: set[str] = set()
        result: List[AgentEvent] = []
        for row in rows:
            event = self._from_row(row)
            if event.turn_id is not None and event.turn_id in seen:
                continue
            if event.turn_id is not None:
                seen.add(event.turn_id)
            result.append(event)
        return tuple(result)


EventSubscriber = Callable[[AgentEvent], None]


class AgentEventStream:
    """Persists events and synchronously fans them out to live clients."""

    def __init__(self, store: AgentEventStore) -> None:
        self.store = store
        self._lock = threading.RLock()
        self._subscribers: Dict[str, EventSubscriber] = {}

    def subscribe(self, subscriber: EventSubscriber) -> Callable[[], None]:
        if not callable(subscriber):
            raise ValueError("subscriber must be callable")
        token = uuid.uuid4().hex
        with self._lock:
            self._subscribers[token] = subscriber

        def unsubscribe() -> None:
            with self._lock:
                self._subscribers.pop(token, None)

        return unsubscribe

    def emit(self, event_type: str, **kwargs: Any) -> AgentEvent:
        event = self.store.append(event_type, **kwargs)
        with self._lock:
            subscribers = tuple(self._subscribers.values())
        for subscriber in subscribers:
            try:
                subscriber(event)
            except Exception:
                # A disconnected UI must never make a committed Agent event
                # or the active turn fail.
                continue
        return event
