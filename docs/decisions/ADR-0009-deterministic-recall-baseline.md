# ADR-0009：Deterministic Recall Baseline and Score Separation

Status: **Accepted**

## Context

Task 2 的研究目标是区分 candidate relevance 与 current-context applicability。如果直接从 online embedding/LLM 开始，provider、模型版本和网络变化会混入 applicability ablation，无法建立稳定对照组。Phase 1 的 `InMemoryVectorStore` 实际是 character 2-gram compatibility placeholder，接口名称也容易造成 semantic/vector 误解。

## Problem

需要一个 deterministic test、current-safe、可解释、可重复的 recall stage，同时不能复制 Task 1 schema、读取 SQLite internals，或把 request-local score 写回长期记忆。

## Options considered

1. 复用 Task 1 character 2-gram placeholder；
2. 先实现 online semantic embedding/vector recall；
3. 先实现 dependency-free BM25-style lexical baseline，并把 content 与 metadata lexical score 分解。

## Decision

采用选项 3。`MemoryRecallEngine` 依赖 `MemoryReadRepository`，从 `list_current()` 构建 ephemeral corpus。内容使用 Okapi BM25 常用正值 IDF 形式；显式 semantic metadata 提供有界 lexical overlap boost。candidate 引用 `MemorySnapshot` 并保留 provenance。

## Why

- deterministic test reproducibility：无 API key、网络或模型下载；
- fair applicability ablation：Phase 2B 可比较 recall-only 与 recall+applicability；
- traceability：content/metadata score 与 matched terms 分开；
- ownership：Task 1 仍是唯一 canonical memory source；
- honesty：实现明确称 lexical recall，不冒充 semantic understanding。

## Rejected alternatives

- 2-gram placeholder：兼容组件缺少 BM25 corpus statistics、typed result 和 metadata trace，名称具有误导风险。
- embedding first：引入 provider confound、依赖和非确定性，且不能单独证明 applicability 价值。
- metadata hard applicability filter：UNKNOWN 与 MISMATCH 未建模，会提前混入 Phase 2B 职责。

## Reference systems / research evidence

Mem0、Letta、Graphiti 调查表明 candidate recall 可以使用 vector/metadata/graph/hybrid signals，但 recall 本身不等于 scenario applicability。这里借鉴 L1/L2 分阶段模式，没有复制外部代码，也没有新增 runtime dependency。

## Consequences

- Phase 2A 是 lexical engineering baseline，不是 semantic retrieval 或 research benchmark。
- CJK 使用 unigram+bigram，可能因高频单字产生 broad candidates；这是 baseline 可观察限制。
- corpus 每次查询重建，适合当前规模但不承诺 production scaling。
- provenance authority 被保留但不参与 score。
- semantic/hybrid retriever、applicability、rerank 与 `EvidenceBundle` 继续 deferred。

## Implementation mapping

- `code/src/memory/retrieval/models.py`
- `code/src/memory/retrieval/tokenizer.py`
- `code/src/memory/retrieval/lexical.py:LexicalBM25Index`
- `code/src/memory/retrieval/recall.py:MemoryRecallEngine`
- `code/src/memory/retrieval/metrics.py`
- `benchmarks/task2/recall_engineering_v1.json`

## Test mapping

- `code/tests/test_recall.py`
- `code/tests/test_recall_benchmark.py`
- `code/scripts/task2_recall_acceptance.py`

Introduced in phase: Phase 2A。

Supersedes: None。Superseded by: None。
