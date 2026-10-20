# Codex Harness 机制集成差距矩阵（当前同步：2026-09-05）

> 术语规范说明（2026-10-20）：本文原始核查时期为 2026-08 至 2026-09；系统名称现统一为 MAPAS。下文“当前”仍指原核查时点，不代表当时已采用现名。当前实现以 [MAPAS 多 Agent 架构](../architecture/multi-agent-foundation.md) 和 [阶段报告](../phases/multi-agent-p0-p1-20261014.md) 为准。

本文件根据 2026-08-21 检查的 OpenAI Codex 上游源码快照，将可审计的公开机制与 MAPAS 实现逐项对照。Codex 快照采用 Apache-2.0；本项目只移植机制和必要的接口思想，不把 Codex 的凭据、运行时状态、嵌套 Git 历史或整套 Rust 二进制直接并入 Python 运行时。凡是复制实现代码，必须保留对应许可证和 NOTICE。当前代码状态以 `main` 分支和本文件的“当前实现进度”为准；本矩阵中的历史阶段文字不代表仍未实现。

## 目标边界

MAPAS 保留现有的个人助手能力：长期记忆、任务状态、自然语言工具决策、工作区文件读写、终端执行、联网工具、CLI 和浏览器 Demo。Codex 能力以追加层集成，分成：

1. 宿主协议与生命周期：让任何前端都能可靠启动、恢复、观察和验收 Agent。
2. 执行安全与工具运行时：把工作区、命令、审批、MCP 和工具结果统一纳入可审计生命周期。
3. 上下文与持久化：按 token 和事件窗口管理上下文，保留完整历史，支持压缩恢复。
4. 高阶 Agent：并行工具、子 Agent、后台/睡眠任务、资料摄取和知识图谱。

## 机制差距矩阵

| Codex 机制 | Codex 参考位置 | MAPAS 当前状态 | 集成结论 | 优先级 |
|---|---|---|---|---|
| 全局 Agent home / 配置、状态和插件分离 | `codex-rs/core/config`、`rollout`、`plugin` | 已有 `~/.agent_data` 和分区存储 | 保留并继续扩展，不迁回项目目录 | P0 |
| JSON-RPC/JSONL App Server | `codex-rs/app-server`、`app-server-protocol` | 已有 `personal_agent_app_server.py`，CLI/浏览器仍共享 `PersonalAgentApp` | 继续完善协议客户端和异步传输；不复制 Rust 服务 | P0 |
| initialize / capability negotiation | `app-server-protocol/src/protocol/v1.rs` | 已实现版本、能力和 `agentHome` 返回 | 后续扩充能力字段并保持向后兼容 | P0 |
| thread start / resume / list / read | `thread-store`、`app-server-protocol` | 已实现 thread/session start、resume、list、read 映射 | 以 session 为内部权威，继续补客户端验收 | P0 |
| turn 生命周期和稳定 ID | `app-server-protocol`、`core/thread_manager` | 已实现 turn start/resume、持久化 started/completed/failed 事件与 sequence | 保持 root/request turn 身份和重放契约 | P0 |
| item 生命周期事件 | `app-server-protocol` 的 item notifications | 已实现 turn、stage、tool、approval、usage、compaction 事件 | 继续扩充事件字段，不改变 journal 权威 | P0 |
| 可恢复的未完成 turn | `thread-store`、rollout/state | 已实现 unfinished turn 查询、`turn/resume` 和工具回执对账 | `turn/interrupt` 当前明确不支持 | P0 |
| usage 和模型能力 | `core/context`、`model-provider` | 已实现 provider usage、上下文能力表和 `turn.usage` 事件 | 继续补真实模型长上下文验证 | P1 |
| 手动/自动 compaction 生命周期 | `core/compact*`、`rollout/model_context` | 已实现 `/compact`、自动 overflow、checkpoint、memory flush、事件和降级标记 | 继续做真实保真度评估 | P1 |
| 完整 transcript + rollout 恢复 | `rollout/recorder`、`thread-store` | SQLite transcript + JSONL 镜像已存在 | 追加事件流，不替代原 transcript | P1 |
| tool call/result 成对记录 | `core/event_mapping`、rollout items | 已有 TOOL paired exchange 和 journal receipt | 追加实时 started/completed；不重复执行工具 | P0 |
| 工具输出上限和模型上下文重写 | `core/exec`、`compact_remote*` | 已有 observation 截断和 overflow compaction | 追加原始输出摘要/截断元数据和可重放证据 | P1 |
| approvals / confirmation | `app-server-protocol` approval requests | 已有副作用工具确认、恢复、approval 事件和 `approvals/list` | 协议级主动 server request 仍待实现，保持本地确定性确认规则 | P0 |
| sandbox policy / writable roots | `sandboxing`、`execpolicy` | Linux bubblewrap + workspace scope；非 Linux fail closed | 追加 policy profile、审计和协议声明，不绕过沙箱 | P1 |
| shell process 生命周期 | `exec`、`shell-command`、`process` | 有受限终端调用，但无跨请求长进程协议 | 追加 bounded process handles 和清理策略 | P2 |
| 文件搜索、watch、apply patch | `file-search`、`file-watcher`、`apply-patch` | 已实现工作区内 `search_files`、Codex 风格 `apply_patch`；watch 未实现 | 后续补 watch 和协议级增量通知 | P1 |
| MCP server discovery/refresh/call | `codex-mcp`、`mcp` 相关 crates | 已实现全局 `config/mcp.json`、stdio discovery/call、schema/工作区边界 | 后续补长连接；当前每次 discovery/call 使用有界短进程 | P1 |
| skills / plugins | `skills`、`plugin` | 存储目录存在，运行时加载不完整 | 追加 manifest、版本、权限和可审计加载 | P2 |
| hooks / pre/post compaction | `core/hook_runtime` | 无统一 hook contract | 追加受限 hook 事件，先用于 memory flush 与审计 | P1 |
| retry / reconnect / graceful shutdown | `app-server/transport`、`responses_retry` | provider 有错误投影，CLI 有等待提示 | 追加服务层连接与 turn 状态；不引入已删除的 API 轮换 | P1 |
| parallel tool calls | `core` turn orchestration | 当前 bounded sequential loop | 追加同一 turn 内的依赖图/并行执行，先做只读工具 | P2 |
| sub-agent / collaboration | `core` sub-agents、thread store | 无 | 后置；必须继承 workspace、审批、记忆 provenance | P2 |
| background/sleep-time agent | Codex background execution 相关模块 | 只有后台记忆沉淀 | 区分 memory worker 和 user task worker，后置 | P2 |
| image/audio/multimodal normalization | `core/context_manager/normalize` | 当前主要是文本 | 后置，先补 context fragment 类型边界 | P2 |
| external doc ingestion / RAG | 参考 `llmwiki`、`llm_wiki`、Codex file search | 当前长期记忆不是资料库 | 追加知识库层，不混淆 conversation memory 与资料索引 | P2 |
| temporal knowledge graph | 参考 Graphiti | 当前有 revision/provenance，无时间关系图 | 后置，先稳定 source authority 和 revision contract | P2 |
| metrics/tracing/diagnostics | `diagnostics`、`rollout/persistence_metrics` | 已实现开发模式工具/阶段/耗时/usage、脱敏日志和持久化事件 | 继续补跨客户端查询和长期聚合 | P0 |

## 分阶段落地顺序

## 当前实现进度（2026-09-05）

- 当前主线已完成：SQLite authoritative Agent event store、JSONL host protocol、thread/session/turn 生命周期、事件游标重放、未完成 turn 恢复、usage/compaction/memory worker 事件、工具 receipt/transcript 闭环。
- 当前主线已完成：工具 JSON Schema catalog、approval requested/decision/completed 事件、tool started/completed 事件、工作区内 `search_files`、Codex 风格 `apply_patch`、显式 MCP stdio 发现与调用。
- 当前仍未实现：真正的进程句柄/跨请求长进程、并行只读 tool-call、watch、主动协议 interrupt、完整 sandbox policy profile；这些不能用展示层或单个场景规则替代。
- 后续阶段：sub-agent、后台用户任务、多模态上下文、资料 ingestion/RAG、temporal knowledge graph。

### Phase 1：宿主协议底座（已完成）

- 引入 append-only、SQLite authoritative 的结构化 Agent event store。
- 每个 turn 产生稳定的 started/completed/failed 生命周期事件；工具和 stage 事件能被客户端读取。
- 提供 JSONL JSON-RPC 风格宿主入口：initialize、thread/session 列表、start/resume、turn start、events read、context/status read。
- 不改变现有 `PersonalAgentApp`、Task 3、Task 1、工具审批和 transcript 的权威关系。

### Phase 2：恢复与上下文闭环（已完成）

- 启动时发现 RUNNING/PENDING turn，报告为可恢复状态而不是假装完成。
- 将 compaction、memory flush、usage、overflow retry 作为同一事件生命周期输出。
- 允许协议客户端从 sequence 游标继续读取，断线后不会丢失已提交事件。

### Phase 3：执行运行时（部分完成）

- 已完成 workspace scope、审批生命周期、工具 catalog、search/apply patch 和 MCP stdio。
- 仍待统一 sandbox/approval policy profile、长进程句柄、watch 和协议级并行只读工具。
- 副作用工具继续保持显式确认和幂等 receipt。

### Phase 4：高阶助手能力

- sub-agent、后台用户任务、资料 ingestion/RAG、temporal knowledge graph、多模态上下文。

## 不采用的做法

- 不把 Codex Rust crates 直接作为 Python 依赖；语言、进程模型和现有 MAPAS 权威存储不同，机械复制会造成双重状态源。
- 不用关键词或正则复制 Codex 的语义决策；意图、工具选择、记忆提炼仍由 Provider typed proposal 决定，本地只做 schema、权限、生命周期和事务校验。
- 不以“文件数量相同”证明功能完成；每个机制必须有真实接口、故障路径、恢复路径和回归测试。
- 不复制 Codex 的 OpenAI 登录、OAuth、凭据或外部服务依赖。

## 验收标准

每个阶段至少满足：

1. CLI 与浏览器仍走同一 MAPAS 应用路径。
2. 断开客户端后，SQLite 中已有事件可按 sequence 续读。
3. 工具执行事实、用户可见回答、Task 3 状态和长期记忆各自保持权威边界。
4. 任何失败都输出真实状态，不把“模型说完成了”当成执行完成。
5. 新增机制有集成测试和至少一个故障/重启/重复请求测试。
