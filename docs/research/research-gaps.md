# Research gaps 与待验证假设

## G1：Contextual applicability and selective memory use

Status: **Known research problem and current engineering focus; broad novelty claim dropped in Phase 2R**。

Phase 2R 的直接碰撞包括：RPEval 的 per-preference `Ignore/Support/Dominate`，BenchPreS 的 context-aware apply/suppress，STITCH 的 pre-injection contextual-intent filtering，MemSyco-Bench 的 Contextual Scope Control，以及更早 SARE 等 situation-aware recommendation。因此“semantic relevance != contextual applicability”、多维 context-dependent preference和 explicit mismatch filtering 均不能再称为本项目 strongest novel hypothesis。

为什么仍重要：个人偏好通常有适用条件；“独自去上海住市中心”不应无条件迁移到“和朋友去山里徒步”，“公司报销的商务酒店”不应决定自费预算。该问题对完整 Personal Agent 的安全与质量仍是硬工程需求。

Closest work 与详细证据：`docs/research/task2-academic-novelty-survey.md`。Graphiti/Mem0/Letta 是 storage/retrieval reference；RPEval、BenchPreS、STITCH、MemSyco-Bench、KnowAct 才是更直接的 applicability/utilization evaluation references。

Phase 2A status：已建立 current-safe deterministic lexical recall 对照组，允许 related-but-not-applicable candidate 保留。该工程 fixture 不含 MATCH/MISMATCH/UNKNOWN labels，也不是 research evaluation dataset。

Phase 2B status：在 caller-supplied typed context/profile 上实现 MATCH/MISMATCH/UNKNOWN/AMBIGUOUS comparison。小型 integration fixture 中 Recall-only mismatch contamination 为 `0.25`，hard mismatch rejection 后为 `0.0`；这只验证 pipeline 能表达并减少已标注 contamination，不证明自然语言 context understanding、真实数据泛化或研究新颖性。

Phase 2C status：controlled v1 扩展到 32 memories、28 query-contexts、224 labeled pairs。K=4 时 B0 contamination=`0.4128`；B1-permissive、B1-strict 和 B2 对明确 symbolic mismatch 都达到 rejection=`1.0`、false rejection=`0.0`。因此当前证据**没有**证明 B2 在 explicit mismatch filtering 上优于简单 B1。差异在 uncertainty semantics：B2 保留 UNKNOWN/AMBIGUOUS status（uncertainty rate=`0.3853`），B1-permissive 虽保留 rows 却伪装成 definite MATCH，B1-strict 则删除全部 uncertain rows。该结果支持继续研究 uncertainty-aware downstream use，但仍是人工 structured inputs 下的 controlled evidence。

Phase 2R interpretation：Phase 2C gold 与 B2 共享人工 symbolic vocabulary，属于 controlled semantic self-consistency/regression evidence，不是独立 effectiveness evidence。B1==B2 的主要原因是 supported subset 可由 exact filter 完全解决，且 fixture follows B2 rules。

Required validation：下一步优先 external task/strong baseline comparison；加入自然语言 context interpretation 和独立人工 labels，分开测 parser、current-state、recall、applicability、comprehension与action error，并比较 naive filter、RPEval/STITCH-style selection、KnowAct-style oracle attribution。只有 external evidence 显示稳定 unmet gap 后，才恢复窄 novelty claim。

## G1a：Incomplete-context calibrated memory-use control

Status: **Possible narrow gap; strong overlap, unverified**。

Severance 已直接表示 structured unknown person-context 并促使 clarification；STALE 有 `unknown-current`；因此 UNKNOWN/abstention 本身不新。尚可调查的窄问题是：对 persistent memory candidate，在不完整/歧义 context 下联合决定 `use / ignore / retain / clarify`，并测 calibration、utility、privacy 与 clarification cost。

## G1b：Lifecycle-aware end-to-end failure attribution

Status: **Possible system/evaluation gap; unverified**。

KnowAct 已分 retrieval/comprehension/application；STALE/HorizonBench/PersonaMem覆盖 evolution/current state；MemSyco覆盖 scope/valid-memory use。可能的剩余问题是用统一 oracle interventions 分开 current-state、recall、scope、comprehension与action failure，而不是 MemoryRevision/provenance schema 本身。

## G2：Provenance across repeated derivation

Status: **Partially addressed in Task 1 baseline**。

多次 extraction/update/summary 后 provenance 容易被覆盖或只保留最新来源。本项目当前 revision-scoped N:M provenance 保留 source references，但尚未实现多级 derived-evidence chain、trust fusion 或 full source transcript。

## G3：Continuation checkpoint

Status: **Engineering baseline implemented; reconstruction fidelity remains an open research gap**。

参考 coding agents 有 session/event log、compaction、Todo/task structures 和 external state。
当前产品已有 session/event 记录、`ContinuationCheckpoint`、未完成 turn resume 和模型感知
compaction，但尚未证明其恢复保真度优于 transcript + structured tasks，也不应宣称创新。

## G4：Progressive compaction loss

Status: **Implemented bounded baseline; research risk remains**。

Repeated summary/compaction 是有损过程。当前已保留 raw event/transcript、可重建 projection
和 typed checkpoint references，并把 summary 作为派生状态；仍需要比较这些机制与纯 summary
continuity 的长期保真度，不应让 summary 成为 canonical task truth。

## Deferred, not gaps claimed as novelty

RL、PPR、大型知识图谱、多 Agent、distributed storage、personality/cognitive modeling 均是明确 deferred research directions，不是当前系统缺陷修复项，也不是已承诺 feature。
