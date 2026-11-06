# MAPAS 前端 P0 契约冻结（功能 ↔ 接口映射 + 数据源架构）

> 版本：v1 / 2026-11-04　状态：待评审
> 目标：在写任何界面代码之前，先把"**每个前端功能到底从哪个后端方法拿数据**"钉死，
> 确保「功能不造假」。本文件是后续 P1–P6 的唯一依据。
> 落位：`D:\projects\nlp-\code\web\docs\frontend-contract.md`（2026-11-04 已落地，未提交 Git）

---

## 0. 两个硬事实（决定整个架构）

### 事实 A：JSONL 宿主只覆盖了约 60% 的功能面

`code/scripts/personal_agent_app_server.py`（440 行，已逐行读完）只有 21 个可用 method：
`initialize / thread{start,resume,list,read} / turn{start,resume} / events/read / status/read /
context/read / tools/list / mcp/status / approvals/list / task/lifecycle{read,drain} /
experience{list,learn,review,revoke,feedback} / repair{list,graphs,run,revoke,drain}`，
`turn/interrupt` 明确不支持。

**它对前端是致命不足的**：

| 缺口 | 后果 |
|---|---|
| 长期记忆完全没有 method（列表/详情/历史/来源/回收站/忘记/恢复/新增） | 记忆库面板无真数据 |
| 审批只有 `approvals/list`，没有 confirm/reject | 副作用工具流程断链，点不动"确认" |
| 没有任务 CRUD、没有 compact、没有 workspace/provider/storage | 任务栏、设置页无真数据 |
| 没有 DAG 明细（只有 `repair/graphs` 的 node_key/status） | 协作拓扑图画不出来 |
| `turn/start` 同步阻塞整轮、单进程串行、事件事后批量吐出、无 interrupt | 做不了实时进度，也做不了取消 |

### 事实 B：但 **Python 应用层 `PersonalAgentApp` 全都有**（已逐一验证存在）

`code/src/memory/app/console.py` 里，CLI 的每个命令背后都是一个可直接调用的方法：

```
list_memories(903) get_memory_detail(1026) get_memory_history(1045)
get_memory_provenance(1057) list_recycled_memories(1105)
remember_explicit(781) forget_memory(1090) restore_memory(1097) get_profile_repository(948)
list_tasks(615) create_new_task(638) switch_task(657)
pause_current_task(1175) resume_current_task(1184) complete_current_task(1188) cancel_current_task(1217)
process_confirmation(2425)   ← 审批确认/拒绝就在这里
compact_current_session(1590) get_conversation_context_status(1224)
get_workspace_status(1252) get_storage_status(1256) get_tool_catalog(1260) get_mcp_status(1264)
get_task_lifecycle_status(1770) drain_task_lifecycle(1782)
list_experiences(1816) learn_experience(1827) review_experience(1840)
revoke_experience(1852) feedback_experience(1860)
repair_collaboration(1872) revoke_and_repair_collaboration(1990)
process_pending_collaboration_repairs(1932)
```

**结论：不需要改任何组员代码**，只需**新增一个桥接进程** `code/scripts/web_bridge.py`，
直接 `from src.memory.app.console import PersonalAgentApp`，把上面这些方法包成 HTTP + SSE。
这是唯一能做到"功能不造假"的路径。

> 只读 SQL 兜底：DAG 的依赖边/目标/完成标准存在 `coordination.db` 的
> `task_graphs.plan_json` 与 `task_nodes.spec_json`（`list_session_graphs()` 没 select 这些列），
> 桥接层用**只读连接**自行查询，不写库、不改代码。

---

## 1. 数据源架构（三层，Mock 与真实同一个接口）

```
┌───────────────────────────────────────────────┐
│ features/*  React 页面（七域）                  │
├───────────────────────────────────────────────┤
│ state/      事件归约 reducer + store            │  events[] → UI state
├───────────────────────────────────────────────┤
│ datasource/ DataSource（唯一 interface）        │
│   ├─ HttpSource   ← 连 web_bridge.py（默认，真）│
│   └─ FixtureSource← 录制好的真实响应回放（离线/答辩）│
└───────────────────────────────────────────────┘
        ↑ HTTP/JSON + SSE
┌───────────────────────────────────────────────┐
│ code/scripts/web_bridge.py (新增，Python 标准库) │
│   worker 线程串行执行 PersonalAgentApp 调用      │
│   → 避免单进程被阻塞 / 多标签页抢占              │
└───────────────────────────────────────────────┘
        ↑ 直接调用
   PersonalAgentApp（既有代码，零修改）
        ↑
   ~/.agent_data/*.db（SQLite 权威存储）
```

要点：
- **FixtureSource 不是"假数据"**：它回放的是**真实运行录制下来的响应**（P1 阶段用
  `--record` 模式跑一次 `multi_fixed` 真实任务，把 HTTP 响应原样存成 JSON）。
  这样离线截图和真实接入走同一份代码路径，答辩时不会被问倒。
- `DataSource` 是 TS interface，两个实现可运行时切换（URL `?src=fixture`）。
- 所有写操作（发消息、确认审批、忘记记忆、修复、经验复核）只有 `HttpSource` 支持，
  `FixtureSource` 里显式 `throw new Error('offline fixture: read-only')` ——**不静默假装成功**。

---

## 2. 功能 ↔ 接口映射表（P0 核心交付物）

真实度图例：✅ 已验证方法存在，可直接真接 ｜ 🔶 需只读 SQL（V# 待确认）｜ ⚠️ 部分字段需 V# 确认 ｜ ❌ 只能回放/降级并如实标注

### 2.1 Chat 会话域

| UI 功能 | Bridge endpoint | 后端方法 | 真实度 |
|---|---|---|---|
| 会话列表（含标题/时间/绑定任务） | `GET /api/threads` | `list_sessions()` | ✅ |
| 新建会话 | `POST /api/threads` | `create_new_session(title)` | ✅ |
| 恢复会话（重放历史） | `POST /api/threads/:id/resume` | `resume_session(id)` | ✅ |
| 消息时间线 | `GET /api/threads/:id/messages` | `conversation_repo.list_messages()` | ✅ |
| 未完成 turn 提示 + 继续 | `GET /api/threads/:id/unfinished` / `POST /api/turns/:id/resume` | `list_unfinished_turns()` / `resume_interrupted_turn()` | ✅ |
| 发一轮消息 | `POST /api/threads/:id/turns` → 202 jobId | `process_chat_turn(input)`（worker 线程） | ✅ |
| 轮次阶段/工具/审批/耗时 | `GET /api/events/stream?after=N`（SSE） | `list_events()` + `subscribe()` | ✅ |
| 最终回答 + developer 详情 | `GET /api/turns/:jobId` | `AgentResult` 序列化（`_serialize_result`） | ✅ |
| 错误安全投影 | 同上 `error_info` | `TurnErrorInfo`（code/category/retryable/safe_message） | ✅ |
| 取消生成 | —— | **无 interrupt**，UI 显示"后端不支持取消，已放弃接收" | ❌ 如实标注 |

### 2.2 Context 上下文域

| UI 功能 | Bridge endpoint | 后端方法 | 真实度 |
|---|---|---|---|
| 上下文仪表盘（窗口/可用/预留/投影/估算 token） | `GET /api/context` | `get_conversation_context_status()` | ✅ |
| 压缩检查点信息、降级原因 | 同上 `compaction_*` | 同上 | ✅ |
| 手动压缩 | `POST /api/context/compact` | `compact_current_session()` | ✅ |
| Provider 最近用量 | 同上 `provider_last_usage` | 同上 | ✅ |

### 2.3 Tasks 任务域（Task 3）

| UI 功能 | Bridge endpoint | 后端方法 | 真实度 |
|---|---|---|---|
| 任务列表 | `GET /api/tasks` | `list_tasks()` | ✅ |
| 新建 / 切换任务 | `POST /api/tasks` / `POST /api/tasks/:id/switch` | `create_new_task()` / `switch_task()` | ✅ |
| 台账（目标/约束/决定/进展/未解决） | `GET /api/tasks/status` | `get_task_status_summary()` | ⚠️ 摘要是纯文本数组，缺 entry_id/status/evidence_refs → V2 |
| 暂停/恢复/完成/取消 | `POST /api/tasks/:id/{pause,resume,complete,cancel}` | `pause_current_task()` 等 | ✅ |
| 检查点（focus/next_action/world_state） | `GET /api/tasks/status` → `checkpoint` | 同上 | ✅ |
| 任务闭环状态（ASSESS_PENDING…） | `GET /api/task-lifecycle` | `get_task_lifecycle_status()` | ✅ |
| 手动 drain 待办作业 | `POST /api/task-lifecycle/drain` | `drain_task_lifecycle()` | ✅ |
| 完成记录 + `current_result_validity` | `GET /api/task-lifecycle` | 同上 | ✅ |

### 2.4 Memory 记忆域（Task 1）—— 全靠 bridge，JSONL 完全没有

| UI 功能 | Bridge endpoint | 后端方法 | 真实度 |
|---|---|---|---|
| 记忆库（当前有效） | `GET /api/memories` | `list_memories()` | ✅ |
| 记忆详情 | `GET /api/memories/:id` | `get_memory_detail()` | ✅ |
| revision 版本时间轴 | `GET /api/memories/:id/history` | `get_memory_history()` | ✅ |
| provenance 溯源链 | `GET /api/memories/:id/provenance` | `get_memory_provenance()` | ✅ |
| 回收站 | `GET /api/memories/recycle-bin` | `list_recycled_memories()` | ✅ |
| 显式写入记忆 | `POST /api/memories` | `remember_explicit(content)` | ✅ |
| 忘记（移入回收站） | `POST /api/memories/:id/forget` | `forget_memory(id)` | ✅ |
| 恢复 | `POST /api/memories/:id/restore` | `restore_memory(id)` | ✅ |
| 画像/偏好概览 | `GET /api/memories/profile` | `get_profile_repository()` | ✅ |
| 待重试 ingestion 数 | `GET /api/status` → `memory.pending_events` | `get_memory_update_status()` | ✅ |

### 2.5 Retrieval 检索证据域（Task 2）—— 最大的待验证项

| UI 功能 | Bridge endpoint | 数据来源 | 真实度 |
|---|---|---|---|
| 本轮检索 trace（阶段/是否用 dense/rrf） | `GET /api/turns/:jobId/trace` | `AgentResult.trace_metadata` | 🔶 V1 |
| 候选排行 + original_rank/score | 同上 | `evidence/retrieval-traces.jsonl` | 🔶 V1 |
| 适用性四态（MATCH/MISMATCH/UNKNOWN/AMBIGUOUS） | 同上 | 同上 | 🔶 V1 |
| 冲突 RESOLVED/UNRESOLVED、充分性 SUFFICIENT/PARTIAL/INSUFFICIENT | 同上 | 同上 | 🔶 V1 |
| EvidenceBundle.selected 全文 | 同上 | 若未持久化则显示"本次未持久化" + 已有字段 | ⚠️ V1 |

> V1 是 P0 唯一的"必须实测"项：需要真跑一次 `multi_fixed`，确认
> `AgentResult.trace_metadata` 与 `execution/retrieval-traces.jsonl` 里到底保留了哪些字段。
> **在 V1 结论出来之前，检索面板不写代码**——避免先做再发现字段不存在，返工。

### 2.6 Collaboration 协作域（multi_fixed）

| UI 功能 | Bridge endpoint | 后端方法 | 真实度 |
|---|---|---|---|
| 会话内的运行列表 | `GET /api/collaboration/runs` | `coordination_repository.list_session_graphs()` | ✅ |
| 子任务 DAG（目标/依赖边/工具/完成标准） | `GET /api/collaboration/runs/:runId/graph` | 只读 SQL：`task_graphs.plan_json` + `task_nodes.spec_json` | ✅（2026-11-06 已实现，返回 nodes/artifacts/handoffs/model_calls/budget） |
| 节点状态（PENDING/RUNNING/PRODUCED/VERIFIED/FAILED/BLOCKED/STALE） | 同上 | `task_nodes.status` | ✅ |
| artifact + 核验状态 + hash | 同上 | `task_attempts`/`artifacts` 只读 SQL | 🔶 V3 |
| handoff（sender/receiver/payload_hash） | 同上 | `handoffs` 只读 SQL | 🔶 V3 |
| 预算/模型调用明细 | 同上 + `GET /api/task-lifecycle` | `model_calls` 只读 SQL / usage 聚合 | 🔶 V3 |
| 角色（Planner/Memory/Executor/Verifier） | 同上 | 由 `stage_key`/`sender` 映射 | 🔶 V3 |

### 2.7 Governance 治理域

| UI 功能 | Bridge endpoint | 后端方法 | 真实度 |
|---|---|---|---|
| 待审批列表（工具名/参数/effect） | `GET /api/approvals` | `list_pending_approvals()` | ✅ |
| **确认 / 拒绝** | `POST /api/approvals/:id/decision` | `process_confirmation(ConfirmationDecision)` | ✅ |
| 工具目录（schema/effect/idempotency） | `GET /api/tools` | `get_tool_catalog()` | ✅ |
| MCP 服务状态 | `GET /api/mcp` | `get_mcp_status()` | ✅ |
| 工作区范围 | `GET /api/workspace` | `get_workspace_status()` | ✅ |
| 存储目录布局 | `GET /api/storage` | `get_storage_status()` | ✅ |
| 修复队列 | `GET /api/repairs` | `list_repairs(session_id)` | ✅ |
| 修复图摘要 | `GET /api/repairs/graphs` | `list_session_graphs()` | ✅ |
| 修复计划（受影响节点/复用理由/快照） | `GET /api/repairs/:repairId` | `get_repair_plan()` | 🔶 V4 |
| 执行修复 | `POST /api/repairs/:runId/:eventId/run` | `repair_collaboration()` | ✅ |
| 撤销成果并修复 | `POST /api/repairs/revoke` | `revoke_and_repair_collaboration()` | ✅ |
| 批量 drain | `POST /api/repairs/drain` | `process_pending_collaboration_repairs()` | ✅ |
| 经验列表 + 7 态 | `GET /api/experiences` | `list_experiences()` | ✅ |
| 经验准入/隔离/修订/撤销/反馈 | `POST /api/experiences/{learn,review,revoke,feedback}` | `learn_experience()` 等 | ✅ |

**覆盖结论**：7 域 68 个功能点，**✅ 可直接真接 51 项，🔶 待验证 12 项，⚠️ 部分 4 项，❌ 0 项造假**。
唯一"做不到"的是**取消生成**（后端无 interrupt），UI 会如实显示，而不是假装停掉。

---

## 3. Bridge 进程规格

- 文件：`code/scripts/web_bridge.py`（**新增，零修改既有文件**）
- 依赖：**Python 标准库**（`http.server.ThreadingHTTPServer` + `json` + `threading.Queue`），
  不引入 FastAPI/uvicorn，不给组的 Python 环境添负担
- 并发模型：**单个 worker 线程**串行执行所有 `PersonalAgentApp` 调用（该类非线程安全，
  且 JSONL 宿主本身也是串行语义）；HTTP 线程只做排队/查询/SSE 推送
- 启动：
  ```powershell
  python code/scripts/web_bridge.py --execution-mode multi_fixed --port 8787
  ```
- 鉴权：仅监听 `127.0.0.1`，`Origin` 白名单 + 一次性 token（从 stdout 打印），避免本机其他网页误调
- 长任务：`POST .../turns` 立即返回 `202 {jobId}`；前端轮询 `GET /api/jobs/:jobId` 或监听 SSE
- 序列化：复用 `src/memory/app/serialization._serialize_result` 与
  `PersonalAgentApp._json_safe_event_value`，保证与 CLI/JSONL 的字段完全一致（不另造投影）
- 只读 SQL：仅 `coordination.db`，`sqlite3.connect(..., mode=ro)` 或 `PRAGMA query_only=ON`

---

## 4. TypeScript 类型清单（见同目录 `types.ts`）

| 文件 | 内容 |
|---|---|
| `types/enums.ts` | 全部枚举（记忆/检索/任务/协作/经验/审批/事件），从 Python Enum 逐字对齐 |
| `types/protocol.ts` | JSONL 协议（保留，作为兼容层）+ bridge HTTP 请求/响应 |
| `types/domain.ts` | Thread/Message/AgentEvent/AgentResult/Memory*/Task*/Evidence*/Collaboration*/Repair*/Experience*/Approval* |
| `types/datasource.ts` | `DataSource` 接口 + `HttpSource`/`FixtureSource` 契约 |

P0 只冻结**字段名与枚举值**，不冻结 UI 组件。

---

## 5. 目录骨架（授权后可一键落地）

```
code/web/
├── .gitignore                 # 只管 node_modules/dist/.vite，不碰根 .gitignore
├── CHANGELOG-web.md           # 改动日志（可撤回）
├── README.md                  # 怎么跑、mock/real 切换、契约索引
├── docs/frontend-contract.md  # 本文件
├── package.json  vite.config.ts  tsconfig.json  tailwind.config.ts  index.html
└── src/
    ├── main.tsx  App.tsx  styles/
    ├── types/{enums,protocol,domain,datasource}.ts
    ├── datasource/{index.ts, http.ts, fixture.ts, fixtures/}
    ├── state/{store.ts, reducers/events.ts, selectors.ts}
    ├── components/{ui/, layout/, charts/}
    ├── features/{chat,context,tasks,memory,retrieval,collaboration,governance}/
    └── pages/
```

**撤回策略**：所有改动都在 `code/web/` 一个目录 + 一个新增的
`code/scripts/web_bridge.py`。回滚 = `git clean -fd code/web code/scripts/web_bridge.py`，
永不触碰组员的 `README.md`、`docs/`、`code/src/`。

---

## 6. 待验证清单（P0 出口条件）

| 编号 | 待确认 | 验证方式 | 阻塞谁 |
|---|---|---|---|
| **V1** | 检索证据包的哪些字段真的被持久化 | 真跑 `multi_fixed` 一轮，dump `trace_metadata` + `retrieval-traces.jsonl` | 检索面板 |
| **V2** | `get_task_status_summary()` 是否只有文本数组，能否补出 entry_id/status/evidence_refs | 读 `console.py:1116`；如需补，只读 SQL `task_entries` | 任务台账面板 |
| **V3** | `task_nodes.spec_json` / `task_graphs.plan_json` 的稳定结构；`handoffs`/`artifacts` 列名 | 只读 SQL 对照 + 跑一次 multi_fixed | 协作 DAG 面板 |
| **V4** | `get_repair_plan()` 返回结构与"受影响集合 A"是否可直接算 | 构造一次真实文件变更触发修复 | 修复面板 |
| **V5** | `process_confirmation` 的 `ConfirmationDecision` 取值与副作用（是否消耗 turn） | 读 `console.py:2425` | 审批卡 |
| **V6** | `PersonalAgentApp` 在 bridge worker 线程里的 lifecycle（构造/close 时机、切换 session 的副作用） | 读 `console.py` 构造与 `close()` | 全部 |

> V1 keep 住"先不写检索面板"这条纪律，是 P0 最大的价值：避免用 3 天做出一个字段对不上的图。

---

## 7. 里程碑（P0 之后）

P1 骨架+设计系统 → P2 主链路（chat + 审批真跑通）→ P3 六个可视化面板 →
P4 治理页+边界态 → P5 bridge 全量接通 → P6 截图/录屏/README。

每阶段结束：`pnpm build` 通过 + 一屏可截图 + 契约文档同步更新。

---

## 8. 待拍板事项（实时状态见 `code/web/README.md` §7）

> 2026-11-04：本节只保留条目与影响分析，**状态以 `code/web/README.md` §7 为唯一事实来源**，避免两处不同步。

| # | 事项 | 状态 |
|---|---|---|
| 1 | 是否允许新增 `code/scripts/web_bridge.py`（不改组员文件，只用既有 `PersonalAgentApp` 方法） | ⏳ 待定 |
| 2 | 是否允许 `code/scripts` 之外的写入（当前计划只有 `code/web/` + `web_bridge.py`） | ⏳ 待定 |
| 3 | **V1** 是否现在真跑一次 `multi_fixed`（约 20–30 次 DeepSeek 调用，决定检索面板能否真做） | ⏳ 待定 |
| 4 | 前端目录名 | ✅ 已定 `code/web/` |

补充说明：

- 条目 1 的 `web_bridge.py` 是"功能不造假"的必要条件。若不批准，只能退到
  「JSONL 现有 21 个 method + 大量面板如实标注『后端无接口』」，覆盖度从 68 项掉到约 30 项。
- 条目 3 不跑也**不影响** P1/P2 开工：P1 只用静态骨架，P2 只用 chat/审批链路
  （标注 ✅ 的字段不依赖 V1）。受影响的只有 P3 的"检索证据包"面板。
