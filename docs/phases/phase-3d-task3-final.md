# Phase 3D：Natural-Language Integration & Final Task3 Freeze

状态：**Implemented / frozen**。

`OpenAICompatibleTaskStateInterpreter` 复用既有 OpenAI-compatible LLM client，通过
`MemoryConfig` runtime 配置 provider、endpoint、credential env、model、reasoning 和
completion budget。它只产生严格结构化 `TaskStateProposalBatch`，解析后仍经过
Phase3B deterministic validator；provider 调用始终位于 repository transaction 外，
失败保持 event 可 retry 且不会写 ledger。

真实 5-event smoke（goal、constraint、question safety、intent/completion safety、
zero proposal）已成功；该 smoke 仅证明 integration，不代表 semantic quality
production PASS。

Task-level `ACTIVE/PAUSED/COMPLETED/CANCELLED` 与 entry lifecycle 分离。pause 会先
建立 continuation checkpoint，resume 会恢复 ACTIVE 并 repair checkpoint；complete/
cancel 保留全部历史但从 active task listing 与默认 working context 排除。
`TaskStateService` 统一提供 create/process/apply/build-context/pause/resume/complete/
cancel/get/list API。

Task3 schema 当前为 v5，覆盖从 Phase3A v1 到当前版本的顺序迁移，并拒绝未来版本。v5 增加了 continuation checkpoint 的 focus、next_action 和 world_state 字段。
多任务在同一 DB 中按 task_id、foreign keys、event/checkpoint/relation ownership
严格隔离。

本阶段不实现 Main Agent、完整 Transcript Store、Task1 automatic promotion、LLM
compaction、personality/cognitive modeling 或大规模 semantic benchmark。
