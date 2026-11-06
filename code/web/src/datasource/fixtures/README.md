# fixtures —— 真实响应的录制回放（只读）

本目录存放**从桥接进程真实录制下来的响应**，供 `FixtureSource` 离线回放
（前端 URL 加 `?src=fixture` 即进入回放模式）。

## 铁律

**禁止手写/编造 fixture。** 本项目的硬要求是"功能不造假"：如果这里放了编的数据，
界面看起来完整、实际是假的，比空着更糟。

## 怎么录制（用仓库自带工具，一条命令）

```powershell
# 1) 启动桥接（默认数据目录，建议跑过几轮真实对话/记忆后再录）
D:\newinstalllocation\Anaconda3\python.exe code\web\bridge\web_bridge.py `
  --execution-mode multi_fixed --port 8787 --no-token

# 2) 录制（默认写到本目录；只原样搬运 bridge 响应，不加工）
D:\newinstalllocation\Anaconda3\python.exe code\web\bridge\record_fixtures.py `
  --bridge http://127.0.0.1:8787

# 桥接若带 token：
#   ... record_fixtures.py --token <启动时打印的 token>

# 3) 离线看效果
cd code/web; pnpm dev
#   浏览器打开  http://localhost:5173/?src=fixture
```

录制工具会按 `fixture.ts` 期望的**读取形状**保存：列表类存数组、对象类存对象，
避免 `{"threads":[...]}` 这类包裹层导致回放读不到。

## ⚠️ 两点注意

1. **内容会进构建产物**：`fixture.ts` 用 `import.meta.glob(..., { eager: true })` 把本目录打进 bundle。
   当前 31 个文件约 94 KB，会让 JS 体积增加约 76 KB。演示可接受；若以后要精简，可改成懒加载。
2. **可能包含测试数据**：录制内容来自你的真实运行，可能包含会话文本、记忆正文（例如测试用的偏好）。
   提交前请确认没有隐私内容；当前这份只含"会议时间偏好"这类无关紧要的测试数据。

## 文件命名（与 `fixture.ts` 的 `read(key)` 严格对应）

| 文件名 | 对应接口 | 对应数据源方法 |
|---|---|---|
| `meta.json` | `GET /api/meta` | `meta()` |
| `threads.json` / `thread.<id>.json` | `GET /api/threads`、`POST /api/threads/:id/resume` | `listThreads()` / `resumeThread()` |
| `messages.<threadId>.json` | `GET /api/threads/:id/messages` | `readMessages()` |
| `unfinished.<threadId>.json` | `GET /api/threads/:id/unfinished` | `listUnfinishedTurns()` |
| `events.<threadId>.json` | `GET /api/events?threadId=` | `readEvents()` |
| `status.json` | `GET /api/status` | `getStatus()` |
| `context.json` | `GET /api/context` | `getContext()` |
| `tools.json` | `GET /api/tools` | `getTools()` |
| `mcp.json` / `workspace.json` / `storage.json` | 对应接口 | 对应方法 |
| `memories.json` | `GET /api/memories` | `listMemories()` |
| `memory.<id>.json` / `memory-history.<id>.json` / `memory-provenance.<id>.json` | 记忆三个子接口 | 对应方法 |
| `recycle-bin.json` / `profile.json` | 回收站 / 画像 | 对应方法 |
| `tasks.json` / `task-summary.json` / `task-lifecycle.json` | 任务三接口 | 对应方法 |
| `approvals.json` / `repairs.json` / `experiences.json` / `graphs.json` | 治理与协作 | 对应方法 |
| `traces.json` / `turn-trace.<turnId>.json` | 检索 trace | `listTurnTraces()` / `getTurnTrace()` |

## 缺失时会发生什么

`FixtureSource` 找不到对应文件会抛 `NO_FIXTURE_RECORDED`，界面显示"未录制 + 需要先录制"，
**不会**退化成空列表或假数据；所有写操作抛 `OFFLINE_FIXTURE_READ_ONLY`。

## 重新录制（2026-11-06）

本目录的录制数据在 2026-11-06 用 `record_fixtures.py` 从真实桥接（multi_fixed 模式）
**整批重新录制**过，原因是早期录制里的中文全部变成了 `?`（记录链路编码问题），
导致 `?src=fixture` 回放时对话、检索查询、记忆正文不可读。

重录时做了两件事（只删不改，不编造数据）：

1. 剔除 1 条内容已损坏、原始文本无法恢复的记忆（`mem_9d4bdad9dfbb`）及其两个子文件，
   并从 `profile.json` 的偏好列表里同步移除、按实际数量校正 `counts`；
2. 剔除 1 条标题/目标已损坏的任务，以及 5 条查询文本已损坏的检索 trace（及其单条 trace 文件）。

因此回放数据里**不再有任何乱码**，但也因此：
- 任务页在回放模式下是空的（唯一一条任务的历史文本已不可恢复）；
- 协作/修复/经验列表为空（当前数据目录里没有对应记录，不是录制失败）。

如需更丰富的回放数据：正常跑几轮真实对话（会自动产生会话/记忆/trace/协作数据），
然后再执行一次录制：

```powershell
python code/web/bridge/web_bridge.py --execution-mode multi_fixed --port 8787 --no-token
python code/web/bridge/record_fixtures.py --bridge http://127.0.0.1:8787
```

录制前请确认数据目录里没有隐私内容。
