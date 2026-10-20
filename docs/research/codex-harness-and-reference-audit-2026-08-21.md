# Codex Harness 与 reference_repos 审计（2026-08-21）

> 历史审计记录。下方 Linux 本地路径描述当时的核查环境，不是 MAPAS 当前工作目录；第三方源码快照不随当前仓库分发。

> **历史审计快照**：本文的 Groq、旧测试数量和“尚未实现”只描述 2026-08-21
> 的代码状态。当前 Codex 机制集成以
> [`codex-integration-gap-matrix-2026-08-31.md`](codex-integration-gap-matrix-2026-08-31.md)
> 和当前 main 为准。

## 本次同步

已从 `https://github.com/openai/codex.git` 克隆当天最新主线快照：

- 本地路径：`/home/china/ysaaaa/agent/reference_repos/codex-latest-2026-08-21`
- HEAD：`2151d3a5b78ca93128496b26333bc30187385a5f`
- 提交时间：2026-08-21 03:40:40 UTC
- 提交主题：`Reset registry retries when refreshing Noise bundles (#39852)`

参考依据：OpenAI 对 Codex harness/App Server 的介绍在[官方文章](https://openai.com/index/unlocking-the-codex-harness/)中说明，开源实现位于 [`openai/codex`](https://github.com/openai/codex)，App Server 负责把线程、turn、事件流和客户端连接起来。

## reference_repos 总览

| 仓库 | 可复用机制 | 对本产品的结论 |
|---|---|---|
| `codex`、`codex-latest-2026-08-21` | App Server JSONL/RPC、thread/resume、结构化 turn/item 事件、model usage、审批/沙箱、MCP、远端 compaction、tool output overflow 重写、compaction hooks/trace | 这是当前最重要的 harness 参考。必须吸收事件化会话、usage 驱动窗口、overflow recovery、工具结果成对保留；沙箱/App Server 属于编程代理阶段能力。 |
| `openclaw` | 自动 compaction、完整 transcript 落盘、tool call/result 成对切分、overflow 后 compaction+retry、compaction 前 memory flush、pruning、`/context` 诊断、session rotation、工具策略 | 直接对应本产品的上下文与长期记忆质量问题；memory flush、overflow retry、上下文诊断优先级高。 |
| `claw-code` | Session/Tool/Task runtime、summary compression、session 持久化、权限/路径范围、任务注册和工具池 | 可作为 Python/CLI 结构参考；它是移植/兼容实现，不把其“镜像了多少文件”当成能力证明。 |
| `claude-code` | 官方仓库主要是发布、插件、脚本、文档与 CLI 分发入口；核心运行时并未以可审计源码形式完整暴露 | 不能据此声称已经复现 Claude Code 内核；只能参考公开产品行为和插件接口。 |
| `claude-dist` | npm 分发包/归档，不是可维护的核心源码树 | 不纳入架构复制依据。 |
| `claude-cookbooks` | automatic context compaction、session memory、memory tool、agentic search/RAG、parallel tools、programmatic tool calling、tool search | 是机制示例和评测思路，不是生产 harness；适合补测试矩阵。 |
| `mem0`、`mem0-phase0b-d70cc00` | LLM 驱动 ADD/UPDATE/DELETE/NOOP、稳定 memory id、history、scope/session、检索/rerank/evaluation | 证明长期记忆不能把所有新内容都 ADD；本次已把自动批量记忆改成 Provider 提议生命周期操作。 |
| `letta` | blocks、message buffer、archival memory、summary agent、context-window calculator、compaction settings、tool-rule solver、parallel/sleeptime agent | blocks/archival 是另一种产品形态；需要借鉴其“工作区状态与对话缓冲分离”和 token accounting，不必引入完整多 agent。 |
| `graphiti` | episode 节点、实体/关系、source provenance、valid/invalid temporal edges、chunking、dedup、hybrid search/RRF、批处理提取 | 当前 Task 1 有 revision/provenance，仍缺时间有效性图和来源级关系；若要成为个人知识库，这是后续 RAG 增强方向。 |
| `llm_wiki` | 文件 watcher/摄取、chunk/index、MCP knowledge-base tools、持久化知识库、学习进度跨 session 恢复 | 支持“外部资料进入个人 RAG”的产品方向；不是当前对话记忆的替代品。 |
| `llmwiki` | 持久 wiki/知识库、文档/笔记摄取、MCP 读写工具、来源与内容管理 | 与 `llm_wiki` 方向相近，重点可复用资料摄取与 MCP 边界，不纳入当前 Task 3 核心。 |
| `rpeval` | 数据/评测/benchmark 管线 | 它是评测基础设施，不是运行时能力；应借鉴其“用可复现实验验证质量”的方式。 |

## 该审计时点产品已经具备的能力

- 完整对话 transcript 持久化；上下文窗口按模型能力计算，不再使用固定“8 条/6000 字符”产品限制。
- Provider 摘要 checkpoint + 最近完整 turn 尾部；这次已改成按消息/turn 分块摘要，避免 head/tail 截断吞掉中间更正。
- Task 3 保留任务工作集：目标、约束、决定、进展、开放问题与背景；它不是简单的“任务是否完成”字段。
- Task 1 批量后台记忆默认开启；普通轮次先进入 durable ingestion buffer，再由 NEMORI 批量提炼。
- 混合检索、EvidenceBundle、provenance/revision/lifecycle、显式 `/remember`。
- Agent tool registry、只读工具、高风险操作确认、真实 `web_search`/`web_fetch`，本次增加真实 `weather` 工具。
- session/new-session/resume、`/context`、`/tools` 等 CLI 诊断面。

## 本次已修复

### 1. 自动长期记忆不再由本地 novelty 阈值决定

删除 `prediction_error_threshold`。Provider 负责判断是否有持久价值以及生命周期动作；本地只负责 proposal 的结构、枚举、confidence、目标 identity、provenance 和原子事务校验。

自动批量路径现在支持 Provider 提议：

```text
NOOP / ADD / UPDATE / CORRECT
```

当模型返回 Groq GPT-OSS 常见的 `action/memory_id/content` 等价字段时，只在协议边界做字段归一化，不重新解释其语义。

### 2. 上下文压缩不再用 head/tail 截断整个摘要输入

旧实现会把过长摘要请求裁成两端，导致中间的更正、决定或开放问题消失。现在改为：

```text
完整旧 transcript
  -> 按模型预算切成连续段
  -> 每段 Provider 摘要
  -> Provider 合并段摘要与旧 checkpoint
  -> 保存新 checkpoint；原 transcript 永久保留
```

最近上下文也改为按完整 turn 选择，避免只留下 assistant 而丢掉对应 user。

### 3. 该审计时点的 Groq 模型能力与专用外部工具

该审计时点 `.env.local` 的真实配置为 Groq `openai/gpt-oss-120b`。Groq 官方模型页标注该模型上下文窗口为 131,072 tokens，因此加入了模型能力表；自定义网关仍可用 `LLM_CONTEXT_WINDOW_TOKENS` 覆盖。

增加 `weather` 只读工具，调用 Open-Meteo 地理编码与预报接口，结果包含地点、当前天气、短期预报、来源和 `fetched_at`。

## 真实 Provider 实验结果（历史）

### 上下文压缩

- 输入：36 条 user/assistant 消息，包含中间更正、开放问题、opaque id 和最新尾部。
- Provider：Groq `openai/gpt-oss-120b`。
- 结果：4 次 Groq 请求；28 条消息进入摘要，8 条最近消息完整保留；原 transcript 仍为 36 条。
- 检查：中间 SQLite/Redis 更正、weather 开放问题、opaque id 和最新 turn 均保留。

### 自动记忆冲突

- 第一批：Python/SQLite 长期偏好。
- 第二批：明确更正为 PostgreSQL。
- 修复前：出现两个 active memory，SQLite 与 PostgreSQL 矛盾并存。
- 修复后：1 个 active identity、2 个 revision，旧 revision=`CONTRADICTED`，当时 revision 为 PostgreSQL；4 次 Groq 请求完成。

### 完整 CLI 多轮烟测

- 第 1 轮：正常回答成功。
- 第 2 轮：模型实际调用 `weather`，返回上海天气、Open-Meteo 来源和抓取时间。
- 第 3 轮：正确回忆“上一轮要求查询上海天气并给出来源/抓取时间”。
- 三轮均 `SUCCESS`，工具调用链闭合，后台长期记忆开关为 ON。

### P0 修复后的 Groq 回归（2026-08-21）

- 使用该快照时 `.env.local` 的 Groq `openai/gpt-oss-120b`，临时数据目录运行三轮真实 CLI turn。
- 三轮均 `SUCCESS`；第 2 轮真实调用 `weather`，持久化 `TOOL` exchange 中确认 `external_observation.v1`、`source=Open-Meteo` 和 `fetched_at`；第 3 轮继续对话成功。
- `/context` 观测到真实 Provider 最近一次用量 `input_tokens=2546`、`output_tokens=113`，窗口来源为 `model-capability:gpt-oss`（131072 tokens）。
- 该实验只证明当前 integration smoke 和来源字段闭环，不外推压缩保真度或长期记忆语义质量。

### 自动长期记忆真实回归（2026-08-21）

- 10 轮前台缓冲达到阈值后自动触发沉淀，最终得到 1 个 active identity；随后提交“SQLite 改为 PostgreSQL”的明确更正。
- 更正后仍保持同一 `memory_id`，history 为旧 revision=`CONTRADICTED`、新 revision=`CURRENT`，Provider 共 4 次请求。
- 首次运行暴露 Groq 将 typed `NOOP` 写成 `operation=NONE` 的协议偏差，已在 `NemoriDistiller` 边界归一化为 canonical `NOOP`；这不是语义关键词门禁。
- 首轮结果曾把助手确认语句带进旧叙述；随后将 NEMORI 的事实输入边界收紧为用户原始消息后复验通过，旧叙述只保留用户事实。仍需在更多语言、假设讨论和助手建议样本上评估误写率。

## 该快照时点仍然存在的问题

### P0：第一轮已修复，仍需真实模型评估质量

1. **Provider usage/overflow 闭环已接入。** `OpenAILLMClient.last_usage` 进入窗口决策；Provider 的 context overflow 有专门类型；Agent 在同一 run 内触发一次压缩并重试，避免重复执行工具。
2. **压缩降级已显式化。** fallback checkpoint 持久化为 `source=FALLBACK`，`/context` 和模型 prompt 都会看到降级警告；原始 transcript 仍是权威数据。
3. **工具调用记录已进入 transcript。** `ConversationRole.TOOL` 以单条 paired exchange 保存 call、arguments、result、status；journal 仍是执行事实源，压缩可按完整消息保留。
4. **自动记忆批内竞争已拦截。** 后续 episode 会看到前序 UPDATE/CORRECT 的 working set；同一批对同一 identity 的多个竞争 mutation 在事务前拒绝，事件保持 PENDING 可重试。
5. **仍需真实模型评估**：压缩摘要的信息保真度、overflow 重试后的回答质量、自动记忆误写/冲突处理，以及多 ADD episode 是否需要更强的跨候选合并协议。

### P1：产品体验与可维护性

1. 在该快照时点，产品是 CLI 内嵌 harness，没有 Codex App Server 式 JSONL/RPC 客户端协议、结构化实时事件流和可恢复的未完成 turn；这些能力后来已在当前 main 中补齐一部分。
2. 在该快照时点仍缺 manual `/compact` 指令与压缩前 memory flush；当前自动 overflow compaction+retry 和 `/compact` 路径已接入。
3. 外部搜索/抓取/天气现已输出统一 `external_observation.v1` 的来源 URL、抓取时间和 citation；仍缺发布时间解析、可信度和多来源冲突呈现。
4. 任意未知 OpenAI-compatible 模型仍不能可靠自动发现最大 context；当前需要能力表或环境覆盖。下一步应在支持的 provider 上实现受控的 `GET /models/{model}` capability discovery，并保留显式覆盖。

### P2：不是当前个人 CLI 的阻塞项

- MCP/远端工具发现与连接管理。
- 文件系统/命令执行 sandbox、权限 profile、审计报告。
- subagent、并行 tool call、sleep-time agent。
- Graphiti 式 temporal knowledge graph、episode/entity/edge 级 RAG。
- 外部资料批量摄取、chunk/dedup/rerank、夜间知识蒸馏。

这些能力对“编程代理”或“个人知识库”阶段很重要，但不应在 P0 记忆/上下文可靠性尚未闭环前扩张实现面。

## 该快照时点提出的后续演进顺序

1. **Harness/上下文闭环**：对已完成的 usage、paired tool transcript、overflow retry、降级标记增加真实 Groq compaction fidelity eval；随后补 manual `/compact` 与压缩前 memory flush。
2. **记忆质量闭环**：增加真实 Groq 回归集，覆盖显式偏好、假设讨论、assistant suggestion、重复、UPDATE、CORRECT、跨 session 和批内多冲突；统计误写率、漏写率、冲突率、回忆准确率。
3. **外部事实闭环**：已统一 search/fetch/weather 的 source DTO、时间戳和 citation 字段；下一步补 CLI/模型侧引用渲染、失败/过期提示，再扩展新闻、日历等专用工具。
4. **RAG/知识库阶段**：再引入 chunk provenance、时间有效性、去重、rerank、资料摄取与 MCP；最后评估 App Server、并行工具和 subagent。
