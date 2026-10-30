"""Artifact revocation must follow verified graph dependencies."""

import sqlite3
from dataclasses import asdict

import pytest
from src.memory.collaboration.budget import CollaborationLimits
from src.memory.collaboration.models import identity
from src.memory.collaboration.repository import (
    CoordinationConflict,
    CoordinationRepository,
)


def test_artifact_revocation_preserves_independent_branch_and_is_idempotent(tmp_path):
    repo = CoordinationRepository(str(tmp_path / "coord.db"))
    run = repo.begin("session", "turn", "goal", asdict(CollaborationLimits()))
    repo.bind_task(run, "task")

    def node(key, deps):
        return {
            "key": key,
            "objective": key,
            "dependencies": deps,
            "tool_names": [],
            "completion_criteria": "verified",
        }

    plan = {
        "schema_version": 1,
        "subtasks": [node("a", []), node("b", ["a"]), node("c", [])],
    }
    graph = repo.record_graph(run, "task", plan, {"constraints": "v1"})
    for key in ("a", "b", "c"):
        repo.record_attempt(run, graph, key, {"input": key})
        version = repo.get_run(run)["version"]
        repo.accept(
            run,
            "execute:" + key,
            "Executor",
            "Coordinator",
            {},
            {"status": "SUCCESS", "answer": key},
            version,
        )
        version = repo.get_run(run)["version"]
        repo.accept(
            run,
            "verify:" + key,
            "Verifier",
            "Coordinator",
            {},
            {"verdict": "PASS"},
            version,
        )
        repo.verify_artifact(run, "execute:" + key, "verify:" + key)
    artifact = identity(run, "execute:a", "artifact")
    event = "source-change-1"
    affected = repo.invalidate_artifact(run, artifact, event)
    assert set(affected) == {identity(graph, "a"), identity(graph, "b")}
    rows = {
        row["node_key"]: row
        for row in CoordinationRepository(repo.db_path).list_task_graphs("task")[0][
            "nodes"
        ]
    }
    assert {key: value["status"] for key, value in rows.items()} == {
        "a": "STALE",
        "b": "STALE",
        "c": "VERIFIED",
    }
    assert all(row["attempts"][0]["status"] == "VERIFIED" for row in rows.values())
    assert repo.invalidate_artifact(run, artifact, event) == affected
    candidate = repo.repair_candidate(run, event)
    assert {row["spec"]["key"] for row in candidate["rerun"]} == {"a", "b"}
    assert [row["node_key"] for row in candidate["reuse_candidates"]] == ["c"]
    assert candidate["reuse_candidates"][0]["reuse_authorized"] is False
    with pytest.raises(CoordinationConflict, match="identity changed"):
        repo.invalidate_artifact(run, identity(run, "execute:c", "artifact"), event)
    with pytest.raises(CoordinationConflict, match="invalid nodes"):
        repo.validate_completed_graph(run)
    with pytest.raises(CoordinationConflict, match="stale"):
        repo.verify_artifact(run, "execute:a", "verify:a")
    with sqlite3.connect(repo.db_path) as db:
        db.execute(
            "UPDATE handoffs SET payload_json='{}' WHERE run_id=? AND stage_key='execute:c'",
            (run,),
        )
    with pytest.raises(CoordinationConflict, match="integrity"):
        repo.repair_candidate(run, event)
