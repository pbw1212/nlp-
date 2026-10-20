# ADR-0007：Recall 与 context applicability 分阶段

Status: **Accepted engineering architecture; implemented through Phase 2C; broad novelty interpretation superseded by ADR-0012**

## Context / Problem

Semantic similarity 会召回“都谈住宿”的记忆，但不能判断独自/朋友、商务/自费、上海/山里或旧/新偏好是否适用于当前请求。

## Options considered

1. vector Top-K 直接注入；2. metadata hard filter；3. recall 后独立 applicability validation，再 rerank/distill。

## Decision / Why

采用选项 3 作为 Task 2 baseline 方向。Applicability 至少需要表达 MATCH/MISMATCH/UNKNOWN，并保留 provenance/conflict/insufficient evidence。具体 schema 和算法留 Phase 2 决定。

## Rejected alternatives

- Top-K：把相关性误当适用性。
- 只有 hard filters：未知 metadata 与真实 mismatch 会被混淆，且复杂约束不一定可结构化过滤。

## Reference evidence

Mem0/Letta/Graphiti 可验证实现主要提供 vector/metadata/graph/temporal retrieval；当时未在这三个系统中发现完整 applicability validator。Phase 2R 扩大到 personalization、memory utilization和 contextual retrieval literature后，确认 RPEval、BenchPreS、STITCH、MemSyco-Bench、KnowAct 与 SARE 对宽泛问题有直接或强重叠。原 gap interpretation由 ADR-0012 supersede；本 ADR 的 pipeline boundary继续有效。

## Consequences

Task 2 query-time state 不得写回 Task 1。不能把当前 2-gram placeholder 称为 semantic retrieval。

## Implementation / Test mapping

Phase 2A 已实现 recall side：`MemoryRecallEngine`、`RecallResult` 和 deterministic engineering benchmark（ADR-0009）。Phase 2B 已实现 caller-supplied structured context/profile 上的 deterministic applicability baseline（ADR-0010），并保持 Recall rank/score 独立。Phase 2C 建立 controlled evaluation。自然语言 context inference、semantic aliases、external benchmark、final rerank 和 `EvidenceBundle` 仍为 Planned；宽泛 research novelty已撤回。

Introduced in phase: Phase 0C。Supersedes: embedding-only retrieval proposal。Superseded by: ADR-0012（仅 research novelty positioning；工程边界不变）。
