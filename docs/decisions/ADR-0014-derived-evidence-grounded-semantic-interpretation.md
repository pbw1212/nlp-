# ADR-0014：Semantic interpretation 是 derived、evidence-grounded 且可独立 attribution

Status: **Accepted**

## Context

Phase 2B 的 `ApplicabilityEngine` 已能正确比较 typed context/profile，但 production request与 `MemorySnapshot` 仍依赖人工 fixture转换。若用一个大 prompt直接给出最终 applicability，无法区分 query understanding、memory scope inference与 deterministic policy error。

## Problem

需要把自然语言和 current revision 转换成已有 DTO，同时防止缺失事实被强填、metadata mention 被误当 scope、模型输出静默污染 Task 1，以及 provider failure被全 UNKNOWN伪装。

## Options considered

1. 一个 LLM prompt直接完成 recall后 applicability；2. 将模型推断 profile持久化进 Task 1；3. 两个独立 derived interpreter复用现有 DTO，再交给 frozen `ApplicabilityEngine`。

## Decision

采用选项 3：

- `ContextInterpreter`: query + bounded recent sources -> `ContextInterpretation(ApplicabilityContext, trace)`。
- `MemoryScopeInterpreter`: current `MemorySnapshot` -> `MemoryScopeInterpretation(MemoryApplicabilityProfile, trace)`。
- 两者依赖共享 `SemanticInterpretationProvider` protocol，真实 backend复用现有 OpenAI-compatible client与 deterministic test/REQUIRED typed failure；fake只用于测试 contract tests。
- 输出严格校验封闭 fields、enum、list/string shape、memory/source evidence refs。KNOWN/AMBIGUOUS与 explicit scope必须有 evidence；失败显式抛出 typed error，不 silent fallback。
- OpenAI-compatible backend可配置 provider label、endpoint、model和credential环境变量名；strict JSON Schema约束基础结构，本地 validator约束 cross-field domain semantics。HTTP rate limit只做有限、header-aware retry。
- `UNSPECIFIED != EXPLICIT_GENERAL`；metadata只是 hint。复杂 conditional不能忠实表达时保留 uncertainty/warning。
- 当前保留三态 KNOWN/UNKNOWN/AMBIGUOUS；未消解冲突属于 AMBIGUOUS。没有新增 CONFLICTING，因为它尚未带来不同 aggregation policy。
- interpretation全部 derived/read-only，不写 Task 1，不改 recall score，不内置最终 rerank或 EvidenceBundle。

## Why / reference evidence

STITCH 提供 structured contextual-intent -> filtering pattern；RPEval `prompts_528.py` 展示逐 preference/query独立 utilization判断；Graphiti extraction/temporal prompts展示 typed result与 source/episode linkage、禁止无证据时间；Letta strict tool schema展示封闭 JSON字段。KnowAct 的 failure attribution启发 A-D oracle intervention。这里采用 L1/L2并独立实现，无 reference code复制。

## Rejected alternatives

- 单 prompt最终判断：attribution不可观测，且把 parser和policy耦合。
- 持久化 inferred profile：会建立 Task 1 之外的平行 truth并让可变模型输出污染 canonical memory。
- absence -> GENERAL 或全 UNKNOWN silent fallback：前者制造 false-known，后者隐藏 provider failure。
- 新 `QueryFrame`：当前 `ApplicabilityContext + ContextInterpretation` 已覆盖 facts/state/evidence/diagnostics，新增 DTO 会重复。
- conditional DSL / semantic alias：超出 Phase 2E，错误简化风险高。

## Consequences

同一 revision可能重复调用 provider，当前没有 persistent cache；model/prompt升级会改变 derived结果，必须记录 identity并重新评估。Provider strict schema仍不能表达全部 domain invariant，所以 repository-side validator是最终边界。2026-08 的 Groq GPT-OSS-120B pilot 曾显示 context/scope ontology 对齐是主要瓶颈；该结果属于历史实验，不能替代当前固定 DeepSeek `deepseek-v4-flash` 的语义质量评测。架构已跑通不等于 semantic quality 可用。

## Implementation mapping

- `code/src/memory/retrieval/interpretation_models.py`
- `code/src/memory/retrieval/interpretation_provider.py`
- `code/src/memory/retrieval/interpreters.py`
- `code/src/memory/retrieval/semantic_pipeline.py`
- `code/scripts/task2_interpretation_eval_support.py`

## Test mapping

- `code/tests/test_semantic_interpretation.py`: states、evidence、strict schema、typed failure、Hybrid production path、read-only。
- `code/tests/test_interpretation_evaluation.py`: independent multilingual dataset、deterministic report、A-D attribution。
- `benchmarks/task2/context_interpretation_eval_v1/`: 30 context + 30 scope + 10 pair human-gold engineering pilot。

Introduced in phase: Phase 2E。Supersedes: none。Superseded by: none。
