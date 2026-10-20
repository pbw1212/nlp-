# ADR-0006：TaskLedger、ContinuationCheckpoint 与 active context 分离

Status: **Accepted; Task3 Phase 3A–3D frozen**

## Context / Problem

早期 six-role working-set 草稿试图同时表示 todo、continuation、selected context 和 summary，容易成为第二套 transcript 或 external world copy。

## Options considered

1. universal six-role persistent working set；2. transcript summary 作为 canonical task state；3. ledger/checkpoint/derived active context 分层。

## Decision / Why

采用选项 3：`TaskLedger` 表示 work/status/dependency；`ContinuationCheckpoint` 保存恢复位置和 references；`ActiveWorkingContext` 有界且可重建；`StructuredTaskContext` 面向模型。

`raw transcript != compaction summary != ledger != checkpoint`。External world 保持 authoritative。

## Rejected alternatives

- universal working set：参考实现未显示这是成熟 coding agent 的 canonical continuity source。
- summary 为 canonical：summary 是 lossy derived representation，会累积信息损失。

## Reference evidence

Codex、Claude Code 官方可验证行为、OpenClaw 与 Claw Code 表明 continuity 由 event/transcript、compaction、instructions、task structures 和 external state 组合提供。Claw Code `Session`、`SessionCompaction`、`TodoWrite`、`TaskRegistry` 是相互分离结构。借鉴 L1/L2。

## Consequences

ContinuationCheckpoint 仍不宣称研究创新，但当前已经作为可恢复运行时的工程能力落地：
它保存恢复位置和引用，不能替代原始 Transcript 或 TaskLedger。当前模型感知 compaction
也已落地为可追踪的派生 checkpoint；任何摘要丢失风险仍属于需要评测的研究问题。

## Implementation / Test mapping

Phase 3A 实现位于 `code/src/memory/task_state/`，使用独立 versioned
`TaskStateRepository`、`TaskContextBuilder` 与 typed DTO。测试位于
`code/tests/test_task_state.py`，测试 acceptance 为
`code/scripts/task3_acceptance.py`。实现不依赖 Task 1 facade，不写 Task1
canonical tables；Phase 3B 的 interpreter 只产生 proposals，deterministic
validator/repository transaction 才能 durable apply。Phase 3C 增加 deterministic
tiered working-context maintenance、reference-based checkpoint refresh/repair 与
noncanonical extractive compaction；当前 MainAgent 还增加了模型感知 compaction、
`/compact` 和完整 Transcript 保留，
这些 derived artifacts 不改变上述 canonical truth boundary。Phase 3D 增加独立
Task lifecycle（不等同 entry lifecycle）、public facade 与 model-agnostic structured
TaskStateInterpreter；natural-language provider 仍只提案，不直接修改 durable state。

Introduced in phase: Phase 0C-T3，Claw Code 补充后修订。Supersedes: universal six-role canonical Working Set proposal。Superseded by: None。
