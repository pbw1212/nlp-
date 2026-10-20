# ADR-0008：正式文档与 raw historical material 分离

Status: **Accepted**

## Context / Problem

用户原始 prompt/history 可帮助人工回顾，但内容未经整理、可能包含过时或互相冲突的设计，且不应进入 Git。若架构知识只存在于 raw history，外部协作者和后续实现无法稳定追溯当前事实与历史原因。

## Options considered

1. 将 raw history 直接版本化；2. 只保留聊天/阶段报告；3. raw history local-only，正式知识按 current architecture、ADR、phase、research、traceability 分层版本化。

## Decision / Why

采用选项 3。以下用户原始材料必须保持在 canonical Git 之外，并由 `.gitignore` 排除；本地是否保留由用户决定，不得换名后原样提交：

- `his.txt`
- `ins.md`
- `note.md`
- `points.md`
- `task1_complete_spec.md`
- `todo.md`
- `短期工作集提示词.txt`
- `记忆检索部分提示词.txt`

Current architecture 描述现在，ADR 记录原因，phase 记录演化，research 记录外部证据，traceability 连接需求、代码和测试。

## Rejected alternatives

- 直接提交 raw history：把草稿当 specification，并扩大私人/历史材料传播范围。
- 只依赖聊天：不可由仓库独立审计，也无法和 commit/test 对齐。
- 单个巨大 architecture 文档：current truth 与 historical rationale 会互相覆盖。

## Reference evidence

本决定来自项目治理和审计需求，不声称源自外部 SOTA，也不涉及代码复用。

## Consequences

改变 architecture/domain/schema/API/failure/research baseline 的 logical change，若未同步正式文档则不算完成。Accepted ADR 的历史原因不得静默改写；新决定通过 superseding ADR。

这组 local-only 路径曾误入已推送历史；经用户单独批准的 history rewrite，canonical branch/tag 可达历史已清除它们。若未来要求彻底清除 Git hosting 上的所有不可达对象缓存，仍需单独走提供方清理流程。

## Implementation / Test mapping

- `.gitignore`：protected-local set
- `docs/README.md`
- `docs/architecture/`, `docs/decisions/`, `docs/phases/`, `docs/research/`
- `docs/traceability.md`
- guard：对 protected-local set 执行 `git check-ignore`、`git ls-files`、reachable-history 与 staged file audit。

Introduced after phase: Phase 1C documentation governance correction。

Git archive: canonical `master` history；不保留重写前 SHA mapping。

Supersedes: prior decision to include local raw materials in baseline。Superseded by: None。
