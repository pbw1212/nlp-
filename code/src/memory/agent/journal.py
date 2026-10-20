"""Durable SQLite Journal for Main Agent runs, pending actions, and tool execution receipts."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Tuple

from src.memory.agent.models import PendingAction, canonical_json_bytes
from src.memory.storage.sqlite_utils import ClosingSQLiteConnection

AGENT_JOURNAL_SCHEMA_VERSION = 4


class JournalIntegrityError(ValueError):
    """Durable journal data or execution identity is inconsistent."""


class JournalConfigurationMismatchError(JournalIntegrityError):
    """Current tool policy conflicts with the immutable durable snapshot."""


def canonicalize_json_string(text: str) -> str:
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise JournalIntegrityError(
            "journal integrity error: arguments_json is not valid JSON"
        ) from exc
    if not isinstance(parsed, dict):
        raise JournalIntegrityError(
            "journal integrity error: arguments_json must encode an object"
        )
    return json.dumps(parsed, sort_keys=True, ensure_ascii=False)


def _bound_persisted_text(text: str, max_chars: int = 10000) -> str:
    if not text or len(text) <= max_chars:
        return text or ""
    return text[:max_chars] + f"\n... [truncated from {len(text)} chars]"


class AgentExecutionJournal:
    """Durable execution journal for MainAgent.

    Tracks runs, durable pending actions (human confirmations), and atomic tool execution claims.
    Maintains isolated ownership separate from Task 1 (memory_store.db) and Task 3 (task_state.db).
    """

    def __init__(self, db_path: str = "agent_journal.db") -> None:
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
                # Migrate v1 -> v2 schema with non-empty execution identities
                connection.execute("ALTER TABLE pending_actions ADD COLUMN agent_step_id TEXT NOT NULL DEFAULT ''")
                connection.execute("ALTER TABLE pending_actions ADD COLUMN arguments_hash TEXT NOT NULL DEFAULT ''")
                connection.execute("ALTER TABLE pending_actions ADD COLUMN idempotency_key TEXT NOT NULL DEFAULT ''")

                # Populate missing identity fields for existing v1 rows
                rows = connection.execute("SELECT * FROM pending_actions WHERE agent_step_id='' OR idempotency_key=''").fetchall()
                for row in rows:
                    pending_id = row["pending_action_id"]
                    t_call_id = row["tool_call_id"]

                    # Check if matching receipt exists
                    rec = connection.execute("SELECT * FROM tool_execution_receipts WHERE tool_call_id=?", (t_call_id,)).fetchone()
                    if rec is not None:
                        step_id = rec["agent_step_id"]
                        arg_h = rec["arguments_hash"]
                        idemp = rec["idempotency_key"]
                    else:
                        run_id = row["agent_run_id"]
                        tool_name = row["tool_name"]
                        args_dict = json.loads(row["arguments_json"]) if row["arguments_json"] else {}
                        step_seed = f"{run_id}:legacy:{t_call_id}".encode("utf-8")
                        step_id = f"step_{hashlib.sha256(step_seed).hexdigest()[:12]}"
                        args_bytes = canonical_json_bytes(args_dict)
                        arg_h = hashlib.sha256(args_bytes).hexdigest()[:12]
                        idemp_seed = f"{run_id}:{step_id}:{tool_name}:{arg_h}:{t_call_id}".encode("utf-8")
                        idemp = f"idemp_{hashlib.sha256(idemp_seed).hexdigest()[:16]}"

                    connection.execute(
                        "UPDATE pending_actions SET agent_step_id=?, arguments_hash=?, idempotency_key=? WHERE pending_action_id=?",
                        (step_id, arg_h, idemp, pending_id),
                    )

                connection.execute("PRAGMA user_version = 2")
                connection.commit()
                version = 2

            if version == 2:
                # Migrate v2 -> v3 schema adding arguments_json, tool_effect, idempotency_support, runtime_reconciled
                connection.execute("ALTER TABLE tool_execution_receipts ADD COLUMN arguments_json TEXT NOT NULL DEFAULT '{}'")
                connection.execute("ALTER TABLE tool_execution_receipts ADD COLUMN tool_effect TEXT NOT NULL DEFAULT 'SIDE_EFFECT'")
                connection.execute("ALTER TABLE tool_execution_receipts ADD COLUMN idempotency_support TEXT NOT NULL DEFAULT 'NONE'")
                connection.execute("ALTER TABLE tool_execution_receipts ADD COLUMN runtime_reconciled INTEGER NOT NULL DEFAULT 0")

                # Populate arguments_json & tool_effect from pending_actions if matching row exists
                rec_rows = connection.execute("SELECT * FROM tool_execution_receipts").fetchall()
                for r_row in rec_rows:
                    t_call_id = r_row["tool_call_id"]
                    p_row = connection.execute("SELECT arguments_json, effect FROM pending_actions WHERE tool_call_id=?", (t_call_id,)).fetchone()
                    if p_row is not None:
                        connection.execute(
                            "UPDATE tool_execution_receipts SET arguments_json=?, tool_effect=? WHERE tool_call_id=?",
                            (p_row["arguments_json"], p_row["effect"], t_call_id),
                        )

                connection.execute("PRAGMA user_version = 3")
                connection.commit()
                version = 3

            if version == 3:
                # Migrate v3 -> v4: pre-v4 DISPATCHING claims transition to UNKNOWN for fail-closed recovery
                connection.execute("UPDATE tool_execution_receipts SET status='UNKNOWN' WHERE status='DISPATCHING'")
                connection.execute("PRAGMA user_version = 4")
                connection.commit()
                version = 4

            if version != AGENT_JOURNAL_SCHEMA_VERSION:
                raise ValueError(
                    f"unsupported agent-journal schema version {version}; expected {AGENT_JOURNAL_SCHEMA_VERSION}"
                )
            required = {"agent_runs", "pending_actions", "tool_execution_receipts"}
            if not required.issubset(tables):
                raise ValueError("agent-journal schema is incomplete")

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        statements = (
            """CREATE TABLE agent_runs (
                agent_run_id TEXT PRIMARY KEY,
                root_turn_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )""",
            """CREATE INDEX idx_agent_runs_root_turn ON agent_runs(root_turn_id)""",
            """CREATE TABLE pending_actions (
                pending_action_id TEXT PRIMARY KEY,
                agent_run_id TEXT NOT NULL REFERENCES agent_runs(agent_run_id) ON DELETE CASCADE,
                task_id TEXT NOT NULL,
                agent_step_id TEXT NOT NULL,
                tool_call_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                arguments_json TEXT NOT NULL,
                arguments_hash TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                effect TEXT NOT NULL,
                status TEXT NOT NULL, -- PENDING, CONFIRMED, REJECTED, EXECUTED
                created_at REAL NOT NULL
            )""",
            """CREATE INDEX idx_pending_actions_run ON pending_actions(agent_run_id)""",
            """CREATE TABLE tool_execution_receipts (
                tool_call_id TEXT PRIMARY KEY,
                agent_run_id TEXT NOT NULL REFERENCES agent_runs(agent_run_id) ON DELETE CASCADE,
                agent_step_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                arguments_json TEXT NOT NULL DEFAULT '{}',
                arguments_hash TEXT NOT NULL,
                idempotency_key TEXT UNIQUE NOT NULL,
                tool_effect TEXT NOT NULL DEFAULT 'READ_ONLY',
                idempotency_support TEXT NOT NULL DEFAULT 'NONE',
                status TEXT NOT NULL, -- DISPATCHING, SUCCEEDED, FAILED, UNKNOWN
                runtime_reconciled INTEGER NOT NULL DEFAULT 0,
                result_json TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )""",
            """CREATE INDEX idx_tool_receipts_idemp ON tool_execution_receipts(idempotency_key)""",
        )
        for stmt in statements:
            connection.execute(stmt)
        connection.execute(f"PRAGMA user_version = {AGENT_JOURNAL_SCHEMA_VERSION}")

    # --- Run Management ---

    def register_run(self, agent_run_id: str, root_turn_id: str, task_id: str) -> dict:
        now = time.time()
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM agent_runs WHERE agent_run_id=?", (agent_run_id,)
            ).fetchone()
            if row is not None:
                if row["root_turn_id"] != root_turn_id or row["task_id"] != task_id:
                    raise ValueError(
                        f"journal integrity error: register_run mismatch for run '{agent_run_id}': "
                        f"stored root_turn_id='{row['root_turn_id']}', task_id='{row['task_id']}', "
                        f"requested root_turn_id='{root_turn_id}', task_id='{task_id}'"
                    )
                return dict(row)
            connection.execute(
                "INSERT INTO agent_runs(agent_run_id, root_turn_id, task_id, status, created_at, updated_at) VALUES(?,?,?,?,?,?)",
                (agent_run_id, root_turn_id, task_id, "RUNNING", now, now),
            )
            return {
                "agent_run_id": agent_run_id,
                "root_turn_id": root_turn_id,
                "task_id": task_id,
                "status": "RUNNING",
                "created_at": now,
                "updated_at": now,
            }

    def get_run(self, agent_run_id: str) -> Optional[dict]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM agent_runs WHERE agent_run_id=?", (agent_run_id,)
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    def update_run_status(self, agent_run_id: str, status: str) -> None:
        now = time.time()
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE agent_runs SET status=?, updated_at=? WHERE agent_run_id=?",
                (status, now, agent_run_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"agent run not found: {agent_run_id}")

    # --- Pending Actions ---

    def add_pending_action(self, pending_action: PendingAction) -> PendingAction:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT agent_run_id, task_id FROM agent_runs WHERE agent_run_id=?",
                (pending_action.agent_run_id,),
            ).fetchone()
            if row is None:
                raise ValueError(
                    "journal integrity error: pending action references an unregistered agent run"
                )
            if row["task_id"] != pending_action.task_id:
                raise ValueError(
                    "journal integrity error: pending action task does not match its agent run"
                )

            existing = connection.execute(
                "SELECT * FROM pending_actions WHERE pending_action_id=?",
                (pending_action.pending_action_id,),
            ).fetchone()
            arguments_json = json.dumps(
                pending_action.validated_arguments, sort_keys=True, ensure_ascii=False
            )
            if existing is not None:
                expected = (
                    pending_action.agent_run_id,
                    pending_action.task_id,
                    pending_action.agent_step_id,
                    pending_action.tool_call_id,
                    pending_action.tool_name,
                    canonicalize_json_string(arguments_json),
                    pending_action.arguments_hash,
                    pending_action.idempotency_key,
                    pending_action.effect,
                )
                actual = (
                    existing["agent_run_id"],
                    existing["task_id"],
                    existing["agent_step_id"],
                    existing["tool_call_id"],
                    existing["tool_name"],
                    canonicalize_json_string(existing["arguments_json"]),
                    existing["arguments_hash"],
                    existing["idempotency_key"],
                    existing["effect"],
                )
                if actual != expected:
                    raise ValueError(
                        "journal integrity error: pending action identity mismatch"
                    )
                return pending_action

            connection.execute(
                """INSERT INTO pending_actions
                (pending_action_id, agent_run_id, task_id, agent_step_id, tool_call_id, tool_name, arguments_json, arguments_hash, idempotency_key, effect, status, created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    pending_action.pending_action_id,
                    pending_action.agent_run_id,
                    pending_action.task_id,
                    pending_action.agent_step_id,
                    pending_action.tool_call_id,
                    pending_action.tool_name,
                    arguments_json,
                    pending_action.arguments_hash,
                    pending_action.idempotency_key,
                    pending_action.effect,
                    "PENDING",
                    pending_action.created_at,
                ),
            )
        return pending_action

    def get_pending_action(self, pending_action_id: str) -> Optional[PendingAction]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM pending_actions WHERE pending_action_id=?",
                (pending_action_id,),
            ).fetchone()
        if row is None:
            return None
        if not row["agent_step_id"] or not row["arguments_hash"] or not row["idempotency_key"]:
            raise ValueError(
                f"journal integrity error: pending action '{pending_action_id}' contains empty execution identity fields"
            )
        arguments = json.loads(row["arguments_json"])
        if not isinstance(arguments, dict):
            raise ValueError(
                f"journal integrity error: pending action '{pending_action_id}' arguments are not an object"
            )
        return PendingAction(
            pending_action_id=row["pending_action_id"],
            agent_run_id=row["agent_run_id"],
            task_id=row["task_id"],
            agent_step_id=row["agent_step_id"],
            tool_call_id=row["tool_call_id"],
            tool_name=row["tool_name"],
            validated_arguments=arguments,
            arguments_hash=row["arguments_hash"],
            idempotency_key=row["idempotency_key"],
            effect=row["effect"],
            created_at=row["created_at"],
        )

    def list_pending_actions(self, *, task_id: Optional[str] = None) -> List[PendingAction]:
        """Return durable, still-actionable confirmation requests newest first."""
        with self._connection() as connection:
            if task_id:
                rows = connection.execute(
                    """SELECT pending_action_id FROM pending_actions
                    WHERE status='PENDING' AND task_id=?
                    ORDER BY created_at DESC, pending_action_id DESC""",
                    (task_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """SELECT pending_action_id FROM pending_actions
                    WHERE status='PENDING'
                    ORDER BY created_at DESC, pending_action_id DESC"""
                ).fetchall()
        actions: List[PendingAction] = []
        for row in rows:
            action = self.get_pending_action(row["pending_action_id"])
            if action is not None:
                actions.append(action)
        return actions

    def update_pending_action_status(self, pending_action_id: str, status: str) -> None:
        if status not in {"PENDING", "CONFIRMED", "REJECTED", "EXECUTED"}:
            raise ValueError(f"invalid pending action status: {status}")
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE pending_actions SET status=? WHERE pending_action_id=?",
                (status, pending_action_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"pending action not found: {pending_action_id}")

    def get_pending_action_status(self, pending_action_id: str) -> Optional[str]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT status FROM pending_actions WHERE pending_action_id=?",
                (pending_action_id,),
            ).fetchone()
        return None if row is None else str(row["status"])

    # --- Atomic Tool Execution Claiming & Receipts ---

    def claim_execution(
        self,
        agent_run_id: str,
        agent_step_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments_hash: str,
        idempotency_key: str,
        arguments_json: str,
        tool_effect: str,
        idempotency_support: str,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """Atomically attempts to claim execution ownership for a tool call using an IMMEDIATE transaction."""
        canon_args = canonicalize_json_string(arguments_json)
        now = time.time()
        with self._transaction() as connection:
            # Check for existing idempotency key or tool_call_id
            row = connection.execute(
                "SELECT * FROM tool_execution_receipts WHERE idempotency_key=? OR tool_call_id=?",
                (idempotency_key, tool_call_id),
            ).fetchone()

            if row is not None:
                # Strictly verify identity and policy snapshot match
                if (
                    row["agent_run_id"] != agent_run_id
                    or row["agent_step_id"] != agent_step_id
                    or row["tool_name"] != tool_name
                    or row["arguments_hash"] != arguments_hash
                    or row["idempotency_key"] != idempotency_key
                    or row["tool_call_id"] != tool_call_id
                    or canonicalize_json_string(row["arguments_json"]) != canon_args
                ):
                    raise JournalIntegrityError(
                        f"journal integrity error: tool execution claim identity mismatch for tool_call_id '{tool_call_id}'"
                    )
                if (
                    row["tool_effect"] != tool_effect
                    or row["idempotency_support"] != idempotency_support
                ):
                    raise JournalConfigurationMismatchError(
                        f"journal configuration mismatch for tool_call_id '{tool_call_id}'"
                    )

                existing_status = row["status"]
                receipt_json = row["result_json"]
                receipt_dict = json.loads(receipt_json) if receipt_json else None
                out_dict = dict(row)
                out_dict["result_data"] = receipt_dict
                return False, existing_status, out_dict

            # Atomic claim: insert DISPATCHING record with execution policy snapshot
            connection.execute(
                """INSERT INTO tool_execution_receipts
                (tool_call_id, agent_run_id, agent_step_id, tool_name, arguments_json, arguments_hash, idempotency_key, tool_effect, idempotency_support, status, runtime_reconciled, result_json, created_at, updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    tool_call_id,
                    agent_run_id,
                    agent_step_id,
                    tool_name,
                    canon_args,
                    arguments_hash,
                    idempotency_key,
                    tool_effect,
                    idempotency_support,
                    "DISPATCHING",
                    0,
                    None,
                    now,
                    now,
                ),
            )
            return True, "DISPATCHING", None

    def complete_execution(
        self,
        tool_call_id: str,
        status: str,
        result_content: str = "",
        error_message: str = "",
        max_payload_chars: int = 10000,
    ) -> Dict[str, Any]:
        """Completes a claimed execution by storing the final receipt status and payload."""
        if status not in {"SUCCEEDED", "FAILED"}:
            raise ValueError(f"invalid terminal tool receipt status: {status}")
        if status == "SUCCEEDED" and error_message:
            raise ValueError("SUCCEEDED tool receipt cannot contain error_message")
        if status == "FAILED":
            if result_content:
                raise ValueError("FAILED tool receipt cannot contain result_content")
            if not error_message.strip():
                raise ValueError("FAILED tool receipt requires error_message")
        now = time.time()
        bounded_content = _bound_persisted_text(result_content, max_payload_chars)
        bounded_error = _bound_persisted_text(error_message, max_payload_chars)

        payload = {
            "tool_call_id": tool_call_id,
            "status": status,
            "result_content": bounded_content,
            "error_message": bounded_error,
            "updated_at": now,
        }
        payload_json = json.dumps(payload, sort_keys=True, ensure_ascii=False)

        with self._transaction() as connection:
            row = connection.execute(
                "SELECT status FROM tool_execution_receipts WHERE tool_call_id=?",
                (tool_call_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"tool execution receipt not found: {tool_call_id}")
            if row["status"] != "DISPATCHING":
                raise ValueError(
                    f"tool execution receipt '{tool_call_id}' cannot complete from status '{row['status']}'"
                )
            cursor = connection.execute(
                "UPDATE tool_execution_receipts SET status=?, result_json=?, updated_at=? WHERE tool_call_id=?",
                (status, payload_json, now, tool_call_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("tool receipt completion lost its claimed row")
        return payload

    def mark_receipt_reconciled(self, tool_call_id: str) -> None:
        now = time.time()
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE tool_execution_receipts SET runtime_reconciled=1, updated_at=? WHERE tool_call_id=?",
                (now, tool_call_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"tool execution receipt not found: {tool_call_id}")

    def get_unreconciled_receipts(self, agent_run_id: str) -> List[Dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM tool_execution_receipts WHERE agent_run_id=? AND status IN ('SUCCEEDED', 'FAILED') AND runtime_reconciled=0 ORDER BY created_at ASC, tool_call_id ASC",
                (agent_run_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_unreconciled_receipts_for_task(self, task_id: str) -> List[Dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT r.* FROM tool_execution_receipts r
                JOIN agent_runs a ON r.agent_run_id = a.agent_run_id
                WHERE a.task_id=? AND r.status IN ('SUCCEEDED', 'FAILED') AND r.runtime_reconciled=0
                ORDER BY r.created_at ASC, r.tool_call_id ASC""",
                (task_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_receipt(self, tool_call_id: str) -> Optional[Dict[str, Any]]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM tool_execution_receipts WHERE tool_call_id=?",
                (tool_call_id,),
            ).fetchone()
            if row is None:
                return None
            return dict(row)

    def get_receipt_by_idempotency_key(self, idempotency_key: str) -> Optional[Dict[str, Any]]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM tool_execution_receipts WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
        if row is None or row["result_json"] is None:
            return None
        return json.loads(row["result_json"])
