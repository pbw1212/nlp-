# MAPAS 联调缺陷记录

> 记录前端联调期间发现的后端问题：现象、证据、根因、修复状态与前端适配。
> 按「已修复 / 遗留 / 已排除」分组；每条给出可复查的证据（运行 ID、数据库字段、提交号）。
> 本文件只描述事实，不含任务分配与沟通措辞。

## 状态总览

| # | 问题 | 严重度 | 状态 | 修复提交 | 详情 |
|---|---|---|---|---|---|
1 | 协作核验误判（`COLLABORATION_VERIFICATION_BLOCKED`） | 高（阻塞） | ✅ 已修复 | `c1b079c` | §1 |
2 | multi_fixed 最终回答是"子任务自述" | 中 | ✅ 已修复 | `ef0dbe1` | §2 |
3 | 失败调用按预留额度计费、`usage` 为空 | 中 | ✅ 已修复（口径透明化） | `53484dc` | §3 |
4 | 未绑定任务轮次写入警告噪音 | 低 | ✅ 已修复（不再产生） | `e830815`/`ef0dbe1` | §4 |
5 | 审批列表被任务绑定限制 | 中 | ✅ 已修复 | `53484dc` | §5 |
6 | 检索证据明细未落盘 | 中 | ✅ 已修复（schema v2） | `53484dc` | §6 |
7 | 会话重命名 / 删除无接口 | 低 | ✅ 已修复 | `53484dc` | §7 |
8 | 协作运行详情无接口 | 低 | ✅ 已修复 | `53484dc` | §8 |
9 | 新会话懒创建（幽灵会话根因） | 中 | ✅ 已修复 | `53484dc` | §9 |
10 | 模型偶发把工具结果 JSON 当最终回答 | 低 | ⏳ 未复现 / 遗留观察 | — | §10 |
11 | multi_fixed 简单问候也走完整协作流程 | 低 | ⏳ 设计取舍，非缺陷 | — | §11 |
12 | 失败运行"阶段记录不全" | — | ❌ 已排除（误判） | — | §12 |

---

# 一、已修复

## §1 协作核验误判：Verifier 收不到工具回执（严重）

**状态**：已修复（`c1b079c fix: carry native evidence into downstream verification`）
**影响**：`multi_fixed` 无法产出 `VERIFIED` 成果；协作拓扑只能显示 `BLOCKED/FAILED`；治理中心修复/经验长期为空。

**现象**
Executor 已成功执行 `read_file`（`handoffs`、`artifacts` 都有记录），但交给 Verifier 的输入里
`receipts` 为空、`source_reads` 恒为 `[]`，Verifier 按"证据不足"规则判 `BLOCKED`，整轮以
`COLLABORATION_VERIFICATION_BLOCKED` 失败。

**证据（两次 100% 复现）**

| 观测点 | 运行 A `ma_3a6caa8b…`（12 次调用） | 运行 B `ma_fa87f481…`（7 次调用） |
|---|---|---|
`task_attempts` / `artifacts` | `read_readme` 的 attempt=VERIFIED、artifact=VERIFIED；`summarize_40` artifact=PRODUCED | `read_readme` attempt=FAILED、artifact=PRODUCED |
`handoffs` | 7 条全链路齐全（含 `execute:*`、`verify:*`） | 齐全 |
`result.executed_tool_call_ids` | 仅 1 个（`call_fdf92a19e37f`） | `[]` |
`trace_metadata.source_reads` | `[]` | `[]` |
Executor 失败调用 | 19,966 units（占预算 42%），`usage={}` | 28,035 units（占 62%），`usage={}` |
终态 | `FAILED` + `EVIDENCE_ERROR` / `STAGE=VERIFICATION` | 同左 |

Verifier 原文（运行 B）：

> 执行器声称已读取 README.md 并给出 6227 字节、sha256=… 及全文，但本次输入中没有任何工具回执
> （receipts 为空、executed_tool_call_ids 为空、source_catalog/source_packets 为空），因此没有可核验的
> read_file 调用记录或文件内容来源。

**根因**（推测，修复提交覆盖）
① Executor 首次失败重试后，回执未并入本次 run 的 receipt 集合；
② `source_catalog` / `source_packets` 未传给 Verifier；
③ 失败调用按上限预约未释放，挤压重试预算。

**修复与验证**
修复后同一类任务（两子任务：读 README + 三点总结）实测 `SUCCEEDED`：
`executed_tool_call_ids=1`、两个子任务均 `VERIFIED`、`role_tasks` 10 条、最终回答为正常自然语言。

**前端适配**：无需改动；协作页自动获得真实 `VERIFIED` 节点与阶段数据。

---

## §2 multi_fixed 最终回答是"子任务自述"

**状态**：已修复（`ef0dbe1 fix: keep collaboration replies conversational`）

**现象**：`multi_fixed` 下输入「你好」，回答是第三人称自述：

> 回复为中文友好问候，并明确邀请用户说明具体需求……未调用任何工具，未虚构已完成的任务，符合子目标完成标准。

`single` 模式不会出现。

**证据（完全绕开前端，直接调用后端 JSONL 宿主）**

| 模式 | 模型调用 | `final_response` |
|---|---|---|
`single` | 3 | 「你好！我是你的个人智能助理。有什么可以帮你的吗？…」 |
`multi_fixed` | 7 | 「**已用**中文友好问候用户（"你好！…"），**并明确邀请**用户说明需求（……）。**未调用任何工具**……」 |

两种模式下 `user_response` 都等于 `final_response`，因此不能靠"前端换字段展示"解决。

**根因（代码级）**

```
Executor  被要求输出面向用户的回答（coordinator.py prompt：禁止 recite role names / internal control rules）
    ↓
Verifier  写 verdict.answer（roles.py："answer is the verified result or truthful blocked explanation"，语义双关）
    ↓
Coordinator  final_response = verdict["answer"]（失败路径）/ overall["answer"]（整体验收），原样透传
    ↓
serialization._user_response() 直接返回 final_response，无独立"用户视角"转述
```

**修复与验证**：修复后实测回答为「你好！有什么可以帮你的？」、「根据 README 原文，总结如下…」。

**前端适配**：协作轮次在时间线打「协作」徽章；`multi_fixed` 模式显示提示条（可一键切到 `single`）。

---

## §3 失败调用按预留额度计费、`usage` 为空

**状态**：已修复（`53484dc`，计费口径透明化）

**现象**：Executor/Verifier 的失败调用仍按预留额度扣核算单位，`usage_json={}`，无法区分"真实用量"与"估算"。

**证据**：运行 A 一次失败 `agent_action` 扣 19,966 units（42% 预算）；运行 B 扣 28,035 units（62%）。

**修复**
`settle_call()` 优先使用 Provider 实报 token；缺失时按预留额度估算，并在 `model_calls.usage_source`
显式标记 `provider` / `reservation`；`usage()` 返回 `provider_reported_token_units` 与 `estimated_token_units`。

**验证**：某次运行 `charged=39,259`，拆分后 Provider 实报 22,429 + 预留估算 16,830。

**前端适配**：协作页预算行显示「Provider 实报 X · 预留估算 Y」，按角色显示估算次数；
不再把"估算"当成"缺陷"展示。

---

## §4 未绑定任务轮次写入警告噪音

**状态**：已修复（宿主不再产生该警告）

**现象**：普通对话每轮 `turn.completed.warnings` 都带
`This turn has no Task 3 binding.`，前端曾把它显示成黄色警告。

**证据**：修复后最近 4 次 `turn.completed` 的 `warnings` 均为 `[]`。

**前端适配**：桥接层保留降噪名单（防御性），正常不再触发。

---

## §5 审批列表被任务绑定限制

**状态**：已修复（`53484dc`）

**现象**：`console.py` 的 `list_pending_approvals()` 在 `active_task_id` 为空时直接 `return []`，
未绑定任务的会话拿不到待审批项。

**修复**：改为按会话隔离（`list_pending_approvals(session_id)`），按 `session + root_turn + task` 归属过滤。

**前端适配**：`/api/approvals` 支持 `?threadId=`，前端按当前会话查询；未知会话返回 404。

---

## §6 检索证据明细未落盘

**状态**：已修复（`53484dc`，trace `schema_version=2`）

**现象**：`execution/retrieval-traces.jsonl` 只保存聚合统计与 `selected_memory_ids`，
缺逐条 `original_rank` / `score_breakdown` / `applicability_status` / `conflict_status` / `sufficiency`，
前端"检索证据"页只能降级显示。

**修复**：落盘 `evidence_candidate_count` + `evidence_candidates[]`
（`memory_id`、`revision_id`、`rank`、`score`、`score_breakdown`、`applicability_status`、
`applicability_decision`、`conflict_status`、`conflict_key`、`conflict_loser`、`current_safe`、`selected`）
以及 `sufficiency`（状态、原因码、缺失方面数、不确定记忆、未解决冲突）。

**前端适配**：证据页新增「证据充分性」卡与「候选与判定（逐条）」表；旧记录（v1）明确标注无逐条字段。

---

## §7 会话重命名 / 删除无接口

**状态**：已修复（`53484dc`）

**现象**：后端无 rename/delete，前端只能由桥接直写 `conversation.db`（schema 变更即失效）。

**修复**：`PersonalAgentApp.rename_session()` / `delete_session()`；
删除为**软删除**（`status='DELETED'`，消息保留供证据引用），删除前拒绝有未决审批/未完成轮次的会话。

**前端适配**：桥接与前端改用官方接口；删除失败给出中文原因（待审批、会话已更新、不存在）。

---

## §8 协作运行详情无接口

**状态**：已修复（`53484dc`）

**现象**：前端只能直读 `coordination.db` 才能画出协作拓扑。

**修复**：`CoordinationRepository.get_run_details(session_id, run_id)` 提供公开投影
（运行、阶段、图节点、usage）。`plan_json` 暂未进入投影。

**前端适配**：详情接口改用它；仅 `plan_json` 仍保留一小段只读 SQL（DAG 需要子任务计划）。

---

## §9 新会话懒创建（幽灵会话根因）

**状态**：已修复（`53484dc`）

**现象**：`create_new_session()` 不落库，会话只存在进程内存；切到别的会话后无法切回（resume 必然 404）。

**修复**：`create_new_session(title, persist=True)`，创建即持久化。

**前端适配**：新建会话即落库；桥接删除了对私有方法 `_ensure_persisted_session` 的调用。

---

# 二、遗留 / 未复现

## §10 模型偶发把工具结果 JSON 当最终回答

**状态**：⏳ 未复现，保留观察
**现象**：普通对话偶尔把工具结果原文（如 `{"path":"D:\\projects","entries":[…]}`）当作最终回答返回。
**影响**：界面显示一段 JSON；属模型/提示层行为，前端不做语义过滤（违反"不得用本地规则做语义判断"）。
**备注**：最近若干轮未再出现；若复现需记录 turn_id 与模型返回。

## §11 multi_fixed 简单问候也走完整协作流程

**状态**：⏳ 设计取舍，非缺陷
**现象**：「你好」也会被 Planner 拆成 1 个子任务，产生约 7 次模型调用。
**说明**：这是协作模式的既定行为，不是错误；若需节省 token，可增加"简单请求快速路径"（建议项）。

---

# 三、已排除（误判）

## §12 "失败运行阶段记录不全"

**曾记录**：失败的多子任务运行 `role_tasks` 只有 4 条，怀疑阶段记录缺失。
**复核结论**：不成立。`role_tasks` 只记录**实际执行过**的阶段；运行提前失败时后续阶段本就不会执行。
两子任务成功运行的 `role_tasks` 为 10 条，与流水线（plan / memory / execute / support / verify / verify:overall）一致。

---

# 四、证据获取方式

```powershell
# 协作运行、调用账本、阶段、产物、交接
$db = "$env:USERPROFILE\.agent_data\execution\coordination.db"
# python 标准库 sqlite3 即可（只读）：
#   SELECT run_id,status FROM runs ORDER BY started DESC;
#   SELECT role,operation,status,charged_units,usage_json FROM model_calls WHERE run_id='<run_id>';
#   SELECT * FROM role_tasks WHERE run_id='<run_id>';
#   SELECT * FROM artifacts;  SELECT * FROM handoffs;  SELECT * FROM task_graphs;

# 检索 trace
Get-Content "$env:USERPROFILE\.agent_data\execution\retrieval-traces.jsonl" -Tail 3

# 会话消息
# conversation.db: conversation_sessions / conversation_messages
```

# 五、修复提交与验证记录

| 提交 | 内容 | 关联问题 |
|---|---|---|
`c1b079c` | carry native evidence into downstream verification | §1 |
`e830815` | guard runtime host and extension failure boundaries | §4 |
`ef0dbe1` | keep collaboration replies conversational | §2、§4 |
`53484dc` | expose reliable backend diagnostics and thread operations | §3、§5、§6、§7、§8、§9 |

验证方式（均未调用模型）：隔离数据目录复制 `conversations/`、`execution/` 后跑
创建 → 改名 → 导出 → 导入 → 审批 → 删除 → 协作详情全链路；结果见各条"修复与验证"。
