# ADR-0021：可检查、可保护的自适应用户特征仓库

Status: **Accepted**

## Context / Problem

MAPAS 需要在长期协作中理解用户的稳定研究偏好，同时避免形成一份不可见、不可修正、无限增长的隐藏画像。用户还需要手动管理权，但常规整理不应要求频繁维护。

## Decision / Why

用户特征仓库是 Task 1 权威长期记忆的读取投影，只展示 `CORE_PROFILE` 与 `PREFERENCE_RULES`，不建立第二套画像存储。

- 手动新增与修正复用显式 Provider lifecycle 和 revision provenance，并默认 `is_pinned=true`。
- 自动对话沉淀始终由 Provider 通过 typed lifecycle proposal 判断类型与操作；首次沉淀也不使用本地固定类型。
- 自动 lifecycle 增加 `REMOVE`。它仅可作用于未锁定的画像类型，持久层再次校验目标 ID、ACTIVE 状态、类型和锁定状态。
- `REMOVE` 不做物理删除，而是把 identity 迁移到 `RECYCLED`；用户可从仓库恢复。
- `is_pinned=true` 是确定性保护边界。Provider 即使误提议 `REMOVE`，应用层与持久层都会拒绝自动移除。

## Rejected alternatives

- 独立 profile JSON/数据库：会与长期记忆产生双写和冲突真相。
- 用关键词判断偏好或撤回：违反 Provider-owned semantic decision 边界，且无法覆盖未知表达。
- 自动物理删除：不可审计、不可恢复。
- 所有推断永久保留：画像会累积过时和重复理解。

## Consequences

前端可显示来源、置信度、更新时间、锁定与自动管理状态；用户可以新增、修正、锁定/解锁、收起和恢复。自动删减仍依赖后台记忆沉淀开启及 Provider 的高置信语义判断。

## Implementation / Test mapping

- `code/src/memory/core/schema.py:LifecycleOperation.REMOVE`
- `code/src/memory/api/memory_manager.py:run_autonomous_dream_pipeline`
- `code/src/memory/storage/sqlite_manager.py:apply_proposals_atomic`
- `code/src/memory/app/console.py:get_profile_repository`
- `code/scripts/personal_agent_demo.py` profile HTTP API
- `code/tests/test_ingestion_journal.py`
- `code/tests/test_personal_agent_demo.py`

Introduced in phase: Adaptive Profile. Supersedes: None. Superseded by: None.
