"""Independent SQLite ownership for immutable handoffs and model-call budgets."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from src.memory.collaboration.models import canonical, digest, identity, parse_plan
from src.memory.storage.sqlite_utils import ClosingSQLiteConnection


class CoordinationConflict(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        # Safe invariant fingerprint: no prompt, source text or exception body.
        self.invariant_code = "COORDINATION_" + digest(message)[:16]


class ObservationFrontierConflict(CoordinationConflict):
    """Persisted tool observations are unavailable or have changed."""


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
                CREATE TABLE IF NOT EXISTS lifecycle_result_outbox(
                    run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
                    created REAL NOT NULL, state TEXT NOT NULL DEFAULT 'PENDING');
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
                CREATE TABLE IF NOT EXISTS task_graphs(
                    graph_id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE REFERENCES runs(run_id),
                    task_id TEXT NOT NULL, plan_hash TEXT NOT NULL, plan_json TEXT NOT NULL,
                    planning_frontier_json TEXT NOT NULL, planning_frontier_hash TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS task_graphs_task ON task_graphs(task_id);
                CREATE TABLE IF NOT EXISTS task_nodes(
                    node_id TEXT PRIMARY KEY, graph_id TEXT NOT NULL REFERENCES task_graphs(graph_id),
                    node_key TEXT NOT NULL, spec_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING',
                    UNIQUE(graph_id,node_key));
                CREATE TABLE IF NOT EXISTS task_attempts(
                    attempt_id TEXT PRIMARY KEY, node_id TEXT NOT NULL REFERENCES task_nodes(node_id),
                    run_id TEXT NOT NULL REFERENCES runs(run_id), input_json TEXT NOT NULL,
                    input_hash TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'RUNNING', artifact_id TEXT,
                    UNIQUE(node_id,run_id));
                CREATE TABLE IF NOT EXISTS task_invalidations(
                    event_id TEXT PRIMARY KEY, graph_id TEXT NOT NULL REFERENCES task_graphs(graph_id),
                    reason TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS artifact_invalidations(
                    event_id TEXT PRIMARY KEY, graph_id TEXT NOT NULL REFERENCES task_graphs(graph_id),
                    artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
                    reason TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS attempt_observations(
                    attempt_id TEXT PRIMARY KEY REFERENCES task_attempts(attempt_id),
                    payload_json TEXT NOT NULL, payload_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS repair_plans(
                    repair_id TEXT PRIMARY KEY, source_run_id TEXT NOT NULL REFERENCES runs(run_id),
                    event_id TEXT NOT NULL REFERENCES artifact_invalidations(event_id),
                    state_hash TEXT NOT NULL, plan_json TEXT NOT NULL, plan_hash TEXT NOT NULL,
                    created REAL NOT NULL, UNIQUE(source_run_id,event_id));
                CREATE TABLE IF NOT EXISTS prepared_snapshots(
                    run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
                    payload_json TEXT NOT NULL, payload_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS repair_queue(
                    event_id TEXT PRIMARY KEY REFERENCES artifact_invalidations(event_id),
                    run_id TEXT NOT NULL REFERENCES runs(run_id), status TEXT NOT NULL,
                    error_type TEXT, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS experiences(
                    experience_id TEXT PRIMARY KEY, source_run_id TEXT NOT NULL REFERENCES runs(run_id),
                    payload_json TEXT NOT NULL, payload_hash TEXT NOT NULL, state TEXT NOT NULL,
                    revision_of TEXT REFERENCES experiences(experience_id), created REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS experiences_source ON experiences(source_run_id,state);
                CREATE TABLE IF NOT EXISTS experience_events(
                    event_id TEXT PRIMARY KEY, experience_id TEXT NOT NULL REFERENCES experiences(experience_id),
                    kind TEXT NOT NULL, payload_json TEXT NOT NULL, payload_hash TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS repair_resolutions(
                    event_id TEXT PRIMARY KEY REFERENCES artifact_invalidations(event_id),
                    repair_id TEXT NOT NULL REFERENCES repair_plans(repair_id),
                    result_run_id TEXT NOT NULL REFERENCES runs(run_id));
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

    def record_graph(
        self, run_id: str, task_id: str, plan: dict[str, Any], frontier: dict[str, Any]
    ) -> str:
        graph_id = identity(run_id, "graph")
        with self._write() as db:
            run = db.execute(
                "SELECT task_id,status FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if run is None or run["task_id"] != task_id or run["status"] != "RUNNING":
                raise CoordinationConflict("graph has no active task ownership")
            old = db.execute(
                "SELECT * FROM task_graphs WHERE run_id=?", (run_id,)
            ).fetchone()
            if old:
                if old["plan_hash"] != digest(plan) or old[
                    "planning_frontier_hash"
                ] != digest(frontier):
                    raise CoordinationConflict("graph or planning frontier changed")
            else:
                db.execute(
                    "INSERT INTO task_graphs VALUES(?,?,?,?,?,?,?)",
                    (
                        graph_id,
                        run_id,
                        task_id,
                        digest(plan),
                        canonical(plan),
                        canonical(frontier),
                        digest(frontier),
                    ),
                )
                for node in plan["subtasks"]:
                    db.execute(
                        "INSERT INTO task_nodes(node_id,graph_id,node_key,spec_json) VALUES(?,?,?,?)",
                        (
                            identity(graph_id, node["key"]),
                            graph_id,
                            node["key"],
                            canonical(node),
                        ),
                    )
        return graph_id

    def record_attempt(
        self, run_id: str, graph_id: str, key: str, frontier: dict[str, Any]
    ) -> str:
        node_id = identity(graph_id, key)
        attempt_id = identity(node_id, run_id, "attempt")
        with self._write() as db:
            row = db.execute(
                "SELECT n.status,n.spec_json,g.run_id FROM task_nodes n JOIN task_graphs g USING(graph_id) WHERE n.node_id=?",
                (node_id,),
            ).fetchone()
            if row is None or row["run_id"] != run_id:
                raise CoordinationConflict("attempt has no graph ownership")
            if row["status"] in {"STALE", "BLOCKED", "FAILED"}:
                raise CoordinationConflict("invalidated task node cannot be attempted")
            for dependency in json.loads(row["spec_json"])["dependencies"]:
                predecessor = db.execute(
                    "SELECT status FROM task_nodes WHERE graph_id=? AND node_key=?",
                    (graph_id, dependency),
                ).fetchone()
                if predecessor is None or predecessor["status"] != "VERIFIED":
                    raise CoordinationConflict("task predecessor is not verified")
            old = db.execute(
                "SELECT input_hash FROM task_attempts WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
            if old:
                if old["input_hash"] != digest(frontier):
                    raise CoordinationConflict("attempt input frontier changed")
            else:
                db.execute(
                    "INSERT INTO task_attempts(attempt_id,node_id,run_id,input_json,input_hash) VALUES(?,?,?,?,?)",
                    (
                        attempt_id,
                        node_id,
                        run_id,
                        canonical(frontier),
                        digest(frontier),
                    ),
                )
                db.execute(
                    "UPDATE task_nodes SET status='RUNNING' WHERE node_id=?", (node_id,)
                )
        return attempt_id

    def list_task_graphs(self, task_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            graphs = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM task_graphs WHERE task_id=? ORDER BY rowid",
                    (task_id,),
                )
            ]
            for graph in graphs:
                graph["nodes"] = [
                    dict(row)
                    for row in db.execute(
                        "SELECT * FROM task_nodes WHERE graph_id=? ORDER BY rowid",
                        (graph["graph_id"],),
                    )
                ]
                for node in graph["nodes"]:
                    node["attempts"] = [
                        dict(row)
                        for row in db.execute(
                            "SELECT * FROM task_attempts WHERE node_id=? ORDER BY rowid",
                            (node["node_id"],),
                        )
                    ]
            return graphs

    def record_attempt_observations(
        self, run_id: str, attempt_id: str, observations: list[dict[str, Any]]
    ) -> None:
        with self._write() as db:
            row = db.execute(
                "SELECT a.status,n.status AS node_status FROM task_attempts a JOIN task_nodes n ON n.node_id=a.node_id WHERE a.attempt_id=? AND a.run_id=?",
                (attempt_id, run_id),
            ).fetchone()
            if row is None or row["node_status"] not in {"PRODUCED", "VERIFIED"}:
                raise CoordinationConflict(
                    "observation attempt is unavailable or stale"
                )
            old = db.execute(
                "SELECT payload_json,payload_hash FROM attempt_observations WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()
            if old is not None:
                try:
                    stored = json.loads(old["payload_json"])
                except (TypeError, ValueError) as exc:
                    raise ObservationFrontierConflict(
                        "stored tool observations are corrupted"
                    ) from exc
                if (
                    not isinstance(stored, list)
                    or digest(stored) != old["payload_hash"]
                    or old["payload_hash"] != digest(observations)
                ):
                    raise ObservationFrontierConflict(
                        "tool receipt observations changed"
                    )
            else:
                if row["status"] != "PRODUCED":
                    raise ObservationFrontierConflict(
                        "cannot reconstruct legacy observation history"
                    )
                db.execute(
                    "INSERT INTO attempt_observations VALUES(?,?,?)",
                    (attempt_id, canonical(observations), digest(observations)),
                )

    def get_attempt_observations(
        self, run_id: str, attempt_id: str
    ) -> list[dict[str, Any]] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT o.payload_json,o.payload_hash,h.payload_json AS execution_json,h.payload_hash AS execution_hash FROM attempt_observations o JOIN task_attempts a ON a.attempt_id=o.attempt_id JOIN task_nodes n ON n.node_id=a.node_id LEFT JOIN handoffs h ON h.run_id=a.run_id AND h.stage_key='execute:' || n.node_key WHERE o.attempt_id=? AND a.run_id=?",
                (attempt_id, run_id),
            ).fetchone()
        if row is None:
            return None
        try:
            observations = json.loads(row["payload_json"])
            execution = json.loads(row["execution_json"])
        except (TypeError, ValueError) as exc:
            raise CoordinationConflict("tool observation integrity failure") from exc
        if (
            not isinstance(observations, list)
            or digest(observations) != row["payload_hash"]
            or not isinstance(execution, dict)
            or digest(execution) != row["execution_hash"]
            or not isinstance(execution.get("executed_tool_call_ids"), list)
            or any(not isinstance(item, dict) for item in observations)
            or any(
                not isinstance(item.get("tool_call_id"), str) for item in observations
            )
            or any(
                not isinstance(item, str)
                for item in execution["executed_tool_call_ids"]
            )
            or len(observations) != len(execution["executed_tool_call_ids"])
            or {item["tool_call_id"] for item in observations}
            != set(execution["executed_tool_call_ids"])
            or any(
                item.get("agent_run_id") != execution.get("agent_run_id")
                for item in observations
            )
        ):
            raise CoordinationConflict("tool observation integrity failure")
        return observations

    def prior_graph_ids(self, task_id: str, current_run_id: str) -> list[str]:
        """Bounded lineage metadata; prior nodes are not authorized for reuse."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT g.graph_id FROM task_graphs g JOIN runs r ON r.run_id=g.run_id "
                "WHERE g.task_id=? AND g.run_id<>? AND r.status IN ('SUCCEEDED','FAILED') "
                "ORDER BY g.rowid DESC LIMIT 8",
                (task_id, current_run_id),
            ).fetchall()
        return [row[0] for row in reversed(rows)]

    def invalidate_run(self, run_id: str, reason: str) -> None:
        """Mark the conservatively affected whole graph; preserve historical artifacts."""
        if reason not in {"TASK_FRONTIER", "SOURCE_FRONTIER", "STALE_REPLAY"}:
            raise CoordinationConflict("invalid invalidation reason")
        graph_id = identity(run_id, "graph")
        event_id = identity(graph_id, "invalidation", reason)
        with self._write() as db:
            if (
                db.execute(
                    "SELECT 1 FROM task_graphs WHERE graph_id=?", (graph_id,)
                ).fetchone()
                is None
            ):
                return
            inserted = db.execute(
                "INSERT OR IGNORE INTO task_invalidations VALUES(?,?,?,?)",
                (event_id, graph_id, reason, time.time()),
            ).rowcount
            if inserted:
                self._stale_experiences(db, run_id, event_id)
                db.execute(
                    "UPDATE task_nodes SET status=CASE WHEN status='PENDING' THEN 'BLOCKED' "
                    "WHEN status IN ('RUNNING','PRODUCED','VERIFIED') THEN 'STALE' ELSE status END "
                    "WHERE graph_id=?",
                    (graph_id,),
                )

    def invalidate_artifact(
        self, run_id: str, artifact_id: str, event_id: str
    ) -> list[str]:
        """Revoke a verified artifact and invalidate its transitive graph dependants.

        This operation accepts host-owned artifact/event identities only; a
        Provider proposal is not authority to call it. Historical attempts and
        artifacts remain immutable. The node projection represents validity.
        """
        if not event_id or len(event_id) > 200:
            raise CoordinationConflict("invalid invalidation event identity")
        graph_id = identity(run_id, "graph")
        with self._write() as db:
            existing = db.execute(
                "SELECT graph_id,artifact_id FROM artifact_invalidations WHERE event_id=?",
                (event_id,),
            ).fetchone()
            if existing is not None and (
                existing["graph_id"] != graph_id
                or existing["artifact_id"] != artifact_id
            ):
                raise CoordinationConflict("invalidation event identity changed")
            artifact = db.execute(
                "SELECT * FROM artifacts WHERE artifact_id=? AND run_id=?",
                (artifact_id, run_id),
            ).fetchone()
            if artifact is None or artifact["verification_status"] != "VERIFIED":
                raise CoordinationConflict(
                    "artifact is not a verified result of this run"
                )
            graph = db.execute(
                "SELECT plan_json,plan_hash FROM task_graphs WHERE graph_id=?",
                (graph_id,),
            ).fetchone()
            if graph is None:
                raise CoordinationConflict("artifact graph missing")
            plan = json.loads(graph["plan_json"])
            if digest(plan) != graph["plan_hash"]:
                raise CoordinationConflict("graph integrity failure")
            rows = db.execute(
                "SELECT node_id,node_key,spec_json FROM task_nodes WHERE graph_id=?",
                (graph_id,),
            ).fetchall()
            specs = {row["node_key"]: json.loads(row["spec_json"]) for row in rows}
            expected = {item["key"]: item for item in plan["subtasks"]}
            if specs != expected or len(rows) != len(expected):
                raise CoordinationConflict("graph node integrity failure")
            key = artifact["stage_key"].removeprefix("execute:")
            if key not in specs or artifact["stage_key"] != "execute:" + key:
                raise CoordinationConflict("artifact does not belong to graph node")
            affected = {key}
            while True:
                expanded = affected | {
                    node
                    for node, spec in specs.items()
                    if set(spec["dependencies"]) & affected
                }
                if expanded == affected:
                    break
                affected = expanded
            if existing is None:
                db.execute(
                    "INSERT INTO artifact_invalidations VALUES(?,?,?,?,?)",
                    (event_id, graph_id, artifact_id, "ARTIFACT_REVOKED", time.time()),
                )
                db.execute(
                    "INSERT INTO repair_queue VALUES(?,?,?,NULL,?)",
                    (event_id, run_id, "PENDING", time.time()),
                )
                self._stale_experiences(db, run_id, event_id)
                for node_key in affected:
                    db.execute(
                        "UPDATE task_nodes SET status=CASE WHEN status='PENDING' THEN 'BLOCKED' WHEN status IN ('RUNNING','PRODUCED','VERIFIED') THEN 'STALE' ELSE status END WHERE graph_id=? AND node_key=?",
                        (graph_id, node_key),
                    )
            return [row["node_id"] for row in rows if row["node_key"] in affected]

    @staticmethod
    def _stale_experiences(
        db: sqlite3.Connection, run_id: str, source_event: str
    ) -> None:
        for row in db.execute(
            "SELECT experience_id FROM experiences WHERE source_run_id=? AND state IN ('ACTIVE','CANDIDATE','NEEDS_CONFIRMATION')",
            (run_id,),
        ).fetchall():
            event = identity(row[0], source_event, "source-stale")
            payload = {"source_event": source_event}
            db.execute(
                "INSERT OR IGNORE INTO experience_events VALUES(?,?,?,?,?,?)",
                (
                    event,
                    row[0],
                    "SOURCE_STALE",
                    canonical(payload),
                    digest(payload),
                    time.time(),
                ),
            )
            db.execute(
                "UPDATE experiences SET state='STALE' WHERE experience_id=?", (row[0],)
            )

    def list_repairs(
        self, session_id: str, *, pending_only: bool = False, limit: int = 20
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100:
            raise ValueError("invalid repair queue limit")
        with self._connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT q.* FROM repair_queue q JOIN runs r ON r.run_id=q.run_id WHERE r.session_id=? AND (?=0 OR (q.status='PENDING' AND r.status IN ('SUCCEEDED','FAILED') AND r.result_json IS NOT NULL)) ORDER BY q.updated,q.event_id LIMIT ?",
                    (session_id, int(pending_only), limit),
                )
            ]

    def list_session_graphs(
        self, session_id: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100:
            raise ValueError("invalid graph limit")
        with self._connect() as db:
            graphs = [
                dict(row)
                for row in db.execute(
                    "SELECT g.graph_id,g.run_id,g.task_id,r.status AS run_status FROM task_graphs g JOIN runs r ON r.run_id=g.run_id WHERE r.session_id=? ORDER BY g.rowid DESC LIMIT ?",
                    (session_id, limit),
                )
            ]
            for graph in graphs:
                graph["nodes"] = [
                    dict(row)
                    for row in db.execute(
                        "SELECT n.node_key,n.status,a.artifact_id FROM task_nodes n LEFT JOIN task_attempts a ON a.node_id=n.node_id AND a.run_id=? WHERE n.graph_id=? ORDER BY n.rowid",
                        (graph["run_id"], graph["graph_id"]),
                    )
                ]
            return graphs

    def finish_repair_queue(
        self, event_id: str, run_id: str, status: str, error_type: str | None = None
    ) -> None:
        if status not in {"SUCCEEDED", "FAILED"}:
            raise CoordinationConflict("invalid repair queue status")
        with self._write() as db:
            row = db.execute(
                "SELECT status FROM repair_queue WHERE event_id=? AND run_id=?",
                (event_id, run_id),
            ).fetchone()
            if row is None:
                raise CoordinationConflict("unknown repair queue event")
            if row["status"] == "PENDING":
                db.execute(
                    "UPDATE repair_queue SET status=?,error_type=?,updated=? WHERE event_id=? AND run_id=?",
                    (status, error_type, time.time(), event_id, run_id),
                )
            elif row["status"] != status:
                raise CoordinationConflict("repair queue outcome changed")

    def ensure_repair_queue(self, run_id: str, event_id: str) -> None:
        with self._write() as db:
            event = db.execute(
                "SELECT 1 FROM artifact_invalidations i JOIN task_graphs g ON g.graph_id=i.graph_id WHERE i.event_id=? AND g.run_id=?",
                (event_id, run_id),
            ).fetchone()
            if event is None:
                raise CoordinationConflict("repair event does not belong to run")
            db.execute(
                "INSERT OR IGNORE INTO repair_queue VALUES(?,?,?,NULL,?)",
                (event_id, run_id, "PENDING", time.time()),
            )

    def repair_resolution(self, run_id: str, event_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT x.* FROM repair_resolutions x JOIN repair_queue q ON q.event_id=x.event_id WHERE x.event_id=? AND q.run_id=?",
                (event_id, run_id),
            ).fetchone()
            return dict(row) if row else None

    def resolve_pending_repairs(
        self, run_id: str, repair_id: str, result_run_id: str
    ) -> None:
        """One checked repair can satisfy all events in the same source snapshot."""
        with self._write() as db:
            plan = db.execute(
                "SELECT * FROM repair_plans WHERE repair_id=? AND source_run_id=?",
                (repair_id, run_id),
            ).fetchone()
            result = db.execute(
                "SELECT status,result_json FROM runs WHERE run_id=?", (result_run_id,)
            ).fetchone()
            if (
                plan is None
                or result is None
                or result["status"] != "SUCCEEDED"
                or not result["result_json"]
            ):
                raise CoordinationConflict(
                    "repair resolution has no successful owned result"
                )
            metadata = (
                json.loads(result["result_json"])
                .get("trace_metadata", {})
                .get("repair", {})
            )
            if (
                metadata.get("repair_id") != repair_id
                or self._repair_state(db, run_id, plan["event_id"])
                != plan["state_hash"]
            ):
                raise CoordinationConflict(
                    "repair source changed before queue resolution"
                )
            for event in db.execute(
                "SELECT event_id FROM repair_queue WHERE run_id=? AND status='PENDING'",
                (run_id,),
            ).fetchall():
                db.execute(
                    "INSERT OR IGNORE INTO repair_resolutions VALUES(?,?,?)",
                    (event[0], repair_id, result_run_id),
                )
                db.execute(
                    "UPDATE repair_queue SET status='SUCCEEDED',error_type=NULL,updated=? WHERE event_id=?",
                    (time.time(), event[0]),
                )

    def validate_completed_graph(self, run_id: str) -> None:
        """A completed result cannot be replayed after any node is revoked."""
        graph_id = identity(run_id, "graph")
        with self._connect() as db:
            if db.execute(
                "SELECT 1 FROM artifact_invalidations WHERE graph_id=? LIMIT 1",
                (graph_id,),
            ).fetchone():
                raise CoordinationConflict("completed graph contains invalid nodes")
            if db.execute(
                "SELECT 1 FROM task_nodes WHERE graph_id=? AND status<>'VERIFIED' LIMIT 1",
                (graph_id,),
            ).fetchone():
                raise CoordinationConflict("completed graph contains invalid nodes")

    def repair_candidate(self, run_id: str, event_id: str) -> dict[str, Any]:
        """Build a bounded repair candidate from persisted, checked evidence.

        This method does not execute or authorize a new turn. Callers must
        validate the authoritative task and source frontier before using it.
        """
        graph_id = identity(run_id, "graph")
        with self._connect() as db:
            db.execute("BEGIN")
            event = db.execute(
                "SELECT graph_id FROM artifact_invalidations WHERE event_id=?",
                (event_id,),
            ).fetchone()
            graph = db.execute(
                "SELECT * FROM task_graphs WHERE graph_id=? AND run_id=?",
                (graph_id, run_id),
            ).fetchone()
            if event is None or event["graph_id"] != graph_id or graph is None:
                raise CoordinationConflict("repair event does not belong to graph")
            plan = json.loads(graph["plan_json"])
            if (
                digest(plan) != graph["plan_hash"]
                or digest(json.loads(graph["planning_frontier_json"]))
                != graph["planning_frontier_hash"]
            ):
                raise CoordinationConflict("graph integrity failure")
            tools = {name for item in plan["subtasks"] for name in item["tool_names"]}
            ordered = parse_plan(plan, tools)
            rows = {
                row["node_key"]: row
                for row in db.execute(
                    "SELECT * FROM task_nodes WHERE graph_id=?", (graph_id,)
                )
            }
            expected = {row["key"]: row for row in plan["subtasks"]}
            revoked = {
                row["stage_key"].removeprefix("execute:")
                for row in db.execute(
                    "SELECT a.stage_key FROM artifact_invalidations i JOIN artifacts a ON a.artifact_id=i.artifact_id WHERE i.graph_id=?",
                    (graph_id,),
                )
            }
            if not revoked <= expected.keys():
                raise CoordinationConflict("revoked artifact does not belong to graph")
            if set(rows) != set(expected) or any(
                json.loads(rows[key]["spec_json"]) != value
                for key, value in expected.items()
            ):
                raise CoordinationConflict("graph node integrity failure")
            rerun, reused, invalid_keys = [], [], set()
            for subtask in ordered:
                node = rows[subtask.key]
                if (
                    subtask.key in revoked
                    or node["status"] != "VERIFIED"
                    or set(subtask.dependencies) & invalid_keys
                ):
                    invalid_keys.add(subtask.key)
                    rerun.append(
                        {"node_id": node["node_id"], "spec": expected[subtask.key]}
                    )
                    continue
                attempt = db.execute(
                    "SELECT * FROM task_attempts WHERE node_id=? AND run_id=? AND status='VERIFIED'",
                    (node["node_id"], run_id),
                ).fetchone()
                artifact = (
                    db.execute(
                        "SELECT * FROM artifacts WHERE artifact_id=? AND run_id=? AND verification_status='VERIFIED'",
                        (attempt["artifact_id"], run_id),
                    ).fetchone()
                    if attempt
                    else None
                )
                execution = db.execute(
                    "SELECT * FROM handoffs WHERE run_id=? AND stage_key=?",
                    (run_id, "execute:" + subtask.key),
                ).fetchone()
                verification = db.execute(
                    "SELECT * FROM handoffs WHERE run_id=? AND stage_key=?",
                    (run_id, "verify:" + subtask.key),
                ).fetchone()
                if (
                    attempt is None
                    or artifact is None
                    or execution is None
                    or verification is None
                    or digest(json.loads(attempt["input_json"]))
                    != attempt["input_hash"]
                    or execution["sender"] != "Executor"
                    or verification["sender"] != "Verifier"
                    or digest(json.loads(execution["payload_json"]))
                    != execution["payload_hash"]
                    or execution["payload_hash"] != artifact["payload_hash"]
                    or digest(json.loads(verification["payload_json"]))
                    != verification["payload_hash"]
                    or json.loads(verification["payload_json"]).get("verdict") != "PASS"
                ):
                    raise CoordinationConflict("reusable artifact integrity failure")
                observations_row = db.execute(
                    "SELECT * FROM attempt_observations WHERE attempt_id=?",
                    (attempt["attempt_id"],),
                ).fetchone()
                observations = None
                if observations_row is not None:
                    observations = json.loads(observations_row["payload_json"])
                    if digest(observations) != observations_row["payload_hash"]:
                        raise CoordinationConflict("tool observation integrity failure")
                    executed = json.loads(execution["payload_json"])
                    expected_calls = executed.get("executed_tool_call_ids")
                    if (
                        not isinstance(observations, list)
                        or not isinstance(expected_calls, list)
                        or any(not isinstance(item, dict) for item in observations)
                        or any(
                            not isinstance(call_id, str) for call_id in expected_calls
                        )
                        or any(
                            not isinstance(item.get("tool_call_id"), str)
                            for item in observations
                        )
                        or len(observations) != len(expected_calls)
                        or {item.get("tool_call_id") for item in observations}
                        != set(expected_calls)
                        or any(
                            item.get("agent_run_id") != executed.get("agent_run_id")
                            for item in observations
                        )
                    ):
                        raise CoordinationConflict(
                            "tool observation ownership mismatch"
                        )
                input_frontier = json.loads(attempt["input_json"])
                reused.append(
                    {
                        "node_id": node["node_id"],
                        "node_key": subtask.key,
                        "artifact_id": artifact["artifact_id"],
                        "payload_hash": artifact["payload_hash"],
                        "attempt_id": attempt["attempt_id"],
                        "input_hash": attempt["input_hash"],
                        "input_frontier": input_frontier,
                        "execution": json.loads(execution["payload_json"]),
                        "verification": json.loads(verification["payload_json"]),
                        "conversation_frontier": input_frontier.get(
                            "conversation_frontier"
                        ),
                        "observations": observations,
                        "reuse_authorized": False,
                        "reason": "conversation and external observations require current validation",
                    }
                )
            return {
                "schema_version": 1,
                "run_id": run_id,
                "task_id": graph["task_id"],
                "graph_id": graph_id,
                "event_id": event_id,
                "execution_contract_hash": json.loads(
                    graph["planning_frontier_json"]
                ).get("execution_contract_hash"),
                "rerun": rerun,
                "reuse_candidates": reused,
            }

    @staticmethod
    def _repair_state(db: sqlite3.Connection, run_id: str, event_id: str) -> str:
        graph_id = identity(run_id, "graph")
        event = db.execute(
            "SELECT graph_id FROM artifact_invalidations WHERE event_id=?", (event_id,)
        ).fetchone()
        run = db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        graph = db.execute(
            "SELECT * FROM task_graphs WHERE graph_id=? AND run_id=?",
            (graph_id, run_id),
        ).fetchone()
        if (
            event is None
            or event["graph_id"] != graph_id
            or run is None
            or graph is None
        ):
            raise CoordinationConflict("repair event does not belong to run")
        if run["status"] not in {"SUCCEEDED", "FAILED"} or not run["result_json"]:
            raise CoordinationConflict("repair requires a terminal run")
        queries = {
            "nodes": (
                "SELECT * FROM task_nodes WHERE graph_id=? ORDER BY node_id",
                graph_id,
            ),
            "attempts": (
                "SELECT * FROM task_attempts WHERE run_id=? ORDER BY attempt_id",
                run_id,
            ),
            "handoffs": (
                "SELECT * FROM handoffs WHERE run_id=? ORDER BY stage_key",
                run_id,
            ),
            "role_tasks": (
                "SELECT * FROM role_tasks WHERE run_id=? ORDER BY stage_key",
                run_id,
            ),
            "artifacts": (
                "SELECT * FROM artifacts WHERE run_id=? ORDER BY artifact_id",
                run_id,
            ),
            "artifact_events": (
                "SELECT * FROM artifact_invalidations WHERE graph_id=? ORDER BY event_id",
                graph_id,
            ),
            "task_events": (
                "SELECT * FROM task_invalidations WHERE graph_id=? ORDER BY event_id",
                graph_id,
            ),
            "observations": (
                "SELECT o.* FROM attempt_observations o JOIN task_attempts a ON a.attempt_id=o.attempt_id WHERE a.run_id=? ORDER BY o.attempt_id",
                run_id,
            ),
        }
        state = {"run": dict(run), "graph": dict(graph)}
        for name, (query, parameter) in queries.items():
            state[name] = [dict(row) for row in db.execute(query, (parameter,))]
        if state["task_events"]:
            raise CoordinationConflict("global invalidation prevents local repair")
        return digest(state)

    def repair_state_hash(self, run_id: str, event_id: str) -> str:
        with self._connect() as db:
            db.execute("BEGIN")
            return self._repair_state(db, run_id, event_id)

    def store_repair_plan(
        self, run_id: str, event_id: str, state_hash: str, plan: dict[str, Any]
    ) -> dict[str, Any]:
        """Persist an idempotent plan only if its source graph is unchanged."""
        repair_id = identity(run_id, event_id, "repair-plan-v1")
        with self._write() as db:
            if self._repair_state(db, run_id, event_id) != state_hash:
                raise CoordinationConflict(
                    "source graph changed during repair planning"
                )
            if (
                not isinstance(plan, dict)
                or "repair_id" in plan
                or plan.get("source_run_id") != run_id
                or plan.get("event_id") != event_id
                or plan.get("execution_status") != "NOT_STARTED"
            ):
                raise CoordinationConflict("invalid repair plan ownership or status")
            row = db.execute(
                "SELECT * FROM repair_plans WHERE repair_id=?", (repair_id,)
            ).fetchone()
            if row is not None:
                if (
                    row["state_hash"] != state_hash
                    or row["plan_hash"] != digest(plan)
                    or digest(json.loads(row["plan_json"])) != row["plan_hash"]
                ):
                    raise CoordinationConflict(
                        "repair plan identity or integrity changed"
                    )
            else:
                db.execute(
                    "INSERT INTO repair_plans VALUES(?,?,?,?,?,?,?)",
                    (
                        repair_id,
                        run_id,
                        event_id,
                        state_hash,
                        canonical(plan),
                        digest(plan),
                        time.time(),
                    ),
                )
        return {"repair_id": repair_id, **plan}

    def get_repair_plan(self, repair_id: str) -> dict[str, Any]:
        with self._connect() as db:
            db.execute("BEGIN")
            row = db.execute(
                "SELECT * FROM repair_plans WHERE repair_id=?", (repair_id,)
            ).fetchone()
            if row is None:
                raise CoordinationConflict("unknown repair plan")
            try:
                plan = json.loads(row["plan_json"])
            except (TypeError, ValueError) as exc:
                raise CoordinationConflict("repair plan integrity failure") from exc
            if not isinstance(plan, dict) or digest(plan) != row["plan_hash"]:
                raise CoordinationConflict("repair plan integrity failure")
            if (
                self._repair_state(db, row["source_run_id"], row["event_id"])
                != row["state_hash"]
            ):
                raise CoordinationConflict("source graph changed since repair planning")
            return {"repair_id": repair_id, **plan}

    def save_prepared_snapshot(self, run_id: str, payload: Any) -> None:
        with self._write() as db:
            old = db.execute(
                "SELECT payload_json,payload_hash FROM prepared_snapshots WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if old:
                if (
                    old["payload_hash"] != digest(payload)
                    or digest(json.loads(old["payload_json"])) != old["payload_hash"]
                ):
                    raise CoordinationConflict("prepared snapshot changed")
            else:
                db.execute(
                    "INSERT INTO prepared_snapshots VALUES(?,?,?)",
                    (run_id, canonical(payload), digest(payload)),
                )

    def get_prepared_snapshot(self, run_id: str) -> Any:
        with self._connect() as db:
            row = db.execute(
                "SELECT payload_json,payload_hash FROM prepared_snapshots WHERE run_id=?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise CoordinationConflict("prepared snapshot unavailable")
        try:
            payload = json.loads(row["payload_json"])
            if digest(payload) != row["payload_hash"]:
                raise ValueError("hash mismatch")
        except (TypeError, ValueError) as exc:
            raise CoordinationConflict("prepared snapshot integrity failure") from exc
        return payload

    def latest_repair_task_state(self, run_id: str) -> str | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT payload_json,payload_hash FROM handoffs WHERE run_id=? AND stage_key LIKE 'repair-task-state:%' ORDER BY rowid DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        if digest(payload) != row["payload_hash"]:
            raise CoordinationConflict("repair task snapshot integrity failure")
        return payload["task_state_hash"]

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
            if role == "Executor":
                node_id = identity(
                    identity(run_id, "graph"), key.removeprefix("execute:")
                )
                node = db.execute(
                    "SELECT status FROM task_nodes WHERE node_id=?", (node_id,)
                ).fetchone()
                if node is not None and node["status"] != "RUNNING":
                    raise CoordinationConflict(
                        "invalidated task node cannot accept a result"
                    )
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
                node_key = key.removeprefix("execute:")
                node_id = identity(identity(run_id, "graph"), node_key)
                attempt_status = (
                    "PRODUCED" if payload.get("status") == "SUCCESS" else "FAILED"
                )
                db.execute(
                    "UPDATE task_attempts SET status=?,artifact_id=? WHERE node_id=? AND run_id=?",
                    (
                        attempt_status,
                        identity(run_id, key, "artifact"),
                        node_id,
                        run_id,
                    ),
                )
                db.execute(
                    "UPDATE task_nodes SET status=? WHERE node_id=?",
                    (attempt_status, node_id),
                )
            db.execute("UPDATE runs SET version=version+1 WHERE run_id=?", (run_id,))

    def verify_artifact(
        self, run_id: str, executor_key: str, verification_key: str
    ) -> None:
        with self._write() as db:
            node_id = identity(
                identity(run_id, "graph"), executor_key.removeprefix("execute:")
            )
            node = db.execute(
                "SELECT status FROM task_nodes WHERE node_id=?", (node_id,)
            ).fetchone()
            if node is not None and node["status"] not in {"PRODUCED", "VERIFIED"}:
                raise CoordinationConflict(
                    "stale or unproduced task node cannot be verified"
                )
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
            db.execute(
                "UPDATE task_attempts SET status='VERIFIED' WHERE node_id=? AND run_id=?",
                (node_id, run_id),
            )
            db.execute(
                "UPDATE task_nodes SET status='VERIFIED' WHERE node_id=?", (node_id,)
            )

    def finish(self, run_id: str, status: str, result: dict[str, Any]) -> None:
        with self._write() as db:
            row = db.execute(
                "SELECT status,result_json FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            text = canonical(result)
            if row is None or (
                row["status"] != "RUNNING"
                and (row["result_json"] != text or row["status"] != status)
            ):
                raise CoordinationConflict("cannot replace a terminal result")
            db.execute(
                "UPDATE runs SET status=?,result_json=? WHERE run_id=?",
                (status, text, run_id),
            )
            # The result and its lifecycle wake-up must survive the same commit.
            if isinstance(result.get("trace_metadata", {}).get("task_frontier"), dict):
                db.execute(
                    "INSERT OR IGNORE INTO lifecycle_result_outbox(run_id,created) VALUES(?,?)",
                    (run_id, time.time()),
                )
            if status == "FAILED":
                graph_id = identity(run_id, "graph")
                db.execute(
                    "UPDATE task_attempts SET status='FAILED' WHERE run_id=? AND status IN ('RUNNING','PRODUCED')",
                    (run_id,),
                )
                db.execute(
                    "UPDATE task_nodes SET status=CASE WHEN status='PENDING' THEN 'BLOCKED' ELSE 'FAILED' END WHERE graph_id=? AND status IN ('PENDING','RUNNING','PRODUCED')",
                    (graph_id,),
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
