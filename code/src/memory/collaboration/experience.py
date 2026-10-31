"""Auditable procedural lessons, separate from canonical user-fact memory."""

from __future__ import annotations

import json
import time
from typing import Any

from src.memory.collaboration.models import canonical, digest, identity
from src.memory.collaboration.repository import CoordinationConflict
from src.memory.conversation.models import ConversationRole
from src.memory.core.schema import payload_hash


class ExperienceService:
    def __init__(self, host: Any) -> None:
        self.host = host
        self.repository = host.repository
        self._validating: set[str] = set()

    def get(self, experience_id: str) -> dict[str, Any]:
        with self.repository._connect() as db:
            db.execute("BEGIN")
            row = db.execute(
                "SELECT * FROM experiences WHERE experience_id=?", (experience_id,)
            ).fetchone()
            if row is None:
                raise CoordinationConflict("unknown experience")
            payload = json.loads(row["payload_json"])
            if digest(payload) != row["payload_hash"]:
                raise CoordinationConflict("experience integrity failure")
            state = "CANDIDATE"
            events = []
            for event in db.execute(
                "SELECT * FROM experience_events WHERE experience_id=? ORDER BY rowid",
                (experience_id,),
            ):
                detail = json.loads(event["payload_json"])
                if digest(detail) != event["payload_hash"]:
                    raise CoordinationConflict("experience event integrity failure")
                state = (
                    "STALE"
                    if event["kind"] == "SOURCE_STALE"
                    else detail.get("state", state)
                )
                events.append(
                    {"event_id": event["event_id"], "kind": event["kind"], **detail}
                )
            if (
                state != row["state"]
                or payload["source_run_id"] != row["source_run_id"]
                or payload.get("revision_of") != row["revision_of"]
            ):
                raise CoordinationConflict("experience projection integrity failure")
            return {
                "experience_id": experience_id,
                "revision_id": row["payload_hash"],
                "state": state,
                "payload": payload,
                "events": events,
            }

    def transition(
        self, experience_id: str, event_id: str, kind: str, payload: dict
    ) -> dict:
        if not isinstance(event_id, str) or not event_id or len(event_id) > 200:
            raise ValueError("invalid experience event identity")
        self.get(experience_id)
        with self.repository._write() as db:
            prior = db.execute(
                "SELECT * FROM experience_events WHERE event_id=?", (event_id,)
            ).fetchone()
            if prior:
                if (
                    prior["experience_id"] != experience_id
                    or prior["kind"] != kind
                    or prior["payload_hash"] != digest(payload)
                ):
                    raise CoordinationConflict("experience event identity changed")
            else:
                current = db.execute(
                    "SELECT state FROM experiences WHERE experience_id=?",
                    (experience_id,),
                ).fetchone()[0]
                target = (
                    "STALE" if kind == "SOURCE_STALE" else payload.get("state", current)
                )
                if current in {"REVOKED", "SUPERSEDED", "STALE"} and target not in {
                    current,
                    "REVOKED",
                }:
                    raise CoordinationConflict(
                        "terminal experience cannot be reactivated; create a revision"
                    )
                if target == "ACTIVE" and current not in {
                    "CANDIDATE",
                    "NEEDS_CONFIRMATION",
                    "ACTIVE",
                }:
                    raise CoordinationConflict(
                        "only a reviewed candidate may become active"
                    )
                if target == "ACTIVE":
                    invalid = db.execute(
                        "SELECT 1 FROM experiences e JOIN task_graphs g ON g.run_id=e.source_run_id JOIN task_nodes n ON n.graph_id=g.graph_id WHERE e.experience_id=? AND n.status<>'VERIFIED' LIMIT 1",
                        (experience_id,),
                    ).fetchone()
                    if invalid:
                        raise CoordinationConflict(
                            "experience source invalidated during admission"
                        )
                db.execute(
                    "INSERT INTO experience_events VALUES(?,?,?,?,?,?)",
                    (
                        event_id,
                        experience_id,
                        kind,
                        canonical(payload),
                        digest(payload),
                        time.time(),
                    ),
                )
                db.execute(
                    "UPDATE experiences SET state=? WHERE experience_id=?",
                    (target, experience_id),
                )
        return self.get(experience_id)

    def source(self, run_id: str) -> dict:
        host = self.host
        run = self.repository.get_run(run_id)
        if run["status"] != "SUCCEEDED" or not run["result_json"]:
            raise CoordinationConflict("experience source must be a successful run")
        saved = json.loads(run["result_json"])
        trace = saved.get("trace_metadata", {})
        if (
            trace.get("observation_frontier_version") != 1
            or "task_frontier" not in trace
            or "conversation_frontier" not in trace
        ):
            raise CoordinationConflict(
                "legacy experience source lacks verified provenance"
            )
        host._validate_refs(trace.get("memory_inputs", []))
        host._validate_source_reads(
            trace.get("source_reads", []),
            trace.get("memory_inputs", []),
            run["task_id"],
        )
        host._validate_task_frontier(
            run["task_id"], trace["task_frontier"], sealed_source_run_id=run_id
        )
        if "repair" in trace:
            self.repository.get_repair_plan(trace["repair"]["repair_id"])
        host._validate_conversation_frontier(trace["conversation_frontier"])
        user_turn = trace.get("prepared_turn_id", run["turn_id"])
        user_message = host.sources.conversations.get_message_by_turn(
            run["session_id"], user_turn, ConversationRole.USER
        )
        if user_message is None:
            raise CoordinationConflict("experience source has no original user request")
        host._validate_experience_refs(trace.get("experience_inputs", []))
        self.repository.validate_completed_graph(run_id)
        host._validate_replay_observations(run_id, run["task_id"])
        graph = next(
            g
            for g in self.repository.list_task_graphs(run["task_id"])
            if g["run_id"] == run_id
        )
        artifacts = []
        with self.repository._connect() as db:
            for node in graph["nodes"]:
                attempt = next(a for a in node["attempts"] if a["status"] == "VERIFIED")
                handoff = db.execute(
                    "SELECT * FROM handoffs WHERE run_id=? AND stage_key=?",
                    (run_id, "verify:" + node["node_key"]),
                ).fetchone()
                verdict = json.loads(handoff["payload_json"]) if handoff else None
                if (
                    verdict is None
                    or digest(verdict) != handoff["payload_hash"]
                    or verdict.get("verdict") != "PASS"
                ):
                    raise CoordinationConflict(
                        "experience verification integrity failure"
                    )
                observations = self.repository.get_attempt_observations(
                    run_id, attempt["attempt_id"]
                )
                receipts = []
                for item in observations:
                    receipt = host.journal.get_receipt(item["tool_call_id"])
                    result = json.loads(receipt["result_json"])
                    text = result.get("result_content", "")
                    receipts.append(
                        {
                            "tool_call_id": receipt["tool_call_id"],
                            "tool_name": receipt["tool_name"],
                            "arguments": json.loads(receipt["arguments_json"]),
                            "status": receipt["status"],
                            "result_content": text[:4000],
                            "truncated": len(text) > 4000,
                        }
                    )
                artifacts.append(
                    {
                        "artifact_id": attempt["artifact_id"],
                        "verification": verdict,
                        "receipts": receipts,
                    }
                )
        return {
            "source_run_id": run_id,
            "task_id": run["task_id"],
            "user_request": {
                "session_id": run["session_id"],
                "message_id": user_message.message_id,
                "role": "USER",
                "content": user_message.content,
                "content_hash": payload_hash(user_message.content),
            },
            "task_completion": (
                host.runtime.task_service.repository.get_completion_receipt(
                    run["task_id"]
                )
                if run["task_id"] != "__unbound__"
                else None
            ),
            "plan": json.loads(graph["plan_json"]),
            "artifacts": artifacts,
            "outcome": saved["final_response"],
            "frontier_hash": digest(
                {
                    key: trace.get(key)
                    for key in (
                        "task_frontier",
                        "conversation_frontier",
                        "memory_inputs",
                        "source_reads",
                        "experience_inputs",
                    )
                }
            ),
        }

    def learn(self, run_id: str, *, revision_of: str | None = None) -> dict:
        run = self.repository.get_run(run_id)
        if revision_of:
            self.get(revision_of)
        experience_id = identity(run_id, revision_of or "", "experience-v1")
        with self.host.client.turn(
            run["session_id"],
            "learn:" + experience_id,
            canonical({"source_run_id": run_id, "revision_of": revision_of}),
        ) as learning_run:
            source = self.source(run_id)
            previous = self.repository.get_run(learning_run)
            if previous["result_json"]:
                saved = json.loads(previous["result_json"])
                return (
                    self.get(saved["experience_id"])
                    if saved.get("experience_id")
                    else saved["result"]
                )
            self.repository.bind_task(learning_run, run["task_id"])
            proposal = self.host._stage(
                learning_run,
                "experience:propose",
                "Experience",
                source,
                lambda: self.host.roles.run("Experience", source),
            )
            if proposal["decision"] == "ABSTAIN":
                result = {"decision": "ABSTAIN", "reason": proposal["reason"]}
                self.repository.finish(
                    learning_run,
                    "SUCCEEDED",
                    {"kind": "experience_learning", "result": result},
                )
                return result
            allowed = {row["artifact_id"] for row in source["artifacts"]}
            if (
                not proposal["preconditions"]
                or not proposal["artifact_ids"]
                or not set(proposal["artifact_ids"]) <= allowed
            ):
                raise CoordinationConflict(
                    "experience proposal lacks grounded conditions or sources"
                )
            payload = {
                **proposal,
                "source_run_id": run_id,
                "source_frontier_hash": source["frontier_hash"],
                "environment_hash": self.host._execution_contract_hash(),
                "revision_of": revision_of,
            }
            with self.repository._write() as db:
                previous = db.execute(
                    "SELECT payload_hash FROM experiences WHERE experience_id=?",
                    (experience_id,),
                ).fetchone()
                if previous and previous[0] != digest(payload):
                    raise CoordinationConflict("experience proposal changed")
                db.execute(
                    "INSERT OR IGNORE INTO experiences VALUES(?,?,?,?,?,?,?)",
                    (
                        experience_id,
                        run_id,
                        canonical(payload),
                        digest(payload),
                        "CANDIDATE",
                        revision_of,
                        time.time(),
                    ),
                )
            result = self._review(experience_id, learning_run, source)
            self.repository.finish(
                learning_run,
                "SUCCEEDED",
                {"kind": "experience_learning", "experience_id": experience_id},
            )
            return result

    def _review(
        self,
        experience_id: str,
        run_id: str,
        source: dict,
        confirmation: str | None = None,
    ) -> dict:
        experience = self.get(experience_id)
        inputs = {
            "candidate": experience["payload"],
            "source": source,
            "user_confirmation": confirmation,
        }
        review = self.host._stage(
            run_id,
            "experience:review",
            "ExperienceReview",
            inputs,
            lambda: self.host.roles.run("ExperienceReview", inputs),
        )
        state = {
            "ADMIT": "ACTIVE",
            "QUARANTINE": "QUARANTINED",
            "NEEDS_CONFIRMATION": "NEEDS_CONFIRMATION",
        }[review["verdict"]]
        if review["verdict"] == "ADMIT" and (
            not set(experience["payload"]["artifact_ids"])
            <= set(review["artifact_ids"])
            or not set(review["artifact_ids"])
            <= {row["artifact_id"] for row in source["artifacts"]}
        ):
            raise CoordinationConflict("experience review invented or omitted support")
        current_source = self.source(experience["payload"]["source_run_id"])
        if digest(current_source) != digest(source):
            raise CoordinationConflict("experience source changed during review")
        result = self.transition(
            experience_id,
            identity(run_id, "experience-review"),
            "REVIEW",
            {"state": state, "review": review},
        )
        if result["state"] == "ACTIVE" and experience["payload"]["revision_of"]:
            parent = self.get(experience["payload"]["revision_of"])
            if parent["state"] not in {"STALE", "REVOKED", "SUPERSEDED"}:
                self.transition(
                    parent["experience_id"],
                    identity(experience_id, "supersedes"),
                    "SUPERSEDED",
                    {"state": "SUPERSEDED", "replacement": experience_id},
                )
        return result

    def review(self, experience_id: str, confirmation: str) -> dict:
        experience = self.get(experience_id)
        if (
            not isinstance(confirmation, str)
            or not confirmation.strip()
            or len(confirmation) > 4000
        ):
            raise CoordinationConflict("review requires a bounded confirmation")
        source_run = self.repository.get_run(experience["payload"]["source_run_id"])
        review_turn = identity(experience_id, digest(confirmation))
        try:
            previous = self.repository.get_run(
                identity(source_run["session_id"], review_turn)
            )
        except CoordinationConflict:
            previous = None
        if previous and previous["result_json"]:
            return experience
        if (
            experience["state"] != "NEEDS_CONFIRMATION"
            or not confirmation.strip()
            or len(confirmation) > 4000
        ):
            raise CoordinationConflict(
                "review requires a pending candidate and bounded confirmation"
            )
        with self.host.client.turn(
            source_run["session_id"], review_turn, confirmation
        ) as run:
            self.repository.bind_task(run, source_run["task_id"])
            result = self._review(
                experience_id, run, self.source(source_run["run_id"]), confirmation
            )
            self.repository.finish(
                run,
                "SUCCEEDED",
                {"kind": "experience_review", "experience_id": experience_id},
            )
            return result

    def list(self, *, limit: int = 100) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid experience limit")
        with self.repository._connect() as db:
            ids = [
                row[0]
                for row in db.execute(
                    "SELECT experience_id FROM experiences ORDER BY rowid DESC LIMIT ?",
                    (limit,),
                )
            ]
        return [self.get(key) for key in ids]

    def validate(self, ref: dict) -> dict:
        experience = self.get(ref["experience_id"])
        if (
            experience["state"] != "ACTIVE"
            or experience["revision_id"] != ref["revision_id"]
        ):
            raise CoordinationConflict("experience is inactive or changed")
        if (
            experience["payload"]["environment_hash"]
            != self.host._execution_contract_hash()
        ):
            raise CoordinationConflict("experience environment is not compatible")
        if (
            experience["experience_id"] in self._validating
            or len(self._validating) >= 64
        ):
            raise CoordinationConflict("experience provenance cycle or depth limit")
        self._validating.add(experience["experience_id"])
        try:
            source = self.source(experience["payload"]["source_run_id"])
            if source["frontier_hash"] != experience["payload"]["source_frontier_hash"]:
                raise CoordinationConflict("experience source frontier changed")
        except ValueError:
            self.transition(
                experience["experience_id"],
                identity(experience["experience_id"], "source-stale"),
                "SOURCE_STALE",
                {"source_event": "CURRENT_VALIDATION"},
            )
            raise
        finally:
            self._validating.discard(experience["experience_id"])
        return experience

    def select(self, run_id: str, inputs: dict) -> list[dict]:
        candidates = []
        for item in self.list():
            if (
                item["state"] != "ACTIVE"
                or item["payload"]["environment_hash"]
                != self.host._execution_contract_hash()
            ):
                continue
            try:
                self.validate(item)
            except ValueError:
                continue
            candidates.append(item)
            if len(candidates) >= 16:
                break
        if not candidates:
            return []
        selection_inputs = {
            **inputs,
            "experiences": [
                {key: item[key] for key in ("experience_id", "revision_id", "payload")}
                for item in candidates
            ],
        }
        proposal = self.host._stage(
            run_id,
            "experience:select",
            "ExperienceSelect",
            selection_inputs,
            lambda: self.host.roles.run("ExperienceSelect", selection_inputs),
        )
        assessments = {row["experience_id"]: row for row in proposal["assessments"]}
        if len(assessments) != len(proposal["assessments"]) or assessments.keys() != {
            row["experience_id"] for row in candidates
        }:
            raise CoordinationConflict(
                "experience selection omitted or invented identity"
            )
        return [
            row
            for row in candidates
            if assessments[row["experience_id"]]["verdict"] == "APPLICABLE"
        ]

    def revoke(self, experience_id: str, event_id: str) -> dict:
        if not event_id or len(event_id) > 200:
            raise ValueError("invalid experience event")
        return self.transition(experience_id, event_id, "REVOKED", {"state": "REVOKED"})

    def feedback(self, experience_id: str, run_id: str, event_id: str) -> dict:
        experience = self.get(experience_id)
        existing = next(
            (row for row in experience["events"] if row["event_id"] == event_id), None
        )
        if existing:
            if existing["kind"] != "FEEDBACK" or existing["consumer_run_id"] != run_id:
                raise CoordinationConflict("feedback event identity changed")
            return experience
        run = self.repository.get_run(run_id)
        if not run["result_json"] or run["status"] not in {"SUCCEEDED", "FAILED"}:
            raise CoordinationConflict("feedback requires a terminal consumer run")
        trace = json.loads(run["result_json"])["trace_metadata"]
        if not any(
            row["experience_id"] == experience_id
            and row["revision_id"] == experience["revision_id"]
            for row in trace.get("experience_inputs", [])
        ):
            raise CoordinationConflict("feedback run did not consume this experience")
        if run["status"] == "SUCCEEDED":
            self.source(run_id)
        state = experience["state"]
        if run["status"] == "FAILED" and state == "ACTIVE":
            state = "QUARANTINED"
        return self.transition(
            experience_id,
            event_id,
            "FEEDBACK",
            {
                "state": state,
                "consumer_run_id": run_id,
                "outcome": run["status"],
                "causal_contribution": "UNKNOWN",
            },
        )
