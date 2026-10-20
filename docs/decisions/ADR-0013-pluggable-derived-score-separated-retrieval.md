# ADR-0013：Retrieval backend 可替换、derived 且 score-separated

Status: **Accepted**

## Context / Problem

Phase 2A lexical BM25 可重复，但无法稳定覆盖低词面重叠的语义改写。直接改成单一 embedding Top-K 会丢失 lexical exact strengths，并把 provider、index、applicability 和 persistent memory ownership混在一起。

## Options considered

1. 用 Dense 取代 frozen BM25；2. 归一化后加权合并 raw scores；3. 保留独立 lexical/dense backend，以统一 protocol 返回 score trace，再用 RRF 建立 hybrid baseline。

## Decision

采用选项 3：

- Task 1 SQLite / `MemoryReadRepository` 是 canonical truth；所有 retrieval index derived、ephemeral、rebuildable。
- `MemoryRetriever` 统一 `RecallRequest -> RecallResult`；backend 不拥有 persistent memory schema。
- `EmbeddingProvider` dependency-injected，真实 backend 必须显式启用并 typed fail；默认 pytest 不联网/下载。
- Lexical、dense cosine、RRF fusion 和 applicability status 分开记录；不把 applicability 乘进 recall score，也不写回 importance。
- Hybrid 使用 `k=60` 的 RRF，避免不同 raw-score scale normalization 和 benchmark-specific weights。
- benchmark adapter provenance 明确区分 OFFICIAL、PAPER_DERIVED_REIMPLEMENTATION、SYSTEM_ADAPTER、OUR_BASELINE。

## Why / reference evidence

Graphiti `401c59a65bde` 将 search method 与 RRF/reranker分离；Letta `ff19ffeafeb5` 对 FTS/vector 使用标准 `k=60` RRF。Mem0 `4a0a9a92a641` 的 additive hybrid 展示了 weighted score 对 normalization/threshold 的额外假设。因此本项目采用成熟的 rank-based pattern，独立重写代码，不复制 reference implementation。

FastEmbed 是显式可选的 lightweight ONNX backend；默认 multilingual MiniLM 模型服务中英工程 baseline。它不是 canonical dependency或 index source of truth。

## Rejected alternatives

- Dense 替换 lexical：会破坏 Phase 2A frozen baseline和 exact keyword优势。
- raw score weighted sum：BM25 与 cosine scale 不同，需要额外 calibration，当前数据不足以正当化权重。
- persistent vector DB：当前规模不需要，且扩大 operational scope。
- silent Dense→Lexical fallback：会把 REQUIRED semantic workflow伪装成成功。

## Consequences

RRF 不保证在每个 dataset 上优于最强单路；必须同时报告三种方法和 per-query failures。Dense 初版每次从 current snapshots 重建 embedding corpus，正确但不适合大规模/高 QPS。模型升级会改变 embedding behavior，必须通过 provider/model identity 与新 evaluation记录。

## Implementation mapping

- `code/src/memory/retrieval/protocols.py`: `MemoryRetriever`, `EmbeddingProvider`
- `code/src/memory/retrieval/embedding.py`: provider identity/validation/FastEmbed backend
- `code/src/memory/retrieval/dense.py`: current-safe cosine recall
- `code/src/memory/retrieval/hybrid.py`: RRF fusion
- `code/scripts/task2_retrieval_eval_support.py`: evaluator-side baseline/provenance contract

## Test mapping

- `code/tests/test_retrieval_backends.py`: cosine、validation、current-safe、read-only、RRF、backward compatibility、typed failure
- `code/tests/test_retrieval_evaluation.py`: dataset validation、determinism、method provenance、metrics
- `code/scripts/task2_retrieval_eval.py`: deterministic test contract run / explicit FastEmbed run

Introduced in phase: Phase 2D。Supersedes: none。Superseded by: none。
