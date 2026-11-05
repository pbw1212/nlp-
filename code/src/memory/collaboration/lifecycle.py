"""Durable task review, atomic Task 3 completion seals and experience outbox."""

from __future__ import annotations

import json
import time
from typing import Any

from src.memory.collaboration.models import (
    TASK_REVIEW_SCHEMA,
    canonical,
    digest,
    identity,
    validate,
)
from src.memory.collaboration.repair import RepairBusy, repair_lock
from src.memory.collaboration.repository import CoordinationConflict
from src.memory.conversation.models import ConversationRole
from src.memory.runtime.task_router import UNBOUND_TASK_ID
from src.memory.task_state.models import EntryKind, TaskStatus


class TaskLifecycle:
    def __init__(self, host: Any) -> None:
        self.host = host
        self.repository = host.repository
        self.tasks = host.runtime.task_service.repository
        with self.repository._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS task_cycles(
                    task_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, source_run_id TEXT NOT NULL,
                    state TEXT NOT NULL, detail_json TEXT NOT NULL, detail_hash TEXT NOT NULL,
                    updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS lifecycle_jobs(
                    job_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, session_id TEXT NOT NULL,
                    source_run_id TEXT NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL,
                    input_json TEXT NOT NULL,input_hash TEXT NOT NULL,
                    result_json TEXT,result_hash TEXT,error_code TEXT,created REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS lifecycle_pending ON lifecycle_jobs(session_id,state,created);
                CREATE TABLE IF NOT EXISTS lifecycle_deliveries(
                    job_id TEXT PRIMARY KEY REFERENCES lifecycle_jobs(job_id),result_hash TEXT NOT NULL);
                INSERT OR IGNORE INTO lifecycle_result_outbox(run_id,created)
                    SELECT run_id,started FROM runs WHERE result_json IS NOT NULL
                    AND json_type(result_json,'$.trace_metadata.task_frontier')='object';
            """
            )

    def _cycle(
        self,
        db: Any,
        task_id: str,
        session_id: str,
        source_run_id: str,
        state: str,
        detail: dict,
    ) -> None:
        db.execute(
            "INSERT INTO task_cycles VALUES(?,?,?,?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET session_id=excluded.session_id,source_run_id=excluded.source_run_id,state=excluded.state,detail_json=excluded.detail_json,detail_hash=excluded.detail_hash,updated=excluded.updated",
            (
                task_id,
                session_id,
                source_run_id,
                state,
                canonical(detail),
                digest(detail),
                time.time(),
            ),
        )

    def _job(self, row: Any) -> dict:
        value = dict(row)
        payload = json.loads(value.pop("input_json"))
        if digest(payload) != value["input_hash"]:
            raise CoordinationConflict("lifecycle job input integrity failure")
        if any(
            payload.get(key) != value[key]
            for key in ("task_id", "session_id", "source_run_id", "kind")
        ):
            raise CoordinationConflict("lifecycle job ownership changed")
        result = json.loads(value["result_json"]) if value["result_json"] else None
        if result is not None and digest(result) != value["result_hash"]:
            raise CoordinationConflict("lifecycle job result integrity failure")
        return {**value, "input": payload, "result": result}

    def jobs(
        self,
        session_id: str,
        *,
        pending: bool = False,
        limit: int = 100,
        task_id: str | None = None,
    ) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid lifecycle job limit")
        with self.repository._connect() as db:
            order = "created DESC,job_id" if pending else "created DESC,job_id DESC"
            values = [
                self._job(row)
                for row in db.execute(
                    "SELECT * FROM lifecycle_jobs WHERE session_id=? AND (?=0 OR state='PENDING') AND (? IS NULL OR task_id=?) ORDER BY "
                    + order
                    + " LIMIT ?",
                    (session_id, int(pending), task_id, task_id, limit),
                )
            ]
        return values if pending else list(reversed(values))

    def status(self, session_id: str, task_id: str | None = None) -> dict:
        with self.repository._connect() as db:
            db.execute("BEGIN")
            rows = db.execute(
                "SELECT * FROM task_cycles WHERE session_id=? AND (? IS NULL OR task_id=?) ORDER BY updated DESC LIMIT 100",
                (session_id, task_id, task_id),
            ).fetchall()
            cycles = []
            for row in rows:
                detail = json.loads(row["detail_json"])
                if digest(detail) != row["detail_hash"]:
                    raise CoordinationConflict("task cycle integrity failure")
                cycles.append(
                    {
                        "task_id": row["task_id"],
                        "source_run_id": row["source_run_id"],
                        "state": row["state"],
                        "detail": detail,
                    }
                )
                usage = db.execute(
                    "SELECT COUNT(*) AS model_calls,COALESCE(SUM(c.charged_units),0) AS charged_units FROM model_calls c JOIN runs r ON r.run_id=c.run_id WHERE r.task_id=?",
                    (row["task_id"],),
                ).fetchone()
                cycles[-1]["usage"] = {
                    "model_calls": usage["model_calls"],
                    "charged_units": usage["charged_units"],
                }
        # Recorded completion is historical; current source validity is checked
        # separately, without model calls or resuming the inspected session.
        workspace = self.host.prompt_composer.workspace_manager
        session = self.host.sources.conversations.get_session(session_id)
        scope = workspace.snapshot() if workspace else None
        same_workspace = not workspace or bool(
            session
            and tuple(scope["roots"]) == tuple(session.workspace_roots)
            and scope["current_root"] == session.current_workspace_root
        )
        for cycle in cycles:
            cycle["current_result_validity"] = "NOT_COMPLETED"
            cycle["current_experience_state"] = cycle["detail"].get("experience_state")
            if cycle["state"] == "COMPLETED":
                if same_workspace:
                    try:
                        self.host.experiences.source(cycle["source_run_id"])
                        cycle["current_result_validity"] = "VALID"
                    except ValueError:
                        cycle["current_result_validity"] = "STALE"
                else:
                    cycle["current_result_validity"] = "NOT_CHECKED_WORKSPACE"
                experience_id = cycle["detail"].get("experience_id")
                if experience_id:
                    experience = self.host.experiences.get(experience_id)
                    if same_workspace and experience["state"] == "ACTIVE":
                        try:
                            self.host.experiences.validate(experience)
                        except ValueError:
                            pass
                    cycle["current_experience_state"] = self.host.experiences.get(
                        experience_id
                    )["state"]
        return {"tasks": cycles, "jobs": self.jobs(session_id, task_id=task_id)}

    def recover_results(self, session_id: str) -> None:
        """Drain the local transactional outbox; never revive an older result."""
        with self.repository._connect() as db:
            rows = db.execute(
                "SELECT o.run_id,r.task_id FROM lifecycle_result_outbox o JOIN runs r USING(run_id) "
                "WHERE r.session_id=? AND o.state='PENDING' ORDER BY o.created DESC,o.rowid DESC LIMIT 100",
                (session_id,),
            ).fetchall()
        for row in rows:
            with self.repository._connect() as db:
                latest = db.execute(
                    "SELECT o.run_id FROM lifecycle_result_outbox o JOIN runs r USING(run_id) "
                    "WHERE r.task_id=? ORDER BY o.created DESC,o.rowid DESC LIMIT 1",
                    (row["task_id"],),
                ).fetchone()
                existing = db.execute(
                    "SELECT 1 FROM lifecycle_jobs WHERE job_id=?",
                    (identity(row["run_id"], "automatic", "task-review-v1"),),
                ).fetchone()
            saved = json.loads(self.repository.get_run(row["run_id"])["result_json"])
            unchanged = existing or (
                row["task_id"]
                and row["task_id"] != UNBOUND_TASK_ID
                and saved["trace_metadata"].get("repair_task_state_hash")
                == self.host._repair_task_state_hash(row["task_id"])
            )
            if latest[0] == row["run_id"] and unchanged:
                self.enqueue_result(row["run_id"])
            else:
                with self.repository._write() as db:
                    db.execute(
                        "UPDATE lifecycle_result_outbox SET state='SUPERSEDED' WHERE run_id=?",
                        (row["run_id"],),
                    )

    def notifications(self, session_id: str) -> list[dict]:
        with self.repository._connect() as db:
            rows = db.execute(
                "SELECT j.* FROM lifecycle_jobs j LEFT JOIN lifecycle_deliveries d ON d.job_id=j.job_id WHERE j.session_id=? AND j.state<>'PENDING' AND j.result_json IS NOT NULL AND d.job_id IS NULL ORDER BY j.created,j.job_id LIMIT 100",
                (session_id,),
            ).fetchall()
        return [
            {
                "job_id": job["job_id"],
                "kind": job["kind"],
                "task_id": job["task_id"],
                "state": job["state"],
                "result": job["result"],
            }
            for job in (self._job(row) for row in rows)
        ]

    def acknowledge(self, notification: dict) -> None:
        with self.repository._write() as db:
            row = db.execute(
                "SELECT result_hash FROM lifecycle_jobs WHERE job_id=? AND state<>'PENDING'",
                (notification["job_id"],),
            ).fetchone()
            if row is None or row[0] != digest(notification["result"]):
                raise CoordinationConflict("lifecycle notification changed")
            db.execute(
                "INSERT OR IGNORE INTO lifecycle_deliveries VALUES(?,?)",
                (notification["job_id"], row[0]),
            )

    def resolve_experience(self, experience: dict) -> None:
        """Resolve a human-reviewed learning question with an auditable new result."""
        if experience["state"] == "NEEDS_CONFIRMATION":
            return
        with self.repository._write() as db:
            rows = db.execute(
                "SELECT * FROM lifecycle_jobs WHERE kind='LEARN' AND state='WAITING_USER' AND source_run_id=?",
                (experience["payload"]["source_run_id"],),
            ).fetchall()
            for row in rows:
                job = self._job(row)
                if (job["result"] or {}).get("experience_id") != experience[
                    "experience_id"
                ]:
                    continue
                event = experience["events"][-1]["event_id"]
                resolution_id = identity(
                    job["job_id"], event, "experience-resolution-v1"
                )
                payload = {
                    **job["input"],
                    "resolution_of": job["job_id"],
                    "experience_event_id": event,
                }
                result = {
                    "completion_id": job["input"]["completion_id"],
                    "experience_id": experience["experience_id"],
                    "experience_state": experience["state"],
                    "question": None,
                    "reason": "Experience confirmation resolved",
                    "resolution_of": job["job_id"],
                }
                db.execute(
                    "INSERT OR IGNORE INTO lifecycle_jobs VALUES(?,?,?,?,?,'SUCCEEDED',?,?,?,?,NULL,?)",
                    (
                        resolution_id,
                        job["task_id"],
                        job["session_id"],
                        job["source_run_id"],
                        "LEARN",
                        canonical(payload),
                        digest(payload),
                        canonical(result),
                        digest(result),
                        time.time(),
                    ),
                )
                db.execute(
                    "UPDATE lifecycle_jobs SET state='SUCCEEDED' WHERE job_id=? AND state='WAITING_USER'",
                    (job["job_id"],),
                )
                # Keep the old question historical and prevent redelivery.
                db.execute(
                    "INSERT OR IGNORE INTO lifecycle_deliveries VALUES(?,?)",
                    (job["job_id"], job["result_hash"]),
                )
                current = db.execute(
                    "SELECT source_run_id FROM task_cycles WHERE task_id=?",
                    (job["task_id"],),
                ).fetchone()
                if current and current[0] == job["source_run_id"]:
                    self._cycle(
                        db,
                        job["task_id"],
                        job["session_id"],
                        job["source_run_id"],
                        "COMPLETED",
                        result,
                    )

    def enqueue_result(self, run_id: str, *, trigger: str = "automatic") -> str | None:
        run = self.repository.get_run(run_id)
        if (
            not run["result_json"]
            or not run["task_id"]
            or run["task_id"] == UNBOUND_TASK_ID
        ):
            return None
        saved = json.loads(run["result_json"])
        trace = saved.get("trace_metadata", {})
        if "task_frontier" not in trace:
            return None  # Explicit memory writes are not verified task graphs.
        job_id = identity(run_id, trigger, "task-review-v1")
        with self.repository._write() as db:
            if db.execute(
                "SELECT 1 FROM lifecycle_jobs WHERE job_id=?", (job_id,)
            ).fetchone():
                db.execute(
                    "UPDATE lifecycle_result_outbox SET state='ENQUEUED' WHERE run_id=?",
                    (run_id,),
                )
                return job_id
        ledger = self.tasks.get_ledger(run["task_id"])
        requirements = [
            {
                "entry_id": entry.entry_id,
                "kind": entry.kind.value,
                "content": entry.content,
            }
            for entry in ledger.active_entries
            if entry.kind
            in {
                EntryKind.GOAL,
                EntryKind.CONSTRAINT,
                EntryKind.OPEN_QUESTION,
                EntryKind.DEPENDENCY,
            }
        ]
        payload = {
            "task_id": run["task_id"],
            "session_id": run["session_id"],
            "source_run_id": run_id,
            "kind": "ASSESS",
            "source_result_hash": digest(saved),
            "ledger_hash": self.tasks.ledger_hash(ledger),
            "task_frontier": trace["task_frontier"],
            "required_entries": requirements,
            "trigger": trigger,
        }
        state = (
            "PENDING"
            if saved["status"] == "SUCCESS" and ledger.status is TaskStatus.ACTIVE
            else "FAILED"
        )
        cycle = "ASSESS_PENDING" if state == "PENDING" else "FAILED"
        if saved["status"] == "AWAITING_CONFIRMATION":
            cycle = "WAITING_USER"
            state = "WAITING_USER"
        error = saved.get("error_info") or {}
        if error.get("code") == "COLLABORATION_EVIDENCE_INSUFFICIENT":
            cycle, state = "WAITING_USER", "WAITING_USER"
        with self.repository._connect() as db:
            if db.execute(
                "SELECT 1 FROM repair_queue WHERE run_id=? AND status='PENDING'",
                (run_id,),
            ).fetchone():
                cycle = "REPAIR_PENDING"
        with self.repository._write() as db:
            # Recheck inside the write transaction: a duplicate enqueue must
            # not reset a completed/cancelled projection or supersede new work.
            if db.execute(
                "SELECT 1 FROM lifecycle_jobs WHERE job_id=?", (job_id,)
            ).fetchone():
                return job_id
            db.execute(
                "UPDATE lifecycle_jobs SET state='SUPERSEDED',error_code='NEWER_TASK_RESULT' WHERE task_id=? AND kind='ASSESS' AND state='PENDING' AND source_run_id<>?",
                (run["task_id"], run_id),
            )
            db.execute(
                "INSERT OR IGNORE INTO lifecycle_jobs VALUES(?,?,?,?,?,?,?,?,NULL,NULL,?,?)",
                (
                    job_id,
                    run["task_id"],
                    run["session_id"],
                    run_id,
                    "ASSESS",
                    state,
                    canonical(payload),
                    digest(payload),
                    None if state == "PENDING" else "SOURCE_NOT_SUCCESSFUL",
                    time.time(),
                ),
            )
            self._cycle(
                db,
                run["task_id"],
                run["session_id"],
                run_id,
                cycle,
                {
                    "question": (
                        saved.get("final_response") if cycle == "WAITING_USER" else None
                    ),
                    "reason": (
                        "Task completion requires whole-task review"
                        if state == "PENDING"
                        else "Execution did not produce an accepted successful result"
                    ),
                },
            )
            db.execute(
                "UPDATE lifecycle_result_outbox SET state='ENQUEUED' WHERE run_id=?",
                (run_id,),
            )
        return job_id

    def _finish(
        self, job: dict, state: str, result: dict, *, cycle_state: str | None = None
    ) -> None:
        with self.repository._write() as db:
            changed = db.execute(
                "UPDATE lifecycle_jobs SET state=?,result_json=?,result_hash=? WHERE job_id=? AND state='PENDING'",
                (state, canonical(result), digest(result), job["job_id"]),
            ).rowcount
            if not changed:
                return
            current = db.execute(
                "SELECT source_run_id FROM task_cycles WHERE task_id=?",
                (job["task_id"],),
            ).fetchone()
            if cycle_state and current and current[0] == job["source_run_id"]:
                self._cycle(
                    db,
                    job["task_id"],
                    job["session_id"],
                    job["source_run_id"],
                    cycle_state,
                    result,
                )

    def _enqueue_learning(self, db: Any, job: dict, seal: dict) -> None:
        payload = {
            "task_id": job["task_id"],
            "session_id": job["session_id"],
            "source_run_id": job["source_run_id"],
            "kind": "LEARN",
            "completion_id": seal["completion_id"],
            "completion_hash": digest(seal),
        }
        key = identity(seal["completion_id"], "learn-v1")
        db.execute(
            "INSERT OR IGNORE INTO lifecycle_jobs VALUES(?,?,?,?,?,'PENDING',?,?,NULL,NULL,NULL,?)",
            (
                key,
                job["task_id"],
                job["session_id"],
                job["source_run_id"],
                "LEARN",
                canonical(payload),
                digest(payload),
                time.time(),
            ),
        )

    def _accepted_sources(self, run_id: str) -> list[dict]:
        run = self.repository.get_run(run_id)
        candidates = {run_id}
        for experience in self.host.experiences.list():
            source_id = experience["payload"]["source_run_id"]
            if (
                experience["state"] in {"ACTIVE", "CANDIDATE", "NEEDS_CONFIRMATION"}
                and self.repository.get_run(source_id)["task_id"] == run["task_id"]
            ):
                candidates.add(source_id)
        result = []
        current = self.host._task_frontier(run["task_id"])
        for source_id in sorted(candidates):
            try:
                self.host.experiences.source(source_id)
                saved = json.loads(self.repository.get_run(source_id)["result_json"])
                frontier = saved["trace_metadata"]["task_frontier"]
                if digest(frontier) == digest(current):
                    result.append(
                        {
                            "run_id": source_id,
                            "result_hash": digest(saved),
                            "task_frontier_hash": digest(frontier),
                        }
                    )
            except ValueError:
                if source_id == run_id:
                    raise
        return result

    def _commit_completion(self, job: dict, assessment: dict) -> dict:
        sources = self._accepted_sources(job["source_run_id"])
        completion_id = identity(job["job_id"], "completion-v1")
        payload = {
            "schema_version": 1,
            "completion_id": completion_id,
            "task_id": job["task_id"],
            "source_run_id": job["source_run_id"],
            "job_id": job["job_id"],
            "ledger_before_hash": job["input"]["ledger_hash"],
            "authorized_sources": sources,
            "assessment": assessment,
        }
        with self.repository._write() as db:
            current = db.execute(
                "SELECT source_run_id,state FROM task_cycles WHERE task_id=?",
                (job["task_id"],),
            ).fetchone()
            pending = db.execute(
                "SELECT state FROM lifecycle_jobs WHERE job_id=?", (job["job_id"],)
            ).fetchone()
            if (
                current is None
                or current[0] != job["source_run_id"]
                or current[1] == "CANCELLED"
                or pending is None
                or pending[0] != "PENDING"
            ):
                raise CoordinationConflict(
                    "newer task result superseded completion review"
                )
            seal = self.tasks.seal_completion(
                job["task_id"], completion_id, job["input"]["ledger_hash"], payload
            )
            self._enqueue_learning(db, job, seal)
            detail = {
                "completion_id": completion_id,
                "experience_state": "PENDING",
                "question": None,
                "reason": assessment["reason"],
            }
            db.execute(
                "UPDATE lifecycle_jobs SET state='SUCCEEDED',result_json=?,result_hash=? WHERE job_id=? AND state='PENDING'",
                (canonical(detail), digest(detail), job["job_id"]),
            )
            self._cycle(
                db,
                job["task_id"],
                job["session_id"],
                job["source_run_id"],
                "COMPLETED",
                detail,
            )
        return detail

    def _recover_completion(self, job: dict, seal: dict) -> dict:
        if (
            seal["job_id"] != job["job_id"]
            or seal["source_run_id"] != job["source_run_id"]
        ):
            raise CoordinationConflict("completion belongs to a different task review")
        self.host.experiences.source(job["source_run_id"])
        detail = {
            "completion_id": seal["completion_id"],
            "experience_state": "PENDING",
            "question": None,
            "reason": seal["assessment"]["reason"],
        }
        model_run_id = identity(job["session_id"], job["job_id"])
        self.repository.finish(
            model_run_id,
            "SUCCEEDED",
            {"kind": "task_review", "decision": "COMPLETE", "job_id": job["job_id"]},
        )
        with self.repository._write() as db:
            self._enqueue_learning(db, job, seal)
        self._finish(job, "SUCCEEDED", detail, cycle_state="COMPLETED")
        return detail

    def _assess(self, job: dict) -> dict:
        seal = self.tasks.get_completion_receipt(job["task_id"])
        if seal:
            return self._recover_completion(job, seal)
        ledger = self.tasks.get_ledger(job["task_id"])
        if (
            ledger.status is not TaskStatus.ACTIVE
            or self.tasks.ledger_hash(ledger) != job["input"]["ledger_hash"]
        ):
            raise CoordinationConflict("task changed before lifecycle assessment")
        run = self.repository.get_run(job["source_run_id"])
        if digest(json.loads(run["result_json"])) != job["input"]["source_result_hash"]:
            raise CoordinationConflict("lifecycle source result changed")
        source = self.host.experiences.source(job["source_run_id"])
        saved = json.loads(run["result_json"])
        source_turn = saved["trace_metadata"].get("prepared_turn_id", run["turn_id"])
        message = self.host.sources.conversations.get_message_by_turn(
            run["session_id"], source_turn, ConversationRole.USER
        )
        if message is None:
            raise CoordinationConflict("task review has no original user request")
        inputs = {
            "required_entries": job["input"]["required_entries"],
            "source": source,
            "current_user_request": message.content,
            "user_message_id": message.message_id,
            "completion_requested_by_host": job["input"]["trigger"] != "automatic",
        }
        with self.host.client.turn(
            job["session_id"], job["job_id"], canonical(job["input"])
        ) as run_id:
            self.repository.bind_task(run_id, job["task_id"])
            proposal = self.host._stage(
                run_id,
                "task:review",
                "TaskReview",
                inputs,
                lambda: self.host.roles.run("TaskReview", inputs),
                TASK_REVIEW_SCHEMA,
            )
            validate(proposal, TASK_REVIEW_SCHEMA)
            assessments = {row["entry_id"]: row for row in proposal["assessments"]}
            required = {row["entry_id"] for row in inputs["required_entries"]}
            known = {row["artifact_id"] for row in source["artifacts"]}
            if (
                len(assessments) != len(proposal["assessments"])
                or assessments.keys() != required
                or any(
                    not set(row["artifact_ids"]) <= known
                    for row in assessments.values()
                )
            ):
                raise CoordinationConflict(
                    "task review omitted or invented requirement/evidence identity"
                )
            if proposal["decision"] == "COMPLETE":
                if (
                    not required
                    or proposal["remaining_requirements"]
                    or proposal["question"] is not None
                    or any(
                        row["status"] != "SATISFIED" or not row["artifact_ids"]
                        for row in assessments.values()
                    )
                ):
                    raise CoordinationConflict(
                        "task completion lacks full verified coverage"
                    )
            else:
                if (
                    not proposal["question"]
                    or not proposal["question"].strip()
                    or not proposal["remaining_requirements"]
                ):
                    raise CoordinationConflict(
                        "unfinished task needs a grounded question and remaining requirements"
                    )
            # Persist the accepted judgement before publishing its job result.
            # A crash here reuses the immutable stage, without another API call.
            self.repository.finish(
                run_id,
                "SUCCEEDED",
                {
                    "kind": "task_review",
                    "decision": proposal["decision"],
                    "job_id": job["job_id"],
                },
            )
            if proposal["decision"] == "COMPLETE":
                detail = self._commit_completion(job, proposal)
            else:
                detail = {
                    "question": proposal["question"],
                    "remaining_requirements": proposal["remaining_requirements"],
                    "reason": proposal["reason"],
                }
                self._finish(job, "WAITING_USER", detail, cycle_state="WAITING_USER")
            return detail

    def _learn(self, job: dict) -> dict:
        seal = self.tasks.get_completion_receipt(job["task_id"])
        if (
            seal is None
            or seal["completion_id"] != job["input"]["completion_id"]
            or digest(seal) != job["input"]["completion_hash"]
        ):
            raise CoordinationConflict("learning job completion seal changed")
        lesson = self.host.experiences.learn(job["source_run_id"])
        state = lesson.get("state", lesson.get("decision"))
        result = {
            "completion_id": seal["completion_id"],
            "experience_state": state,
            "experience_id": lesson.get("experience_id"),
            "question": None,
            "reason": "Verified task experience reviewed",
        }
        if state == "NEEDS_CONFIRMATION":
            result["question"] = lesson["events"][-1]["review"]["reason"]
        self._finish(
            job,
            "WAITING_USER" if state == "NEEDS_CONFIRMATION" else "SUCCEEDED",
            result,
            cycle_state="COMPLETED",
        )
        return result

    def drain(self, session_id: str, *, limit: int = 4) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("invalid lifecycle drain limit")
        outcomes = []
        self.recover_results(session_id)
        deferred_tasks = set()
        for _ in range(limit):
            pending = [
                job
                for job in self.jobs(session_id, pending=True, limit=100)
                if job["task_id"] not in deferred_tasks
            ]
            if not pending:
                break
            job = pending[0]
            try:
                with repair_lock(
                    self.repository.db_path, identity(job["task_id"], "task-lifecycle")
                ):
                    # Reload after acquiring the process lock.
                    fresh = next(
                        (
                            row
                            for row in self.jobs(session_id, pending=True)
                            if row["job_id"] == job["job_id"]
                        ),
                        None,
                    )
                    if fresh is None:
                        continue
                    with self.repository._connect() as db:
                        current = db.execute(
                            "SELECT source_run_id,state FROM task_cycles WHERE task_id=?",
                            (job["task_id"],),
                        ).fetchone()
                    if (
                        current is None
                        or current[0] != job["source_run_id"]
                        or current[1] == "CANCELLED"
                    ):
                        self._finish(
                            job,
                            "SUPERSEDED",
                            {
                                "reason": "A newer result or cancellation superseded this job"
                            },
                        )
                        continue
                    result = (
                        self._assess(job)
                        if job["kind"] == "ASSESS"
                        else self._learn(job)
                    )
                    outcomes.append(
                        {"job_id": job["job_id"], "kind": job["kind"], "result": result}
                    )
            except RepairBusy:
                deferred_tasks.add(job["task_id"])
            except Exception as exc:  # noqa: BLE001 - durable scheduler boundary
                # Task 3 may already have committed while the coordination
                # transaction rolled back. Its verified seal is the recovery
                # authority; retain the pending job for the next drain.
                try:
                    seal = self.tasks.get_completion_receipt(job["task_id"])
                except ValueError:
                    seal = None  # A corrupt seal is never a recovery authority.
                if (
                    job["kind"] == "ASSESS"
                    and seal
                    and seal["job_id"] == job["job_id"]
                    and seal["source_run_id"] == job["source_run_id"]
                ):
                    deferred_tasks.add(job["task_id"])
                    outcomes.append(
                        {
                            "job_id": job["job_id"],
                            "kind": job["kind"],
                            "error_code": "COMPLETION_RECOVERY_PENDING",
                        }
                    )
                    continue
                code = getattr(exc, "invariant_code", type(exc).__name__)
                detail = {
                    "reason": "Task automation stopped; source and budget must remain valid",
                    "error_code": code,
                    "question": None,
                }
                if job["kind"] == "LEARN":
                    detail.update(
                        {
                            "completion_id": job["input"]["completion_id"],
                            "experience_state": "FAILED",
                        }
                    )
                turn_id = (
                    job["job_id"]
                    if job["kind"] == "ASSESS"
                    else "learn:" + identity(job["source_run_id"], "", "experience-v1")
                )
                model_run_id = identity(job["session_id"], turn_id)
                with self.repository._connect() as db:
                    model_run = db.execute(
                        "SELECT status FROM runs WHERE run_id=?", (model_run_id,)
                    ).fetchone()
                if model_run and model_run[0] == "RUNNING":
                    self.repository.finish(
                        model_run_id,
                        "FAILED",
                        {
                            "kind": "task_automation_failure",
                            "job_id": job["job_id"],
                            "result": {"decision": "FAILED", "error_code": code},
                        },
                    )
                self._finish(
                    job,
                    "FAILED",
                    detail,
                    cycle_state="COMPLETED" if job["kind"] == "LEARN" else "FAILED",
                )
                outcomes.append(
                    {"job_id": job["job_id"], "kind": job["kind"], "error_code": code}
                )
        return outcomes

    def cancel(self, session_id: str, task_id: str) -> None:
        with self.repository._write() as db:
            current = db.execute(
                "SELECT source_run_id FROM task_cycles WHERE task_id=?", (task_id,)
            ).fetchone()
            if current:
                self._cycle(
                    db,
                    task_id,
                    session_id,
                    current[0],
                    "CANCELLED",
                    {"question": None, "reason": "Task cancelled by host"},
                )
            db.execute(
                "UPDATE lifecycle_jobs SET state='CANCELLED',error_code='TASK_CANCELLED' WHERE task_id=? AND state='PENDING'",
                (task_id,),
            )
