# MAPAS 项目进展

更新日期：2026-11-05。当前默认模式为 `single`，`multi_fixed` 提供串行、有界的多角色协作。
本页描述当前实现；带日期的阶段报告与早期联调记录只描述当时版本。

## 当前功能

| 方向 | 当前实现 |
| --- | --- |
| Task 1 长期事实 | 生命周期提案、不可变 revision、provenance、原子写入、回收与恢复 |
| Task 2 检索 | BM25、可选 Dense/RRF、适用性与 EvidenceBundle；协作中按需求追加检索和原文窗口读取 |
| Task 3 长程任务 | 任务台账、约束/目标、工作上下文、检查点、任务与对话输入版本核验 |
| 协作执行 | 最多三个子任务的 DAG、只读工具、独立验证、持久交接与共享模型调用预算 |
| 证据不足处理 | EvidenceSearch 结构化提案驱动最多两轮补查；保留原需求、证据来源和未解决缺口 |
| 来源版本 | 原消息/记忆 revision/回执完整性；read_file 绑定原定位路径、实际路径和完整字节哈希，恢复时重新检查 |
| 局部修复 | 依赖闭包失效、独立子运行、快照恢复、锁、持久预算；未变化且可核验的独立根节点可复用 |
| 自动处理 | 轮次前后、会话恢复或显式 drain 处理修复、整个任务验收和经验作业；同源失效事件合并执行 |
| 任务级闭环 | TaskReview 全目标覆盖、原子完成封存、正常结束后的来源资格、持久经验 outbox 与恢复投递 |
| 程序性经验 | 与用户事实分区；候选、独立审核、适用性筛选、反馈、隔离、撤销与新修订；来源失效时停止复用 |
| 产品入口 | CLI 和 JSONL 的修复及经验操作，保留真实回执和状态 |
| 运行与调度审计 | 来源结果与验收入队标记原子提交、完成后普通异常恢复、锁冲突跳过、取消保护、终态不可变、跨会话查询隔离与当前有效性诊断 |
| 下游验证证据 | supporting_evidence v1 将真实回执、原文窗口和选中记忆传至声明依赖与整体验证；持久化交接支持重启复用 |
| 运行边界处理 | JSONL 解析限制、启动 I/O 故障、工具返回身份/元数据校验、MCP 握手与写入时限、长会话最近作业窗口 |

## 当前验收

离线验收使用受控测试 Provider、真实文件和临时 SQLite，不消耗真实 API。
覆盖绑定任务的双文件读取与合并、修改一个文件后关闭/重开应用、只重做受影响分支、
第二次修改另一文件、重复恢复幂等、同时变更的事件合并、来源删除/权限收回、
长原文窗口补查、按需求扩大召回、经验准入/不确定性/撤销/反馈和修订。
任务级自动闭环的审计前基线为 **542 passed、1 skipped**，见 [基线验收记录](evaluations/task-lifecycle-20261031.json)。跳过项为 Windows 可选 readline。前一功能版本为 520 passed、1 skipped。
2026-10-31 的全系统检查、故障修复及当时最终结果见 [审计报告](maintenance/system-audit-20261031.md) 与
[审计验收记录](evaluations/system-audit-20261031.json)；原有数字不覆盖本次增量。
当时全量回归为 **560 passed、1 skipped**；新增 18 项故障注入与边界检查。
当时产品场景 14/14、50 步扰动及真实 Provider 六项闭环断言均通过。
2026-11-05 修复下游 Verifier 只收到成果 ID/答案而缺原始证据的问题；新增 11 项边界回归，
该次全量为 **571 passed、1 skipped**。真实 API 的 5 项原始证据交接断言通过（15 次模型调用），
具体协议、复现与证据见 [修复报告](maintenance/verifier-evidence-20261105.md) 和
[验收记录](evaluations/verifier-evidence-20261105.json)。不将该图执行专项联调描述为自动任务验收/经验学习的重新联调。
随后进行运行边界复检：新增 23 项故障注入，当前全量为 **594 passed、1 skipped**；
扩展产品场景 **32/32**、50 步扰动及最终真实 API **5/5** 均通过。
新增问题、修复和范围见 [运行复检报告](maintenance/runtime-reaudit-20261105.md) 与
[验收快照](evaluations/runtime-reaudit-20261105.json)。
前一功能版本的代码快照保留在 [功能验收记录](evaluations/functional-closeout-20261031.json)，不覆盖本次增量。

真实 Provider 联调与离线回归分开保存。新的双文件/修复/经验联调见
[真实联调记录](evaluations/live-source-repair-experience-20261031.json)。
只有该记录中的 `engineering_assertions_passed=true` 才代表整条联调通过；
这不是系统性能测评。请求模型身份与服务端实际返回模型身份不能混同。
长原文尾部窗口的真实 Provider 联调另见 [证据补查联调](evaluations/live-evidence-expansion-20261031.json)。
失败尝试作为独立 attempt 记录保留，未并入成功结论。

2026-10-28/29 的单节点真实 API 历史证据保留在 `evaluations/live-*.json`，
包含任务约束、成果撤销、对话/回执变化、修复快照和队列恢复。其测试数字不作为当前版本结果。

任务级闭环实现与恢复边界见 [任务级自动闭环](architecture/task-lifecycle.md)。
真实连续任务联调见 [记录](evaluations/live-task-lifecycle-20261031.json)，六项闭环断言均通过：等待真实批准、重启修复、自动封存与经验审核、重复处理无新调用、新任务复用，以及撤销后阻断旧答案。未通过的尝试单独保留；后续任务的新经验提案被门禁拒绝时，不会伪造保存成功。
系统审计后的最终代码再次通过同六项真实闭环断言，见
[审计真实联调](evaluations/live-system-audit-20261031.json)。此次两次失败尝试单列保存；
最终一次结构化响应缺字段在原预算内纠正，未增加重试次数或放宽来源门禁。

## 明确边界

- 目标、约束或规划阶段暴露的来源变化仍使整图失效，要求新请求。
- 外部当前状态核验现支持 `read_file`；网页、天气、MCP 等没有适配器的观察保持 UNKNOWN，不能据旧回执授权复用。
- 当前成果复用只开放独立根节点。带依赖的其他节点即使仍有效也保守重跑；不承诺任意 DAG 或任意外部工具复用。
- 来源检查与多数据库写入没有分布式原子保证；恢复机制不是常驻后台服务。
- 任务完成后自动排队提炼和审核经验，显式 learn 保留；已准入经验的筛选、来源重验及消费结果反馈接入协作流程。没有策略训练或强化学习。
- 旧记录缺少完整版本/快照或协议不兼容时失败关闭，不猜测迁移和复原。
- 仍暂缓正式性能对照。没有 MemoryArena 完整依赖组得分、同预算单多 Agent 优劣结论、只读工作者并行或分布式多用户能力。

## 文档入口

- [核心算法与技术方案](research/mapas-core-algorithms.md)
- [协作架构](architecture/multi-agent-foundation.md)
- [需求驱动证据与原文窗口](architecture/source-backed-evidence.md)
- [来源版本与修复](architecture/repair-execution.md)
- [程序性经验生命周期](architecture/procedural-experience.md)
- [任务级自动闭环](architecture/task-lifecycle.md)
- [系统运行与调度审计](maintenance/system-audit-20261031.md)
- [CLI/JSONL 使用说明](cli-user-guide.md)

更早的进度保留在 [2026-09-05 历史快照](history/project-progress-2026-09-05.md)。
