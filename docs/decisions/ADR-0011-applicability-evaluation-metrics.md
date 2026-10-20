# ADR-0011：Applicability Evaluation Requires Safety, Utility, and Uncertainty Metrics

Status: **Accepted**

## Context

Phase 2B 的六-memory integration fixture 证明 typed context/profile 可驱动 deterministic mismatch rejection，但单个 contamination 数字无法排除 reject-all、只测简单 case 或把 UNKNOWN 当 MATCH 的退化策略。

## Problem

需要可重复、可扩展的 evaluation protocol，回答 Recall 是否产生 hard negatives、独立 applicability 是否降低明确 mismatch、naive exact filter 是否已经足够，以及 uncertainty preservation 是否产生可观察差异。

## Options considered

1. 继续只报告 contamination；
2. 只报告 categorical accuracy；
3. 同时评估 retrieval、safety、utility、uncertainty、coverage、K sensitivity，并保留 per-dimension/family/subset error analysis。

## Decision

采用选项 3。Controlled v1 使用 32 memories、28 query-contexts、224 pre-labeled pairs。每案在固定 8-memory candidate universe 内运行 frozen Recall，并比较 B0 Recall-only、B1 permissive/strict exact filters 与 B2 frozen ApplicabilityEngine。

必须同时报告：Recall@K、MRR、mismatch rejection、applicable retention、false rejection、false acceptance、retained mismatch contamination、UNKNOWN/AMBIGUOUS preservation与retention、definite-decision coverage、uncertainty rate。按 dimension、scenario family 与 supported/rich subset breakdown；K=`1/2/4/8`。

## Why

- contamination alone 可通过 reject-all 人为降低；
- applicable retention/false rejection 暴露 utility loss；
- false acceptance/mismatch rejection 暴露 safety failure；
- uncertainty preservation 区分 permissive pass-through、strict delete 与 typed uncertainty；
- coverage/rate 防止系统靠全部 UNKNOWN 获得安全假象；
- K sensitivity 观察 larger recall pool 的收益与污染。

## Rejected alternatives

- 只有 contamination：缺少 utility denominator。
- UNKNOWN 计作 usable MATCH：抹去 evidence insufficiency。
- benchmark-specific tuning：破坏 frozen Phase 2A/2B ablation。
- gold 混入 runtime profile/context：造成直接 leakage。

## Reference systems / research evidence

本 ADR 来自项目 controlled evaluation 需求，而不是对某论文 metric suite 的复现。Phase 2R 将使用此 taxonomy 去检索相关 academic task/dataset/metrics。当前不能声称 first、novel、SOTA 或 research contribution proven。

## Consequences

- runtime input 与 gold 文件物理分离；validator 检查全部 references/coverage/schema。
- production retrieval modules不 import benchmark。
- B1 permissive/strict 是 evaluator-only weak baselines。
- 当前结果允许得出“Recall-only 有明确 mismatch contamination”与“typed uncertainty 可保留”，但不能声称 B2 explicit filtering 优于 B1；controlled v1 中二者 safety 相同。
- complex conditional case 放入 rich-reasoning subset并预测 UNKNOWN，不扩展 DSL。

## Implementation mapping

- `benchmarks/task2/applicability_eval_v1/`
- `code/scripts/task2_eval_support.py`
- `code/scripts/task2_applicability_eval.py`

## Test mapping

- `code/tests/test_applicability_evaluation.py`
- existing Task 1/2A/2B acceptance runners

Introduced in phase: Phase 2C。

Supersedes: None。Superseded by: None。
