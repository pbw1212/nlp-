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
    {"schema_version": VERSION, "selected_memory_ids": IDS, "missing_information": IDS}
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
