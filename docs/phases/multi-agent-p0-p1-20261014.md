# 多智能体 P0–P1 实施报告

日期：2026-10-14。用户已批准设计的 P0–P1 首次搭建范围。

## 结果与状态

**P1 最小工程闭环已实现并验证。P0 基线已冻结，外部完整 travel 环境的数据准备尚未完成。** 当前可以通过 CLI/JSONL 显式选择四角色串行协作，完成只读工具任务、来源选择、回执核验、整体目标检查和持久化交接。默认 single 继续保留。

没有执行正式 MemoryArena 任务组，没有多 Agent 相对单 Agent 的质量实验，也没有接入远端 GPU 或训练模型。

## 修改范围

| 路径 | 修改 |
| --- | --- |
| `code/src/memory/collaboration/` | 版本化提案、DAG 校验、Provider 角色、协调器、事务交接及全局调用账本 |
| `agent/main_agent.py` | 可选 prepared_runner；用户 Runtime 处理一次；显式记忆与确认保留原路 |
| `app/console.py` | single/multi_fixed 装配、turn 预算范围及真实 session 身份 |
| 两个现有 CLI/JSONL 脚本 | 增加 execution-mode 参数，默认 single |
| `collaboration_smoke.py` | 临时数据库/工作区的限额真实 API 与外部工具 smoke |
| `memoryarena_resource_probe.py` | 官方 checkout、CSV hash、真实 CitySearch 与公开任务数据访问记录 |
| `test_collaboration.py` | 22 项协作、故障、重放、预算及原显式记忆兼容性测试 |

Task1、Task3、对话和工具 journal 原格式保持不变。未删除用户数据，未恢复 UI，真实 smoke 没有使用个人正式运行数据库。

## 工程与真实 API 验证

原 HEAD `425bb9da3764ae4a54b473d036d3484a65f8b0bc` 基线：410 passed、1 skipped。新增协作专项：22 passed。最终全量与静态/打包检查以 [verification.json](../evaluations/multi-agent-p0-p1-20261014/verification.json) 为准；测试 XML 作为复现证据一并保存。

最终全量结果：**432 passed、1 skipped**，跳过 Windows 未安装的可选 readline 专用测试。新增模块/脚本/测试的 Ruff 与 Black 检查通过；wheel 构建通过，六个协作模块均入包，包内源文件与最终工作区逐字节一致。

真实 DeepSeek smoke 使用 12 次调用上限，实际 **9 次调用**，Provider 报告 input+output tokens 合计 **17034**。流程包含原任务绑定、原 Task3 解释、Planner、Memory、Executor 工具决策、工具后 Task3 解释、Executor 答复、子任务 Verifier 和整体 Verifier。

输入：通过官方离线 CitySearch 查询 California，按原顺序列出前三个城市，仅用一个子任务和该工具。

预期与实际：San Diego、Redding、Sacramento；返回 SUCCESS，原工具回执 SUCCEEDED，USER 消息仅一条。完整结果见 [live-smoke.json](../evaluations/multi-agent-p0-p1-20261014/live-smoke.json)。数据是离线环境事实，不是实时旅游推荐；本实验是工程集成测试，不是官方 benchmark 得分。

调用使用请求模型别名 `deepseek-v4-flash`；当前客户端没有保留服务端实际模型标识，不能据此声称模型快照永久固定。后续正式实验需要补齐该观测。

## 关键验证点

- 四角色都实际经过各自 Provider 契约，Executor 使用真实 calculator/官方 CitySearch，而非只有完成文字。
- 伪造记忆、伪造回执、不存在或越权工具、循环/重复子任务 ID 会失败。
- 子任务通过后仍需整体目标核验；不完整目标不会被标为成功。
- 来源 revision/保留状态校验；撤销记忆不能经完成缓存重放返回旧答案。
- 受验证的前置成果可交给依赖后继；未验证成果不能升级。
- 已完成 turn 重放不新增模型请求、工具效果或重复对话消息。
- 调用前预约在重启后保留；共享调用上限包括 Runtime；缺失 usage、失败与迟到响应不会清空已花预算。
- 自然语言显式记忆请求仍取得真实 Task1 写入，未转交协作工作者或把事实写成 Task3 进度。

## 外部资源与未完成项

MemoryArena 官方 revision：`6cd9de14b71915e39ac742a20dc33785e14b6aab`。公开 travel 数据预览可访问，270 组；预览组 id=1，包含 8 个问题。评分答案没有给任何 Agent。

五类已有 CSV 已记录 hash，官方 CitySearch 已原位实际运行。航班文件不在 Git checkout 中，官方 Google Drive 文件约 291 MB。尝试下载后在 300 秒资源上限停止，获得约 22 MB 的不完整前缀；它已改名 `.partial`，放在忽略目录，**未作为有效 flight 数据或工具输入**。详见 [资源记录](../evaluations/multi-agent-p0-p1-20261014/memoryarena-resources.json)、[下载尝试](../evaluations/multi-agent-p0-p1-20261014/flight-download.json)。因此完整 ToolExecutor/整组 travel 评测未完成，不能标成正式环境验收通过。

CoMemBench 官方项目的公开 HuggingFace 数据仓库访问成功，revision 为 `d1cef0fb70ada0ddf9b80d23aa30ed86ee56e016`，包含任务/上下文/语料及 schema；官方说明评分 oracle 和污染计划保留在评测端。本轮没有验证可运行评分器；固定 revision README 下载另遇到网络超时。见 [补充访问记录](../evaluations/multi-agent-p0-p1-20261014/additional-resource-probes.json)。公开数据可读不等于已经能产生官方成绩。

## 后续边界

P2：原文来源解析、可靠角色化记忆提案/提交、经验分区、dense 派生缓存；同时补齐 flight 数据并运行完整外部任务的最小组。P3：完整输入派生版本检查、依赖失效、局部修复和只读并行。P4：预算匹配的强单 Agent 对照和消融。

本地实现与正式结果尚未推送 GitHub。具体代码/环境 hash 见 verification manifest；后续提交时只选择本阶段文件，不包含 .runtime、私密配置、权重、原始私人会话或不完整数据。
