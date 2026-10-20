# ADR-0002：Stable MemoryItem + semantic MemoryRevision

Status: **Accepted**

## Context / Problem

单行 mutable memory 会让 UPDATE/CORRECT 覆盖历史，无法解释“以前成立”与“原推断错误”的区别；旧 `status` 又混合 semantic truth 和 retention。

## Options considered

1. 原地覆盖 content；2. 每次变化创建新 identity；3. stable identity 关联 immutable semantic revisions。

## Decision / Why

采用选项 3。`MemoryItem` 保存稳定 identity/operational state；`MemoryRevision` 保存 content、confidence、semantic metadata、valid/event time 和 controlled validity transition。`SemanticValidity` 与 `RetentionDisposition` 正交。

UPDATE 将旧 revision 标为 SUPERSEDED；CORRECT 标为 CONTRADICTED；restore 不复活旧 semantic truth。

## Rejected alternatives

- 原地覆盖：破坏历史与 provenance。
- 新 identity：失去同一长期事实的连续性。
- 用 graph edge 代替 revision state：无法可靠执行 current-safe read invariant。

## Reference evidence

Mem0 stable memory ID/history、Graphiti temporal facts 为 L1/L2 动机；本项目没有复制其代码。

## Consequences

semantic payload 不提供普通 update API；状态只能合法迁移。cross-identity destructive merge deferred。

## Implementation / Test mapping

- `code/src/memory/core/schema.py:MemoryItem,MemoryRevision,SemanticValidity,RetentionDisposition`
- `code/src/memory/storage/sqlite_manager.py:apply_revision`
- `code/tests/test_revisions.py`
- `code/tests/test_lifecycle.py and code/tests/test_revisions.py:scenario_a,scenario_b,scenario_c,scenario_d`

Introduced in phase: Phase 1B。Supersedes: mutable Phase 1A row semantics。Superseded by: None。
