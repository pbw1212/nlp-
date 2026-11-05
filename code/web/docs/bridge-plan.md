# web_bridge.py 说明与审批材料

> 用途：给项目负责人评审"是否允许新增 `code/scripts/web_bridge.py`"这一个文件。
> 状态：**未创建、未批准**。本文件只做说明，不含实现。
> 相关：`code/web/README.md` §8、`code/web/docs/frontend-contract.md` §0/§3

---

## 1. 一句话

`web_bridge.py` 是**浏览器和现有 Python 系统之间的一层薄适配器**：它把
`PersonalAgentApp` 已经存在的 44 个公开方法包成 HTTP 接口，让网页能读到
"长期记忆、任务台账、审批、协作 DAG、修复、经验"这些**现在只有 CLI 能看**的东西。

它**不新增任何业务逻辑**，**不修改任何现有文件**，**不直接改数据库**（读除外）。

---

## 2. 为什么必须有它

前端只有两条路能拿到数据：

| 方案 | 能力覆盖 | 结论 |
|---|---|---|
| 只用现有 JSONL 宿主 `personal_agent_app_server.py` | **约 30 / 68 项** | 记忆库、审批操作、任务 CRUD、协作 DAG、上下文压缩、工作区等**全部做不到** |
| 加一层 bridge 直连 `PersonalAgentApp` | **68 / 68 项** | 唯一能满足"功能不造假"的方案 |

具体缺口（来自逐行读 `personal_agent_app_server.py` 440 行 + 逐字核对 `console.py`）：

| 缺口 | 后果 |
|---|---|
| JSONL 完全没有 `memory/*` 方法 | 「记忆库 / 版本时间轴 / 溯源 / 回收站」四个界面无真数据 |
| JSONL 只有 `approvals/list`，**没有 confirm/reject** | 副作用工具的确认流程断链，网页点不动"确认"（**CLI 里是有这个能力的**） |
| JSONL 没有任务 CRUD、没有 `compact`、没有 workspace/provider/storage | 任务栏、上下文仪表盘、设置页无真数据 |
| JSONL 没有 DAG 明细（`repair/graphs` 只给 node_key/status） | 「协作拓扑图」画不出来 |
| `turn/start` 同步阻塞、单进程串行、事件事后批量抛出、无 interrupt | 网页做不了实时进度（这一条 bridge 也只能缓解，不能根治） |

**关键判断：这些能力在 Python 里全都已经实现了**，只是没有被 JSONL 协议暴露。
所以我们不需要"重做一遍"，只需要"翻译一层"。

---

## 3. 它具体做什么

```
浏览器 (React)
   │  HTTP/JSON + SSE(Server-Sent Events)
   ▼
web_bridge.py  ──────────────────────────────────────────────
   │  ① 单 worker 线程：串行执行所有 PersonalAgentApp 调用
   │     （该类不是线程安全的，JSONL 宿主本身也是串行语义）
   │  ② HTTP 线程只负责：接收请求、排队、返回状态、推送事件
   │  ③ 只读 SQL：仅 coordination.db，用于 DAG 的边/目标/产物明细
   ▼
PersonalAgentApp（既有代码，零修改）
   ▼
~/.agent_data/*.db（SQLite 权威存储）
```

设计要点：

- **标准库实现**：`http.server.ThreadingHTTPServer` + `json` + `threading`，
  不引入 FastAPI/uvicorn，不给组的 Python 环境加依赖（当前只有 `jsonschema`）。
- **只监听 127.0.0.1**，加一次性的本地 token，避免本机其他网页误调。
- **长任务不阻塞界面**：`POST /turns` 立即返回 `202 {jobId}`，worker 线程在后台跑，
  界面通过 SSE 或轮询取事件与结果。
- **序列化复用现有代码**：直接调用 `serialization._serialize_result` 和
  `PersonalAgentApp._json_safe_event_value`，保证字段和 CLI/JSONL 完全一致，
  **不另造一套投影**。
- **只读 SQL 兜底**：`task_graphs.plan_json`、`task_nodes.spec_json` 这些
  `list_session_graphs()` 没有 select 的列，用 `PRAGMA query_only=ON` 的只读连接补出来。

接口清单（完整字段见 `src/types/index.ts` §10–§11）：

| 域 | 主要端点 | 对应的既有权能 |
|---|---|---|
| 会话 | `GET/POST /api/threads`、`POST /api/threads/:id/turns` | `create_new_session` / `resume_session` / `process_chat_turn` |
| 事件 | `GET /api/events/stream`（SSE） | `list_events` + `subscribe` |
| 记忆 | `GET/POST /api/memories…`（含 history/provenance/回收站/forget/restore） | 同名方法，全部已存在 |
| 任务 | `GET/POST /api/tasks…`（含 pause/resume/complete/cancel） | 同名方法，全部已存在 |
| 审批 | `GET /api/approvals`、`POST /api/approvals/:id/decision` | `list_pending_approvals` / **`process_confirmation`** |
| 协作 | `GET /api/collaboration/runs[/:id/graph]` | `coordination_repository.list_session_graphs` + 只读 SQL |
| 修复 | `/api/repairs…` | `list_repairs` / `get_repair_plan` / `repair_collaboration` / `revoke_and_repair_collaboration` |
| 经验 | `/api/experiences…` | `list_experiences` / `learn` / `review` / `revoke` / `feedback` |
| 诊断 | `/api/context`、`/api/tools`、`/api/mcp`、`/api/workspace`、`/api/storage` | 同名方法，全部已存在 |

---

## 4. 它不做什么（划清边界）

- ❌ 不修改 `personal_agent_app_server.py`、`console.py` 或任何现有文件。
- ❌ 不重新实现 Agent / 检索 / 记忆逻辑，只调用既有方法。
- ❌ 不写业务数据库；写操作全部走 `PersonalAgentApp` 的既有方法（保持事务与校验语义）。
- ❌ 不做多用户、鉴权体系、部署方案（课设范围内不需要）。
- ❌ 不解决"实时流"的根本问题：现有 `turn/start` 语义是一次性完成整轮，
  bridge 只能把"等待"变成"后台跑 + 事件推送"，不能变成逐字流式。

---

## 5. 写入范围（对应你问的"允许区域以外的写入"）

**结论：已批准放在操作区内，不再有区域外写入。**

| 路径 | 说明 |
|---|---|
| `code/web/bridge/web_bridge.py` | 桥接进程，**已批准**放在前端目录内 |
| `code/web/**` | 前端全部代码 |

因此本项目**不再需要**在 `code/web/` 之外新增任何文件，也不修改仓库既有文件：

- 不改根 `README.md`、`docs/**`、`code/src/**`、`code/tests/**`、`setup.cfg`、`.gitignore`（都属于组员）。
- 运行期产物（`~/.agent_data/*.db`、`.runtime/`）已被根 `.gitignore` 忽略，不进仓库。

---

## 6. 代价与风险（你审批前该知道的）

| 风险 | 说明 | 缓解 |
|---|---|---|
| 多开一个本地端口 | 127.0.0.1 上的 HTTP 服务 | 仅本机、一次性 token、默认端口可改 |
| 需要真实 API Key | 真跑对话会消耗 DeepSeek 调用 | 与 CLI 同一套 `~/.agent_data/config/.env.local`，不新增凭据 |
| 并发模型限制 | `PersonalAgentApp` 非线程安全 → 单 worker 串行 | 显式串行 + 队列，拒绝并发写；多标签页会排队 |
| 长期维护成本 | 多一个进程要跟着 `console.py` 的 API 变化走 | 只调用**公开方法**，不改内部；字段不重新投影，后端变了前端立刻能反映 |
| 与组员代码的一致性 | 若组员重构 `PersonalAgentApp`，bridge 会失效 | 失效表现为明确报错（不静默降级），易于发现和修复 |

---

## 7. 不批准时的退路（效果对比）

| 面板 | 批准 bridge | 不批准 |
|---|---|---|
| 会话 / 事件时间线 / 工具卡 | ✅ 真 | ✅ 真（JSONL 够用） |
| 上下文仪表盘 | ✅ 真 | ✅ 真（`context/read`） |
| 审批卡 | ✅ 可确认/拒绝 | ⚠️ **只能看，点不动** |
| 记忆库（列表/版本/溯源/回收站） | ✅ 真 | ❌ 只能标注"后端无接口" |
| 任务台账 / 检查点 | ✅ 真 | ⚠️ 只有文本摘要 |
| 协作 DAG | ✅ 真 | ⚠️ 只有节点状态，没有边和目标 |
| 修复 / 经验 | ✅ 真 | ✅ 大体够用（JSONL 已暴露） |
| 上下文压缩 / 工作区 / 存储 | ✅ 真 | ❌ 无接口 |

覆盖度：**68 项 → 约 30 项**。

---

## 8. 决策状态

| 事项 | 状态 |
|---|---|
| 是否允许新增 bridge | ✅ 已批准 |
| 位置 | ✅ 已定 `code/web/bridge/web_bridge.py`（不碰 `code/scripts/`） |
| "单 worker 串行 + 长任务后台跑 + SSE"并发模型 | ⏳ 待确认（P5 实现前必须定；若否决则退化为"整轮跑完再刷界面"） |
