# Phase 0A — Repository Audit

## Starting state / Goal

项目包含 Task 1 原型、研究笔记与重叠 schema 草稿；Task 1 被报告为不可运行。目标是只读确认真实代码、入口、依赖、测试、数据库和架构冲突，不重构 Task 2/3。

## Problems discovered

- Task 1 有实际代码但边界粗糙；provenance、lifecycle、router 和 provider error semantics 不可靠。
- Task 2、Task 3、Main Agent 没有可用实现。
- persistent state、retrieval-time state 和 task-local state 在草稿中混用。
- 根目录最初没有可审计的正式 Git history；reference checkouts 不能作为 runtime dependency。

## Decisions / Architecture implications

- 现有 `MemoryItem` 是 canonical identity 候选，不创建第二套 persistent memory。
- 在 Phase 0B/0C 完成前不进行大规模架构改写。
- 历史 DB 只做 hash/read-only 安全检查。

## Tests / Evidence

实际运行入口和 tests，用错误类型区分 environment/dependency/import/config/schema/runtime/logic 问题。详细原始 audit 报告当时未纳入 Git；本文件是从审核通过的阶段结论整理，不伪造未保留的命令输出。

## Deferred

Task 1 repair、Task 2、Task 3、Main Agent、Task 4。

## Git archive

当时无 commit。后续代码状态随 Phase 1B baseline 进入 canonical `master`；Task 1 frozen 状态由 tag `phase-1c-task1-frozen` 定位。
