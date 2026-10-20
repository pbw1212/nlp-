# Phase 2A — Deterministic Current-Safe Memory Recall Baseline

## Starting state

Task 1 已通过 Phase 1C 并由 `phase-1c-task1-frozen` 冻结；canonical read boundary 是 `MemoryReadRepository` / `MemorySnapshot`。Task 2 只有 recall/applicability 分阶段 ADR，没有实现。

## Goal

建立 deterministic test、deterministic、current-safe、traceable 的 candidate recall baseline，作为后续 applicability experiment 的对照组。本阶段不实现 semantic embedding、applicability、rerank、`EvidenceBundle` 或生成。

## Decisions made

- 采用 dependency-free BM25-style lexical baseline（ADR-0009）。
- tokenizer 使用 NFKC、casefold、Latin/number words 与 CJK unigram+bigram。
- content BM25 与 bounded metadata lexical overlap 分开报告。
- 每次从 `list_current()` 重建 ephemeral corpus；SQLite/Task 1 是 source of truth。
- candidate 引用 `MemorySnapshot`，不创建第二套 persistent memory model。
- authority/provenance 保留但不参与 Phase 2A ranking。
- total score 相同按 `memory_id ASC`；score `<=0` 排除。

## Scoring implementation

设 corpus size 为 `N`，term document frequency 为 `df(t)`：

```text
IDF(t) = ln(1 + (N - df(t) + 0.5) / (df(t) + 0.5))

BM25_content(D,Q) = Σ IDF(t) * tf(t,D)*(k1+1)
                         / (tf(t,D) + k1*(1-b+b*|D|/avgdl))

metadata_overlap = w * Σ IDF_meta(t), t matched in explicit metadata
                       / Σ IDF_meta(t), t in unique query terms

recall_score = BM25_content + metadata_overlap
```

默认 `k1=1.5`、`b=0.75`、`w=0.35`。metadata boost 被限制在 `[0, 0.35]`；它是 lexical signal，不代表 location/person/scenario applicability。

## Components affected

- `code/src/memory/retrieval/`：DTO、tokenizer、lexical scorer、orchestration、metrics。
- `benchmarks/task2/recall_engineering_v1.json`：14 memories / 9 queries 的工程 fixture。
- `code/scripts/task2_recall_acceptance.py`：temporary SQLite acceptance runner 与 JSON report。
- `code/tests/test_recall.py`、`code/tests/test_recall_benchmark.py`。
- Task 2 current architecture、ADR-0007/0009、research gaps、traceability 和 README。

## Tests / acceptance

聚焦验收覆盖 current corpus、historical/recycled exclusion、deterministic ranking、zero-score exclusion、metadata trace、provenance、read-only 与 network guard。工程指标固定为 `Recall@4=1.0`、`MRR=1.0`；仅作为小型 deterministic regression，不能作为论文结果。

完整 phase acceptance：45 tests、Task 1 acceptance、Task 2A acceptance、compileall、wheel import、secret guard 与 historical DB hash guard 全部 PASS。

Git archive：

- annotated tag `phase-2a-recall-baseline`

## Known limitations / Deferred work

- lexical only；CJK unigram 可能产生 broad candidate；
- 无 semantic embedding / hybrid recall；
- 无 applicability、MATCH/MISMATCH/UNKNOWN；
- 无 rerank、conflict、distillation、`EvidenceBundle`；
- corpus 每个 request 在内存重建；
- fixture 是 engineering regression，不是 frozen research benchmark。

## Architecture / research implication

Phase 2A 只实现 ADR-0007 的 recall side。它有意不消除 related-but-not-applicable candidates，使 Phase 2B 能在同一 recall output 上验证 scenario-conditioned applicability hypothesis。
