# MAPAS Web 前端

> **网页使用指南请直接阅读第 14 部分。**

面向长程任务的多智能体记忆系统（MAPAS）的 **Web 界面层**。本目录包含：

- 一个 React 单页应用（7 个界面）；
- 一个本地桥接进程（Python，把后端 `PersonalAgentApp` 暴露为 HTTP + SSE）；
- 与后端交互的字段契约、录制回放数据、缺陷记录与使用文档。

技术栈：**React 19 + TypeScript 5.9 + Vite 8 + Tailwind 4**（无第三方状态库、无图表库、无 UI 组件库）。

---

## 1. 当前状态

| 项 | 状态 |
|---|---|
界面 | 7 个页面全部接入真实后端数据（对话 / 记忆 / 证据 / 任务 / 协作 / 治理 / 上下文） |
数据源 | 真实桥接（http）与录制回放（fixture，只读）两种，可运行时切换 |
桥接 | 29 组路由，覆盖会话、轮次、事件流、审批、上下文、记忆、任务、协作、治理、模式、口令、API Key |
静态检查 | `pnpm typecheck`（tsc）与 `pnpm build`（vite）均通过 |
自动化测试 | 前端暂无单测/E2E；有手工验收流程 `docs/test-plan.md` |
已知阻塞 | 无。历史问题与修复确认见 `docs/bug-report-verifier-receipts.md` |

代码规模（不含录制数据）：

```
src/            48 个文件 / 约 11,900 行（ts + tsx + css）
bridge/         web_bridge.py 2,637 行 · session_transfer.py 177 行 · record_fixtures.py 176 行
fixtures/       31 个录制 JSON
docs/           9 个文档
```

---

## 2. 快速开始

仓库根目录执行：

```powershell
python -m pip install -e .            # 后端依赖（jsonschema）
cd code\web
pnpm install                          # 前端依赖
pnpm dev                              # 打开 http://localhost:5173
```

另开一个终端启动桥接：

```powershell
python code\web\bridge\web_bridge.py --execution-mode single --port 8787
```

浏览器访问 **http://localhost:5173**（必须 `localhost`，桥接 CORS 白名单只允许 `localhost:5173/4173`），
在顶栏「口令」粘贴启动时打印的 `token=…`，在「API Key」填入 DeepSeek 密钥，点「＋」新建会话即可对话。

**完整步骤、界面说明、常见问题见 [`本文第 14 部分`](#14-前端测试与使用指南)。**

无 API Key 也能看界面：访问 `http://localhost:5173/?src=fixture`，读取仓库内录制数据（只读、零模型调用）。

---

## 3. 架构

```
┌───────────────────────────┐
│ 浏览器：React SPA          │  Vite dev 5173 / 构建产物 dist
│ 7 个页面 + 设计系统         │
└─────────────┬─────────────┘
              │ HTTP / SSE（X-MAPAS-Token 口令）
┌─────────────▼─────────────┐
│ 本地桥接：web_bridge.py    │  127.0.0.1:8787，单 worker 串行化
│ 协议适配 / 作业登记 / 降噪  │  Python 标准库（http.server + 线程）
└─────────────┬─────────────┘
              │ 直接调用 Python API
┌─────────────▼─────────────┐
│ 后端 PersonalAgentApp      │  code/src/memory/**
│ Agent 运行时 / 记忆 / 任务  │  数据在 ~/.agent_data
└───────────────────────────┘
```

**为什么需要桥接**

后端只提供 CLI 与 JSONL（stdin/stdout）两种宿主，没有 HTTP 服务；且 `PersonalAgentApp` 不是线程安全的。
桥接做两件事：

1. **协议适配**：一次调用 → 一个 HTTP 请求；长时间任务 → 异步作业（`jobId` + 轮询），进度用事件流增量读取；
2. **串行化**：所有应用调用经单个 worker 线程排队，避免并发写坏状态（事件读取走独立存储接口，不排队）。

**前端不做的事**（硬性边界）

- 不做语义判断：没有关键词/正则规则去猜意图、改记忆、选上下文（根 `AGENTS.md` 规定）；
- 不造假：缺数据就显示占位与原因，不用假数据填充；
- 不改后端文件：本目录只做增量新增（`code/web/**`）。

---

## 4. 目录结构

```
code/web/
├─ src/
│  ├─ app/                 # 入口组合与页面注册（App.tsx / pages.ts）
│  ├─ components/          # 通用件：Brand、FlameLoader、Icon、ApiKeyDialog、ui/*
│  ├─ datasource/          # 数据源层：契约实现（http / fixture）、错误模型、31 个录制 JSON
│  ├─ features/            # 七个页面：chat / memory / retrieval / tasks / collaboration / governance / context
│  ├─ lib/                 # 工具：useAsync、errorText（错误中文化）、theme、download、format
│  ├─ state/               # 全局状态（AppState：视图/主题/数据源/模式/API Key/口令探针）
│  ├─ styles/              # index.css、tokens.css（四主题变量）、components.css（组件样式）
│  └─ types/               # 与后端契约对应的 TypeScript 类型（约 1,000 行）
├─ bridge/
│  ├─ web_bridge.py        # 本地桥接（29 组路由）
│  ├─ session_transfer.py  # 会话导出/导入（后端暂无对应 API，桥接受控读写）
│  └─ record_fixtures.py   # 录制真实响应到 fixtures/
├─ docs/                   # 见 §11 文档索引
├─ index.html             # Vite 入口 HTML
├─ README.md               # 本文件
└─ CHANGELOG-web.md        # 逐版本开发记录（v0.1 → 当前）
```

---

## 5. 功能

### 5.1 七个页面

| 页面 | 作用 | 主要能力 |
|---|---|---|
**对话** | 与 Agent 工作 | 会话列表（新建/导入/重命名/删除/导出）、事件时间线（过程折叠、审批卡、产出文件）、发送与取消、模式切换 |
**记忆** | 查看长期记忆 | 列表检索、正文与版本时间轴、来源溯源、回收站（恢复/彻底删除）、手动写入 |
**证据** | 查看检索与证据链 | 证据充分性、逐条候选（排名/分数/分解/适用性四态/冲突）、用到的记忆正文、召回阶段与查询扩写 |
**任务** | 长任务推进 | 任务台账、检查点、新建/切换、暂停/恢复/完成/取消、闭环作业状态 |
**协作** | 多 Agent 分工 | 运行列表、六列 DAG（计划→记忆→执行→证据→核验→整体核验）、节点细节、失败原因、按角色调用分布 |
**治理** | 运维与安全 | 审批、修复队列与修复计划、程序性经验（提炼/审核/撤销/反馈）、工具目录、MCP/存储/工作区状态 |
**上下文** | 上下文与预算 | 上下文预算条、手动压缩、消息/压缩检查点、最近一次用量 |

> 协作、修复、经验数据只在 `multi_fixed`（多 Agent 协作）模式产生；`single` 模式下这些页面会给出说明与一键切换。

### 5.2 关键交互

- **事件时间线**：按 `turn` 分组；轮次编号按"用户第几次发言"；过程默认折叠成一行（`过程 N 项 · 耗时`），点开看完整日志；待审批、失败原因、警告永远不折叠。
- **发送即显示**：用户消息在本地立即渲染（不等轮询），后端落库后自动让位给真实消息；生成中显示小火苗指示器（`token 燃烧中…`）。
- **审批**：有副作用的工具必须人工确认；卡片展示工具名与参数，确认/拒绝写入后端。
- **模式切换**：顶栏下拉，`single ⇄ multi_fixed` 在下一轮生效；切换由桥接"同数据目录重建 app"实现（token 不变、数据不丢），有作业在跑时拒绝。
- **口令（访问校验码）**：桥接默认启动打印 `token=…`，前端只存本机 localStorage；`--no-token` 启动则显示"本机免校验"。界面不叫它 token，避免与模型词元混淆。
- **API Key**：只写不读（页面无法回显密钥）；更新后自动重建 Agent 生效；可用性用 DeepSeek `GET /models` 探活（不消耗 token），按钮三色（可用/不可用/未填入）。
- **导出/导入会话**：导出为 `mapas.session/v1` JSON；导入生成新会话、不覆盖已有；只含消息，不含事件流。
- **主题**：浅色 / 深色 / 晨光 / 夕阳 / 随北京时间变化（默认）；自动规则 04–09 晨光、09–16 浅色、16–18 夕阳、18–04 深色；切换有渐变过渡。
- **离线回放**：`?src=fixture` 读取录制响应，只读、零调用，用于演示与无网环境。

---

## 6. 数据源层

`DataSource` 是前端唯一的数据入口，共 65 个方法，两个实现：

| 实现 | 说明 |
|---|---|
`HttpSource` | 直连本地桥接；带口令头 `X-MAPAS-Token`；错误统一为 `DataSourceError`（含 code / message / detail） |
`FixtureSource` | 读取 `src/datasource/fixtures/*.json`；只读，写操作抛 `OFFLINE_FIXTURE_READ_ONLY` |

- **连接探针**：`probeDataSource()` 先查 `GET /api/auth`（免鉴权）判断桥接是否要求口令，再取 `GET /api/meta`；据此区分「本机免校验 / 需要填写口令 / 后端未连接 / 未配置 API Key」四种状态。
- **错误模型**：`BRIDGE_NOT_RUNNING / HTTP_ERROR / BAD_RESPONSE / NO_FIXTURE_RECORDED / OFFLINE_FIXTURE_READ_ONLY / NOT_SUPPORTED / AUTH_REQUIRED`；界面始终显示真实错误，并附中文解释（`lib/errorText.ts`）。

---

## 7. 桥接层

启动参数：

```
python code/web/bridge/web_bridge.py --execution-mode {single|multi_fixed} --port 8787 [--no-token] [--data-dir PATH]
```

主要路由（29 组）：

| 域 | 路由 |
|---|---|
元信息 / 鉴权 | `GET /api/meta`、`GET /api/auth`、`GET/POST/DELETE /api/api-key` |
会话 | `GET/POST /api/threads`、`POST /api/threads/import`、`GET /api/threads/:id/messages|unfinished|export`、`POST /api/threads/:id/resume|rename|delete|turns[/:turnId/resume]` |
执行模式 | `GET/POST /api/mode`（热切换） |
作业 / 事件 | `GET /api/jobs/:id`、`GET /api/events`、`GET /api/events/stream`（SSE） |
审批 | `GET /api/approvals?threadId=`、`POST /api/approvals/:id/decision` |
上下文 | `GET /api/context`、`POST /api/context/compact` |
记忆 | `GET/POST /api/memories`、`/memories/recycle-bin`、`/memories/profile`、`/memories/:id[/history|/provenance|/forget|/restore]` |
任务 | `GET/POST /api/tasks`、`/tasks/:id[/switch|/status|/current/{pause,resume,complete,cancel}]`、`/task-lifecycle[/drain]` |
协作 | `GET /api/collaboration/runs`、`GET /api/collaboration/runs/:runId` |
治理 | `/api/repairs[/:id|/run|/revoke|/drain]`、`/api/experiences[/learn|/review|/revoke|/feedback]`、`/api/tools`、`/api/mcp`、`/api/workspace`、`/api/storage`、`/api/status` |
检索记录 | `GET /api/traces`、`GET /api/turns/:turnId/trace` |

行为约定：

- **单 worker 串行化**：所有 `PersonalAgentApp` 调用排队执行；
- **异步作业**：写操作立即返回 `jobId`，前端轮询终态；长任务期间事件流可持续增量读取；
- **降噪**：宿主对未绑定任务轮次产生的技术 warnings 在桥接层过滤（前端不做关键词过滤）；
- **错误映射**：不存在 → 404、参数错 → 400、未配置 Key → 503 + 统一中文提示、协作能力不可用 → 400。

---

## 8. 配置与运行

| 项 | 说明 |
|---|---|
开发 | `pnpm dev` → `http://localhost:5173`（`strictPort`） |
构建 | `pnpm build` → `dist/`（含 sourcemap） |
预览 | `pnpm preview` → `http://localhost:4173`（CORS 已允许） |
类型检查 | `pnpm typecheck` |
数据目录 | `AGENT_DATA_DIR` > `AGENT_HOME` > `~/.agent_data`（会话/记忆/任务/协作数据库与凭据都在这里，**不在 Git 中**） |
凭据 | `<data>/config/.env.local` 中的 `DEEPSEEK_API_KEY`（仅 DeepSeek，UTF-8 无 BOM）；也可在界面「API Key」中填 |
模式 | `single`（默认，单 Agent）/ `multi_fixed`（四角色串行协作，实验性） |
端口 | 桥接默认 8787；前端 5173（dev）/ 4173（preview） |

---

## 9. 设计系统

- **语义 token**：`src/styles/tokens.css` 定义基础层 + 语义层（状态 7 态、适用性四态、权威性、角色色），四套主题各一份变量；
- **四主题**：浅色（中性灰白）、深色（蓝黑）、晨光（冷调 + 渐变氛围）、夕阳（暖调 + 渐变氛围）；切换走 View Transitions 渐隐，不支持时退化为 CSS 过渡；
- **组件样式**集中 `components.css`（约 1,500 行），页面内只用内联样式做局部排版；
- **无 UI 框架**：卡片/徽章/折叠/弹窗/图标均为自建（`components/ui/primitives.tsx`、`Icon.tsx`），体积与可控性优先。

---

## 10. 验证与已知限制

**已完成的验证**

- `pnpm typecheck` / `pnpm build` 通过（构建产物约 CSS 34 kB / JS 470 kB，gzip ≈ 135 kB）；
- 桥接 29 组路由在真实后端上逐个验证（含错误路径 400/404/503）；
- 31 个录制 fixture 与真实响应形状一致；
- 手工验收流程：`docs/test-plan.md`（五阶段，含预期现象与调用成本）；
- 后端缺陷的复现与修复确认：`docs/bug-report-verifier-receipts.md`。

**已知限制**

| 限制 | 原因 | 现状 |
|---|---|---|
协作 DAG 只支持静态布局 | 未引入图库 | 节点/边为固定网格 + SVG，可读性优先 |
`plan_json` 仍由桥接只读 SQL 读取 | 后端 `get_run_details` 未包含计划 | 其余协作数据均走官方接口 |
会话导出不含事件流 | 后端无导出 API，事件存储在 `agent_events.db` | 导出/导入只覆盖消息与元信息 |
前端无自动化测试 | 项目未引入测试框架 | 以 tsc/build + 手工验收兜底 |
旧检索记录无逐条字段 | trace `schema_version=1` | 页面明确标注"旧记录无逐条字段"，新记录（v2）完整显示 |

---

## 11. 文档索引

| 文档 | 内容 |
|---|---|
[本文第 14 部分](#14-前端测试与使用指南) | **使用指南**：安装、启动、界面说明、首次配置、七页功能、排障 |
[`docs/frontend-contract.md`](docs/frontend-contract.md) | 与后端的字段契约与覆盖映射（改接口先看） |
[`docs/bridge-plan.md`](docs/bridge-plan.md) | 桥接设计：协议、并发模型、错误映射 |
[`docs/test-plan.md`](docs/test-plan.md) | 手工验收流程（含预期现象与成本） |
[`docs/bug-report-verifier-receipts.md`](docs/bug-report-verifier-receipts.md) | 联调缺陷记录：现象/证据/根因/修复状态/遗留 |
[`docs/backend-issues.md`](docs/backend-issues.md) | 旧清单归档索引（已合并到上一文件） |
[`docs/brand-preview.svg`](docs/brand-preview.svg) / [`docs/theme-preview.png`](docs/theme-preview.png) | 品牌标识与主题氛围预览 |
[`CHANGELOG-web.md`](CHANGELOG-web.md) | 逐版本开发记录（功能、原因、验收、撤回方式） |

---

## 12. 开发历史

| 阶段 | 内容 |
|---|---|
P0 | 契约先行：字段映射、数据源接口、错误模型 |
P1 | 骨架与设计系统：三栏布局、语义 token、7 页占位、主题基础 |
P2 | 对话主链路：桥接 + 异步作业 + 事件时间线 + 审批 |
P3 | 记忆 / 任务 / 上下文 / 证据四页接入真实数据 |
P4 | 治理中心（审批 / 修复 / 经验 / 工具 / 环境） |
P5 | 口令鉴权、录制回放（fixture）、验收流程 |
P6 | 协作拓扑页（DAG）、模式热切换、API Key 管理、四主题、会话导出/导入、错误中文化、UI 打磨 |

逐版本的改动、原因、验收数据与撤回流见 [`CHANGELOG-web.md`](CHANGELOG-web.md)。

---

## 13. 开发约定

1. **只做增量，不影响后端**：所有改动限于 `code/web/**`；不修改后端源码、根 README、根 `.gitignore`、`setup.cfg`；
2. **不造假**：缺字段显示"后端未持久化"，错误原样展示 + 中文解释；
3. **不做语义规则**：意图识别、记忆抽取、上下文选择一律由后端 Provider 决定，前端不写关键词表；
4. **可撤回**：每个变更记录在 `CHANGELOG-web.md`，并写明撤销方式；
5. **提交前自检**：`pnpm typecheck && pnpm build`。

---

## 14. 前端测试与使用指南

> 前面是主流程（照着做即可），最后是附录（细节、原理、排障）。

### 一、5 分钟跑起来（主流程）

#### 0. 你需要什么

| 依赖 | 版本要求 | 说明 |
|---|---|---|
Python | **3.10+** | 跑后端 Agent 与本地桥接 |
Node.js | **20.19+ 或 22.12+** | Vite 8 的硬要求 |
pnpm | 9+（推荐） | 仓库带 `pnpm-lock.yaml`；用 `npm` 也能装，但版本可能漂移 |
DeepSeek API Key | `sk-` 开头 | 只能 DeepSeek（后端 Provider 固定） |

#### 1. 安装后端依赖（仓库根目录执行一次）

```powershell
cd <你的仓库路径>\nlp-
python -m pip install -e .
```

macOS/Linux 用 `python3 -m pip install -e .`。这一步会装上 `jsonschema`（后端依赖）。

#### 2. 安装前端依赖（只需一次）

```powershell
cd code\web
pnpm install
```

没装 pnpm 的话：`corepack enable`（Node 自带），或 `npm i -g pnpm`。

#### 3. 准备 API Key（二选一，推荐 A）

**A. 忽略本步骤，待网页启动后直接在网页里填key**（见第 7 步）

**B. 备用方法：写配置文件**（在仓库之外，不会被 git 提交）

```
C:\Users\<你>\.agent_data\config\.env.local
```

内容（UTF-8 **无 BOM**）：

```ini
MEMORY_PROVIDER_MODE=REQUIRED
DEEPSEEK_API_KEY=sk-你的密钥
```

#### 4. 启动后端桥接（终端 A）

**二选一，推荐方式一。**

**方式一：免校验（不用填口令，本地开发推荐）**

```powershell
cd <你的仓库路径>\nlp-
python code\web\bridge\web_bridge.py --execution-mode single --port 8787 --no-token
```

- 启动后顶栏会显示 🟢 **口令：本机免校验**，第 7 步**不需要**填任何口令；
- 仅本机可用（桥接只监听 `127.0.0.1`），适合自己开发/演示。

**方式二：需要校验（桥接会打印一次性口令）**

```powershell
cd <你的仓库路径>\nlp-
python code\web\bridge\web_bridge.py --execution-mode single --port 8787
```

- 启动横幅会打印 `token=xxxxxxxx`，**先复制下来**（第 7 步要粘贴）；
- 桥接每次重启都会换新口令，重启后要重新粘贴。

**两种方式共同的注意点**

- 终端出现 `MAPAS Web Bridge`、`executionMode: single`、`data dir: …` 表示启动成功；
- 这个终端**不要关**（Ctrl+C 才停）；
- **启动参数只决定"进程刚起来时的默认模式"**。日常要用多 Agent 协作，直接在界面顶栏「模式」下拉切换即可
  （切换到下一轮生效，会话与历史保留，口令不变）；
- 只有当你希望"桥接重启后默认就是 `multi_fixed`模式"时才需要改启动参数：
  把 `--execution-mode single` 换成 `--execution-mode multi_fixed`。

#### 5. 启动前端（终端 B）

```powershell
cd <你的仓库路径>\nlp-\code\web
pnpm dev
```

#### 6. 打开浏览器

**http://localhost:5173**  ← 必须用 `localhost`，不要用 `127.0.0.1`。

> 原因：桥接的 CORS 白名单只允许 `http://localhost:5173` 与 `http://localhost:4173`。

#### 7. 首次配置（页面右上角）

1. **口令**：
   - 第 4 步用**方式一**（`--no-token`）→ 顶栏应显示 🟢 **口令：本机免校验**，**不用填**；
   - 第 4 步用**方式二**（默认）→ 点顶栏「口令」，粘贴启动时打印的 `token=…`，点「保存并重连」。
2. **API Key**：点顶栏「API Key」→ 粘贴 `sk-...` → 点「更新」。
   成功时按钮变 🟢 **API Key 可用**（黄色=未填，红色=不可用）。

#### 8. 开始第一次对话

1. 左侧「会话」列 → **＋ 新建**（图标按钮）；
2. 底部输入框输入内容 → **Enter** 发送；
3. agent处理会话，顶栏显示「执行中…」；
4. 回答出现后，过程自动折叠成一行「过程 N 项」。

**到这里就成功了。**

### 二、界面速览

```
┌ 顶栏：MAPAS │ 数据源 │ API Key │ 口令 │ 主题 │ 模式 ─────────────┐
├─────────┬──────────────────────────────────────────┬───────────┤
│ 左导航   │               主工作区                    │  右侧检查器 │
│ 对话     │  （当前页面的内容）                        │ （辅助信息）│
│ 记忆     │                                          │           │
│ 证据     │                                          │           │
│ 任务     │                                          │           │
│ 协作     │                                          │           │
│ 治理     │                                          │           │
│ 上下文   │                                          │           │
├─────────┴──────────────────────────────────────────┴───────────┤
│ 左下角：北京时间                                                 │
└────────────────────────────────────────────────────────────────┘
```

**顶栏从左到右**：

| 控件 | 作用 |
|---|---|
品牌 `MAPAS 记忆工作台` | 点击回到对话页 |
数据源：桥接 / 回放 | 「桥接」= 连真实后端；「回放」= 离线样例（只读） |
API Key 状态 | 🟢 可用 / 🔴 不可用 / 🟡 未填入（点开可更新或删除） |
口令 | 本机桥接的访问校验码状态（不是模型 token，也不是 API Key） |
界面主题 | 4种
模式 | single（单 Agent）/ 多 Agent 协作，随时切换，下一轮生效 |

**左侧页面**：每项右侧的小圆点表示就绪度（绿=已接入真实数据）。

### 三、七个页面分别能做什么

| 页面 | 一句话作用 | 关键操作 |
|---|---|---|
**对话** | 和 Agent 工作 | 新建/改名/删除/导出/导入会话，发消息，处理审批，切换模式 |
**记忆** | 看 Agent 记住了什么 | 看列表/正文、历史版本、来源、回收站、手动写入一条记忆 |
**证据** | 看这一轮用了哪些记忆、为什么 | 选一条检索记录 → 看充分性、逐条候选（排名/分数/四态/冲突）、记忆正文 |
**任务** | 看长任务推进到哪了 | 新建任务、切到某任务、看台账/检查点/闭环状态、暂停/恢复/完成/取消 |
**协作** | 看多 Agent 怎么分工 | 选一次协作运行 → 看 DAG、节点状态、失败原因、模型调用分布 |
**治理** | 审批 / 修复 / 经验 / 工具 / 环境 | 处理待确认操作、看修复队列、经验提炼与审核、工具目录、MCP/存储状态 |
**上下文** | 看模型这次能读多少上下文 | 看预算条、手动压缩、看最近一次用量 |

> **只有 `multi_fixed`（多 Agent 协作）模式才会产生协作/修复/经验数据**；`single` 模式下这些页面会给出说明并提供一键切换。

### 四、第一次对话的完整流程

1. **新建会话** → 左侧出现「会话 N」，状态 `ACTIVE`；
2. **发消息** → 时间线立即显示你的话 + 生成动画；
3. **看过程** → 每轮展开可见：阶段完成、工具调用、审批、压缩；平时折叠成「过程 N 项」；
4. **处理审批** → 有副作用的工具会弹「需要确认」卡片，点「确认执行 / 拒绝」（不确认就不会执行）；
5. **看结果** → 回答气泡 + 「产出」文件列表（如果这一轮写了文件）；
6. **看沉淀** → 去「记忆」看是否新增/更新了记忆；去「证据」看这一轮用到的记忆；去「任务」看任务台账是否推进。

### 五、常见问题（按出现频率排序）

| 现象 | 原因 | 解决 |
|---|---|---|
页面顶部「后端未连接」 | 桥接没启动 / 端口不对 / 口令错 | 看终端 A 是否在跑；确认端口 8787；重新粘口令 |
顶栏「口令：需要填写」 | 桥接要求口令但没填/填错 | 粘启动时打印的 `token=…`；或改用 `--no-token` 启动 |
「口令：需要填写」一直不消失 | 桥接重启后口令会变 | 重新复制新 token 粘贴 |
API Key 显示 🔴 不可用 | Key 错误 / 过期 / 余额不足 | 右上角「API Key」更新；用 `GET /models` 探活，不消耗 token |
API Key 显示 🟡 未填入 | 还没配置 | 同上，填一个 `sk-` 开头的 DeepSeek key |
对话页变成「您还未设置 DeepSeek API Key」 | 配置文件里没有 key（被删了或没配） | 右上角「API Key」填入即恢复，无需重启 |
会话/记忆列表是空的 | 数据在**本机** `~/.agent_data`，不进 Git | 新机器本来就是空的；跨机器共享用「导出/导入会话」 |
协作页提示"当前是 single 模式" | 协作数据只在 multi_fixed 产生 | 顶栏「模式」切到多 Agent 协作，再发一条消息 |
点「刷新」没反应/一直转圈 | 桥接被上一次长任务占住 | 等当前轮结束；或重启桥接（终端 A Ctrl+C 后重新启动） |
`pnpm dev` 报端口被占用 | 5173 被占用（`strictPort`） | 关掉占用进程；端口写死在配置里，不建议改 |
`pnpm install` 很慢/失败 | 网络或 registry | 换镜像 `pnpm config set registry https://registry.npmmirror.com` |
Node 版本报错 | 低于 Vite 8 要求 | 升级到 Node 20.19+ / 22.12+ |
`pip install -e .` 后仍报 `jsonschema` 缺失 | 装到了别的 Python | 用同一个 Python 解释器启动桥接（`where python` 确认） |

### 六、无需 Key 的离线演示（答辩/没额度时）

直接打开：

```
http://localhost:5173/?src=fixture
```

- 页面读的是仓库里 `code/web/src/datasource/fixtures/*.json`（我们真实录制过的响应）；
- **只读**：不能发消息、不能审批、不能改记忆；
- 不需要后端、不需要 API Key、**零模型调用**。

### 附录

#### A. 仓库里前端相关的目录

```
code/web/
├─ bridge/                 # 本地桥接（Python，把后端能力暴露成 HTTP/SSE）
│  ├─ web_bridge.py
│  ├─ session_transfer.py  # 会话导出/导入（后端暂无对应 API）
│  └─ record_fixtures.py   # 录制 fixture 用
├─ docs/                   # 前端契约、缺陷报告、测试流程、本指南
├─ src/
│  ├─ app/                 # 路由与页面注册
│  ├─ components/          # 通用组件（含顶栏、弹窗、图标）
│  ├─ datasource/          # 数据源：http（真实）/ fixture（回放）
│  ├─ features/            # 七个页面各自的实现
│  ├─ lib/                 # 工具（错误翻译、主题、下载等）
│  ├─ state/               # 全局状态
│  ├─ styles/              # tokens（四主题）/ components（组件样式）
│  └─ types/               # 与后端契约对应的类型
├─ README.md               # 交付记录（做了什么、为什么、怎么撤）
└─ CHANGELOG-web.md        # 版本记录
```

#### B. 数据存在哪

| 内容 | 位置 |
|---|---|
会话与消息 | `<data>/conversations/conversation.db` |
长期记忆 | `<data>/memory/memory.db` |
任务/上下文 | `<data>/task_state/*.db` |
协作/事件流/检索记录 | `<data>/execution/*.db`、`retrieval-traces.jsonl` |
凭据 | `<data>/config/.env.local` |

`<data>` 的解析顺序：`AGENT_DATA_DIR` → `AGENT_HOME` → `~/.agent_data`。

**这些都不在 Git 里**。所以：别的组员 clone 之后看到的是**他自己机器上的数据**（新机器 = 空）。
要共享演示数据，用「导出会话 → 发给对方 → 对方导入」；或者用回放模式（`?src=fixture`）。

#### C. single 与 multi_fixed 的区别

| | single（默认） | multi_fixed（实验性） |
|---|---|---|
执行方式 | 单 Agent 直接回答 | Planner / Memory / Executor / Verifier 串行协作 |
消耗 | 低 | 明显更高（简单问候约 7 次模型调用） |
产生的数据 | 记忆/任务/上下文 | 额外产生协作 DAG、修复、经验 |
适用 | 日常对话 | 复杂任务、需要证据链/任务闭环的演示 |

**怎么切换**（两种方式，日常用第一种）：

1. **界面热切换**：顶栏「模式」下拉 → 选另一个。切换在**下一轮**生效，已有会话与历史都保留，
   口令不变（进程没重启）；有作业正在跑时不允许切换。
2. **启动参数**（只影响"重启后的默认模式"）：`--execution-mode multi_fixed` 让桥接启动后默认就是协作模式；
   不带参数则默认 `single`。

#### D. 会话管理

- **新建**：创建即落库，可以立刻重命名/删除/切走再切回；
- **重命名 / 删除**：会话右侧「⋯」菜单；删除是软删除（消息保留供证据引用），有未处理审批时会被拒绝；
- **导出**：生成 `标题.json`（只含消息与元信息）；
- **导入**：生成新会话（标题加「（导入）」），不会覆盖已有会话。
  限制：只导出消息，不含事件流，所以导入后时间线没有当时的工具/阶段过程。

#### E. 主题

- 浅色 / 深色 / 晨光 / 夕阳 / **随北京时间变化**（默认）；
- 自动规则：04–09 晨光 · 09–16 浅色 · 16–18 夕阳 · 18–04 深色；
- 换主题有渐变过渡；选择保存在浏览器 localStorage（键 `mapas.theme`）。

#### F. 构建与预览（不跑 dev server）

```powershell
cd code\web
pnpm build        # 产物在 code/web/dist
pnpm preview      # 本地预览：http://localhost:4173（CORS 允许）
```

#### G. 可选环境变量（写在 `<data>/config/.env.local`）

| 变量 | 作用 |
|---|---|
`DEEPSEEK_API_KEY` | 唯一必需的凭据 |
`MEMORY_PROVIDER_MODE` | 固定 `REQUIRED`（不降级到假数据） |
`LLM_REQUEST_TIMEOUT_SECONDS` | 单次模型请求超时（默认 60） |
`LLM_MAX_RETRIES` / `LLM_RETRY_DELAY_SECONDS` | 重试次数与间隔 |
`LLM_REASONING_EFFORT` | 可选：`low` / `high` / `max` |
`DEEPSEEK_MAX_TOKENS` | 单次输出上限 |
`LLM_CONTEXT_WINDOW_TOKENS` | 测试时压小上下文窗口 |
`AGENT_DATA_DIR` / `AGENT_HOME` | 改变数据目录（用于隔离测试） |

#### H. 安全须知

1. **不要把 API Key 写进仓库、截图或聊天记录**；Key 只在 `~/.agent_data/config/.env.local` 或网页弹窗里填；
2. 网页里是"只写不读"：页面不会显示已保存的 Key，也无法读回；
3. 「口令」是**本机桥接的访问校验码**（不是词元、不是 API Key），每个进程随机生成，**重启就变**；
4. 桥接只监听 `127.0.0.1`，别人无法从外网访问你的界面。

#### I. 重置 / 备份

- **重置所有数据**：关闭桥接 → 删除（或改名）`~/.agent_data` 目录 → 重新启动；
- **备份**：先给要保留的会话做「导出」，再重置；重要记忆/任务数据库也可整体复制 `~/.agent_data`（注意里面有你的 Key，不要传给他人）。

#### J. 想进一步了解

| 文档 | 内容 |
|---|---|
[`docs/frontend-contract.md`](docs/frontend-contract.md) | 与后端的字段契约（改接口先看这个） |
[`docs/test-plan.md`](docs/test-plan.md) | 手把手验收流程（含预期现象与成本） |
[`docs/bug-report-verifier-receipts.md`](docs/bug-report-verifier-receipts.md) | 已知后端问题与修复确认 |
