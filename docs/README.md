# 项目文档地图

> 项目名称：MAPAS（2026-10-20 起）。2026-10-14 [维护报告](maintenance/2026-10-14/Agent系统维护与升级准备报告.md) 是当日状态的历史记录；当前多 Agent 状态见下方架构与阶段文档。

当前 Git 远端和同步前核验要求以[仓库绑定规则](repository-policy.md)为准。带日期的维护、评测和发布材料保留当时的仓库字段，仅供历史追溯，不作为当前 Git 操作的远端指引。

新增可选多 Agent 基础层：使用与限制见 [当前架构](architecture/multi-agent-foundation.md)，验收及外部数据缺口见 [P0–P1 报告](phases/multi-agent-p0-p1-20261014.md)。默认单 Agent 路径保留。

本目录是项目可维护知识的 source of truth。Protected-local 原始材料不属于 canonical Git，也不是正式规格；所需设计知识必须整理进本目录。

## 文档层级

- `architecture/`：系统当前是什么；随实现同步更新。
- `decisions/`：为什么采用当前设计；Accepted ADR 原则上 append-only，变更通过新 ADR supersede。
- `phases/`：每个阶段的起点、工作、验收、限制和 Git 归档。
- `research/`：外部实现证据、借鉴等级和研究缺口。
- `traceability.md`：需求/问题到 ADR、代码、测试、阶段和 commit/tag 的映射。

## 先读哪些文档

- 项目名称与兼容约定：[ADR-0023](decisions/ADR-0023-project-rename-to-mapas.md)。
- 使用产品：[`cli-user-guide.md`](cli-user-guide.md)；CLI、全局数据目录、工作区、工具、MCP 和两种输出模式均以此为准。
- 理解系统：[`architecture/overview.md`](architecture/overview.md) 与 [`architecture/system-integration.md`](architecture/system-integration.md)。
- 查看当前进度：[`project-progress.md`](project-progress.md)。
- 使用 JSONL host：查看 `code/scripts/personal_agent_app_server.py` 的模块说明和 `system-integration.md` 的协议列表。

`phases/`、`evaluations/` 和带日期的 `research/` 文件是历史记录或研究证据，
其中的旧 Provider、旧测试数量和“当时尚未实现”只描述对应快照，不覆盖当前 main
状态。当前能力判断不得引用历史报告中的旧结论。

Task 2 当前 academic positioning 与 source register 见 `research/task2-academic-novelty-survey.md`；它 supersede 早期宽泛 novelty interpretation，但不改变已实现的 Recall/Applicability 工程边界。

当前运行时失败与日志契约见
`decisions/ADR-0020-failure-truthfulness-and-journal-integrity.md`，对应实施与门禁记录见
`phases/phase-4d-failure-truthfulness-hardening.md`。

## 状态词

- **Implemented**：代码和测试均存在。
- **Simplified baseline**：可运行，但不代表论文或成熟产品的完整复现。
- **Planned**：已有边界，尚无实现。
- **Deferred**：明确不在当前阶段实现。
- **Research hypothesis/gap**：需要实验验证，不能作为已实现能力宣传。
