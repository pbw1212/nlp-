"""Versioned model proposals; no proposal is permission or a durable result."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator


def canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def identity(*parts: str) -> str:
    return "ma_" + digest(list(parts))[:24]


def object_schema(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


TEXT = {"type": "string", "minLength": 1, "maxLength": 4000}
IDS = {
    "type": "array",
    "items": {"type": "string", "minLength": 1, "maxLength": 200},
    "maxItems": 32,
    "uniqueItems": True,
}
VERSION = {"type": "integer", "const": 1}
SUBTASK_SCHEMA = object_schema(
    {
        "key": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,40}$"},
        "objective": TEXT,
        "dependencies": IDS,
        "tool_names": IDS,
        "completion_criteria": TEXT,
    }
)
PLAN_SCHEMA = object_schema(
    {
        "schema_version": VERSION,
        "subtasks": {
            "type": "array",
            "minItems": 1,
            "maxItems": 3,
            "items": SUBTASK_SCHEMA,
        },
    }
)
MEMORY_SCHEMA = object_schema(
    {
        "schema_version": {"type": "integer", "const": 2},
        "selected_memory_ids": IDS,
        "missing_information": IDS,
        "evidence_needs": {
            "type": "array",
            "maxItems": 8,
            "items": object_schema(
                {
                    "need_id": {"type": "string", "minLength": 1, "maxLength": 80},
                    "requirement": TEXT,
                    "requires_original": {"type": "boolean"},
                }
            ),
        },
        "source_requests": {
            "type": "array",
            "maxItems": 4,
            "items": object_schema({"source_ref": TEXT, "need_ids": IDS}),
        },
    }
)
EVIDENCE_CHECK_SCHEMA = object_schema(
    {
        "schema_version": VERSION,
        "assessments": {
            "type": "array",
            "maxItems": 8,
            "items": object_schema(
                {
                    "need_id": {"type": "string", "minLength": 1, "maxLength": 80},
                    "status": {
                        "type": "string",
                        "enum": ["SUPPORTED", "MISSING", "UNCERTAIN"],
                    },
                    "evidence_ids": IDS,
                    "reason": TEXT,
                }
            ),
        },
    }
)
SEARCH_SCHEMA = object_schema(
    {
        "schema_version": VERSION,
        "queries": {
            "type": "array",
            "maxItems": 2,
            "items": object_schema({"query": TEXT, "need_ids": IDS}),
        },
        "source_windows": {
            "type": "array",
            "maxItems": 2,
            "items": object_schema(
                {
                    "source_ref": TEXT,
                    "offset": {"type": "integer", "minimum": 0, "maximum": 10000000},
                    "length": {"type": "integer", "minimum": 1, "maximum": 4000},
                    "need_ids": IDS,
                }
            ),
        },
        "reason": TEXT,
    }
)
EXPERIENCE_SCHEMA = object_schema(
    {
        "schema_version": VERSION,
        "decision": {"type": "string", "enum": ["PROPOSE", "ABSTAIN"]},
        "task_type": TEXT,
        "preconditions": IDS,
        "action_summary": TEXT,
        "observed_outcome": TEXT,
        "artifact_ids": IDS,
        "applicability_scope": TEXT,
        "reason": TEXT,
    }
)
EXPERIENCE_REVIEW_SCHEMA = object_schema(
    {
        "schema_version": VERSION,
        "verdict": {
            "type": "string",
            "enum": ["ADMIT", "QUARANTINE", "NEEDS_CONFIRMATION"],
        },
        "artifact_ids": IDS,
        "reason": TEXT,
    }
)
EXPERIENCE_SELECT_SCHEMA = object_schema(
    {
        "schema_version": VERSION,
        "assessments": {
            "type": "array",
            "maxItems": 16,
            "items": object_schema(
                {
                    "experience_id": TEXT,
                    "verdict": {
                        "type": "string",
                        "enum": ["APPLICABLE", "INAPPLICABLE", "UNCERTAIN"],
                    },
                    "reason": TEXT,
                }
            ),
        },
    }
)
VERIFY_SCHEMA = object_schema(
    {
        "schema_version": VERSION,
        "verdict": {"type": "string", "enum": ["PASS", "BLOCKED"]},
        "evidence_ids": IDS,
        "answer": TEXT,
        "reason": TEXT,
    }
)
TASK_REVIEW_SCHEMA = object_schema(
    {
        "schema_version": VERSION,
        "decision": {"type": "string", "enum": ["COMPLETE", "WAITING_USER"]},
        "assessments": {
            "type": "array",
            "maxItems": 64,
            "items": object_schema(
                {
                    "entry_id": TEXT,
                    "status": {
                        "type": "string",
                        "enum": ["SATISFIED", "UNSATISFIED", "UNCERTAIN"],
                    },
                    "artifact_ids": IDS,
                    "reason": TEXT,
                }
            ),
        },
        "remaining_requirements": IDS,
        "question": {"type": ["string", "null"], "maxLength": 4000},
        "reason": TEXT,
    }
)


def validate(proposal: Any, schema: dict[str, Any]) -> dict[str, Any]:
    Draft202012Validator(schema).validate(proposal)
    return proposal


@dataclass(frozen=True)
class RoleContext:
    run_id: str
    role_task_id: str
    role: str
    session_id: str
    task_id: str
    turn_id: str


@dataclass(frozen=True)
class Subtask:
    key: str
    objective: str
    dependencies: tuple[str, ...]
    tool_names: tuple[str, ...]
    completion_criteria: str


def parse_plan(raw: dict[str, Any], available_tools: set[str]) -> tuple[Subtask, ...]:
    validate(raw, PLAN_SCHEMA)
    rows = {item["key"]: item for item in raw["subtasks"]}
    if len(rows) != len(raw["subtasks"]):
        raise ValueError("duplicate subtask identity")
    for key, item in rows.items():
        if key in item["dependencies"] or not set(item["dependencies"]) <= rows.keys():
            raise ValueError("invalid task dependency")
        if not set(item["tool_names"]) <= available_tools:
            raise ValueError("plan requires unavailable or unauthorized tools")
    ordered: list[Subtask] = []
    done: set[str] = set()
    while len(done) < len(rows):
        ready = [
            item
            for key, item in rows.items()
            if key not in done and set(item["dependencies"]) <= done
        ]
        if not ready:
            raise ValueError("cyclic task dependencies")
        for item in ready:
            ordered.append(
                Subtask(
                    item["key"],
                    item["objective"],
                    tuple(item["dependencies"]),
                    tuple(item["tool_names"]),
                    item["completion_criteria"],
                )
            )
            done.add(item["key"])
    return tuple(ordered)
