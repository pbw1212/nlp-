# Phase 2B — Deterministic Context Applicability Layer

## Starting state / Goal

Task 1 frozen at `phase-1c-task1-frozen`；Task 2A frozen at `phase-2a-recall-baseline`。目标是在不实现 NL parser、LLM、embedding 或 `EvidenceBundle` 的前提下，对 Recall candidates 做 structured deterministic applicability assessment。

## Decisions / Implementation

- `ApplicabilityContext` 显式表达 KNOWN/UNKNOWN/AMBIGUOUS current facts。
- `MemoryApplicabilityProfile` 是 Task 2 annotation，不写入 Task 1。
- Candidate scope 区分 EXPLICIT_CONSTRAINT、EXPLICIT_GENERAL、UNSPECIFIED。
- Dimension 支持 PERSON/LOCATION/TIME/TASK/SCENARIO/CONSTRAINT。
- CONSTRAINT 支持 REQUIRES/EXCLUDES；其他 dimension 使用 normalized set overlap。
- 一个 MISMATCH → REJECT；AMBIGUOUS/UNKNOWN → RETAIN_WITH_UNCERTAINTY；否则 MATCH/RETAIN。
- `DimensionAssessment` 保存 stable reason code 和 pointer-style evidence refs，不复制完整文本。
- batch 保持原始 Recall rank/score/order，不做 final rerank。

## Engineering fixture

`applicability_profiles_v1.json` 与 `applicability_contexts_v1.json` 覆盖 solo Shanghai、friends hiking、company/self-paid、unknown payment、explicit general、unspecified scope、ambiguous task 和 EXCLUDES constraint。所有 values 是 fixture symbols，engine 不理解业务 ontology。

Recall integration query 同时产生 applicable、mismatch、unknown candidates。阶段验收结果：

```text
Applicability accuracy:             1.0
Mismatch rejection accuracy:        1.0
Unknown preservation accuracy:      1.0
Recall-only mismatch contamination: 0.25
After applicability:                0.0
```

这些是 deterministic engineering regression 数值，不是 research benchmark/result。

## Components affected

- `code/src/memory/retrieval/applicability_models.py`
- `code/src/memory/retrieval/applicability.py`
- `code/src/memory/retrieval/applicability_metrics.py`
- applicability fixtures、tests、acceptance runner
- Task 2 architecture、ADR-0007/0010、research gaps、traceability、README

## Tests / acceptance

覆盖 exact match、explicit mismatch、unknown、unspecified、ambiguous、general、REQUIRES/EXCLUDES、multi-dimension aggregation、original recall preservation、deterministic typed loading、read-only 与 network guard。

完整 phase acceptance：55 tests、Task 1 acceptance、Task 2A recall acceptance、Task 2B applicability acceptance、compileall、wheel import、secret guard 与 historical DB hash guard 全部 PASS。

Git archive：

- annotated tag `phase-2b-applicability-baseline`

## Known limitations / Deferred

- 无自然语言 context/profile extraction；
- 无 semantic alias/ontology；
- 无 complex conditional rule（例如 busy area implies requires soundproof）；
- 无 LLM judge、final rerank、conflict resolver、EvidenceBundle；
- engineering annotations 尚未成为独立 research dataset；
- symbolic context/profile correctness 由 caller/fixture 保证。

## Research interpretation

本阶段证明系统能够在已正确结构化的 context 下区分 related-but-inapplicable、unknown 与 ambiguous candidate，并保持 Recall ablation。它没有证明真实自然语言场景上的总体效果或研究新颖性。
