# Task 1：长期记忆与生命周期（当前实现）

> 当前同步日期：2026-09-05。长期记忆默认存于全局
> `~/.agent_data/long_term_memory/memory.db`；当前主线全量回归为 `386 passed`。

## Canonical model

```text
MemoryItem                         stable identity + operational state
  memory_id
  type / granularity
  current_revision_id
  importance/access/decay
  RetentionDisposition
        ↓ 1:N
MemoryRevision                     immutable semantic payload, controlled validity transition
  revision_id / revision_no
  content / confidence / SemanticMetadata
  created_at / event_time / valid_from / valid_until
  SemanticValidity
  supersedes_revision_id
        ↓ N:M
ProvenanceRef
  source_event_id / session_id / message_id
  authority / content_hash / bounded excerpt
```

`MemoryItem.content/status/metadata` 仅是 deprecated Phase 1A compatibility projection。canonical lifecycle 和未来 Task 2 read boundary 不依赖它们。

## 正交状态

- `SemanticValidity`：`CURRENT / SUPERSEDED / CONTRADICTED / EXPIRED`，属于 revision。
- `RetentionDisposition`：`ACTIVE / ARCHIVED / RECYCLED`，属于 identity。

Restore 只改变 retention。重新确认历史偏好必须创建新 revision，不能复活旧 revision。

## Lifecycle

- ADD：创建 identity + revision #1 CURRENT。
- UPDATE：旧 current → SUPERSEDED；新 revision → CURRENT。
- CORRECT：旧 current → CONTRADICTED；新 revision → CURRENT。
- NOOP：不创建 semantic row；ingestion event 仍标记 APPLIED 并 scrub raw text。
- access/promote/demote/decay：只改变 operational state。时间遗忘开关默认关闭；
  生产默认不会因为时间或未访问自动降权，批量 Provider 若确实判断重要性变化，
  可通过生命周期提议显式设置 `importance_score`。
- recycle/archive/restore：只改变 retention。
- RISE：创建新的 abstract identity；source identities 仅增加 `DERIVED_TO` relation。
- cross-identity destructive merge：Deferred。

## Persistence 与事务

Schema v2 使用 `PRAGMA user_version=2`。核心表：`memory_items`、`memory_revisions`、`provenance_refs`、`memory_revision_provenance`、`ingestion_events`。

Provider 调用位于 DB transaction 外；所有 validated proposals、revision/provenance、event APPLIED 与 raw scrub 在一个 transaction 内提交。PENDING event 的 raw input 只保留到成功提交。

普通对话只有在 Agent 选择 `update_long_term_memory` 内部工具时，才注册为持久化的
PENDING `ingestion_event` 并立即调用批量梦境管道。Provider/NEMORI 决定事件是否
产生 durable episode 以及具体生命周期操作；成功提交后事件标记 APPLIED，失败时
保留 PENDING 原文以便后续重试。系统不再使用轮数或墙上时钟触发隐藏 worker。

约束包括：foreign keys、`UNIQUE(memory_id, revision_no)`、每个 memory 最多一个 CURRENT 的 partial unique index，以及 current pointer 同 identity/CURRENT trigger。

## Read boundary

`MemoryReadRepository` 提供 `get_current()`、`list_current()`、`get_history()`。默认读取只返回 `ACTIVE + CURRENT`；历史必须显式请求。Task 2 不得直接查询 Task 1 SQLite internals。

## 实现映射

- models：`code/src/memory/core/schema.py`
- repository/schema/transaction：`code/src/memory/storage/sqlite_manager.py`
- public manager：`code/src/memory/api/memory_manager.py`
- lifecycle：`code/src/memory/lifecycle/crud_engine.py`
- acceptance：`code/tests/test_lifecycle.py and code/tests/test_revisions.py`
