# ADR-0015：Applicability interpretation 使用受控、可扩展 ontology

Status: **Accepted**

> Phase 2E 的真实 pilot 和下方指标属于历史证据；当前产品固定使用 DeepSeek
> `deepseek-v4-flash`，本 ADR 的 schema/validator 边界仍然有效，但不应把旧 pilot
> 指标当成当前模型评测。

## Context

Phase 2E 真实 pilot 的 context F1=`0.1026`、false-known=`0.7619`、scope-state accuracy=`0.0141`。输出结构虽然是严格 JSON，但 `field` 是自由字符串；模型生成 `persons`、`tasks`、`expense_responsibility`、`hotel_search` 等近义或不适用概念，而 frozen `ApplicabilityEngine` 以 `(dimension, field)` 精确对齐。

## Problem

自由 field vocabulary不是可靠 production contract。单纯放宽 evaluator alias会掩盖真实 runtime mismatch；把所有自然语言 value做全局 enum又无法覆盖城市、时间与预算等开放值。还必须区分 absent、UNKNOWN、AMBIGUOUS，以及 query intent与真正影响 memory applicability 的 current facts。

## Options considered

1. 保持自由 field，继续 prompt tuning；2. 把 ontology写入 Task 1/SQLite；3. Task 2维护 versioned registry，field closed、value按 field选择 closed/open；4. 使用另一个 LLM做隐式 normalization。

## Decision

采用选项3：

- `ApplicabilityFieldRegistry` 是 Task 2 derived configuration，不持久化、不修改 MemoryRevision。
- field identity是 `(ApplicabilityDimension, field_id)`；field输出 vocabulary封闭且由 JSON Schema暴露。
- closed-value field拒绝未知 value；open-text field只做Unicode/case normalization和registry声明的alias。
- known field/value alias是deterministic mapping；不引入隐藏LLM normalizer。
- absent表示未提及/不相关；UNKNOWN只用于相关但值确实不可得；AMBIGUOUS要求至少两个有证据候选。
- query intent不自动成为 applicability fact；unsupported concept必须omit并warning。
- `EXPLICIT_GENERAL`需要明确 universal/general文本证据；metadata mention不是scope proof。
- provider输出保留raw values trace，canonical DTO继续供frozen `ApplicabilityEngine`消费。

## Why

受控 schema-guided extraction能在生成阶段阻止field invention，并让错误归因拆成field selection、value normalization与scope/state判断。它保留开放世界文本值，不把Task 2 derived推断升级为Task 1 truth，也不改变Phase 2B policy。

## Rejected alternatives

- 自由 field + evaluator semantic equivalence：线上engine仍会错配，且会污染正式指标。
- Task 1增加profile表/columns：建立第二套canonical truth，并将模型版本依赖写入长期存储。
- 全局value enum：城市、时间、预算等不可穷举。
- LLM normalization layer：增加不可观测误差源与调用成本。

## Reference systems / research evidence

采用成熟的schema-guided extraction、closed tool/schema vocabulary与conservative abstention模式；Groq GPT-OSS strict JSON Schema用于结构约束。没有复制外部实现代码，也不声称ontology schema具有研究novelty。

## Consequences

Registry覆盖之外的concept会abstain；alias必须显式维护；open value仍可能有semantic variation。Ontology升级必须versioned并在frozen/hard datasets上重新评估。复杂conditional reasoning仍deferred。

## Implementation mapping

- `code/src/memory/retrieval/applicability_ontology.py`
- `code/src/memory/retrieval/interpreters.py`
- `code/src/memory/retrieval/interpretation_models.py`
- `code/src/memory/core/config.py`
- `code/src/memory/llm/llm_client.py`

## Test mapping

- `code/tests/test_applicability_ontology.py`
- `code/tests/test_semantic_interpretation.py`
- `code/tests/test_interpretation_evaluation.py`
- `benchmarks/task2/context_interpretation_hard_v1/`
- `code/scripts/task2_semantic_hardening_eval.py`

Introduced in phase: Phase 2F。Supersedes: none。Superseded by: none。
