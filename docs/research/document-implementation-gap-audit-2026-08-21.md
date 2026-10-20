# 初始设计文档与实际实现差距审计

审计日期：2026-08-21

> **历史审计快照**：本文记录 2026-08-21 对初始设计材料的审计结论。文中的“当前”只
> 指该审计时点，不代表今天的 main 分支。当前实现、全局存储规则和回归结果以
> [`docs/project-progress.md`](../project-progress.md)、
> [`docs/architecture/overview.md`](../architecture/overview.md) 和当前代码为准。

本审计对照了当时的初始设计文档、提示词与 points 材料，以及：

- `docs/architecture/task1-memory-lifecycle.md`
- `docs/architecture/task2-retrieval.md`
- `docs/architecture/task3-working-state.md`

上述原始需求材料现保存在维护者本地归档，不随仓库分发；本文保留其 2026-08-21 的审计结论。

这里的“未实现”只表示原文中出现、但当前代码没有同名或等价运行机制；不把研究设想、示例代码、未来扩展点自动当成产品缺陷。状态分为：

- **已实现**：当前代码有可运行实现并有测试覆盖。
- **等价实现**：没有按原文类名/字段名实现，但产品能力已经由更严格的现有边界提供。
- **部分实现**：主链路有了，但原文要求的语义范围或可审计字段不完整。
- **缺失**：当前没有对应机制。
- **有意不做**：实现会扩大误写、破坏安全边界或与当前产品目标冲突，暂不应直接补。

## 一、总判断

当前仓库已经不是原提示词里的“六个模块 + 规则工作集 Demo”，而是一个真实 Provider 驱动的产品组合根：

```text
用户回合
  → Task 3 typed proposal + 原子校验
  → Task 2 召回/情景适用性/证据包
  → Task 3 derived working context
  → Main Agent 工具循环与回答
  → 完整会话 Transcript + 批量长期记忆缓冲
```

因此，不建议为了满足原文名目再复制一套 `Controller/Selector/Updater/WorkingSet`。真正应继续补的是可观察的产品能力，而不是类名。

## 二、Task 1：长期记忆与生命周期

| 原文机制 | 实际状态 | 评估与建议 |
|---|---|---|
| Memory ID、内容、来源、时间、置信度、有效性、作用域、关系 | **已实现** | `MemoryItem/MemoryRevision/ProvenanceRef/SemanticMetadata` 已覆盖主体；来源 authority、event/session/message、时间线和 revision history 可追溯。 |
| `persons/entities/locations/topics/keywords/scopes/conditions` 元数据 | **已实现/部分** | 已有结构化字段，但不会自动凭空补齐；查询时推断必须留在 request-local。这个保守边界是正确的。 |
| 用户基本信息、显式偏好、隐式偏好、任务状态、反馈、表达样例、行为轨迹等完整类型 | **部分实现** | 当前 `MemoryType` 只有 `CORE_PROFILE/PREFERENCE_RULES/EPISODIC_GRAPH`，`Granularity` 表示粒度；没有独立的反馈、行为轨迹、SOP、隐式认知模型 schema。当前产品不应为了“类型齐全”自动把模型推断写成用户事实。 |
| `confidence`、显式/推断来源 | **已实现/部分** | revision confidence 和 `ProvenanceAuthority` 已有；行为样本数、推导链、隐私级别、验证状态等 points.md 中的扩展字段还没有。只有确实需要用户可见审查时才应增加。 |
| ADD / UPDATE / CORRECT / NOOP | **已实现** | 是当前 canonical lifecycle operation，并有原子事务、幂等和历史 revision。 |
| MERGE / 抽象上浮 | **部分实现** | `RISE` 会创建新的 abstract identity 并建立 `DERIVED_TO`；`merge_abstract_memories()` 只是兼容别名，不是跨 identity destructive merge。真正的跨身份合并按当前 ADR 仍 deferred。 |
| DELETE / FORGET | **已实现/有意不做物理删除** | CLI 提供 `/forget`、`/restore-memory`、`/memory-recycle-bin`；删除先进入可恢复回收站并保留历史/provenance。自动按时间清理属于遗忘功能，默认关闭。 |
| 记忆衰减、强化、巩固、回收站 | **已调整** | 时间遗忘默认关闭，不再因未访问而降权或回收；`MEMORY_ENABLE_FORGETTING=true` 才启用旧维护路径。普通产品中的权重变化来自 Provider 在批量生命周期提议中显式设置 `importance_score`，而不是 Ebbinghaus。旧 `run_periodic_consolidation()` 的前三条 Episode 规则已移除。 |
| CORRECT 后保留旧版本并建立 corrected-from 关系 | **等价实现** | 当前以同一 `memory_id` 的新 revision + `supersedes_revision_id`/validity 保存历史；不是新 identity，但更适合“同一用户事实被修正”的查询语义。 |

### Task 1 是否要继续补

短期只建议补两项：

1. 评估 Provider 提出的 `importance_score` 调整是否符合真实用户对降权/冲突的预期。
2. 继续完善回收站的最终清理和批量审计导出；不自动引入跨身份 MERGE。

不建议现在实现隐式人格、RL 偏好模型、完整 SOP/行为轨迹类型。这些会显著增加误写风险，且不是当前 CLI “好用、能记住、能调用工具”的必要条件。

## 三、Task 2：检索、情景判断与 EvidenceBundle

| 原文机制 | 实际状态 | 评估与建议 |
|---|---|---|
| 稳定 `retrieve()` 入口、从现有 MemoryRecord 独立测试 | **已实现** | `MemoryRetrievalFacade.retrieve_evidence()`、typed pipeline 和临时库测试均存在。 |
| 符号召回 | **已实现** | BM25/中文 tokenizer/metadata lexical overlap。 |
| embedding 召回、可替换 provider | **已实现** | Dense provider contract、FastEmbed backend、Hybrid RRF 均有；provider 失败显式报错。 |
| QueryFrame / ScoredMemory / EvidenceBundle | **等价实现** | `ApplicabilityContext`、`RecallCandidate`、`EvidenceBundle` 提供相同边界，但没有完全采用原提示词命名。 |
| 时间、人物、地点、实体、别名、否定、偏好变化 | **部分实现** | ontology、模型解释和 evidence refs 支持结构化情景；完整相对时间消解、复杂否定和跨 revision 时间问答还不是确定性生产能力，真实 Provider 语义质量仍需评估。 |
| Query expansion（三路查询） | **已实现基础版** | Provider 提取出的当前上下文值作为 request-local expanded queries，词法/稠密召回做多查询合并；不会持久化同义词规则。 |
| 图结构 PPR / random walk | **已实现基础版** | `PersonalizedGraphMemoryRetriever` 对当前 identity-level relation 做有界 personalized random walk；无图边时透明跳过，并保留 trace。 |
| 统一 semantic + lexical + context + recency 公式、LLM rerank | **部分实现/可选** | 默认保持可解释的分层分数；`MEMORY_ENABLE_MODEL_RERANK=true` 可启用严格 ID 白名单的 Provider reranker，默认关闭，避免未经评估的额外 Provider 请求改变排序。 |
| 去重、多样性、冲突标注、证据充分性 | **已实现/部分** | deterministic selection/dedup/diversity、scope conflict annotations、bounded extractive distillation、sufficiency 和 omission trace 已有；复杂自然语言冲突仍要求上游显式提供，不会靠相似度臆测。 |
| Query-conditioned LLM evidence distillation | **部分实现** | 当前默认是 bounded extractive distillation；Provider 结构化解释用于情景和 scope，不直接替代原文证据。若接入 LLM 改写，必须继续保留原文 span 和失败即终止。 |
| 完整 RetrievalError 字段（request/stage/provider/model/retry） | **等价实现** | ProviderError + runtime `TurnErrorInfo` 统一向 CLI 暴露阶段、Provider、操作、状态码和 retryable；未知细节不会泄露原始异常。 |
| 全量 JSONL 每轮实验日志 | **已实现基础版** | 每次成功构建 EvidenceBundle 会向 `.agent_data/retrieval-traces.jsonl` 写入 request、查询变体、各阶段候选数、选中 ID 与警告；原始对话仍以 SQLite 为事实源。 |

### Task 2 是否要继续补

优先级应是：真实 Provider 的语义误判评估 → 时间/冲突/拒答质量 → 校准可选 reranker；当前 query expansion、PPR 和 JSONL trace 已有工程基础，但仍需真实数据验证收益。

原始检索提示词与工作集提示词在 API 失败策略上有冲突：工作集提示词要求“失败后 fallback 到规则模式”，检索提示词又明确要求“API 失败不能伪装正常结果”。当前产品采用后者的 fail-closed 策略，这是正确的生产选择；离线 fake 只能在请求开始前显式注入，不能作为中途降级。

## 四、Task 3：短期上下文、工作集和任务状态

### 4.1 数据结构与 action 元语对照

原工作集提示词定义：

```text
role: goal / constraint / decision / progress / background / open_question
action: ADD / KEEP / PROMOTE / DEMOTE / MODIFY / COMPRESS / REPLACE / INVALIDATE / REMOVE
relation: SUPPORTS / CONFLICTS / SUPERSEDES / DEPENDS_ON / RELATED_TO / REFINES
```

当前代码定义：

```text
EntryKind: GOAL / CONSTRAINT / DECISION / PROGRESS / OPEN_QUESTION / BACKGROUND / DEPENDENCY
ProposalAction: ADD / RESOLVE / SUPERSEDE / REMOVE / LINK_RELATION / SET_PRIORITY
RelationKind: SUPERSEDES / DEPENDS_ON / SUPPORTS / CONFLICTS_WITH
Priority: LOW / NORMAL / HIGH / CRITICAL
EntryStatus: ACTIVE / RESOLVED / SUPERSEDED / REMOVED
```

对应关系：

| 原文 | 当前代码 | 判断 |
|---|---|---|
| goal/constraint/decision/progress/background/open_question | `EntryKind` | **等价实现**，并额外有 dependency。 |
| ADD | `ProposalAction.ADD` | **已实现**。 |
| KEEP | 空 proposal / replay-safe no-op | **等价实现**，没有单独写一条 KEEP 事件。 |
| PROMOTE/DEMOTE | `SET_PRIORITY` | **等价实现**，但优先级是 1-4 枚举，不是 0-1 浮点数。 |
| MODIFY | `SUPERSEDE` 新 entry | **等价实现**，保留旧条目历史，避免原地覆盖。 |
| COMPRESS | derived `TaskContextCompaction` | **等价实现**，压缩不是 canonical ledger mutation。 |
| REPLACE | `SUPERSEDE` | **已实现**。 |
| INVALIDATE | `RESOLVE` 或 `REMOVE` | **部分等价**，没有单独的 INVALIDATE 状态。 |
| REMOVE | `ProposalAction.REMOVE` | **已实现**。 |
| RELATED_TO / REFINES | 无对应 `RelationKind` | **缺失**，但不是当前 Agent 连续性必需；可在多跳任务确有需求时增加。 |

### 4.2 工作集主链路

| 原文机制 | 实际状态 | 评估与建议 |
|---|---|---|
| `TaskWorkingSet` 独立对象、跨轮维护 | **等价实现** | `TaskLedger → ContinuationCheckpoint → ActiveWorkingContext → StructuredTaskContext` 是持久化、可重建的更严格版本。 |
| Controller 输出 task_stage/query_intent/information_needs/retrieval_needed | **部分实现** | 当前 `AgentRuntime` 每轮生成 TaskEvent；Task 3 Provider 只提出 typed proposals，Task 2 adapter/evidence pipeline 负责检索和筛选，没有独立 `TaskAnalysis/QueryPlan` DTO。功能上有任务上下文和检索阶段，控制器名目未单独落地。 |
| Retriever → Selector → Updater 分层 | **等价实现** | Retriever/selector 位于 Task 2；Task state updater 位于 Task 3；最终由 AgentRuntime 编排，避免把长期记忆、证据筛选和任务 ledger 混成一套工作集。 |
| priority 动态更新 | **部分实现** | Provider 可以提出 priority，TaskContextBuilder 按 priority、checkpoint、entry kind 和更新时间排序；没有独立 `PriorityManager` 和原文那套 role weight 公式。当前更容易审计，先不复制一套规则管理器。 |
| capacity control、非 FIFO 淘汰 | **已实现/等价实现** | 生产根按模型 context window 动态构建 Task 3 budget，优先保留 checkpoint/goal/critical constraints，再对省略项做可追溯压缩；不使用固定 `20 entries/5000 chars` 作为产品上限。 |
| task completion write-back 到长期记忆 | **缺失** | `TaskStatus.COMPLETED` 生命周期和 CLI `/complete` 已有，但完成任务不会自动把整段 trajectory 写入 Task 1。建议暂不自动写回，先提供用户显式 `/remember` 或后续审核式归纳，避免整任务误沉淀。 |
| current step/focus/next action/world-state references | **部分实现** | `ContinuationCheckpoint` 有 current step、goal/constraint/question/decision/evidence refs；没有独立 focus/next_action/world-state 字段。外部工具观察作为 request-local/task event 传入，不冒充外部事实源。 |
| LLM compaction of Task Working Set | **部分实现** | 会话 Transcript 有 Provider 摘要压缩；Task 3 工作集的 omitted entries 使用 bounded extractive compaction，不调用 LLM。当前是有意把 canonical task state 和 lossy summary 分开。 |

## 五、哪些 gap 应该现在实现

### 现在应该实现或已在本轮补齐

- CLI 的任务暂停、恢复、完成、取消入口；
- 终端工具结果写入当前对话，支持后续追问；
- 确认/拒绝操作写入 transcript，重启后恢复待确认操作；
- 任务标题真正写入 Task 3 goal，并避免重启后自动落到已完成任务；
- 当前用户手册和实际命令保持一致。

### 本轮已完成

- 移除旧周期巩固入口中的“前三条 episode”硬编码；
- 增加记忆撤回、恢复、回收站和 provenance 展示；
- 增加 focus/next_action/world_state 检查点字段；
- 增加 request-local 多查询召回、图 random walk、可选 Provider rerank 和 retrieval JSONL trace；
- 默认关闭时间遗忘，并让 Provider 可在批量生命周期提议中显式调整 importance。

### 后续优先实现

- 用真实 Provider 评估时间变化、冲突、自动记忆误写和压缩保真度；
- 评估 Provider importance 调整的校准和回滚体验；
- 增加 provenance/retrieval trace 的用户导出与隐私清理入口。

### 暂不实现

- 跨 identity destructive MERGE；
- RL 训练、隐式人格/认知模型、自动风格模型；
- 任务完成后无确认地自动写回长期记忆；
- 为了满足原文名称而复制 Controller/Selector/WorkingSet 类。

## 六、结论

当前真正的实现缺口集中在“研究增强能力”和“用户可见控制/审计”，不在基础 Task 1/2/3 主链路。Task 3 应保留：

```text
完整会话 Transcript
+ 模型感知压缩投影
+ Task 3 结构化 Working Context
+ 外部观察与工具事件
```

它不是多余的“任务完成标志”，而是解决“说过什么”和“当前任务持续需要什么”两个不同问题的结构层。文档中的旧固定容量、规则 controller 和自动 write-back 应视为研究草案，不应直接恢复为产品规则。
