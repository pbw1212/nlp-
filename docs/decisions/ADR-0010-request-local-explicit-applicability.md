# ADR-0010：Applicability Is Request-Local and Explicitly Uncertain

Status: **Accepted engineering baseline; research novelty positioning superseded by ADR-0012**

## Context

Phase 2A 能稳定召回 lexically relevant memories，但 relevance 不能判断独自/朋友、上海/山里、商务/自费等 current-context applicability。要验证 reasoning 本身，必须先隔离自然语言 context extraction 的误差。

## Problem

需要对已有 `RecallCandidate` 做可解释、deterministic 的 structured comparison，同时严格区分明确冲突、缺失信息、歧义和明确通用 scope，并保持 Task 1/Recall 不变。

## Options considered

A. 继续只使用 semantic/lexical similarity；
B. 使用 metadata hard filter，缺失即删除或默认通过；
C. 独立 request-local applicability assessment，显式保存 MATCH/MISMATCH/UNKNOWN/AMBIGUOUS。

## Decision

采用 C。Applicability profile、current context 与 assessment 都是 derived/rebuildable Task 2 DTO。一个明确 hard MISMATCH 导致 `REJECT`；UNKNOWN 与 AMBIGUOUS 均 `RETAIN_WITH_UNCERTAINTY`。`UNSPECIFIED` scope 是 UNKNOWN，不等于 `EXPLICIT_GENERAL`。

## Why

- `MISMATCH != UNKNOWN`，避免把缺失 context 当成冲突；
- `UNKNOWN != MATCH`，避免无证据泛化个人偏好；
- `UNKNOWN != REJECT`，保留未来 clarification/sufficiency reasoning 所需证据；
- Recall score/rank 与 applicability 可独立观察，支持 ablation；
- typed fixture 先验证 comparison，避免混入 NL parser/provider error。

## Rejected alternatives

- similarity only：相关性无法证明场景适用。
- hard metadata filter：容易把 UNKNOWN 当 mismatch，或把 absent scope 当 general。
- opaque float：会掩盖 MISMATCH/UNKNOWN 的分类错误，当前阶段不采用。
- string-prefix DSL 写入 Task 1 metadata：污染 canonical model，并把 benchmark annotation 误当长期事实。

## Reference systems / research evidence

Phase 0B 调查显示 Mem0/Letta/Graphiti 的 recall/filter/temporal mechanisms 未提供覆盖 person/location/task/scenario/constraint 的统一 applicability layer。Phase 2R 扩展检索后确认 RPEval、BenchPreS、STITCH、MemSyco-Bench 与 SARE 已覆盖 preference utilization、context selectivity、intent-aware filtering或 situation-aware preference。Phase 2B 继续作为 deterministic engineering probe；宽泛 novelty interpretation由 ADR-0012 supersede。

## Consequences

- Engine 只理解 dimension、knowledge/scope state、REQUIRES/EXCLUDES 与 normalized symbolic set relation。
- 不实现 Shanghai=上海 等 semantic alias，也不内置业务 ontology。
- Profile fixture 与 Task 1 semantic metadata 分离且不持久化。
- Explicit mismatch 可以 reject；其余 candidate 不做 final rerank。
- Conditional preference 的通用逻辑、NL extraction、LLM judge、EvidenceBundle 均 deferred。

## Implementation mapping

- `code/src/memory/retrieval/applicability_models.py`
- `code/src/memory/retrieval/applicability.py:ApplicabilityEngine`
- `code/src/memory/retrieval/applicability_metrics.py`
- `benchmarks/task2/applicability_profiles_v1.json`
- `benchmarks/task2/applicability_contexts_v1.json`

## Test mapping

- `code/tests/test_applicability.py`
- `code/tests/test_applicability_benchmark.py`
- `code/scripts/task2_applicability_acceptance.py`

Introduced in phase: Phase 2B。

Supersedes: None。Superseded by: ADR-0012（仅 research positioning；request-local semantics继续有效）。
