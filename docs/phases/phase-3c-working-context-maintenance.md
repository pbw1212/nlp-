# Phase 3C：Working Context Maintenance, Checkpoint Refresh & Compaction

状态：**Implemented / deterministic test accepted**。

## Contract

TaskLedger 继续是 durable truth。`ActiveWorkingContext` 与
`StructuredTaskContext` 是 request-local、bounded、rebuildable views。当前 selection
由 `WorkingContextPolicy` 的 tiered deterministic rules 完成：有效 checkpoint refs、
current goal 和 critical constraints 优先；active decision/open question/progress
其次；低优先 background 先被省略。非 ACTIVE entries 不会成为 current truth。

`TaskContextMaintenanceService` 在 Phase3B mutation 成功后按 meaningful change、
current-step、pause/suspend/checkpoint request 等触发器刷新 checkpoint；checkpoint
仅保存 refs，不复制 ledger payload。`rebuild_checkpoint` 可处理 restart、missing
checkpoint 和 stale refs。

压力下才运行 `DeterministicTaskCompactor`。compaction 是 extractive、lossy、bounded
且带 source entry IDs 的 `TaskContextCompaction`，不写回 TaskLedger，也不作为持久
canonical artifact。recent events 与 external observations 仅是 bounded/fresh
request-local inputs；Task2 EvidenceBundle 保持 provider-independent DTO boundary。

## Acceptance

`code/tests/test_task_state_maintenance.py` 和
`code/scripts/task3c_acceptance.py` 覆盖 critical retention、low-priority eviction、
stale suppression、resolved question removal、checkpoint refresh/no-op、repair/
restart、fresh observation、compaction traceability、hard budget 与 maintenance
failure non-rollback。压力 fixture 为
`benchmarks/task3/task_context_pressure_eval_v1.json`。

本阶段不实现真实 LLM compaction、完整 transcript、Task1 promotion 或 Main Agent。
