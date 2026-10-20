# Phase 2D：Strong Retrieval Foundation

## Starting state / Goal

Task 1、2A、2B、2C frozen；只有 `lexical_bm25_v1` candidate recall。本阶段增加可替换 Dense/Hybrid strong baseline，同时保持 current-safe、read-only、deterministic test-default 与 score separation。

## Implementation

- 统一 `MemoryRetriever` 与 `EmbeddingProvider` protocols。
- `DenseEmbeddingRetriever`：revision content embedding、L2 normalize、cosine、stable tie、zero exclusion。
- `HybridRRFMemoryRetriever`：BM25 + Dense ranks，`k=60`，candidate depth 50，memory ID de-duplicate。
- `FastEmbedEmbeddingProvider`：显式 optional local backend；multilingual MiniLM 384d；typed configuration/request/response failures。
- `RecallScoreBreakdown` backward-compatible optional dense/fusion trace。
- 40-memory / 32-query retrieval engineering evaluation；8 domains、3 subsets；R@1/4/8、MRR和逐 query failure categories。

## Reference implementation decision

Graphiti 与 Letta 的 source-level evidence支持 independent candidate generation + RRF；Mem0 展示 additive hybrid 的 normalization/threshold成本。采用 L2 architecture pattern / L3 reimplementation，不直接复制代码。PEARL/STITCH等 context/generation-calibrated机制 deferred，不混入纯 recall。

## Evaluation interpretation

默认 deterministic-test provider 只是 contract regression，不是 semantic result。显式 FastEmbed engineering run 才能比较真实 semantic backend；该小数据仍不是论文 benchmark。Hybrid 没有“SOTA/必然优于 Dense”的保证。

显式 FastEmbed 0.8.0 engineering run（model=`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`）：BM25 `R@1=.6875/R@4=.9375/R@8=.9688/MRR=.7953`；Dense 与 Hybrid 均为 `R@1=.8750/R@4=.9688/R@8=1.0/MRR=.9263`。Dense-only/Hybrid recovery case 为 `exercise_q4`，三者共同未在 Top-4 命中 `work_q3`。这些数字仅是当前 engineering fixture 的 regression observation。

## External benchmark / system plan

Primary external full-system benchmark：MemSyco-Bench。Secondary：CAME-Bench。后续 Mem0/Graphiti/Letta 使用隔离环境的 `SYSTEM_ADAPTER` 公平比较，不成为主 package dependency。

## Tests / acceptance

覆盖 dense score/vector validation、dimension mismatch、tie/current safety/read-only、RRF duplicate fusion、backward compatibility、network-free default、dataset validation、metrics与 deterministic JSON。全套 Task 1/2A/2B/2C acceptance必须继续通过。

## Known limitations / Deferred

- Ephemeral corpus 每次重算 embedding；无 cache/persistent ANN/scaling。
- 只有一个显式 local semantic backend；无 online API provider。
- 无 cross-encoder reranker、query transform、semantic metadata embedding。
- 无自然语言 applicability extraction、conflict/distillation/EvidenceBundle/Main Agent。
- Engineering fixture 规模小，不能支持论文效果或 external-system superiority claim。

## Architecture changes

ADR-0013。Task 1/Applicability semantics 无改变。

## Git archive

- Implementation commit: `a1424bd feat(retrieval): add dense and hybrid recall backends`
- Phase tag: `phase-2d-strong-retrieval`

