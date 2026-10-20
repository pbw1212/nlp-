# Phase 3A — Durable Task State Foundation

## Result

Phase 3A implements the first deterministic test Task3 vertical slice. It does not modify
Task1/Task2 frozen contracts, call a real LLM, implement a natural-language
state updater, perform LLM compaction, promote state to Task1, or generate Main
Agent answers.

## Layering

```text
TaskLedger                 durable authoritative task state
        ↓ references
ContinuationCheckpoint     durable compact continuation pointer
        ↓ rebuilds
ActiveWorkingContext       bounded derived request-local view
        ↓
StructuredTaskContext      typed model-facing sections
```

`TaskLedger` is made of typed entries (`GOAL`, `CONSTRAINT`, `DECISION`,
`PROGRESS`, `OPEN_QUESTION`, `DEPENDENCY`, `BACKGROUND`) with explicit lifecycle
states (`ACTIVE`, `RESOLVED`, `SUPERSEDED`, `REMOVED`). Priority is a small
task-local ordinal. Evidence is referenced through bundle/memory/revision IDs,
not copied into the ledger. Relations are task-local and intentionally small.

## Durable storage

`TaskStateRepository` owns a separate versioned SQLite store (`user_version=1`)
with task ledger, entries, relations, checkpoints and operation-id tables. It
does not reuse or alter Task1 canonical tables. Entry status updates are
deterministic; superseding an entry and creating its replacement is one
transaction. Repeated operation IDs return the original result rather than
creating duplicates. Unsupported schema versions are refused.

## Checkpoint and context

`ContinuationCheckpoint` stores only restart pointers: last event, current step,
continuation note, critical entry IDs, open question/decision IDs and Task2
EvidenceRefs. It is not a ledger copy. `TaskContextBuilder` reconstructs a
bounded context from durable state after reopening the database. It prioritizes
checkpoint references, critical/high entries, active goals, constraints,
decisions and open questions before low-priority background. Omitted/truncated
entries produce warnings. EvidenceBundle items are optional and enter through
the Task2 boundary only.

`StructuredTaskContext` exposes typed sections for goal, constraints, decisions,
progress, open questions, dependencies, background, retrieved evidence,
continuation, current turn and external observations. It has deterministic JSON
serialization and is never persisted as canonical truth.

## Acceptance

`code/tests/test_task_state.py` and `code/scripts/task3_acceptance.py` cover lifecycle,
history, idempotency, atomic supersede, task-local relations, checkpoint
persistence/restart recovery, bounded priority selection, evidence integration,
deterministic serialization and the Task1 table boundary.

## Deferred

Natural-language `TaskStateUpdater`, LLM compaction, automatic Task1 promotion,
transcript storage, world-state synchronization and Main Agent integration are
Phase 3B or later work.
