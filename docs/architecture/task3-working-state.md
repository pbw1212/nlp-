# Task 3：任务连续性、会话上下文与 Active Context

> 当前同步日期：2026-09-05。本文的当前产品边界以 main 分支实现为准；当前全量回归为
> `386 passed`。

> 本文同时保留 Phase 3A-D 的历史边界与当前产品实现。早期阶段中标记为
> deferred 的能力，不能当作当前发布状态；当前实现以本文最后的“当前产品边界”为准。

Phase 3A 已实现 Task3-owned durable ledger/checkpoint 与 derived working
context。六类 `goal/constraint/decision/progress/open_question/background`
是 ledger entry kinds，不是 transcript，也不是 Task1/Task2 schema。

当前 conceptual boundary：

```text
TaskLedger                 durable: what work exists/status/dependencies
        ↓
ContinuationCheckpoint     durable pointers: where execution resumes
        ↓
ActiveWorkingContext       derived, bounded, task-local, rebuildable
        ↓
StructuredTaskContext      model-facing view
```

必须分离：

```text
raw transcript/event history
!= lossy compaction summary
!= TaskLedger
!= ContinuationCheckpoint
!= external world state
```

`ContinuationCheckpoint` 候选字段只保存 focus、next action 和 task/blocker/question/constraint/decision/evidence/world-state references，不复制完整 ledger 或 external world。Filesystem、database 与 tool-observed state 仍是对应事实的 authoritative source。

## 为什么 Task 3 仍需要 Working Set

最早的任务定义要求：检索到的长期记忆不能简单作为列表塞进 prompt，系统要把它们组织成任务背景、决策依据、硬约束和风格指导；同时要在连续交互中维护当前活跃任务的目标、约束、决定、任务状态和关键背景。原始需求材料保存在维护者本地归档，不随仓库分发；因此，Task 3 不是简单记录“任务完成/未完成”。

当前采用两个互补层，而不是二选一：

```text
Conversation Session / Transcript
  ├─ 完整 User/Assistant 原始记录，永久保留
  └─ model-aware context projection
       ├─ compaction checkpoint（旧内容的可追溯摘要）
       └─ 未压缩的近期完整消息尾部

Task 3 TaskLedger
  ├─ 当前任务的目标、约束、决定、进展、待解问题、背景
  ├─ task-local checkpoint / dependencies / lifecycle
  └─ rebuildable StructuredTaskContext（工作集）
```

会话 Transcript 回答“我们说过什么”，跨任务/会话隔离；Task 3 Working Set 回答“当前任务持续需要什么结构化状态”，可以在会话切换或长对话压缩后继续使用。Task 状态只是工作集中的一个字段，不能替代工作集，也不能替代原始对话。

工作集仍然需要资源边界，因为它和会话摘要、近期消息、检索证据、工具结果及回答共同占用模型输入；但当前生产组合根会根据同一个模型上下文能力动态分配 Task 3 budget，不再把 `20 entries / 5000 chars` 当成通用产品上限。显式传入的 `TaskContextBudget` 仍可用于评测和受控实验。

## 当前产品的会话窗口与压缩

`ConversationContextManager` 不再把“最近 8 条 / 6000 字符”作为产品策略。每次模型调用前，它按以下优先级解析上下文能力：

1. `LLM_CONTEXT_WINDOW_TOKENS` 精确配置；
2. 已知模型能力表；
3. 生产模型固定为 DeepSeek V4 Flash，使用官方 1M-token 能力表。

当投影估算接近可用预算时，系统调用 provider 生成 compaction checkpoint，只把 checkpoint 摘要和未压缩的近期尾部发送给模型。原始 Transcript 从不删除，摘要不是 canonical truth；provider 失败时保留原记录并写入可重试的边界诊断。`/context` 可查看窗口来源、预算、完整消息数量、当前投影和压缩状态。

“不设限”在产品上应理解为“不做人为的 8/6000 截断”，而不是突破模型 API 的真实上下文上限。任意 API 都存在最大输入窗口，超过它只能压缩、分层投影或拒绝继续发送；因此动态预算 + 可追溯压缩比无限拼接更可靠。

Reference survey 支持的工程观察是：长任务 continuity 通常来自 transcript/event history、compaction、persistent instructions、task structures 与 authoritative external state 的组合，而不是统一 six-role working set。`ContinuationCheckpoint` 是 partial engineering/research gap，不是已证明的新颖贡献。

## Phase 3A implementation

`TaskStateRepository` 使用独立、versioned Task3 SQLite store（默认
`task_state.db`），不修改 Task1 的 `PRAGMA user_version=2` 或 canonical
memory tables。它提供 create/get ledger、add/status transition、atomic
supersede、task-local relations、operation-id idempotency、history/active
views 与 latest checkpoint API。

`TaskContextBuilder` 从 ledger + checkpoint + optional Task2 `EvidenceBundle`
重建 `ActiveWorkingContext` 和 `StructuredTaskContext`。selection 是
request-local、deterministic、priority/checkpoint-aware，并有 entry/char/
evidence budgets；省略项和截断会留下 warnings。Task2 evidence 只通过
EvidenceBundle 进入，Task3 不直接查询 Task1 memory tables。

Phase 3A 不实现自然语言 TaskStateUpdater、LLM compaction、Task1 promotion
或 Main Agent。

## Phase 3B：Validated Task State Update Engine

Phase 3B 将事件更新固定为：

```text
TaskEvent → TaskStateInterpreter → TaskStateProposalBatch
          → deterministic validator → atomic repository apply
          → TaskStateUpdateReceipt
```

解释器（包括未来的 LLM provider）只能在事务外提出 typed proposals，不能直接
修改 `TaskLedger`。`TaskStateProposalValidator` 检查 task/event 身份、source
authority、support spans、entry 生命周期、关系端点和 evidence refs；合法 batch
才由 `TaskStateRepository` 在单一事务中应用。空 proposal batch 是合法的 no-op。

`SourceKind` 区分 `USER/AGENT/TOOL/EXTERNAL`，持久化 entry 另保留
`ProposalBasis`（`EXPLICIT/OBSERVED/INFERRED`），因此 agent 建议不会自动成为用户
constraint，tool failure 也不会成为 completed progress。修正通过 `SUPERSEDE`
保留历史，resolution 保留 resolved entry；event identity 提供 replay-safe
idempotency，provider failure 只记录可重试的失败状态且不产生 ledger mutation。

Phase 3B 提供 `propose(event)` 与 `apply(event, batch)` 分离接口，以及
`process_event` convenience API。当前使用 fake/typed deterministic test paths 验证，不绑定
Groq、GPT-OSS 或任何模型；自然语言 interpreter 和自动 Task1 promotion、完整
transcript、compaction、Main Agent 均 deferred。

## Phase 3C：Working Context Maintenance

`WorkingContextPolicy` 使用 deterministic tiered selection：checkpoint 有效引用、
current goal、critical constraints 优先，其次是 active decisions/open questions/
progress，再到 dependencies/background。只有 `ACTIVE` entry 可以成为 current
truth；resolved/superseded/removed entry 不会泄漏到 active sections。selection、
omission、truncation 和 policy version 都在 derived context 中可追踪。

`TaskContextMaintenanceService` 在 Phase3B ledger commit 之后按显式变化、暂停/
checkpoint request、current-step change 等 trigger 刷新 reference-only checkpoint，
并提供 `rebuild_checkpoint` 修复 stale/missing refs。checkpoint 失败不会回滚已提交
ledger；下次可重建。

压力下由 `DeterministicTaskCompactor` 生成 bounded、extractive、lossy、带 source
entry refs 的 `TaskContextCompaction`。它不修改 TaskLedger、不持久化为 canonical
truth；没有 pressure 时不生成 compaction。recent task events 和 external
observations 是 bounded/fresh request-local inputs，Task2 EvidenceBundle 仍只经
冻结 DTO 进入。

## Phase 3D：Natural-Language Integration & Final Freeze

`OpenAICompatibleTaskStateInterpreter` 复用 DeepSeek V4 Flash JSON Output client，使用
runtime `MemoryConfig` 的固定 provider、endpoint、credential env 与 model，输出严格
结构化 `TaskStateProposalBatch`。它不拥有 durable write 能力，仍必须经过 Phase3B
validator 与 repository transaction；真实 smoke 只证明 integration，不代表 semantic
quality production benchmark。

`TaskStateService` 是 Task3 public facade，统一编排 event→proposal→validated apply→
checkpoint maintenance、typed proposal path、context build 与 task lifecycle。Task
status（ACTIVE/PAUSED/COMPLETED/CANCELLED）与 entry status 分离；completed/cancelled
任务保留历史且不进入默认 active context。CLI 的 `/tasks` 会列出全部任务并显示终态，方便用户
找到历史任务；运行时只允许 ACTIVE 任务继续处理普通回合。

## 当前产品边界补充

- 普通对话的长期记忆更新由 Agent 自主选择内部工具；Task 3 工作状态与该工具仍保持独立。
- 默认全局运行数据位于 `~/.agent_data/`，所有 session 的 transcript 与压缩检查点集中在
  `conversations/conversation.db`，不在每个项目工作目录或每个 session 文件夹下重复创建。
- CLI、浏览器 Demo 和 JSONL host 共用同一 `PersonalAgentApp`；JSONL host 的事件存储用于
  客户端观察和重放，不替代 Task 3 ledger 或 Conversation transcript。
