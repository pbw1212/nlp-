# MAPAS 系统架构

MAPAS 将长期事实、当前任务证据、持续任务状态和受控 Agent 执行组织为统一系统，并提供实验性的多角色串行协作。核心算法、可并存的研究机制和实验协议见 [MAPAS 核心算法与技术方案](../research/mapas-core-algorithms.md)。

## 1. 运行入口

- CLI：`code/scripts/personal_agent.py`；JSONL：`code/scripts/personal_agent_app_server.py`。
- 默认 `execution_mode=single`：MainAgent 处理推理与工具执行循环。
- 可选 `execution_mode=multi_fixed`：Planner、Memory、Executor、Verifier 由 Coordinator 串行调度。
- 系统在受限权限下调用模型与工具；目前不提供正式网页产品入口，也不具备生产级多角色并行、分布式一致性或局部自动重规划。

## 2. 信息流与职责

```text
CLI / JSONL
  → Conversation Session（原始消息和动态上下文压缩）
  → MainAgent / AgentRuntime
      ├─ Task 1：长期记忆 Identity / Revision / Provenance
      ├─ Task 2：BM25 / 可选图扩展 / 可配置 Dense 和 RRF
      │         → 语义情境解释 → Applicability → EvidenceBundle
      └─ Task 3：事件提案 → TaskLedger → Checkpoint → Working Context
  → single：AgentLoop → Tool Registry / Executor → Execution Journal
  → multi_fixed：Coordinator
      → Planner → Memory → Executor → Verifier（子任务与整体）
      → Coordination Repository（handoff / artifact / model_calls）
```

该图说明数据所有权和调用关系；Task 1 普通对话的自动写入由 Agent 在适当时候选择内部工具，不是每个 Turn 都强制更新长期记忆。P1 的 Memory 角色当前只选择有效记忆证据，尚未承担全量自动记忆沉淀。

## 3. 权威存储与保护边界

| 数据 | 所有者 | 完整性保证 |
| --- | --- | --- |
| 用户/助手原始对话、历史压缩检查点 | Conversation Repository / SQLite | session 与 task 分离；原始消息可追溯 |
| 长期事实、历史版本、来源 | Task 1 / SQLite | 生命周期类型、版本有效性、来源权威和原子提交 |
| 检索候选和证据包 | Task 2 派生只读数据 | current-safe 召回、适用性、不确定性、冲突和预算 |
| 任务目标、约束、进度及恢复位置 | Task 3 / SQLite | 受验证的事件提案、状态转换、Checkpoint |
| 真实工具调用与回执 | Agent Execution Journal | 工具权限、回执归属、幂等/不确定效果处理 |
| 协作任务、交接、产物、预算 | Coordination Repository / SQLite | DAG、hash、角色输入输出 Schema、验证状态和共享预算 |

模型只负责语义解释、计划和操作提案，不能绕过宿主的身份、权限、版本、来源与事务约束。当前有效检索不应使用撤销或失效的记忆；未经验证的子任务成果不能作为后继的已完成依赖。

## 4. 当前实现与目标扩展

**已实现：** 记忆提炼/修正与生命周期、BM25 和可选 Dense/RRF、情境适用性四态判定、EvidenceBundle、任务台账与恢复检查点、Agent 工具执行，以及实验性 P1 串行验证交接。

**尚待实现并评测：** `EvidenceNeed` 驱动的原始来源升级检索、持久可重建的 dense 索引缓存、完整 `InputFrontier`、失效依赖传播与局部修复、程序性经验分区与写回、受限并行和正式外部基准对照。

进一步的算法说明见 [Task 1](task1-memory-lifecycle.md)、[Task 2](task2-retrieval.md)、[Task 3](task3-working-state.md)、[多 Agent P1](multi-agent-foundation.md)、[运行时集成](system-integration.md) 和 [项目进展](../project-progress.md)。
