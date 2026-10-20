> 2026-10-14 维护说明：完整浏览器/桌面 UI 已移除，CLI 与 JSONL 为现有入口。最新验证和限制见维护报告。

# MAPAS CLI 用户使用手册

这是一份只面向使用者的说明。你不需要了解 Task 1、Task 2、Task 3 或数据库结构；只需要配置一次 API，然后在 `你 >` 提示符后输入问题即可。

## 1. 第一次使用

在用户全局 Agent 主目录准备配置文件（不放在当前工作区）：

```bash
mkdir -p ~/.agent_data/config
cp .env.example ~/.agent_data/config/.env.local
```

编辑 `~/.agent_data/config/.env.local`，填入唯一支持的 DeepSeek API：

```dotenv
MEMORY_PROVIDER_MODE=REQUIRED
DEEPSEEK_API_KEY=你的真实密钥
```

产品固定请求 `https://api.deepseek.com/chat/completions` 的
`deepseek-v4-flash` 模型，不读取其他 Provider、endpoint 或 API key 选择变量。
密钥只放在全局配置文件或系统环境变量中，不要放进聊天内容、Git 或截图。Linux/macOS 可以执行：

请求字段和限制以 [DeepSeek Chat Completions 官方文档](https://api-docs.deepseek.com/api/create-chat-completion/)
为准；模型、价格、限额和错误码见 [官方 Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/)、
[Rate Limit](https://api-docs.deepseek.com/quick_start/rate_limit/) 和
[Error Codes](https://api-docs.deepseek.com/quick_start/error_codes/)。

```bash
chmod 600 ~/.agent_data/config/.env.local
```

## 2. 启动和退出

Linux/macOS 推荐：

```bash
./code/start_agent.sh
```

如果尚未创建 Conda 环境，先执行：

```bash
conda env create -f environment.yml
```

Windows：

```powershell
.\code\start_agent.cmd
```

也可以从任意目录用绝对路径启动：

```bash
python /home/china/ysaaaa/agent/code/scripts/personal_agent.py
```

启动成功后会看到 `你 >`。直接输入自然语言即可。输入 `/quit`、`/exit`，或在提示符处按 `Ctrl+D` 退出。请求执行过程中按 `Ctrl+C` 会取消当前请求，通常可以继续输入下一轮。

CLI 支持终端的 bracketed paste：从聊天窗口、文档或其他终端复制包含换行的长文本后，在 `你 >` 提示符处粘贴并提交，整段内容会作为同一轮用户输入，不会按其中的换行拆成多条指令。对于像 Codex 这类通过 PTY 直接发送未带标记的连续多行输入，CLI 也会合并当前已到达的连续行；普通单次回车仍表示提交当前输入。

输入编辑快捷键沿用 GNU readline 的确定行为：`↑/↓` 或 `Ctrl-P/Ctrl-N` 浏览历史，`Ctrl-R/Ctrl-S` 反向/正向搜索历史，`Ctrl-A/Ctrl-E` 跳到行首/行尾，`Ctrl-B/Ctrl-F` 左右移动，`Alt-B/Alt-F` 按词移动，`Ctrl-K` 删除到行尾，`Ctrl-U` 删除到行首，`Ctrl-W` 删除前一个词，`Ctrl-H` 删除前一个字符，`Ctrl-Y` 粘回已删除内容，`Alt-D` 删除后一个词，`Ctrl-L` 清屏。`Ctrl-D` 在空提示符退出，`Ctrl-C` 退出当前 CLI/取消正在执行的请求。

Codex CLI 中依赖其专用 composer 或生命周期的 `@` 文件引用、`Ctrl-O` 复制最近输出、`!` shell 快捷命令、工作中 `Tab/Enter` 排队或注入、双 `Esc` 编辑并分叉等能力，在 MAPAS 当前没有对应实现，因此不会绑定成表面上可用但行为不完整的快捷键。

看到 `PS ...>` 或普通 shell 提示符，说明 CLI 已经退出，不是产品输入提示符。

### 输出模式

开发阶段默认使用 `developer` 模式。它会显示模型处理阶段、等待进度、最终耗时、Provider 用量（若有）、任务状态阶段、工具名称/参数/执行状态/耗时以及安全诊断，便于定位“是否调用工具”和上下文/任务状态问题。

```text
/mode                 # 查看当前模式
/mode user            # 切换为用户模式
/mode developer       # 切回开发模式
```

用户模式只显示最终思考用时、最终回答，以及 Provider 明确返回的思考过程（若 Provider 返回该字段）。它不会显示内部工具参数、任务状态阶段或开发诊断。启动前也可设置 `AGENT_OUTPUT_MODE=USER`；当前默认值保持为 `DEVELOPER`，待产品发布阶段再切换默认值。

## 3. 普通对话

在 `你 >` 后直接输入问题：

```text
你 > 帮我规划一个周末上海旅行，预算 2000 元
```

每次输入一轮。模型处理时间较长时，终端会显示当前阶段，并定期提示仍在等待；这不是卡死。如果 API 失败，CLI 会显示安全的错误原因和处理建议，不会悄悄改用假数据。

工具可以由 Agent 自动调用。例如：

```text
你 > 查询上海今天的天气，并告诉我是否适合步行
```

如果回答依赖联网结果，结果会带来源和获取时间。外部网页内容只作为不可信参考，不能替代用户明确指令。

## 4. 所有可用命令

### 对话、诊断与上下文

| 命令 | 用途 | 示例 |
|---|---|---|
| `/help` | 显示命令帮助 | `/help` |
| `/provider` | 查看 Provider、模型、端点和 Key 是否已配置；不会显示密钥 | `/provider` |
| `/mode [user\|developer]` | 查看或切换输出模式；默认 `developer` | `/mode user` |
| `/context` | 查看模型上下文窗口、当前投影、压缩检查点和最近用量 | `/context` |
| `/compact` | 手动压缩当前会话旧上下文，不隐式修改长期记忆 | `/compact` |
| `/session` | 查看当前会话 ID、标题、关联任务和时间 | `/session` |
| `/clear` | 清空终端画面；不会删除任何数据 | `/clear` |
| `/quit` 或 `/exit` | 退出 CLI | `/quit` |

`/context` 中的“完整 Transcript”表示原始对话仍然保留；“当前投影”表示这一次模型调用实际准备使用的上下文。上下文压缩不会删除原始对话，只会生成可追踪摘要和近期消息投影。

### 会话管理

会话是“一段对话记录”。如果你想开启一个完全独立的话题，用新会话：

```text
/new-session 上海旅行
/sessions
/resume
/resume <session_id>
```

`/sessions` 输出的带 `*` 的项目是当前会话，并显示该会话第一条用户消息的主题、Session ID 和工作区。
输入 `/resume`（不带 ID）会打开上下箭头选择器：用 ↑/↓ 选择，按 Enter 恢复，按 Esc 或 `q` 取消。
恢复后，CLI 会先恢复该 session 绑定的文件夹范围，再把该 session 的完整 Transcript 重新打印到当前终端，随后继续对话。
仍可使用 `/resume <session_id>` 兼容直接恢复。每个 session 独立保存自己的工作区；使用 `/workspace-add`、`/workspace-remove`、`/workspace-use` 的变更会写入当前 session。

### 任务管理

任务是“持续需要维护目标、约束、决定和进度的工作”。它和会话不同：一个任务可以跨多个会话继续。

```text
/new 写 CLI 使用文档
/tasks
/switch <task_id>
/status
/pause 暂停原因
/resume-task
/complete
/cancel
```

说明：

- `/new [标题]` 会创建任务，并把标题保存为任务目标；
- `/status` 查看当前任务的目标、约束、决定、进展和待解决问题；
- `/pause` 暂停任务但保留历史，暂停后不能继续普通对话；
- `/resume-task` 只能恢复当前已暂停任务；
- `/complete` 标记任务完成；完成任务不会删除历史，继续工作请用 `/new`；
- `/cancel` 取消任务但保留历史；
- 已完成或已取消的任务在重启后不会被自动当成当前可对话任务。

如果当前有等待确认的高风险操作，必须先 `/confirm` 或 `/reject`，才能切换会话或任务。

模型提出的任务状态会区分两个范围：`TASK` 是跨会话仍然有效的目标、约束、决定和已验证进度；`SESSION` 只属于当前对话，例如临时代号或一次性上下文。后者仍保留在当前会话 Transcript 中，但不会写入 Task 任务账本，因此新建会话不会误召回临时信息。

### 长期记忆

重要事实、偏好或长期约束可以显式保存：

```text
/remember 我不吃香菜
/memories
/memory <memory_id>
/memory-history <memory_id>
/memory-provenance <memory_id>
/forget <memory_id>
/restore-memory <memory_id>
/memory-recycle-bin
```

`/remember` 会立即写入长期记忆。`/memories` 只列出当前有效记忆；`/memory-history` 查看同一记忆的修订历史，包括被修正的旧版本；`/memory-provenance` 查看每个版本来自哪一轮输入、哪个会话以及来源类型。

也可以直接用自然语言明确要求保存长期事实或偏好，例如：

```text
写入一个记忆，我 solotrip 喜欢住在市中心
```

这条路径由 Provider 进行结构化意图判断，不依赖本地关键词表。只有 Provider 提取出明确的用户事实、且 Task 1 返回真实持久化 ID 后，系统才会回复“已记录到长期记忆”；判断不明确或写入失败时不会虚假确认。自然语言写入成功后不会再次进入普通对话的批量沉淀缓冲。

`/forget` 是可恢复删除：记忆会进入回收站，不再参与检索；可以用 `/restore-memory` 恢复，用 `/memory-recycle-bin` 查看。CLI 不会因为时间自动删除记忆。

普通成功对话不会再进入隐藏的“10 轮或 12 小时”批量 worker。每轮 Agent 都有机会自主决定是否调用 `update_long_term_memory` 内部工具；调用后，应用把本轮用户原文和会话/消息 provenance 交给 Provider/NEMORI 做分段、持久性和生命周期判断，并同步写入长期记忆。Agent 未调用时，不会因为对话内容看起来像偏好就由本地规则偷偷写入。Provider 失败时，原始事件会留在可重试 journal 中；`/status` 会显示待重试事件数。`/compact` 只压缩会话，不隐式修改长期记忆。

时间遗忘功能默认关闭。系统不会因为记忆长期未访问而降低权重或移入回收站；自动降权只来自 Provider 在记忆更新流程中提出并通过校验的生命周期变更。如果确实需要时间衰减，可在启动前设置 `MEMORY_ENABLE_FORGETTING=true`，这是全局开关。

### 直接工具命令

这些命令不需要模型往返，结果也会保存在当前会话中，所以后面可以继续追问刚才的结果：

| 命令 | 用途 | 示例 |
|---|---|---|
| `/calc <表达式>` | 安全计算，只支持白名单算术运算 | `/calc (123 * 456) / 2` |
| `/time` | 获取本机当前时间 | `/time` |
| `/search <关键词>` | 通过 DuckDuckGo 搜索公开网页 | `/search Python 3.13 新特性` |
| `/fetch <URL>` | 抓取一个公开网页的有界正文 | `/fetch https://www.python.org` |
| `/weather <地点>` | 通过 Open-Meteo 查询当前天气和短期预报 | `/weather 上海` |
| `/tools` | 查看 Agent 当前可用工具 | `/tools` |
| `/mcp` | 查看全局 MCP 服务发现状态 | `/mcp` |

### 本地工作区与终端

新数据目录启动时，Agent 默认可见并使用启动时的当前工作目录。工作区范围会持久化在 Agent 数据目录中；它控制本地文件工具的读写范围，也控制终端命令允许选择的 `cwd`：

```text
/workspace                 查看当前可见文件夹和默认终端工作区
/workspace-add <路径>      添加一个已存在的文件夹
/workspace-remove <路径>  删除一个可见文件夹
/workspace-use <路径>     将已可见文件夹设为默认终端工作区
/storage                  查看 Agent 运行数据的固定目录布局
```

Agent 可以根据任务自行选择 `read_file`、`list_directory`、`create_file` 和 `run_terminal_command`，不需要用户先输入对应工具命令。读取和目录查看会直接执行；写文件和运行任意终端命令属于副作用，Agent 先提出已验证的操作参数，用户输入 `/confirm` 后才执行，`/reject` 则取消。

当前还提供 `search_files`（工作区内文字搜索）和 `apply_patch`（Codex 风格多文件 patch）。前者只读，后者沿用副作用确认、工作区限制和执行 journal。`/tools` 或 App Server 的 `tools/list` 返回带 JSON Schema 的完整工具契约。

MCP 扩展配置固定放在全局 `~/.agent_data/config/mcp.json`，格式为：

```json
{
  "servers": [
    {
      "name": "example",
      "command": ["python", "-m", "my_mcp_server"],
      "read_only_tools": ["lookup"]
    }
  ]
}
```

启动时只发现用户明确配置的服务，工具会以 `mcp.<server>.<tool>` 命名；未列入 `read_only_tools` 的 MCP 工具默认按副作用处理。`/mcp` 查看发现状态。模型不能修改 MCP 配置或注册工具。

文件参数可以使用当前工作区内的相对路径，也可以使用已授权范围内的绝对路径。越过已配置文件夹的路径会被拒绝。
Linux 上的终端命令通过系统 `bubblewrap` 隔离运行：授权工作区可写，系统依赖只读，未授权目录不会作为终端文件系统提供。若系统缺少 `bwrap`，终端工具会安全地拒绝执行并提示安装该依赖。Windows/macOS 当前没有等价的受工作区限制终端沙箱，因此会明确拒绝终端命令，不会绕过授权范围直接执行宿主机 shell。

联网工具有以下边界：只访问公开 `http/https` 地址；不访问本地主机、内网地址、带账号密码的 URL；网页和天气结果可能因为网络、目标站点或服务限流而失败。失败时会显示工具错误，不会伪造结果。

### 高风险操作确认

当 Agent 需要执行具有副作用的工具操作时，会暂停并显示工具名和参数：

```text
输入 /confirm 确认允许执行
输入 /reject 拒绝执行
```

确认之前不会执行副作用。确认或拒绝会写入对话记录。退出后重新启动，仍可看到待确认操作并继续处理；在处理前不能用普通对话或切换任务绕过确认。

## 5. 数据会保存在哪里

默认数据目录是用户主目录下的全局 `~/.agent_data/`，不会随当前工作区或每个 session 重复创建。产品运行数据固定按职责归档：

```text
.agent_data/
├── long_term_memory/memory.db       # Task 1 长期记忆及版本历史
├── conversations/conversation.db   # 所有会话索引、transcript 与压缩检查点的集中权威存储
├── task_state/task_state.db         # Task 3 任务工作状态
├── execution/                       # Agent Journal、检索追踪、脱敏运行日志（含 agent.log）
├── cli/cli_history                  # CLI 输入历史
├── workspaces/workspace_scope.json  # 用户配置的 Agent 可见文件夹范围
├── config/                          # 全局用户配置（含 .env.local 与 mcp.json）
├── plugins/                         # 预留的全局插件目录
└── cache/                           # 运行缓存
```

首次启动新版时，如果发现旧版项目目录 `.agent_data/`，会将其整体迁移到 `~/.agent_data/`；如果发现旧版平铺文件（如 `memory.db`），会移动到对应子目录并继续使用原数据。这些都是可逆的文件移动，不会重建或清空历史记忆。

不要删除 `~/.agent_data/`，除非你确认不再需要这些历史。删除它会使本地会话、任务和记忆无法从 CLI 恢复。

测试或需要完全隔离运行时，可以用环境变量覆盖全局目录：

```bash
AGENT_DATA_DIR=/path/to/my-agent-data ./code/start_agent.sh
```

也可以设置 `AGENT_HOME=/path/to/.agent_data` 指定另一处全局 Agent 主目录；正常使用不需要设置任何目录变量。

## 6. 常见问题

### 启动失败：未配置 API 凭据

确认 `~/.agent_data/config/.env.local` 存在，并且变量名为 `DEEPSEEK_API_KEY`，然后执行 `/provider` 检查状态。不要把密钥直接写到命令历史中。

### HTTP 401、402、429 或 5xx

- 401：Key 无效、过期或变量名选错；
- 402：账户额度不足；
- 429：DeepSeek 的并发/请求速率限制，等待后重试；系统只对同一 DeepSeek endpoint 做有限重试，不轮换 API；
- 5xx：Provider 暂时不可用，稍后重试。

### 看到上下文压缩或摘要警告

这表示对话已经接近模型输入预算，系统在保留原始 Transcript 的前提下生成了摘要。如果提示“降级”，说明摘要 Provider 当时失败，原始记录仍在；恢复网络后可执行 `/compact` 重试。

### `/memory-history` 找不到 ID

先执行 `/memories` 复制完整的 `memory_id`。ID 区分字符，当前有效记忆和历史记录使用同一个 memory ID。

### Agent 说任务没有写入

这是保护行为：模型提出的 Task 状态修改没有通过身份、证据或生命周期校验。回答可以生成，但系统不会伪造“已完成”。重新明确目标、约束或当前进展即可。

### 想从零开始

保留旧数据的同时使用 `/new-session` 和 `/new` 创建新会话/新任务。只有确定要丢弃全部历史时，才在 CLI 停止后备份或移走 `~/.agent_data/`。

## 7. 推荐的日常用法

1. 启动 CLI 后先运行 `/provider`，确认模型和凭据状态正确。
2. 普通问题直接输入自然语言。需要联网的信息也直接描述目标即可；不要求用户先输入
   工具命令，Agent 会根据任务自行判断是否调用搜索、网页抓取或天气工具。若问题有
   时间、地点或来源要求，最好一并写明，便于 Agent 返回可核验结果。
3. 重要且长期有效的偏好可以用 `/remember`；普通 turn 由 Agent 自主决定是否调用长期记忆工具。
4. 长期项目先用 `/new 项目名称`，在同一任务中持续交流；用 `/status` 检查当前工作集。
5. 话题完全改变时用 `/new-session`，不要把无关内容混在同一会话。
6. 长对话中用 `/context` 查看预算，必要时用 `/compact` 主动整理。
7. 退出前不需要手动保存；正常退出、EOF 和中断都会保留已经写入的数据。
