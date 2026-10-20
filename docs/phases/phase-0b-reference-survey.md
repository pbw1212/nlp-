# Phase 0B — External Reference Implementation Survey

## Goal

通过公开源码/官方资料调查 coding/general agents 与长期 memory systems，为边界设计提供可核查证据，而非直接复制实现。

## Systems inspected

- Coding/task continuity：OpenAI Codex、Claude Code 官方可验证材料、OpenClaw。
- Long-term memory：Mem0、Letta、Graphiti。

具体 commit、path、symbol 和证据边界见 [reference-systems.md](../research/reference-systems.md)。

## Findings

- Coding agent continuity 通常组合 transcript/event persistence、compaction、persistent instructions、task structures 和 external world state。
- Long-term memory systems普遍有 extraction/update/retrieval，但 canonical representation、temporal model 和 provenance 保留程度差异很大。
- Semantic/vector/graph retrieval 不自动等于 scenario-conditioned applicability。
- Persistent memory、retrieval result 和 working state 应自然分层。

## Borrowing policy

默认 L1 Concept / L2 Architecture Pattern / L3 Reimplementation Candidate；没有将 reference code 复制进主项目，也没有引入 reference runtime dependency。

## Limitations

Claude Code 核心内部源码不可公开验证；产品行为、公开 distribution/docs 和第三方 parity implementation 必须分开标记。

## Git archive

Reference repos 被 `.gitignore` 排除；阶段当时无主项目 commit。整理后的结论由当前 documentation commit 归档。
