> 本文主要说明单 Agent Runtime 与 Task 1/2/3 的集成契约；系统当前架构见 [系统总览](overview.md)，四角色链路见 [多 Agent P1](multi-agent-foundation.md)，完整算法与研究方向见 [核心算法总述](../research/mapas-core-algorithms.md)。维护报告记录对应历史时点。

# 系统集成与 AgentRuntime 架构 (System Integration & Runtime Architecture)

> 本文保留 Runtime 的核心契约描述；四角色可选串行协作由 CollaborationCoordinator 组织。历史阶段名仅用于归档定位。

本文档定义 Task 1 (Long-Term Memory)、Task 2 (Context-Aware Retrieval)、Task 3 (Task State & Working Context)、MainAgent、工具运行时和 **AgentRuntime** 的集成关系、所有权边界、生命周期流程与错误/事务语义。CLI 和 JSONL host 共用同一套 `PersonalAgentApp` 运行时，不各自维护 Agent 状态。

---

## 1. 模块所有权与职责分层 (Ownership & Layering)

```text
[ Main Agent Layer (Phase 4B - Reasoning & Planning) ]
       ↓ consumes AgentTurnContext
[ AgentRuntime Layer (system orchestration) ]
       ├── ProviderTaskBindingResolver (per-turn semantic proposal)
       ├── Task binding validator (ID/status/lifecycle only)
       ├── TaskRetrievalContextAdapter (Task3 updated state → Task2 query/context)
       └── Turn & Tool Result Orchestration Mechanics
       ↓                       ↓                           ↓
[ Task 3: Task State ]   [ Task 2: Retrieval ]       [ Task 1: Long-Term Memory ]
  TaskLedger              MemoryRetrievalFacade        MemoryItem / MemoryRevision
  ContinuationCheckpoint    └── Semantic & Evidence    MemoryReadRepository
  ActiveWorkingContext          Pipelines                Ingestion Journal
  (task_state.db v5)                                   (long_term_memory/memory.db v2)
```

### 关键所有权划分：
- **Task 1**：拥有长期事实的 Identity、Semantic Revision、Validity、Retention 与 Provenance。单独立 `memory_store.db`（schema v2）。
- **Task 2**：只读 Task 1 (`MemoryReadRepository.list_current()`)，派生并返回 `EvidenceBundle`。通过 `MemoryRetrievalFacade` 向 Runtime 暴露统一公共检索接口。不写入 Task 1，不持存 Task 2 状态。
- **Task 3**：拥有 Task-scoped 独立持久化 Ledger 与 Continuation Checkpoint（`task_state.db` schema v5）。提供事件更新、提案验证、focus/next_action/world-state 检查点与工作上下文提炼。
- **AgentRuntime**：薄的系统编排层。拥有依赖装配 (Composition Root)、Task 路由 (Task Router)、Turn 生命周期 (Turn Lifecycle) 以及工具调用结果入库 Plumbing。
  - **禁止直接访问 Task 1**：Normal Turn 逻辑绝不直接 query Task 1 或持有 Task 1 数据库对象。
  - **不实现 Main Agent Reasoning**：不包含 LLM Planner、Chain-of-Thought、Auto Reasoning Loop 或 Prompt Template 拼装。

---

## 2. Turn 生命周期 (User Turn Lifecycle Flow)

```text
TurnRequest(turn_id, user_input, task_id?)
  │
  ├── 1. Provider Task Binding Proposal (every user turn)
  │      ├── EXISTING ➔ copy one supplied ACTIVE task_id
  │      ├── NEW ➔ Provider supplies a title; Runtime creates a new Task 3 ledger
  │      └── UNBOUND ➔ no Task 3 ledger/checkpoint/retrieval mutation for this turn
  │
  ├── 2. Deterministic Binding Validation & Task 3 Mutation
  │      ├── only validate IDs, ACTIVE lifecycle, provenance and idempotent identity
  │      ├── EXISTING/NEW ➔ TaskStateService.process_event_result
  │      └── UNBOUND ➔ typed auxiliary analysis may classify memory intent, but no Task 3 proposal is applied
  │      ├── 构造 TaskEvent(event_id=f"turn:{turn_id}:user", source_kind=USER)
  │      ├── Interpreter 提出 Proposals ➔ Validator 校验 ➔ 原子事务写入
  │      └── 若捕获 ProviderError (429/Auth/Timeout) ➔ 记录 FAILED，零 Ledger 突变，透传 sanitized TurnErrorInfo
  │
  ├── 3. Maintenance Observability (TaskContextMaintenanceService)
  │      └── 获取 refresh_after_update 结果；若有 warning 标为 WARNING，Turn 继续
  │
  ├── 4. Task 2 Retrieval (TaskRetrievalContextAdapter + MemoryRetrievalFacade)
  │      ├── 从更新后的 Task 3 状态中提炼 current query 与 active constraints/goals
  │      ├── 调用 facade.retrieve_evidence() ➔ 得到 EvidenceBundle
  │      └── 若 Task 2 抛出 ProviderError ➔ 标记 RETRIEVAL=FAILED；Task 3 仍保持已提交状态（不回滚！）
  │
  ├── 5. Task 3 Context Build (TaskContextBuilder.build)
  │      └── 组合 Ledger + Checkpoint + EvidenceBundle + ExternalObservations ➔ ActiveWorkingContext
  │
  └── 6. AgentTurnContext Assembly
         └── 组装 derived request-local AgentTurnContext 并返回 TurnExecutionResult
```

Conversation Session 与 Task 不是同一身份：CLI 启动和 `/new-session` 都先创建一个仅存在于
当前进程内、尚未落库且未绑定 Task 的 Session。首个真实用户 turn 到达后，Session 才写入集中式
`conversations/conversation.db`。只有显式 `/resume` 才恢复历史 Session 及其绑定的 Task；启动新终端
不会调用“最新 Session”恢复逻辑。

---

## 3. 错误传播、重试契约与部分成功语义 (Error Semantics, Retryability & Partial Success)

### 3.1 异常隔离与安全投影 (Safe Error Projection Contract)
- **Task3 Service 边界**：在 Python 进程内存内保持原始 typed `ProviderError` 可识别性。
- **Runtime 边界**：所有 provider 异常通过 `ProviderError.to_safe_projection()` 转化为结构化的安全 `TurnErrorInfo`（包含 `code`, `category`, `provider`, `operation`, `status_code`, `safe_message`, `retryable`），绝对禁止 raw exception 文本泄漏。
- **数据库持久化**：写入 `task_events.error_text` 数据库中的日志同样经过脱敏清洗。
- **通用边界诊断**：`core.diagnostics.log_boundary_failure()` 只记录 event、exception class、
  非敏感运行标识与 Provider 安全字段；不记录 raw exception、prompt、response body、token 或
  credential。CLI 入口把这些记录写入有界 rotating `agent.log`。
- **真实性不变量**：未知/损坏状态必须失败关闭；禁止使用占位 ID、虚构空参数、零计数或默认
  成功文本掩盖异常。非阻断的部分失败必须进入 warning 和日志。

### 3.2 显式重试判定矩阵 (Provider Retryability Matrix)
- `ProviderConfigurationError` ➔ `retryable = False`（配置/凭据错误）
- `ProviderResponseError` ➔ `retryable = False`（格式/解析错误）
- `ProviderRequestError`：
  - 非暂态客户端错误 (HTTP 400, 401, 403, 404) ➔ `retryable = False`
  - 暂态速率限制或服务端错误 (HTTP 408, 429, 500, 502, 503, 504) ➔ `retryable = True`
  - 底层网络超时/连接中断 (`status_code is None`) ➔ `retryable = True`

### 3.3 显式 Turn 重试契约 (Turn Retry Contract)
- Provider 每个 turn 都给出 `EXISTING`、`NEW` 或 `UNBOUND` 提议；Runtime 不用唯一旧 Task、Task 数量或关键词替 Provider 做语义选择。
- `EXISTING` 只能引用当前提供的 ACTIVE Task；`NEW` 的 ID 由 Runtime 根据 turn identity 稳定生成，标题仍来自 Provider 提议；`UNBOUND` 不创建隐藏 Task。
- Provider 提议失败、ID 不存在或 Task 非 ACTIVE 时，Runtime fail-closed 返回结构化错误，不执行 Task 3 写入。

### 3.4 阶段运行语义矩阵
| 场景 | Task3 Durable Ledger | Task2 Retrieval | Turn 结果 | 说明 |
|---|---|---|---|---|
| **Task3 Provider 失败** (429/Auth/Timeout) | 零变更 (`FAILED`) | 未触发 (`SKIPPED`) | `TurnExecutionResult(error_info)` | `ProviderError` 分类透传；绝对不破坏 Ledger 强安全 |
| **Task3 成功 + Maintenance Warning** | 已提交 (`APPLIED`) | 正常完成 (`SUCCESS`) | `TurnExecutionResult(context)` | `stage_traces` 中包含 `MAINTENANCE=WARNING` |
| **Task3 成功 + Task2 Provider 失败** | 已提交 (`APPLIED`) | 失败 (`FAILED`) | `TurnExecutionResult(error_info)` | 部分成功：Task3 不回滚，`error_info.stage = RETRIEVAL` |
| **Provider 选择 UNBOUND** | 零 Task3 变更 | 未触发 | 正常进入 Agent | Task 3 上下文为 request-local unbound context；Task 1 仍可独立处理 |
| **Provider 选择无效 Task** | 零变更 | 未触发 | `TurnExecutionResult(error_info)` | 报 `TASK_LIFECYCLE_ERROR` 或 `TASK_NOT_FOUND` |

### 3.5 宿主协议与事件重放

`code/scripts/personal_agent_app_server.py` 是面向浏览器或其他客户端的 JSONL
host。客户端先发送 `initialize`，随后可使用 `thread/start|resume|list|read`、
`turn/start|resume`、`events/read`、`status/read`、`context/read`、
`tools/list`、`approvals/list` 和 `mcp/status`。每行一个 JSON 对象，响应和事件
立即 flush；事件由 `execution/agent_events.db` 持久化，客户端可用 sequence 游标
断线续读。当前 host 是同步适配器，`turn/interrupt` 明确返回不支持。

事件流不是新的事实源：工具执行事实仍由 Journal，任务状态由 Task 3，长期记忆
由 Task 1，会话原文由 Conversation Repository 分别负责。事件用于客户端观察、
重放和验收。

### 3.6 Agent 自主长期记忆更新

每轮普通回合都向 Agent 暴露 `update_long_term_memory` 内部工具。Agent 选择调用后，
当前用户原文进入 Task 1 的持久化 ingestion journal，并同步运行批量梦境管道；
Provider 失败不会清除待处理原文，后续更新可重试。系统不再使用默认 10 轮、12 小时
或隐藏后台 worker 触发长期记忆。

---

## 4. 工具调用集成 (Tool Result Ingestion)

```text
ToolCallResult(tool_call_id, tool_name, status, result_content | error_message)
  ↓
AgentRuntime.process_tool_result(result, task_id)
  ├── 若当前 turn 为 UNBOUND ➔ 仅保留工具回执，不写入 Task 3
  ├── 构造 TaskEvent(event_id=f"tool:{tool_call_id}:result", source_kind=TOOL,
  │                  structured_payload={success, status, tool_name})
  ├── Task 3 process_event_result() 写入 Ledger
  └── 具备 Event Replay 幂等性：同一 tool_call_id 重试不产生额外 Ledger 突变
```

---

## 5. Main Agent 推理与工具循环架构 (Main Agent Reasoning & Execution Architecture)

- **Main Agent (`code/src/memory/agent/`)**：负责顶层推理 (`AgentReasoner`)、Prompt 信任边界构建 (`PromptComposer`)、工具选择与确认策略 (`ToolRegistry`)、有限步状态机 (`AgentLoopController`) 以及顶层门面 (`MainAgent.run()`)。
- **持久化隔离与 Schema Version 4 (`agent_journal.db` v4)**：Main Agent 拥有独立的 `AgentExecutionJournal`（schema v4），提供 `agent_runs`、`pending_actions`（人类确认）与 `tool_execution_receipts`（原子 `IMMEDIATE` 声明、执行策略 Snapshot 与 `runtime_reconciled` 对账状态）。独立于 Task 1 存储与 Task 3 状态图。
- **Turn 身份区分 (`root_turn_id` vs `request_turn_id`)**：`root_turn_id` 标识 Agent Run 根交互 Turn；`request_turn_id` 标识当前确认消息 Turn。确认恢复时验证持久 Run 身份并保持 `root_turn_id` 稳定，不重新注册覆盖已有 Agent Run。
- **工具确认与副作用安全**：所有 `ToolEffect.SIDE_EFFECT` 工具强制进入 `AWAITING_CONFIRMATION` 并持久化至 `PendingAction`。`ToolEffect` 必须显式声明（无默认 fallback），重复工具注册与非法 `parameters_schema` 在启动注册阶段将被拒绝。参数校验使用 `jsonschema`。
- **Pre-Reasoner 确定性 Runtime 对账引擎**：进程启动/重试时，Main Agent 在调用 `AgentReasoner` 之前优先对 `runtime_reconciled = 0` 的终端回执执行 `AgentRuntime.process_tool_result()` 对账。若 Runtime 对账失败，立即暂停推理并返回安全错误，物理工具零二次重复执行。对账成功后标记 `runtime_reconciled = 1`。
- **执行策略 Snapshot 与崩溃恢复**：`claim_execution()` 原子事务中冻结 `arguments_json`、`tool_effect` 与 `idempotency_support` 策略 Snapshot。崩溃恢复时不依赖当前 Registry 飘移配置。针对 `IdempotencySupport.NONE` 且尚无回执的崩溃，恢复时返回 `EXECUTION_STATUS_UNKNOWN` 阻止无条件二次调度。
- **零 Task 3 AGENT 事件污染**：Main Agent 产生的中间草稿、推理过程与 Final Response 不直接向 Task 3 写入 AGENT 事项；Task 3 事项仅通过 Runtime 的 USER 与 TOOL 权威路径产生。
