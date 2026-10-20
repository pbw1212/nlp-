> MAPAS 当前架构。2026-10-14 维护报告记录的是当日状态；本文后续的冻结阶段段落属于历史记录，当前多 Agent 实现见 [P1 架构说明](multi-agent-foundation.md)。

> 2026-10-14 新增：可显式启用四角色串行协作 P1；见 [multi-agent-foundation.md](multi-agent-foundation.md)。默认仍为 `single`，Task1/2/3 格式不变；完整实验和 GPU 尚未实施。

# 当前系统总览

> 当前同步日期：2026-09-11。记忆更新相关回归、Session/Task 隔离与输入边界已覆盖；Product Trial `14/14`、50 步 fuzz 通过。

## 实现状态

```text
Task 1 — Long-Term Memory Domain + Lifecycle       Implemented / frozen baseline (main: generic /remember path)
User Profile Repository                            Implemented as a Task 1 projection (manual protection + inferred lifecycle)
Task 2 — Context-aware Retrieval                   Recall/applicability/semantic interpretation implemented
Task 3 — Durable Task Continuity + Active Context  Phase 3D frozen + invalid proposal recovery patch
Conversation Session Context                        Implemented (short-term dialogue continuity, session_id != task_id)
Phase 4A — AgentRuntime Orchestration Layer       Phase 4A frozen
Main Agent Reasoning & Execution Loop              Implemented (CLI / Browser / JSONL host)
Codex-style host lifecycle                        Implemented (events, replay, resume, catalog, approvals)
Workspace and MCP extensions                      Implemented (scope, search, patch, stdio discovery)
Product Trial Harness                              Implemented (14 scenarios PASS, 50-step fuzz, Overfit Guard)
Phase 4C/4D runtime hardening                     Implemented (DTO/idempotency/provenance/failure truthfulness)
Task 4 — Cognitive/personality modeling            Deferred
```

模块边界：

```text
Conversation Session (~/.agent_data/conversations/conversation.db; SQLite 是所有 session transcript 的集中权威存储)
Short-term dialogue context → USER/ASSISTANT transcript history
        ↓
Task 1 (~/.agent_data/long_term_memory/memory.db)
MemoryItem → MemoryRevision → ProvenanceRef
SQLite repository + current-safe read API
/remember explicit route → NemoriDistiller.propose_explicit_lifecycle → LifecycleProposal → apply_proposals_atomic
ordinary turn → Agent-selected `update_long_term_memory` internal tool → exact user event → Provider/NEMORI lifecycle proposal
profile projection → CORE_PROFILE + PREFERENCE_RULES → pin / recyclable REMOVE
        ↓ read-only MemorySnapshot
Task 2
current-safe lexical/dense/hybrid recall
→ independent NL context + memory-scope interpretation
→ structured deterministic applicability
→ controlled safety/utility/uncertainty evaluation
→ conflict → bounded selection/distillation → EvidenceBundle
        ↓
Task 3 (~/.agent_data/task_state/task_state.db)
TaskLedger → ContinuationCheckpoint → ActiveWorkingContext → StructuredTaskContext
        ↓
Phase 4A AgentRuntime
Provider Task Binding → lifecycle validation → Turn Orchestration → Task2 Adapter → AgentTurnContext
        ↓
Main Agent Reasoning & Execution Loop (`code/src/memory/agent/`)
MainAgent facade → AgentLoopController → AgentExecutionJournal → ToolRegistry
        ↓
App Server (`code/scripts/personal_agent_app_server.py`)
         ↓
SQLite event store / JSONL replay
         ↓
Product Trial Harness (`code/src/memory/product_trial/`)
100% TemporaryDirectory isolation → Hard Oracle & Invariants → Overfit Guard (Non-production)
```

关键所有权：

- **Conversation Session** 拥有短期对话历史 (`~/.agent_data/conversations/conversation.db` 集中存储)、会话切换与会话截取。启动终端或 `/new-session` 只创建内存中的新 Session，首条真实对话到达时才落库；`session_id != task_id` 严格解耦。
- **Task 1** 拥有长期事实的 identity、semantic revision、validity、retention、provenance 与 ingestion lifecycle。显式 `/remember` 指令通过 `NemoriDistiller.propose_explicit_lifecycle` 产出 `LifecycleProposal`，经校验后由 `apply_proposals_atomic` 原子提交，生产代码零特定词表硬编码。
- **User Profile Repository** 不创建第二套画像数据库，而是投影 Task 1 中 `CORE_PROFILE` 与 `PREFERENCE_RULES`。手动写入默认锁定；自动沉淀由 Provider 提议 `ADD/UPDATE/CORRECT/REMOVE/NOOP`，其中 `REMOVE` 只能作用于未锁定的画像记忆，并迁移到 `RECYCLED` 以便恢复。
- **Task 2** 只读 Task 1；recall score、semantic interpretation、applicability profile/context/assessment、conflict/selection/distillation 与 EvidenceBundle 都是 derived request-local state。
- **Task 3** 拥有独立 Task3-owned durable task ledger/checkpoint；active context 是有界、派生、可重建的 view。Session 默认不绑定 Task；每个用户 turn 由 Provider 提议继续、切换、创建或释放 Task，确定性边界只校验 ID、状态与生命周期。一个 Task 仍可被 Agent 判断为跨多个 Conversation Session 延续，但不会因启动新 Session 或存在唯一旧 Task 而自动共享；`TaskStateInterpreter` 在大模型产出无效 `support_span` proposal 时进行容错恢复（warning + 保留现有有效状态 + 继续对话）。
- **AgentRuntime (Phase 4A)** 拥有依赖装配 (Composition Root)、Task 路由 (Task Router)、Turn 生命周期与工具结果入库。
- **App Server / Event Store** 提供 JSONL/JSON-RPC 风格的 initialize、thread/session、turn、状态、上下文、工具目录、审批查询和 sequence 事件重放；当前为同步 host，`turn/interrupt` 尚未支持。
- **Main Agent (`code/src/memory/agent/`)** 拥有顶层推理、Prompt 信任分层、工具确认策略、有限步 FSM 与独立持久化 Journal (`agent_journal.db`)。
- **Workspace / MCP** 分别负责用户管理的可见目录边界和显式配置的 MCP stdio 扩展；两者都经过 ToolRegistry、schema、权限和执行记录边界。
- **Phase 4C runtime contracts** 要求跨层 DTO 字段可执行验证、Conversation identity 原子防重、promotion 来源可追踪、Provider 仅对暂态失败重试，并保持 Task 2/3 冻结算法语义不变。
- **Product Trial Harness (`code/src/memory/product_trial/`)** 拥有测试自动化测试、硬不变性校验与防过拟合守卫（`test_production_hardcoding_overfit_guard`）。必须 100% 临时目录隔离，不属于生产推理路径。

SQLite 是 Task 1/2/3/Session/Journal 权威存储。所有临时检索与缓存均可从 SQLite 安全重建。

默认全局运行目录是 `~/.agent_data/`；`AGENT_DATA_DIR` 用于测试/隔离覆盖，
`AGENT_HOME` 用于显式自定义全局目录。项目级旧 `.agent_data/` 只在首次启动时迁移，
不会成为新 session 的默认存储位置。
