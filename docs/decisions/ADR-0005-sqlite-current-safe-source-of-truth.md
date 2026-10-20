# ADR-0005：SQLite source of truth 与 current-safe read

Status: **Accepted**

## Context / Problem

Task 1 需要可重复测试运行、明确 migration 和数据库完整性；derived lexical/vector indices 不应成为 canonical truth。Temporal storage 本身也不保证默认只返回当前有效事实。

## Options considered

1. in-memory/vector store 为主；2. heavyweight vector/graph DB；3. versioned SQLite canonical repository + rebuildable indices。

## Decision / Why

采用选项 3。Schema 使用 `PRAGMA user_version`，legacy migration 必须显式。默认 read 为 `ACTIVE + CURRENT`，历史显式获取。FK、unique index 与 trigger 执行 revision/current pointer invariants。

## Rejected alternatives

- index 为 truth：重建、版本和 provenance 语义不可靠。
- heavyweight DB：超出 baseline 验证所需复杂度。
- 普通启动自动迁移：可能破坏用户历史数据库。

## Reference evidence

多种 memory system 将 index/persistence 分层；Graphiti 说明 temporal fields 不自动等于 current-safe query。借鉴等级 L1/L2。

## Consequences

Task 2 只能消费 read protocol；字符 2-gram store明确为 lexical placeholder。未知 future schema 显式拒绝。

## Implementation / Test mapping

- `code/src/memory/storage/sqlite_manager.py:SQLiteMemoryManager`
- `code/src/memory/storage/repository.py:MemoryReadRepository`
- `code/tests/test_migration.py`
- `code/tests/test_sqlite_integrity.py`

Introduced in phase: Phase 1B；integrity hardened Phase 1C。Supersedes: unversioned Phase 1A SQLite schema。Superseded by: None。
