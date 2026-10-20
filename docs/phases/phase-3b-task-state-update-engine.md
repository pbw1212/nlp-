# Phase 3B：Validated Task State Update Engine

状态：**Implemented / accepted deterministic test**。

## Contract

一次 `TaskEvent` 由调用方提供，至少包含 task/event identity、`SourceKind`、内容
和时间。`TaskStateInterpreter` 在数据库事务之外返回可为零条的
`TaskStateProposalBatch`；它不能直接取得可写 ledger。`TaskStateProposalValidator`
随后执行 deterministic checks，`TaskStateRepository.apply_proposal_batch` 在单一
SQLite transaction 中执行所有 mutation，并返回 `TaskStateUpdateReceipt`。

支持 `ADD/RESOLVE/SUPERSEDE/REMOVE/LINK_RELATION/SET_PRIORITY`。修正使用
supersede 保留旧状态，event idempotency 防止 replay 重复写入；provider failure
状态可重试，且保证零 durable mutation。`SourceKind` 与 `ProposalBasis` 分离
用户明确事实、工具观察、agent 输出和推断。问题不是 decision，意图不是完成，
条件/假设不会升级为 hard state。

## Boundaries

Task3 store 是独立、versioned SQLite schema（当前 v3），不修改 Task1 表；
Task2 `EvidenceRef` 只能作为轻量引用，不能把 EvidenceBundle 自动复制为 ledger
truth。当前无真实 LLM 依赖，fake provider 和 typed proposal path 是验收入口。
完整 transcript、自然语言 state updater、compaction、Task1 promotion 和 Main
Agent 不属于本阶段。

## Acceptance

`code/tests/test_task_state_updates.py` 与 `code/scripts/task3b_acceptance.py` 覆盖 zero-op、
source authority、correction/supersede、resolution/priority/relation、tool
success/failure、atomic rejection、provider failure retry、restart replay 和
idempotency。Fixture `benchmarks/task3/task_state_update_eval_v1.json` 冻结了
proposal/update semantics 场景；它不是模型排行榜。
