# Phase 2R — Academic Novelty Collision Audit

## Starting state

Task 1、Task 2A、Task 2B、Task 2C 已 frozen/PASS。Phase 2C 的 controlled evaluation显示 B0 有 explicit mismatch contamination，但 B1 exact filter与B2在 supported mismatch safety 上相同；B2只额外保留 UNKNOWN/AMBIGUOUS semantics。

Protected-local Git hygiene 已在本阶段前独立完成并通过。本阶段保留用户图片删除与 `logo.png` 未跟踪状态，不纳入 staging。

## Goal

调查成熟 academic work 与公开源码，判定 applicability 候选 claims 的碰撞程度，提取可直接借鉴的 strong baseline/evaluation patterns，为完整 Personal Agent 系统和后续 baseline comparison服务。Novelty 不是 PASS 条件。

## Evidence inspected

全文/正式记录覆盖 RPEval、BenchPreS、KnowAct、PersonaMem、PrefEval、PEARL、LongMemEval、PersonaLens、SARE、RF-Mem、FluxMem、MemCon、APOLLO，并补充直接碰撞的 STITCH/CAME-Bench、MemSyco-Bench、STALE、HorizonBench、CIMemories、Severance、SAMD。

RPEval official repository inspected at commit `0f8bce80a03e6b86426485264b14ae7de49cc8f9`；当时的 reference checkout 位于 ignored 本地路径 `reference_repos/rpeval/`，不随本仓库分发，也不是 runtime dependency。

## Decisions

- 原宽泛 applicability novelty：**DROP**。
- 系统工程边界：**KEEP**。
- 后续研究定位：**NARROW**。
- Phase 2C：正式定位为 controlled semantic self-consistency/regression evaluation。
- 下一步优先 external benchmark feasibility、strong baseline replication/comparison与 oracle failure attribution；不继续堆同构 symbolic rules。

ADR-0012 记录上述变化，不删除或重写 ADR-0007/0010 的历史。

## Strongest collisions

- RPEval：逐 preference Ignore/Support/Dominate utilization policy。
- BenchPreS：recipient/task context 下 apply/suppress。
- STITCH：pre-injection contextual-intent filtering/ranking。
- KnowAct：retrieval/comprehension/application failure attribution。
- MemSyco-Bench：contextual scope、current valid memory、evidence conflict、personalized use。
- STALE/HorizonBench：implicit invalidation、evolving preference、provenance/current state。
- Severance：structured unknown context与clarification。

## Reuse candidates

L2: RPEval per-memory protocol、BenchPreS dual safety/utility metrics、KnowAct oracle decomposition、MemSyco taxonomy、Severance clarification evaluation。

L2/L3: STITCH contextual-intent retrieval、PEARL generation-calibrated retrieval、RF-Mem uncertainty-routed retrieval。

No direct code reuse；no production dependency changes。

## Known limitations / deferred work

- 未运行论文 benchmark，尚未完成数据许可/成本 feasibility mapping；
- 未证明 narrowed candidates 独立 novelty；
- 未测试自然语言 context/profile inference；
- 未测试 online model或 end-to-end agent improvement；
- 未实现 EvidenceBundle、final reranking或 LLM judge；
- README 保持不变，project-level cleanup deferred。

## Components affected

Documentation only：academic survey、research gaps、ADR-0012、traceability、this phase record。

## Acceptance

- detailed claim-by-claim collision matrix；
- Phase 2C circularity audit；
- at most three narrowed candidates with risks/costs；
- existing production regressions remain passing；
- only path-specific documentation staging；
- ignored reference repository remains outside canonical Git。

Validation result：`64 passed`；Task 1、Task 2A、Task 2B、Task 2C acceptance runners均 PASS；compileall、wheel build/install import、`git diff --check` 与 credential-shaped tracked-file scan PASS。五个 historical DB/WAL/SHM 文件的 SHA-256 在回归前后完全一致。

## Git archive

Phase tag: `phase-2r-novelty-audit`。具体 commit 由 canonical Git history记录，不在 phase 文档维护易失 SHA mapping。
