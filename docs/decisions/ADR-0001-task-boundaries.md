# ADR-0001：Task 1 / Task 2 / Task 3 ownership boundary

Status: **Accepted**

## Context / Problem

早期草稿重复定义 `MemoryItem/MemoryRecord/MemoryEntry/WorkingSetEntry`，混合 persistent memory、request-local retrieval state 和 task-local continuity state。

## Options considered

1. 三个任务共享一个大而全 schema；
2. 每个模块维护平行 memory schema；
3. canonical Task 1 model + read DTO + module-local runtime views。

## Decision / Why

采用选项 3。Task 1 拥有长期 domain/lifecycle；Task 2 只读并拥有 query-time state；Task 3 拥有 task ledger/checkpoint 和 derived context。该边界避免 score/priority 污染 persistent memory，也避免 schema 复制。

## Rejected alternatives

- 统一大 schema：生命周期、retrieval score、working priority 的更新频率和语义不同。
- 平行 schema：会产生 adapter 漂移和多个 source of truth。

## Reference evidence

Codex/Claw Code/OpenClaw 的 session/task/runtime state 与长期 memory 分层；Mem0/Letta/Graphiti 的 persistent memory representations 也不等于 query result state。借鉴等级：L1/L2。

## Consequences

Task 2 必须通过 `MemoryReadRepository`；Task 3 不写 Task 1 importance。模块间需要 DTO/view，但 canonical identity 只有一套。

## Implementation / Test mapping

- `code/src/memory/storage/repository.py:MemoryReadRepository`
- `code/src/memory/core/schema.py:MemorySnapshot`
- `code/tests/test_storage.py`
- `code/tests/test_sqlite_integrity.py:test_current_safe_read_matrix`

Introduced in phase: Phase 0C；Task 1 boundary implemented Phase 1B。

Supersedes: None。Superseded by: None。
