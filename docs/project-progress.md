# MAPAS 项目进展

## 当前实现

MAPAS 已完成长期记忆生命周期（Task 1）、上下文感知证据检索（Task 2）、任务状态与工作上下文维护（Task 3），并通过 Conversation Session、AgentRuntime、MainAgent 和 Tool Journal 形成可运行的持续智能体系统。另提供可选的四角色串行协作模式 `multi_fixed`；默认模式为 `single`。

| 技术方向 | 当前功能 | 下一阶段工作 |
| --- | --- | --- |
| 长期记忆 | 语义生命周期提案、不可变 Revision、Provenance、回收/恢复、原子写入 | 经验记忆分区与派生索引失效 |
| 检索证据 | 默认 BM25 与可选图扩展；可配置 Dense/RRF；情境适用性和 EvidenceBundle | 任务证据需求驱动的原文核验、向量缓存 |
| 长程任务状态 | 任务事件解释与校验、TaskLedger、ContinuationCheckpoint、WorkingContext | 与协作 DAG 的状态依赖集成 |
| Agent 执行 | CLI/JSONL、工具执行与真实回执、持久化会话和执行恢复 | 更大规模长程任务端到端实验 |
| 多智能体协作 P1 | Planner/Memory/Executor/Verifier 串行链、有限 DAG、核验交接、共享预算 | 完整版本前沿、依赖失效、局部修复、选择性经验写回、可选只读并行 |
| 正式基准 | 工程回归、有限真实 Provider 工具 Smoke | MemoryArena 完整依赖组、强单 Agent 对照和消融 |

完整算法、论文对照与下一阶段研究机制统一见 [MAPAS 核心算法与技术方案](research/mapas-core-algorithms.md)。分层证据检索、依赖感知执行记忆、版本化协作恢复与选择性经验演化是同一套系统中的互补机制。

## 验证状态

2026-10-14 [P0–P1 实施报告](phases/multi-agent-p0-p1-20261014.md)记录的工程结果为 **432 passed、1 skipped**，并验证了一次真实 Provider 只读工具执行闭环。该数字对应当日版本，尚未在当前 GitHub HEAD 上重新运行全量测试。

目前尚无完整 MemoryArena 整组任务结果、同预算强单 Agent 对照、有效的多 Agent 性能提升结论或自动局部修复测量。四角色 P1 采用串行只读执行，不具备分布式并行或经验全自动写入。

## 文档入口

- [核心算法与技术方案](research/mapas-core-algorithms.md)
- [当前系统架构](architecture/overview.md)
- [四角色协作基础层](architecture/multi-agent-foundation.md)
- [原研究设计与实施阶段记录](research/mapas-multi-agent-plan-2026-10-14.md)
- [CLI/JSONL 使用说明](cli-user-guide.md)

早期技术进展保留为 [2026-09-05 历史快照](history/project-progress-2026-09-05.md)。快照内的旧运行入口、测试数量及阶段结论仅描述其产生时的版本。
