# 第一阶段：项目地图与清理判定

扫描日期：2026-10-14。工作区 `D:\MAPAS`，Git 根目录 `D:\MAPAS\MAPAS`。本地原为 `9ee53672`，只读比较远程后快进到 `bb71641e`；保留已有 stash。扫描文件清单见 inventory-before.zip 内的 inventory-before.json（排除 Git 对象、目录链接，不包含文件正文或凭据）。

```text
项目整体结构
├── 核心服务模块：code/src/memory/runtime、app
├── Agent逻辑模块：code/src/memory/agent、task_state
├── Memory管理模块：code/src/memory/api、lifecycle、retrieval、router
├── API调用模块：code/src/memory/llm
├── 数据存储模块：storage、conversation、agent/journal.py、runtime/events.py
├── 测试模块：code/tests、code/scripts/*acceptance.py、benchmarks
├── 配置模块：core/config.py、core/paths.py、.env.example、environment.yml、setup.cfg
├── 前端/接口模块：code/frontend、personal_agent_demo.py、personal_agent_app_server.py
└── 其他文件：Reference Repos、docs、test_figure、工作区外层展示材料
```

## 入口与所有权

- `code/scripts/personal_agent.py`：CLI；`start_agent.cmd/.sh` 启动。
- `personal_agent_demo.py`：本地 HTTP 服务，静态前端与 API；调用同一个 PersonalAgentApp。
- `personal_agent_app_server.py`：JSONL host、会话、事件与审批接口。
- `origin_desktop.py`：桌面包装与本地密钥配置；不是独立 Agent 实现。
- Task 1 管理 memory identity/revision/provenance/retention；Task 2 只读当前有效快照并构建 EvidenceBundle；Task 3 管理任务状态。MainAgent 管理有限推理循环、工具与执行 Journal。
- 默认产品数据位于用户目录 `.agent_data`，不是仓库数据；AGENT_DATA_DIR/AGENT_HOME 可覆盖。长期记忆、任务、对话、执行分别存 SQLite。旧项目级 `.agent_data` 是迁移输入，需要保留。

## 记忆完整链路

用户输入 → PersonalAgentApp 保存会话/构造 turn → Provider 类型化任务绑定及请求分析 → MainAgent。

显式记忆请求：Provider 提议 memory intent/content，Task 3 proposal 必须为空 → Task 1 显式 lifecycle 提案 → 类型、目标、来源校验 → SQLite 原子提交 revision、provenance、ingestion 结果 → 才能回应写入结果。

普通请求：MainAgent 按需调用 `update_long_term_memory`，工具绑定原始用户事件 → 持久化 ingestion journal → NEMORI partition → 仅 DURABLE episode 进入 lifecycle 提案（ADD/UPDATE/CORRECT/REMOVE/NOOP）→ 校验 → 原子提交。失败事件保留供重试；当前版本没有隐藏的定时/轮数后台触发。

未来对话：Task 1 current-safe 快照 → lexical/dense/hybrid recall → Provider 上下文与 scope 解释 → applicability/冲突/选择 → EvidenceBundle → PromptComposer 注入。无任务绑定轮次的 runtime 检索被跳过，初始代码没有独立画像注入补救，构成调用断点；本轮已修复为无任务绑定也检索 Task 2，见最终报告。

`InMemoryVectorStore` 是旧字符 2-gram 索引，不是真实 embedding。Task 2 有可插拔 dense backend；不能把默认召回描述为 embedding 语义质量保证。SQLite 是权威源，文件 transcript 和索引为派生数据。

## 初轮问题

最新主线回归：407 passed、9 failed。事件库连接没有 context-exit close，Windows 临时目录清理失败；另有 readline/Linux 环境假设、路径分隔符和桌面密钥测试污染。

审查发现显式 lifecycle 写入绕过旧索引同步，NOOP 可错误引用任意第一条记忆，episode 来源索引未校验上界；前端 1.6 秒统一超时、失败后演示回答、假样本和未确认仍显示修改成功构成反馈断点。

## A/B/C 分类与删除依据

| 对象 | 分类 | 判断与操作 |
|---|---|---|
| code/src、必要入口、测试、配置、benchmarks、架构/ADR | A | 保留；支撑运行、契约和复现 |
| 真实浏览器/桌面/JSONL 接口 | A | 保留，修复反馈一致性 |
| Reference Repos 第三方快照 | B | 无运行导入，仍有学习价值；保留，后续建议独立资料仓库 |
| 历史 research/phases/evaluations、源需求材料 | B | 保留；提供历史决策和实验依据，不据此宣称当前通过 |
| 产品 .agent_data/.demo_data、diagnostic_runs | B | 保留真实数据和诊断，不作为垃圾删除 |
| PPT 汇报、test_figure、product-showcase、外层 03_测评资料 | C | 展示或重复生成材料，用户明确授权；删除前逐文件记录 |
| 外层 00_原始资料 | B | 包含发票/官方通知等非工程个人资料；保留 |
| __pycache__、pytest cache、旧渲染/制作过程 | C | 派生输出，不影响运行，可再生；清理 |
| Team Space/自动化前端 shell 与虚构 seed | C | 无对应后端协同或调度能力；移除界面入口与代码 |

未读核心代码不得按文件名删除；本轮不改数据库格式，不实现多 Agent。
