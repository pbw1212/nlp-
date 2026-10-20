"""Independent SQLite ownership for immutable handoffs and model-call budgets."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from src.memory.collaboration.models import canonical, digest, identity
from src.memory.storage.sqlite_utils import ClosingSQLiteConnection


class CoordinationConflict(ValueError):
    pass


class CoordinationRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS coordination_meta(version INTEGER NOT NULL);
                INSERT INTO coordination_meta SELECT 1 WHERE NOT EXISTS(SELECT 1 FROM coordination_meta);
                CREATE TABLE IF NOT EXISTS runs(
                    run_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, turn_id TEXT NOT NULL,
                    input_hash TEXT NOT NULL, started REAL NOT NULL, limits_json TEXT NOT NULL,
                    task_id TEXT, version INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'RUNNING', result_json TEXT);
                CREATE TABLE IF NOT EXISTS role_tasks(
                    run_id TEXT NOT NULL REFERENCES runs(run_id), stage_key TEXT NOT NULL,
                    role TEXT NOT NULL, input_hash TEXT NOT NULL, status TEXT NOT NULL,
                    PRIMARY KEY(run_id,stage_key));
                CREATE TABLE IF NOT EXISTS handoffs(
                    message_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
                    stage_key TEXT NOT NULL, sender TEXT NOT NULL, receiver TEXT NOT NULL,
                    payload_json TEXT NOT NULL, payload_hash TEXT NOT NULL,
                    UNIQUE(run_id,stage_key));
                CREATE TABLE IF NOT EXISTS artifacts(
                    artifact_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
                    stage_key TEXT NOT NULL, payload_hash TEXT NOT NULL,
                    verification_status TEXT NOT NULL DEFAULT 'PRODUCED',
                    UNIQUE(run_id,stage_key));
                CREATE TABLE IF NOT EXISTS model_calls(
                    call_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
                    role TEXT NOT NULL, operation TEXT NOT NULL, status TEXT NOT NULL,
                    charged_units INTEGER NOT NULL, usage_json TEXT NOT NULL DEFAULT '{}',
                    elapsed REAL NOT NULL DEFAULT 0);
            """
            )
            if (
                db.execute("SELECT version FROM coordination_meta").fetchall()[0][0]
                != 1
            ):
                raise CoordinationConflict("unsupported coordination schema")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=10, factory=ClosingSQLiteConnection)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            yield db

    def begin(
        self, session_id: str, turn_id: str, user_input: str, limits: dict[str, Any]
    ) -> str:
        run_id = identity(session_id, turn_id)
        with self._write() as db:
            row = db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if row:
                if row["input_hash"] != digest(user_input) or row[
                    "limits_json"
                ] != canonical(limits):
                    raise CoordinationConflict(
                        "turn identity or budget changed during replay"
                    )
            else:
                db.execute(
                    "INSERT INTO runs(run_id,session_id,turn_id,input_hash,started,limits_json) VALUES(?,?,?,?,?,?)",
                    (
                        run_id,
                        session_id,
                        turn_id,
                        digest(user_input),
                        time.time(),
                        canonical(limits),
                    ),
                )
        return run_id

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise CoordinationConflict("unknown run")
            return dict(row)

    def bind_task(self, run_id: str, task_id: str) -> None:
        with self._write() as db:
            row = db.execute(
                "SELECT task_id FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None or row[0] not in (None, task_id):
                raise CoordinationConflict("task ownership changed during replay")
            db.execute("UPDATE runs SET task_id=? WHERE run_id=?", (task_id, run_id))

    def get_stage(
        self, run_id: str, key: str, role: str, inputs: Any
    ) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT t.*,h.payload_json,h.payload_hash FROM role_tasks t LEFT JOIN handoffs h USING(run_id,stage_key) WHERE run_id=? AND stage_key=?",
                (run_id, key),
            ).fetchone()
            if row is None:
                return None
            if row["role"] != role or row["input_hash"] != digest(inputs):
                raise CoordinationConflict(
                    "handoff input or role changed; new turn required"
                )
            if row["payload_json"] is None:
                return None
            payload = json.loads(row["payload_json"])
            if digest(payload) != row["payload_hash"]:
                raise CoordinationConflict("handoff integrity failure")
            return payload

    def accept(
        self,
        run_id: str,
        key: str,
        role: str,
        receiver: str,
        inputs: Any,
        payload: dict[str, Any],
        expected_version: int,
    ) -> None:
        with self._write() as db:
            row = db.execute(
                "SELECT version,status FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if (
                row is None
                or row["version"] != expected_version
                or row["status"] != "RUNNING"
            ):
                raise CoordinationConflict("run version changed or run is terminal")
            db.execute(
                "INSERT INTO role_tasks VALUES(?,?,?,?,?)",
                (run_id, key, role, digest(inputs), "PRODUCED"),
            )
            db.execute(
                "INSERT INTO handoffs VALUES(?,?,?,?,?,?,?)",
                (
                    identity(run_id, key),
                    run_id,
                    key,
                    role,
                    receiver,
                    canonical(payload),
                    digest(payload),
                ),
            )
            if role == "Executor":
                db.execute(
                    "INSERT INTO artifacts(artifact_id,run_id,stage_key,payload_hash) VALUES(?,?,?,?)",
                    (identity(run_id, key, "artifact"), run_id, key, digest(payload)),
                )
            db.execute("UPDATE runs SET version=version+1 WHERE run_id=?", (run_id,))

    def verify_artifact(
        self, run_id: str, executor_key: str, verification_key: str
    ) -> None:
        with self._write() as db:
            row = db.execute(
                "SELECT sender,payload_json FROM handoffs WHERE run_id=? AND stage_key=?",
                (run_id, verification_key),
            ).fetchone()
            if (
                row is None
                or row["sender"] != "Verifier"
                or json.loads(row["payload_json"])["verdict"] != "PASS"
            ):
                raise CoordinationConflict("artifact has no passing verification")
            db.execute(
                "UPDATE artifacts SET verification_status='VERIFIED' WHERE run_id=? AND stage_key=?",
                (run_id, executor_key),
            )
            db.execute(
                "UPDATE role_tasks SET status='VERIFIED' WHERE run_id=? AND stage_key=?",
                (run_id, executor_key),
            )

    def finish(self, run_id: str, status: str, result: dict[str, Any]) -> None:
        with self._write() as db:
            row = db.execute(
                "SELECT status,result_json FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            text = canonical(result)
            if row is None or (
                row["status"] != "RUNNING" and row["result_json"] != text
            ):
                raise CoordinationConflict("cannot replace a terminal result")
            db.execute(
                "UPDATE runs SET status=?,result_json=? WHERE run_id=?",
                (status, text, run_id),
            )

    def reserve_call(self, run_id: str, role: str, operation: str, units: int) -> str:
        with self._write() as db:
            row = db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None or row["status"] != "RUNNING":
                raise CoordinationConflict("no active budget")
            limits = json.loads(row["limits_json"])
            count, total = db.execute(
                "SELECT COUNT(*),COALESCE(SUM(charged_units),0) FROM model_calls WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if (
                count >= limits["max_model_calls"]
                or total + units > limits["max_token_units"]
                or time.time() - row["started"] >= limits["deadline_seconds"]
            ):
                raise CoordinationConflict("system model budget exhausted")
            call_id = identity(run_id, "call", str(count))
            db.execute(
                "INSERT INTO model_calls(call_id,run_id,role,operation,status,charged_units) VALUES(?,?,?,?,?,?)",
                (call_id, run_id, role, operation, "RESERVED", units),
            )
            return call_id

    def settle_call(
        self, call_id: str, usage: dict[str, int], elapsed: float, status: str
    ) -> None:
        actual = usage.get("input_tokens", usage.get("prompt_tokens", 0)) + usage.get(
            "output_tokens", usage.get("completion_tokens", 0)
        )
        with self._write() as db:
            row = db.execute(
                "SELECT charged_units,status FROM model_calls WHERE call_id=?",
                (call_id,),
            ).fetchone()
            if row is None or row["status"] != "RESERVED":
                raise CoordinationConflict("call already settled or unknown")
            # Missing usage (including process death) keeps the reservation charged.
            db.execute(
                "UPDATE model_calls SET status=?,charged_units=?,usage_json=?,elapsed=? WHERE call_id=?",
                (
                    status,
                    actual if actual > 0 else row[0],
                    canonical(usage),
                    elapsed,
                    call_id,
                ),
            )

    def usage(self, run_id: str) -> dict[str, Any]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT role,operation,status,charged_units,usage_json,elapsed FROM model_calls WHERE run_id=? ORDER BY rowid",
                (run_id,),
            ).fetchall()
        return {
            "model_calls": len(rows),
            "charged_token_units": sum(row["charged_units"] for row in rows),
            "calls": [
                {**dict(row), "usage": json.loads(row["usage_json"])} for row in rows
            ],
        }
