# Phase 0C — Architecture Gap Analysis

## Goal

综合仓库 audit 与 reference evidence，确定最小统一边界，避免把六个项目拼成 Frankenstein system。

## Decisions

- Task 1/2/3 ownership 采用 canonical model + read DTO + module-local views（ADR-0001）。
- Task 3 从 universal six-role working set 修订为 TaskLedger/ContinuationCheckpoint/ActiveWorkingContext 分层（ADR-0006）。
- Task 2 把 recall 与 applicability 分阶段（ADR-0007）。
- SQLite canonical source + derived indices；provider capability/error semantics必须显式。

## Research implications

当前最强研究假设是 scenario/context-conditioned long-term memory applicability，而非 task checkpoint 本身。该假设需要 Phase 2 deterministic benchmark 和后续模型实验验证。

## Deferred

最终 EvidenceBundle schema、Task 3 public schema、complex temporal/as-of retrieval、RL/PPR/graph DB、Task 4。

## Git archive

阶段当时无 commit；相关边界随 Phase 1B baseline 进入 canonical `master`，并由正式 ADR 持续维护。
