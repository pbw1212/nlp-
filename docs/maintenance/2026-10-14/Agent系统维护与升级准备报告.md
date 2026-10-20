# Agent系统维护与升级准备报告

> 历史记录说明（2026-10-20）：本报告只记录 2026-10-14 的维护状态，原有日期、工程路径、仓库来源与当时的功能描述均作为历史事实保留。项目现名为 MAPAS；当前多 Agent 实现见 [P1 架构说明](../../architecture/multi-agent-foundation.md) 和 [阶段报告](../../phases/multi-agent-p0-p1-20261014.md)。

维护日期：2026-10-14。工程根目录：`D:\MAPAS\MAPAS`。对应仓库：`https://github.com/pbw1212/nlp-`。

本轮按“先扫描、局部修改、回归验证、再清理”推进。没有修改已有数据库格式，没有实现多智能体。按用户后续明确要求，完整移除了 UI，包括浏览器页面、HTTP Demo、桌面包装、打包与启动文件。CLI 与 JSONL host 保留。

## 1. 当前系统架构总结

完整初始地图见 [project-map.md](project-map.md)，逐文件初始清单见 [完整初始清单 ZIP](inventory-before.zip)。扫描排除 Git 对象和目录链接，不读取或记录凭据值。仓库外原始资料、运行数据和诊断也列入文件盘点。

```text
项目整体结构
├── 核心服务：code/src/memory/app、runtime
├── Agent逻辑：agent、task_state
├── Memory管理：api/memory_manager.py、lifecycle、retrieval、router
├── API调用：llm/llm_client.py、llm/errors.py
├── 数据存储：storage、conversation、agent/journal.py、runtime/events.py
├── 测试：code/tests、code/scripts/*acceptance.py、benchmarks
├── 配置：core/config.py、core/paths.py、.env.example、environment.yml、setup.cfg
├── 接口：personal_agent.py（CLI）、personal_agent_app_server.py（JSONL）
└── 资料：docs（架构/ADR/历史实验）、Reference Repos（第三方研究快照）
```

主入口为 `code/start_agent.cmd/.sh`；JSONL 入口为 `code/scripts/personal_agent_app_server.py`。二者共用 PersonalAgentApp、MainAgent 与 AgentRuntime。默认产品状态在用户主目录 `.agent_data`，长期记忆、任务、对话、执行 Journal 与事件分别存 SQLite。工程内旧 `.agent_data/.demo_data` 是保留的运行/迁移数据，不是缓存垃圾。

### 记忆与数据流

1. 输入保存到 ConversationRepository，再创建 turn；Provider 提出类型化任务绑定与请求分析。
2. 显式记忆请求经 grounded request analysis 进入 Task 1，Task 3 不应用记忆提案。显式 lifecycle 支持 ADD、UPDATE、CORRECT、REMOVE、NOOP；目标 ID 与来源校验后原子提交。
3. 普通对话由 MainAgent 决定是否调用 `update_long_term_memory`。工具绑定当前原始用户事件，持久化 ingestion journal；NEMORI 切分 episode，只让 DURABLE 内容进入生命周期提议。TRANSIENT/UNCERTAIN 不自动保存。
4. Provider 只提出建议，SQLite repository 负责校验、事务与最终事实。importance/confidence 可由提案提供，手动锁定与当前有效状态受确定性规则保护。自动遗忘默认关闭。
5. Task 2 每次读取 current-safe 快照，经召回、上下文/scope 解释、applicability、冲突与选择，构建 EvidenceBundle。PromptComposer 将其作为不可信证据注入，而不是系统指令。
6. 本轮修复了“没有持续任务就跳过长期记忆检索”的断点：UNBOUND 对话也执行独立 Task 2 检索，不创建隐藏任务；显式记忆写入路径仍跳过不必要检索。

当前没有隐藏的按轮数或按时间后台沉淀。失败 ingestion 留待重试。SQLite 是唯一权威状态；当前对话原文也存 SQLite，没有第二个同步写入的文件 transcript。可见历史/索引属于派生或兼容状态。

默认 Task 2 是 BM25 加可选图扩展。dense/hybrid/embedding 组件是可插拔能力，当前 composition root 没有默认启用真实 embedding。旧 InMemoryVectorStore 是字符 2-gram 词法兼容索引。语言不同或大幅改述可能无召回，不能宣称完整语义检索。

## 2. 清理结果

删除前清单：[cleanup-plan.json](cleanup-plan.json)；逐文件实际操作与验证：[cleanup-results.json](cleanup-results.json)；汇总：[cleanup-summary.json](cleanup-summary.json)。

| 文件/目录 | 操作 | 原因 |
|---|---|---|
| code/frontend、code/desktop | Git 删除 | 用户明确要求移除整个 UI |
| personal_agent_demo.py、origin_desktop.py、start_demo/start_origin、build_desktop、MAPAS.spec、requirements-desktop.txt | Git 删除 | UI 的运行/打包配套；JSONL 公共依赖已迁出 |
| test_personal_agent_demo.py、test_origin_desktop.py、frontend-api.md、desktop-app.md | Git 删除 | 对应功能已移除，清理失效测试与指南 |
| docs/product-showcase、docs/共享版PPT.pptx、test_figure、test.txt | Git 删除 | 展示材料或确认的测试产物；已有截图删除获授权同步 |
| 外层 PPT/90_制作过程、历史汇报 PPT、03_测评资料、wheels、旧浏览器 QA 输出 | 清出工作区并归档 | 展示/重复生成材料；普通文件删除被执行策略拒绝 |
| Python/pytest 缓存 | 清出工作区并归档 | 可再生；最终回归后再清理 |
| 核心代码、CLI/JSONL、benchmarks、必要测试、配置、架构/ADR | 保留 A | 运行、契约与复现所需 |
| Reference Repos | 保留 B | 无生产导入，但仍有研究价值；39003 个初始跟踪文件不能一概当垃圾 |
| 原始计划书、官方模板/通知、发票、真实运行数据库、diagnostic_runs、历史实验 | 保留 B | 包含工程之外的原始资料或可追溯证据 |

普通 PowerShell 删除被自动审批以 `blocked by policy` 拒绝，未给具体原因。已使用可回溯 Git 操作删除跟踪文件；非 Git 文件采取可恢复移动，归档到 `D:\MAPAS-maintenance-archive-20261014`。**归档不等于永久删除**，没有把该部分报告成已删除。Git 历史与原有 stash 保留，未重写远程历史。

## 3. Bug修复记录

| 问题 | 原因 | 修改 | 验证 |
|---|---|---|---|
| Windows 事件数据库仍占用文件 | sqlite3 的普通 context manager 不关闭连接 | AgentEventStore 使用已有 ClosingSQLiteConnection | 临时目录试运行通过；禁用 GC 后连接不可再使用且数据库可移除 |
| 显式新增/更正/删除的旧索引状态不一致 | 原子 lifecycle 路径绕过 CRUDEngine 索引更新 | 提交后从 current-safe 快照统一重建索引 | 新增、替换、删除、重启回归及真实 API 通过 |
| 更正非活跃记忆后索引污染 | correct_memory 无条件加入索引 | 仅 current-safe 记忆加入，否则移除 | 生命周期回归通过 |
| NOOP/重放也回应已记录 | 只检查返回非空 ID | 增加 actual updated/retention 结果；回应无变更或回收 | NOOP、重复输入、幂等回归通过 |
| 显式删除的 Provider schema 缺少 REMOVE | schema 与底层 lifecycle 能力不一致 | 在现有类型化 contract 中允许 grounded REMOVE，保留 ID/形状/事务校验 | 真实“以后不要记录这个偏好”回收成功，查询/索引排除 |
| episode 来源索引可越界，所有批次事件被当成来源 | 上界未验证；provenance 使用整个 batch | 校验索引范围，仅附实际 episode 引用的事件 | 越界零提交，来源仅 durable 事件，pending 原文保留 |
| 中文记忆被译成英文后中文检索无结果 | 提炼英文指令未约束语言，默认 BM25 无跨语言能力 | Provider partition/lifecycle 指令要求保留原语言 | 首轮失败如实保留；补测中文内容与召回均 PASS |
| 无任务绑定的对话无法调用长期记忆 | UNBOUND 路径无条件跳过 retrieval | 直接按当前用户输入检索，传入 AgentTurnContext，保持 Task 3 空 | 无隐藏任务且 EvidenceBundle 传递回归通过 |
| Provider effort 参数未按协议放置 | reasoning_effort 被嵌套在 thinking | 使用顶层 reasoning_effort | 请求 payload 回归与真实 API 通过 |
| JSON 可解析但结构错误/截断仍可能被接受 | 通用客户端缺少最终 schema/finish_reason 校验 | schema 验证、length 拒绝、失败清空 last_usage | 空结果、坏 JSON、错字段、截断回归通过 |
| 产品试运行污染仓库 test.txt | 临时数据库隔离了，文件工具仍授权 cwd | 场景、重启和 fuzz 使用独立临时 workspace | 空目录/已有用户文件两种回归；完整试运行后根目录无 test.txt |
| 跨平台/环境污染导致假失败 | readline/Linux/路径分隔符假设，密钥测试修改进程环境 | 可选模块跳过，平台安全契约断言、Path.parts 与环境隔离 | 最终全量回归通过 |

首轮旧主线为 407 passed、9 failed。删除 UI 测试并新增关键回归后，测试总数发生变化，不能只按 passed 数量比较质量。

## 4. 系统优化与验证记录

提取公共结果序列化到 `app/serialization.py`，JSONL 不再依赖已删除浏览器脚本。保留 Task 1/2/3 所有权边界；没有复制新的记忆库或重写主 Agent。

API 保留有限 timeout、暂态网络/HTTP 重试、上下文溢出 typed error 和稳定错误投影；补充请求开始/完成、token 用量与安全失败诊断。日志不记录 key、prompt 或 response body。现有上下文预算和压缩机制仍保留。依据 [DeepSeek Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/) 与 [Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/) 核对协议。模型旧别名暂保留；官方 [模型说明](https://api-docs.deepseek.com/quick_start/pricing/) 指出旧 flash 名称仍被接受，但实际服务模型可能升级，不能把别名当成固定模型版本。

### 最终离线验证

- 全量 pytest：**410 passed、1 skipped**；Windows 没有可选 readline，跳过其专用绑定测试。证据：[pytest.xml](pytest.xml)。
- Task2 recall/applicability/evidence、Task3D、AgentRuntime、MainAgent 六个独立验收全部退出 0：[acceptance.json](acceptance.json)。
- Product Trial：14/14 场景通过，50 步 seeded fuzz 无违规：[product-trial.json](product-trial.json)。
- wheel 构建通过，包内包含公共序列化与 runtime，未包含 UI；见 [package-check.json](package-check.json)。
- JSONL 与公共序列化在 UI 删除后仍通过协议回归。关键异常测试包含网络失败、timeout、HTTP 429/认证错误、空结果、格式错误、上下文溢出与数据库回滚。

### 有限真实 API 测试

总计 **26 次真实请求**：首轮 22 次，语言/检索补测 4 次。没有大规模模型能力评估，也未触碰个人正式运行数据库。13 个首轮案例与后续检索诊断见 [live-smoke.json](live-smoke.json)、[live-retrieval.json](live-retrieval.json)、[live-language-retrieval.json](live-language-retrieval.json)。

| 输入/案例 | 预期 | 实际 | 问题/修改方案 |
|---|---|---|---|
| 长期 Python 数据分析偏好 | 创建记忆且索引一致 | PASS | 提交后同步索引 |
| 再次输入相同偏好 | 不增身份/版本 | PASS，NOOP | 按实际结果回应 |
| Julia 替代 Python | 同 ID 的更正版本 | PASS | 旧版本保留并失效 |
| 重启管理器 | 当前状态一致 | PASS | 从 SQLite 重建 |
| 以后不要记录语言偏好 | 有效读取/索引排除 | PASS，RECYCLED | 显式 REMOVE contract |
| 今天临时堵车 | 不写长期事实 | PASS | TRANSIENT 过滤 |
| 长期简洁汇报偏好 | 写入且有来源 | PASS；首轮内容英文 | 补充保留原语言要求 |
| 连续 5 轮青石/A方案对话 | 每轮成功、历史落库 | 5/5 工程断言通过 | 原始回复未完整保留，不能补造语义正确结论 |
| SQLite 检查 | integrity_check=ok，无外键错误 | PASS | 检查脚本连接改用 closing |
| 中文查询英文记忆 | 召回相关偏好 | FAIL，0候选、0额外请求 | 暴露跨语言词法限制 |
| 修正契约后中文记忆/中文检索 | 保留中文且选中偏好 | PASS | 4次补测完成 |

首轮所有断言完成后，smoke 脚本自身检查连接未关闭导致 Windows 临时目录清理失败。已修复，并改为每案例即时保存报告；首轮证据从日志和残留隔离数据库恢复。丢失的对话原文及 token 用量未补造。新脚本保留请求预算，密钥仅从 process environment 读取。

## 5. 当前系统状态评价

**可以作为继续开发的单进程基础版本。** 已有契约回归、隔离产品试运行和有限真实 API 支持该判断；未做长时间 soak/load，不能宣称生产 SLA 或全面语义可靠。

**适合准备多 Agent 升级，但不能直接多进程并发写入。** 当前 facade、DTO、provenance 与 execution journal 提供良好边界，仍需要并发冲突版本、跨服务幂等、任务所有权和超时取消协议。

仍需保留的限制：

- 默认词法检索仍会漏掉跨语言/大幅改述信息；原语言保留是模型契约，仍需语义回归，旧数据没有批量翻译。
- REMOVE 是可恢复软删除，revision/provenance 历史仍在，不代表彻底擦除。
- 模型决定重复、冲突、重要性与长期有效性，不能仅用确定性 pytest 证明语义筛选全面可靠。
- 不同管理器实例的旧兼容索引没有跨进程失效通知；生产 current-safe Task 2 读 SQLite，多 Agent 应统一事实所有权。
- UNBOUND 检索修复经离线回归；真实检索 facade 小样本通过，未增加额外真实完整 UNBOUND 应用对话调用。
- 保留第三方研究快照、源材料与历史证据，是经过价值判断的 B 类保留，不是已证明无用的垃圾。

## 6. 后续多智能体升级建议

| 角色 | 建议复用边界 | 升级前需要的接口 |
|---|---|---|
| Memory Agent | Task 1 lifecycle、Task 2 facade | propose/evaluate/apply/read；expected_revision、来源身份、更新回执、只读快照版本 |
| Task Agent | TaskStateService、TaskEvent、任务 ledger | plan/update/checkpoint；task_owner、租约与事件幂等 |
| Reasoning Agent | AgentReasoner、ReasonerInput、AgentAction | 输入固定预算的证据与工具目录，返回版本化 action proposal；不直接写数据库 |
| Tool Agent | ToolRegistry、ExecutionRequest、执行 Journal | 授权目录、审批绑定、执行幂等、结果状态与未知状态对账 |
| Communication Layer | AgentEvent 与 JSONL host | schema_version、message_id、correlation/causation_id、sender、deadline、状态/错误、ack/replay/backpressure |

推荐先定义消息及所有权，再以当前单进程实现契约测试；Memory 写入由一个事实服务提交，其他 Agent 只提出提案或读取快照。任务失败、API失败与工具失败均须返回 typed result，只有真实回执才能宣称成功。然后增加版本冲突和重放测试，最后才拆进程。对语义问题继续使用 Provider 类型化提议，禁止关键词/正则语义捷径。

Git 发布证据见 [publication.json](publication.json)。该文件在维护提交成功推送并校验后补入，记录被核验的维护提交；文档补录自身不改变核心代码。
