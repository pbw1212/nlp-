# Phase 4A：AgentRuntime — 薄系统编排层 (AgentRuntime Phase Report)

状态：**Implemented / Frozen**

本文档记录 Phase 4A 系统的架构设计、实现范围、测试断言与验收状态。

---

## 1. 背景与阶段目标 (Phase Goals)

在前三个阶段中，系统冻结了三大核心领域子系统：
- **Task 1** (`phase-1c-task1-frozen`)：长期记忆模型与生命周期 (SQLite v2)
- **Task 2** (`phase-2g-task2-final`)：上下文感知检索与 EvidenceBundle Pipeline
- **Task 3** (`phase-3d-task3-final`)：任务状态 Ledger、Continuation Checkpoint 与 Working Context (SQLite v4)

Phase 4A 的目标是引入薄的 **AgentRuntime** 系统编排层 (System Orchestration Layer)，完成组件装配 (Composition Root)、请求/Turn 生命周期编排、确定性 Task 路由 (Baseline Router)、P1 错误与维护观察性修复 (Typed Provider Error Preservation & Maintenance Observability)、工具 DTO 接入 (Tool Result Ingestion)，以及生成下游 Main Agent 所需的派生输入包 (`AgentTurnContext`)。

---

## 2. 核心实现与机制 (Key Implementation Details)

1. **Task 2 Facade 隔离与只读边界**：
   - 引入 `MemoryRetrievalFacade` Protocol 和 `DefaultMemoryRetrievalFacade`。
   - Runtime 仅依赖统一检索 Facade，解耦 Task 2 内部多阶段 Pipeline。
   - **Normal Turn Path 绝对不直接访问 Task 1**。

2. **Composition Root 与 DI 解耦**：
   - 提供生产装配工厂 `build_agent_runtime(config: AgentRuntimeConfig)`。
   - `AgentRuntime` 构造函数使用 Constructor Injection，方便测试注入 Fake 对象。
   - 构造过程为纯本地初始化，**无网络副作用 (NO NETWORK SIDE EFFECT during construction)**。

3. **Retry-Safe Task Creation & Turn Correlation**：
   - 当无可用 Active Task 时，Router 依据 `turn_id` 确定性派生 `task_id` (`task_{hash(turn_id)}`)。
   - 保证同一 `turn_id` 重试时不会生成多个 Task 实例。
   - User Event ID 标为 `turn:{turn_id}:user`，Tool Event ID 标为 `tool:{tool_call_id}:result`，具有天然的事件幂等防重保障。

4. **P1 Gaps 修复**：
   - **Typed Provider Error Preservation**：`TaskStateUpdater.propose()` 保留并抛出 `ProviderConfigurationError`, `ProviderRequestError`, `ProviderResponseError`，同时记录日志为 `FAILED`，保证 Ledger 零突变 (`zero ledger mutation`)。
   - **Maintenance Observability**：`TaskStateService` 新增 `process_event_result()` 和 `apply_proposals_result()` 返回 `TaskServiceResult(receipt, maintenance)`，供 Runtime 显式读取 Checkpoint Maintenance 结果与 warnings。

5. **Task2 Current-Context Adapter**：
   - `TaskRetrievalContextAdapter` 将 Task 3 更新后的状态（如新提出的预算 4000）转换为 Task 2 的 query 和 recent context 输入，确保新提的约束可被检索阶段感知。

6. **Tool Result Ingestion**：
   - 支持 `ToolCallResult` (含 `ToolCallStatus` Enum) 转化为 `TaskEvent(source_kind=SourceKind.TOOL)` 写入 Task 3 Ledger，重试具备幂等防护。

---

## 3. 验收与断言 (Acceptance Status)

运行 `code/scripts/agent_runtime_acceptance.py` 的 100% deterministic test 验收测试结果：

```text
============================================================
PHASE 4A AGENT RUNTIME ACCEPTANCE RESULTS
============================================================
No network during runtime construction       : PASS
Runtime uses Task2 public boundary           : PASS
Runtime normal turn direct Task1 access      : NONE
Retry-safe task creation                     : PASS
Stable Turn->Task correlation                : PASS
Stable Turn->TaskEvent identity              : PASS
Updated task state visible to retrieval adapter: PASS
No EvidenceBundle double counting            : PASS
Maintenance result explicit                  : PASS
Typed provider error categories preserved    : PASS
Tool result replay idempotent                : PASS
============================================================
ALL PHASE 4A ACCEPTANCE CHECKS PASSED.
```

单元测试覆盖：`pytest` 全套 166 个测试 100% PASS。

---

## 4. 边界与限制 (Known Limitations)

- **Main Agent Reasoning**：Autonomous Planning, Chain-of-Thought, Action Selection, Final Answer Generator 仍未实现（为 Phase 4B 目标）。
- **Task 4**：Personality/Cognitive preference modeling 显式延迟。
- **Task 3 ➔ Task 1 Promotion**：推理结果写入长期记忆仍然延迟。
- **Transcript Store**：全量对话记录服务延迟。
