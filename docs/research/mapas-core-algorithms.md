# MAPAS 核心算法与技术方案

## 1. 研究目标与系统定位

MAPAS（Multi-Agent Personalized Agentic System）研究面向长程任务的多智能体记忆与可信执行。项目构建了长期事实管理、上下文感知证据检索、任务状态维护、推理和工具执行机制，并将这些机制逐步扩展为多角色协作系统，使智能体能够跨会话维护目标、约束、证据和阶段性成果。

**研究问题：** 在有限上下文和计算预算下，如何让多个 Agent 使用当前有效、适用于任务且来源可核验的信息，维护具有依赖关系的执行状态，限制协作中的错误传播，并把经过验证的经验用于后续任务？

“长程任务”强调跨阶段执行、约束持续有效和中断后恢复；“多智能体”强调责任分工、结构化交接与协同验证；“记忆”同时覆盖稳定事实、情景记录、执行状态和可复用经验。它们是共同工作的系统能力，不是互斥技术路线。长上下文、检索增强生成、持久化记忆与多 Agent 编排分别解决输入容量、证据访问、跨时状态和协作组织问题，可在同一架构内共存。

系统的研究机制分为四个相互衔接的方向：

1. **生命周期与分层证据检索：** 从历史交互形成可追溯记忆，按任务需求检索、判断适用性和充分性。
2. **依赖感知执行记忆：** 用任务台账、检查点和执行依赖图保存跨阶段状态。
3. **版本化协作交接与局部恢复：** 保证后继 Agent 使用经过验证且仍有效的输入，限制过期信息的影响范围。
4. **选择性经验沉淀：** 将通过结果与条件验证的执行经验形成可控、可更新的程序性记忆。

以上四项从记忆写入、读取、执行到复用形成一个闭环；每项机制都应能够独立测量其对任务质量、成本或可靠性的贡献。

## 2. 系统结构与现阶段能力

### 2.1 模块所有权

| 模块 | 权威数据与职责 | 当前能力 | 核心位置 |
| --- | --- | --- | --- |
| 长期记忆 Task 1 | `MemoryItem`、`MemoryRevision`、`ProvenanceRef`；生命周期写入与版本历史 | 已实现 | `code/src/memory/lifecycle/`、`storage/`、`api/memory_manager.py` |
| 记忆检索 Task 2 | 当前有效快照、候选召回、适用性解释、冲突与 `EvidenceBundle` | 已实现 | `code/src/memory/retrieval/` |
| 任务状态 Task 3 | `TaskLedger`、`ContinuationCheckpoint`、结构化工作上下文 | 已实现 | `code/src/memory/task_state/` |
| 对话与执行 | Conversation Session、`AgentRuntime`、`MainAgent`、真实工具 `Journal` | 已实现 | `conversation/`、`runtime/`、`agent/`、`app/` |
| 协作 P1 | `Planner`、`Memory`、`Executor`、`Verifier`；`Coordinator`、持久化交接、共享预算 | 可选实验性串行协作 | `code/src/memory/collaboration/` |
| 协作记忆增强 | 原文升级、完整输入依赖版本、局部修复、验证后经验写入 | 下一阶段算法设计 | 第 4 节 |
| 外部端到端评测 | MemoryArena 整组任务、单/多 Agent 对照与消融 | 尚未形成正式对照结果 | 第 6 节 |

当前默认执行模式为 `single`；显式选择 `multi_fixed` 才启用四角色串行协作。P1 每次规划最多三个结构化子任务，允许在有限依赖 DAG 中进行拓扑执行，子任务工具访问限于授予的只读能力。当前没有已运行的多工作者并行、分布式事务、完整执行分支局部恢复或自动程序性经验写回。

### 2.2 完整执行流程与数据边界

```text
用户消息 → Conversation Session（原始消息与上下文）
          → AgentRuntime（任务绑定、Task 3 事件更新、Task 2 证据）
            ├─ Task 1：长期事实与来源、版本
            ├─ Task 2：检索 → 情境解释 → 适用性 → EvidenceBundle
            └─ Task 3：任务台账 → Checkpoint → Working Context
          → MainAgent
            ├─ single：受限推理/工具循环 → 工具 Journal
            └─ multi_fixed：Coordinator
                 → Planner（子任务 DAG）
                 → Memory（当前有效记忆选择）
                 → Executor（受限工具）
                 → Verifier（子目标与整体目标核验）
                 → 协作数据库（角色交接、成果、用量与恢复）
```

这是一张数据与职责关系图，而不是每轮必须按箭头顺序调用全部组件的流程图。SQLite 存储权威事实与执行记录；索引、工作上下文、证据包为可重建的派生视图。模型负责语义解释与结构化提案，宿主代码负责来源、身份、权限、状态和事务校验。模型“声称完成”不等同于工具成功或成果通过验证。

### 2.3 当前工程证据

2026-10-14 的 P0–P1 阶段记录报告了 **432 passed、1 skipped** 的回归结果；九次实际 DeepSeek API 调用组成的只读工具 Smoke 验证了任务分解、角色调用、证据核验与记录恢复的基础闭环。这些是该时点的工程证据，不是当前 HEAD 重新运行的成绩，也不是 MemoryArena 任务成功率。完整外部数据准备、整组依赖任务基准和强单 Agent 公平对照仍需实施。

## 3. 已实现的核心算法与技术细节

### 3.1 NEMORI 风格记忆提炼和版本化生命周期

#### A. 语义信息识别

系统支持由模型对对话进行事件划分（Episode Partitioning），产生来源消息索引、情境线索 `cue`、叙述 `narrative` 及持久性标签（`DURABLE / TRANSIENT / UNCERTAIN`）。`NemoriDistiller` 也提供以历史记忆为条件、比较预期信息与实际叙述的预测残差接口，用于分析潜在新增信息。

但实际长期记忆的持久化取决于明确的工具路径和生命周期提案，不应把预测残差接口说成每次自动写入必经的在线流水线，也不能把某个新颖性数值误写成代码硬编码的入库阈值。

#### B. 生命周期决策与原子提交

```text
真实用户事件 / Agent 选择记忆工具
  → Provider 生成有类型的 LifecycleProposal
  → 校验操作、memory_id、来源权威、revision 与字段合法性
  → 一次 SQLite 事务完成语义版本、来源及 ingestion 状态更新
  → CURRENT + ACTIVE 视图供后续检索
```

操作类型包括 `ADD`、`UPDATE`、`CORRECT`、`NOOP`，并支持受约束的移除等生命周期处理。`MemoryItem` 保存稳定 identity 与保留状态；`MemoryRevision` 保存不可变语义内容、版本号、有效性和时间信息；`ProvenanceRef` 保存来源事件、会话/消息引用及内容哈希。状态分为两条正交轴：语义有效性（`CURRENT / SUPERSEDED / CONTRADICTED / EXPIRED`）与保留状态（`ACTIVE / ARCHIVED / RECYCLED`）。

当用户更正旧偏好时，对同一记忆身份生成新的当前 revision，旧版本仍可用于追踪历史，但不会再次作为默认当前事实。回收后的恢复只改变保留状态，不自动把旧的失效事实变为当前版本。Provider 只能提出操作，必须由数据库约束、来源校验与原子事务决定是否真正写入。失败的 ingestion 可保留以便重试；产品默认不会因未访问或时间流逝自动删除事实。

**系统设计：** 语义提炼与版本演化分离；来源权威与模型判断分离；保留策略与事实当前有效性分离。这些机制由项目的生命周期数据结构、来源校验和状态转换共同实现。

源码：`lifecycle/nemori_distiller.py`、`core/schema.py`、`storage/sqlite_manager.py`、`api/memory_manager.py`。

### 3.2 候选记忆检索：BM25、Dense 和 RRF

Task 2 的默认召回从 Task 1 读取 `ACTIVE + CURRENT` 的记忆快照，每次构建可重建的只读词法索引。默认使用 BM25；分词支持中英文基础归一化，另根据显式元数据与查询词重叠进行有限加权。主要词法评分：

\[
\operatorname{BM25}(q,d)=\sum_{t\in q}\operatorname{IDF}(t)\frac{f(t,d)(k_1+1)}{f(t,d)+k_1(1-b+b|d|/\mathrm{avgdl})}.
\]

默认 `k1=1.5`、`b=0.75`，元数据词法权重为 `0.35`。元数据加权用于增加候选可召回性，不能据此直接判定记忆对当前任务适用。

代码另外提供 `DenseEmbeddingRetriever`（L2 归一化的向量余弦相似度）和 `HybridRRFMemoryRetriever`（多个检索器按排名融合）：

\[
\operatorname{RRF}(d)=\sum_{i:d\in L_i}\frac{1}{60+\operatorname{rank}_{L_i}(d)}.
\]

默认单路 RRF 候选上限为 50。Dense/Hybrid 是可选后端；当前产品构建器的默认路径使用 BM25 和可选图扩展，并未默认全量启用嵌入检索。当前 Dense 的衍生向量尚需按 revision/hash/model 建立持久可重建缓存，避免反复嵌入整个有效记忆集。

源码：`retrieval/lexical.py`、`recall.py`、`dense.py`、`hybrid.py`、`graph.py`、`runtime/builder.py`。

### 3.3 上下文适用性判断：相关 ≠ 可用

语义相似度不能区分“检索到一条相关偏好”与“当前任务确实应遵循该偏好”。系统将这一问题划分成三个独立步骤：解释当前任务情境、解释记忆约束范围、在结构化维度上比较二者。

| 维度 | 典型含义 |
| --- | --- |
| PERSON | 谁的偏好、事实或条件 |
| LOCATION | 哪个地点适用 |
| TIME | 有效时间或时段 |
| TASK | 哪一项任务适用 |
| SCENARIO | 哪种场景适用 |
| CONSTRAINT | 明确要求或排除的条件 |

Scope 可为 `EXPLICIT_CONSTRAINT`（有明确限定）、`EXPLICIT_GENERAL`（证据表明不限于该维度）或 `UNSPECIFIED`（尚无证据判断）。Context 另区分 `KNOWN / UNKNOWN / AMBIGUOUS`。适用性结果为 `MATCH / MISMATCH / UNKNOWN / AMBIGUOUS`；确定的 MISMATCH 被拒绝，而 UNKNOWN/AMBIGUOUS 保留为带不确定性标记的候选，不自动提升成 MATCH。对于约束还支持 `REQUIRES` / `EXCLUDES`。

例如，用户曾说“我不能吃辣”。新任务为朋友设计聚餐菜单时，这条记忆可能是语义相关的，却未必适用于朋友；系统需要识别偏好的归属对象及当前任务角色，而不能盲目复用。

`SemanticRetrievalPipeline` 负责 LLM 提议的情境/范围解释及重查；`ApplicabilityEngine` 根据受控结构比较。模型异常时保留相应不确定性而不是把未知条件虚构为确定值。该功能已有代码，但对于宽泛的“情境适用性检索”新颖性，项目的研究综述已发现 RPEval、BenchPreS、STITCH 等强相关工作，因此应将它表述为项目的算法设计与系统能力，不宜声称是整个领域首次提出。

源码：`retrieval/semantic_pipeline.py`、`interpretation_provider.py`、`applicability.py`；相关工作：`docs/research/task2-academic-novelty-survey.md`。

### 3.4 证据选择、冲突分析与 EvidenceBundle

检索之后，系统组织 `CandidateEvidence`，保留有效 revision、来源、检索评分分解、适用性理由和冲突记录；之后进行确定性去重、多样性/预算控制、抽取式蒸馏及 `EvidenceSufficiency` 判断：

```text
RecallResult + ApplicabilityResult
  → 当前有效候选与冲突标注
  → 适用性 gate（MISMATCH 拒绝，未知带标记）
  → 去重与受限选择
  → Extractive DistilledEvidence
  → SUFFICIENT / PARTIAL / INSUFFICIENT
  → EvidenceBundle
```

冲突检测首版针对结构化范围中相反的 `REQUIRES / EXCLUDES`，不会把任意文本相似自动解释成真正语义矛盾。分数、适用性状态与证据充分性是三个不同维度，也不回写为长期记忆真相。现阶段的 `EvidenceSufficiency` 是查询后证据判断；下一阶段还需要明确 `EvidenceNeed` 与原文访问路由，才能形成完整的动态分层证据算法。

源码：`retrieval/evidence.py`、`retrieval/facade.py`。

### 3.5 任务连续性：事件提案、任务台账、Checkpoints

Task 3 使用六类结构化状态记录：`goal / constraint / decision / progress / open_question / background`。源事件来自用户、工具、Agent 或外部观察，流转为：

```text
TaskEvent
  → TaskStateInterpreter：生成有类型的 TaskStateProposalBatch
  → TaskStateProposalValidator：事件身份、support_spans、来源权威、合法操作
  → TaskStateRepository：原子写入、覆盖/结束旧条目与幂等记录
  → TaskContextMaintenanceService：维护 ContinuationCheckpoint
  → WorkingContextPolicy：在预算内组合 ActiveWorkingContext
```

校验层不允许工具失败被写成已完成进度，也不允许 Agent 自行将推测提升为用户的强约束。`TaskLedger` 是权威任务状态，`ContinuationCheckpoint` 保存恢复指针，而 `ActiveWorkingContext` 是从有效条目、记忆证据和近期事件组织的可重建视图。工作集优先纳入当前目标、关键约束、有效检查点，再覆盖决策、问题、进展和背景；预算压力下才进行有界、保留来源指针的抽取式压缩。

会话原文、长期事实、任务状态、压缩摘要和外部工具观察各有自己的数据所有者。任务可以跨会话继续，但新会话不自动等同于旧任务。

源码：`task_state/updater.py`、`repository.py`、`maintenance.py`、`context.py`。

### 3.6 多 Agent P1：DAG、角色契约、核验交接和共享预算

`Planner` 对用户目标提出至多三个子任务，每项包含 `key / objective / dependencies / tool_names / completion_criteria`；`Coordinator` 检查重复标识、缺失前驱、非法工具、环与拓扑顺序。当前是串行消费已准备好的子任务，不是异步并行队列。

执行顺序：

1. 原始用户 turn 由 Runtime 真实处理一次，形成任务绑定、当前任务状态与记忆证据。
2. Planner 生成 JSON DAG，Coordinator 进行 Schema、权限与依赖合法性检验。
3. Memory 为当前子目标选择已经提供的当前有效证据，并标识信息缺口。
4. Executor 在授予的只读工具范围内调用现有 `AgentLoopController`，获得实际执行结果。
5. Verifier 检查子任务成果的必要证据 ID 和真实工具回执，只有通过检查的产物才能升级为 VERIFIED 并供后继使用。
6. 全部子目标完成后，整体 Verifier 还需检查原始用户目标是否得到覆盖；Coordinator 保存终态并支持验证后的重放。

协调记录中的 `runs / role_tasks / handoffs / artifacts / model_calls` 保存稳定身份、哈希、阶段版本、交接及预算。全局预算覆盖 Runtime、各角色和实际调用，避免单个角色超额调用。已处理的 run 重放不重复写入用户消息或执行工具；相关长期记忆被修改/回收后，过期结果不能作为有效答案重放。

现阶段没有 `Input Frontier` 完整输入版本追踪、分支自动局部修复或真正并行执行；P1 Memory 角色负责选择证据，不负责完整的自动长期记忆沉淀。独立验证能阻止某些结构不一致和伪造回执，但不等于形式化证明所有语义结论正确。

源码：`collaboration/models.py`、`coordinator.py`、`roles.py`、`repository.py`、`budget.py`、`agent/journal.py`。

## 4. 四项可并存的算法研究机制

以下四项形成同一个协作记忆体系，并与第 3 节的实现平滑衔接；“目标算法”明确指尚待扩展、实现和测量的部分。

### 4.1 机制 A：需求驱动的分层证据检索

**算法问题：** 查询时已经检索到语义相关摘要，怎样判断摘要是否包含当前子任务的关键约束？什么时候应为可靠性付出额外成本访问原始记录？

**输入：** 子任务目标、必要完成条件、当前环境、角色访问权限、Task 1 当前有效快照及可溯源资料。

**新增对象：** `EvidenceNeed` 可包含 entity、required_fields、scope、time/environment、authority、minimum_support；`EvidencePacket` 包含来源 ID、revision、支持片段、条件覆盖和验证状态。

目标算法：

```text
Subtask → 提出 EvidenceNeed
    → 先按角色/归属/权限限制检索范围
    → BM25 / Dense / RRF 候选召回
    → Context Applicability + 当前 revision 检查
    → Evidence Coverage / Sufficiency
        ├─ 完整：生成 EvidencePacket，进入执行
        └─ 不足：SourceResolver 追踪原始消息/回执/不可变成果
                 → 有界补充检索与核验
                 → 重新检查覆盖；仍不足则询问、阻塞或拒绝执行
```

原始来源应从 Conversation Repository、Execution Journal 与成果仓库读取，而不是从 ingestion 成功后已清理的原文字段猜测。摘要和原文层必须绑定同一来源身份；派生缓存键应包含 source ID、content hash、revision、模型版本和作用域。对用户撤销的记忆，原文索引不能使其重新注入有效上下文。

**研究变量：** 召回后直接输入 vs 适用性过滤 vs 条件驱动的原文升级。**指标：** 必需约束覆盖、证据忠实度、Task Success、追加读取次数、Token 和延迟。

**论文联系：** TierMem 的摘要—原文两层与 Sufficiency Router；AMA 的多角色记忆管理。我们在 Task 2 中新增 EvidenceNeed 与 provenance-backed SourceResolver，而不是直接复制论文的专用训练方法。

### 4.2 机制 B：依赖感知的协作执行记忆

**算法问题：** 多个 Agent 需要共享的不仅是文本摘要，而是“每个子目标在什么证据下完成、哪些成果能被后继安全引用”。

目标数据结构：

```text
TaskNode = {
  node_id, objective, completion_criteria,
  dependency_ids, required_evidence,
  input_frontier, artifact_ids,
  owner_role, status, checkpoint_ref
}
```

状态建议为 `PENDING / RUNNING / PRODUCED / VERIFIED / STALE / FAILED / BLOCKED / CANCELLED`。`PRODUCED` 表示 Agent 给出了成果，`VERIFIED` 表示成果满足来源、前置依赖与适当工具核验条件。只有具有正确授权且所需前驱已 VERIFIED 的节点才成为可执行候选。

**记忆压缩：** 将完成且验证通过的执行片段折叠为紧凑子目标摘要，同时保留关键约束、真实成果引用、依赖闭包和未完成问题；被阻塞或失效的轨迹不得被摘要改写成成功事实。

**状态调度：** 初期以现有串行拓扑调度为基线，之后在资源与权限条件满足时，可构建 ready set 并允许受限只读并行。真正并行前需要消除共享 LLM client 中可变 usage 字段的竞争，设计独立客户端或无状态请求结果，并处理跨工作者交接。

**论文联系：** MemoBrain 的依赖骨架与轨迹折叠、MAGE 的 Grow/Compress/Maintain/Revise；二者分别贡献上下文管理和执行状态管理视角。

### 4.3 机制 C：版本化交接、依赖失效与局部修复

**算法问题：** 一个任务规划时使用的事实已变化，后继 Agent 如何避免继续执行旧计划，并只重做真正受影响的分支？

为子任务维护真实输入前沿 `InputFrontier`：任务约束版本、记忆 revision、工具/外部状态回执版本、验证过的前驱成果版本。不要只使用模型自报的依赖；应记录真实暴露给角色、且相关的输入集合与来源身份。

定义计划生成时的版本集合为 **F**，执行者当前使用的版本为 **C**，权威数据的当前版本为 **H**。在必要输入集合内，执行前需要验证 `F=H` 且 `C=H`。版本不一致则阻断后继、更新证据并重新规划。此检查不能证明内容本身绝对正确；语义真伪仍由权威来源、原生工具观察和适当的 Verifier 判断。

目标算法：

```text
权威输入更新 / MemoryRevision 撤销 / Artifact 被否定
  → 定位引用该版本的 TaskNode
  → 在 DAG 上追踪受影响后继集合 Descendants(node)
  → 将相关节点标记 STALE / BLOCKED
  → 保留无关且仍 VERIFIED 的成果
  → 重新取证 → 局部规划/执行 → 重新核验
  → 更新 checkpoint 与可消费前沿
```

需要用稳定事件 ID 防止重复修复，限制每个分支的重试次数和全局预算。对外发送消息、文件发布等已经产生的副作用，不能靠内部状态回退直接撤销；应使用授权补偿、幂等请求与真实服务状态对账。Near-Miss 风格的执行前检查关注真实证据/权限是否到位，即使最终结果碰巧正确也应识别过程缺陷。

**研究变量：** 无版本交接、只检查当前记忆版本、完整 InputFrontier、加入局部修复。**指标：** 旧计划执行率、错误传播节点数、正确修复率、恢复耗时及额外成本。

**论文联系：** MAGE 的 Revise、MemoBrain 的依赖维护、Near-Miss 的过程证据，及 PlanFence 的输入版本一致性思想。

### 4.4 机制 D：选择性经验沉淀与任务反馈

**算法问题：** 历史任务虽取得成功，其策略未必适用于新任务；如何在积累经验时避免错误或条件错位的经验污染？

与用户确认事实分开，为程序性经验设计结构：

```text
Experience = {
  experience_id, task_type, preconditions,
  tool_environment_versions, action_summary,
  observed_outcome, source_refs,
  verification_status, applicability_scope,
  reuse_observations
}
```

新经验经过：任务结果与关键过程验证 → 形成候选经验 → 检查来源、适用条件和数据权限 → 准入、隔离或修订。后续复用时重新比较新任务的对象、约束和环境，提供经验来源与适用范围；不将任意相似的历史轨迹直接当成可执行建议。

经验使用后的任务结果可成为效用反馈，但一次成功不证明某条经验具有因果贡献。先实施可审计的管理和有条件的复用，再以足够的真实轨迹研究策略学习；不急于引入没有对照数据支撑的强化学习训练。

**研究变量：** 不积累经验、全量存储、验证后选择性存储、按适用条件筛选。**指标：** 后续任务成功率、错误经验复用率、经验规模、存取成本及有效性更新能力。

**论文联系：** Experience-Following 解释错误传播和 misaligned replay；MemPO 等工作提供以后学习记忆策略的可能方向。

### 4.5 统一协作闭环

```text
长期事实 + 当前用户目标 + 任务台账
             ↓
Planner：子任务目标 / 依赖 / 证据需求
             ↓
Memory：当前有效检索 + 适用性 + 必要时原文核验
             ↓
EvidencePacket：来源、版本、约束覆盖、可信程度
             ↓
Coordinator：输入前沿 / 角色权限 / 任务预算
             ↓
Executor：受限工具执行，生成真实回执和 Artifact
             ↓
Verifier：结果与执行前置条件、整体目标核验
        ├─ VERIFIED → 推进 DAG，保留验证摘要
        ├─ STALE/BLOCKED → 依赖失效传播与局部恢复
        └─ 任务结束 → 选择性提炼经验供后续使用
```

这套体系中的四项机制相互依赖而并存：需求驱动检索解决**取什么信息**，执行记忆解决**任务推进到哪里**，版本化交接解决**成果还能否可靠复用**，经验演化解决**未来任务如何利用历史行动**。按任务决定是否多角色执行或是否只读并行，可作为以后单独评价的执行策略，不能将其和四项记忆机制混为同一级备选路线。

## 5. 论文体系与技术选择

### 5.1 六篇专题论文

| 工作 | 核心问题与方法 | MAPAS 对应机制 | 采用方式 |
| --- | --- | --- | --- |
| **MemoryArena**（He et al., ICML 2026） | 在相互依赖的跨会话真实行动中评价记忆，而不只考查历史问答 | 整组长程任务基准 | 官方 benchmark / 实验问题定义 |
| **MemoBrain**（Qian et al., Findings ACL 2026） | Dependency-aware Executive Memory；折叠已完成推理轨迹、裁剪无效片段 | 依赖感知执行记忆、工作集压缩 | 借鉴设计原理，不宣称复现训练 |
| **MAGE**（Chen et al., 2026） | 分层执行状态树；Grow、Compress、Maintain、Revise | 长程状态、验证与局部修复 | 借鉴状态管理及错误隔离 |
| **From Lossy to Verified / TierMem**（Zhu et al., ICLR 2026 MemAgent Workshop；后续发展为 TierMem） | 摘要优先、充分性路由、必要时原文核验 | 分层证据访问和 SourceResolver | 借鉴证据分配思想；训练复现后置 |
| **Experience-Following**（Xiong et al., ACL 2026） | 高相似历史经验可能导致错误传播和不适用复用 | 经验准入、适用性、后续效用反馈 | 经验质量研究依据 |
| **Near-Miss**（Rabinovich et al., GEM 2026） | 最终结果正确仍可能缺少应有的过程核验 | 关键操作前置证据、Verifier 过程检查 | 过程评价与执行前条件核验 |

MemoryArena 的正式版本为 ICML 2026（PMLR 306）。TierMem 在 2026 年还有后续题名和会议版本，引用时必须注明采用的具体版本，不能混为同一发表状态。

### 5.2 其他相关工作

- **AMA**：Constructor / Retriever / Judge / Refresher 等职责分离，启发 Memory 角色的多层检索与评判；不必每轮增设多个模型调用。
- **Chain-of-Memory**：从片段召回走向相互关联的记忆推理链，适合作为多跳证据组织的候选路线。
- **BMAM、GAM**：分别提供功能性记忆分类、层次图/知识组织的参考；语义知识图与执行依赖 DAG 应有独立语义。
- **AgeMem、MemPO**：自适应记忆操作与策略学习是后续研究候选；训练前先获得真实效果和代价基线。
- **PlanFence、MACE、ToCoMAS、CoMemBench**：分别参考输入版本一致性、功能性经验单元、跨角色责任边界和协作记忆专项评价。
- **RPEval、BenchPreS、STITCH、MemSyco-Bench**：已研究记忆适用性/抑制、任务情境和作用域判断，避免把这一广泛问题当作完全未探索的原创概念。

项目的系统创新应通过**一套可执行的数据契约、若干互补算法模块以及明确的对照与消融**来论证，而不是通过堆叠论文名称或复杂术语来论证。

## 6. 评测设计与可检验研究问题

**RQ1：** 相同工具、模型与系统级预算下，多 Agent 协作在哪类任务上优于强单 Agent？

**RQ2：** 适用性控制、分层原文核验和结构化证据需求是否改善后续行动，代价是多少？

**RQ3：** 完整输入版本和依赖局部恢复能否减少过期计划执行及错误传播？

**RQ4：** 验证后选择性经验沉淀能否提高后续任务表现，避免错误经验复用？

主实验采取 2×2 对照：

| | 当前记忆流水线 | 增强记忆流水线 |
| --- | --- | --- |
| 强单 Agent（可合理自检） | S-Base | S-Memory |
| 固定多 Agent | M-Base | M-Memory |

在此基础上分别移除适用性过滤、原文升级、版本核验、局部修复、独立 Verifier 与选择性经验存储。实验需要控制相同的模型实际版本、工具权限、可见历史、系统总调用上限与任务组划分。并行、检索后端或模型切换另做单独消融，避免混淆归因。

| 评价维度 | 指标 |
| --- | --- |
| 长程任务结果 | 完整 task group 成功率、关键约束满足率、环境原生得分 |
| 记忆利用 | 证据 Recall、适用性误用、关键来源覆盖、修正/删除后的有效视图 |
| 协作可靠性 | 已验证交接比例、过期计划执行比例、失效传播规模、修复率 |
| 过程安全 | 关键前置检查完成比例、Near-Miss、无回执成功声明 |
| 经验质量 | 错误经验复用率、后续成功率、每单位经验的维护成本 |
| 资源 | 模型输入/输出 Token、API/工具调用、重试、端到端与 P95 延迟、实际开销 |

主基准首选 MemoryArena 的完整依赖任务组，不能把相互依赖的子任务拆开作为独立评测；LongMemEval 用于长期事实更新与跨会话检索的诊断；CoMemBench 在核实公开资源及评分接口后作为协作专项候选。不同测试组的初始状态与历史必须隔离，gold 和未来会话不得进入 Agent 输入。正式实验保存数据/代码 revision、模型标识、配置、预算、失败、逐样本 trace 与聚合统计，按 task group 进行配对分析。

目前尚无正式 MemoryArena 整组任务得分或有效的单多 Agent 比较结果，不能填写虚构的性能提升百分比。已实现工程机制与需要验证的研究假设在报告和展示中分别呈现。

## 7. 逐阶段落地计划

| 阶段 | 主要工作 | 交付与验收依据 |
| --- | --- | --- |
| 当前已实现 | Task 1/2/3、单 Agent、P1 串行协作；代码和基础回归 | 现有代码、阶段工程报告与真实执行回执 |
| 外部基准准备 | 补齐 MemoryArena 完整可运行任务组、强单 Agent 主基线 | 原始任务组输入、trace、结果与调用成本 |
| 证据增强 | EvidenceNeed、SourceResolver、原文核验、按 revision 的派生索引缓存 | 来源/撤销/版本/失败回归与证据充分性测评 |
| 协作恢复 | 输入前沿、Verified DAG、依赖失效传播、局部修复 | 旧计划和错误分支注入试验、影响闭包和修复成本 |
| 经验演化 | 独立程序性经验管理、准入、复用与后续效果记录 | 时间序列任务、错误经验污染对照 |
| 系统评估 | 强单 Agent / 固定多 Agent × 原有/增强记忆的对照，关键模块消融 | 任务结果、成本、置信区间及失败类别 |
| 可选增强 | 只读并行、任务感知执行路由、远端 GPU 检索或策略训练 | 相对固定方式的独立质量—成本收益 |

第一阶段的重点是把能正常运行的工程能力转化为明确、可重复的端到端实验；后续算法应依次加入并保留消融开关。不为了增加“技术点数量”而引入无法检验的独立子系统。

## 8. 资料入口与论文链接

**内部文档：** `docs/architecture/overview.md`、`docs/architecture/task1-memory-lifecycle.md`、`docs/architecture/task2-retrieval.md`、`docs/architecture/task3-working-state.md`、`docs/architecture/multi-agent-foundation.md`、`docs/project-progress.md`、`docs/research/task2-academic-novelty-survey.md`、`docs/phases/multi-agent-p0-p1-20261014.md`。

**六篇主要论文：**

1. He et al., *Benchmarking Agent Memory in Interdependent Multi-Session Agentic Tasks*, ICML 2026. https://proceedings.mlr.press/v306/he26am.html
2. Qian et al., *MemoBrain: Executive Memory as an Agentic Brain for Reasoning*, Findings ACL 2026. https://aclanthology.org/2026.findings-acl.127/
3. Chen et al., *Beyond Semantic Organization: Memory as Execution State Management for Long-Horizon Agents*, 2026. https://arxiv.org/abs/2606.06090
4. Zhu et al., *From Lossy to Verified: A Provenance-Aware Tiered Memory for Agents*, ICLR 2026 Workshop MemAgent. https://openreview.net/pdf?id=dJgeY3Awrv （TierMem 后续版本：https://github.com/FreedomIntelligence/Tiermem）
5. Xiong et al., *How Memory Management Impacts LLM Agents: An Empirical Study of Experience-Following Behavior*, ACL 2026. https://aclanthology.org/2026.acl-long.27/
6. Rabinovich et al., *Near-Miss: Latent Policy Failure Detection in Agentic Workflows*, GEM 2026. https://aclanthology.org/2026.gem-main.30/

**扩展研究：** AMA https://aclanthology.org/2026.findings-acl.152/ ；PlanFence https://arxiv.org/abs/2609.03340 ；CoMemBench https://comembench.world/ 。其他论文精确版本在正式引用前使用公开原文与出版页复核。
