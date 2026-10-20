# MAPAS Personal Agent Product Trial Harness

## 目的 (Purpose)
MAPAS Personal Agent Product Trial Harness 是一个全自动产品试用与故障发现研判框架。
它的核心目标是模拟“虚拟用户 Agent”，自动对现有的 `PersonalAgentApp` 完整产品 Stack（包含 `MainAgent`、`AgentRuntime`、Task 3 任务状态、Task 2 记忆检索、Task 1 长期记忆、Conversation Session、Journal、ToolRegistry）进行多轮连续聊天、Session/Task 频繁切换、应用重启恢复、副作用操作确认/拒绝、动态事实断言与边界碰撞测试。

---

## 当前组件与层级状态 (Layer & Component Status)

| 层级 / 组件 | 状态 | 说明 |
|---|---|---|
| **Layer 1 Deterministic Scenarios** | **IMPLEMENTED (14/14 PASS)** | 14 个硬性覆盖场景 (C001-C003, M001-M005, G001, TOOL001, S001-S002, X001, L001) 全部通过 |
| **Hard Oracle Evaluator** | **IMPLEMENTED** | 包含 10 项硬不变性断言（文本包含、记忆版本数、Session隔离、工具物理执行数、防编造天气断言等） |
| **Observer & Event Logger** | **IMPLEMENTED** | 记录完整的 Trial Log 与 JSON 结果，支持故障复现与定位 |
| **Seeded Fuzz Engine** | **IMPLEMENTED (PASS)** | 支持 50 步确定性 Seeded Fuzzing 随机探索 |
| **Overfit Guard** | **IMPLEMENTED** | `test_production_hardcoding_overfit_guard` 自动检测，防止测试字面量泄漏进生产代码 |
| **Virtual-User Explorer** | **STUB / 确定性存根** | 仅基于固定场景脚本，未实现开放式 LLM 自由探索虚拟用户 |
| **Autonomous Fixer** | **DISABLED** | 自动修复代码功能保持关闭，避免算法过拟合 |
| **数据隔离 (Temporary Isolation)** | **100% ISOLATED** | 强制在 `tempfile.TemporaryDirectory()` 中运行，绝不读写真实 `~/.agent_data/` |

---

## 核心原则 (Core Architecture & Design Principles)

### 1. 100% 数据隔离 (100% Data Isolation)
- 每次场景测试与随机 Fuzz 均在独立的 `tempfile.TemporaryDirectory()` 中运行。
- 绝不读取或写入真实用户的 `~/.agent_data/` 目录。

### 2. 真实 Core Stack 经过 (Runs Real Production Stack)
- 不构建虚假的 mock product。所有试用动作均直接经过 `PersonalAgentApp` 真实 API。

### 3. 确定性 Hard Oracle 优先 (Hard Deterministic Invariants)
- 拒绝使用 LLM 主观打分判断“看起来对不对”。
- 全部使用硬性确定性断言（包含文本断言、状态断言、数据库 Counts 断言、外键断言、确定性全局系统不变量检查）。

### 4. 故障校准 (Calibration Suite)
- Harness 包含已知的故障校准用例（例如 `M003` 冲突 `/remember`）。在 Bug 未修复之前，Harness 精准报告 FAIL；在基于 `LifecycleProposal` 的通用修复引入后，成功验证 PASS。

### 5. 自动修复开关 (Fixer Status)
- 当前阶段 **Autonomous Fix Loop 被禁用 (Fixer Status: DISABLED)**。必须通过架构防过拟合审查，严禁开启无约束的自动修代码。

---

## 支持的用户动作 (Supported Actions)

- `CHAT`：普通多轮对话交互
- `COMMAND`：CLI 快捷命令 (如 `/remember`)；普通对话由 Agent 自主决定是否调用 `update_long_term_memory` 工具
- `RESTART_APP`：模拟应用程序崩溃或重启
- `RESUME_SESSION`：恢复指定的历史 Conversation Session
- `CREATE_SESSION`：创建并切换新的对话会话
- `CREATE_TASK`：创建新的 Task 3 任务
- `SWITCH_TASK`：切换关联的 Task 3 任务
- `CONFIRM`：确认具有副作用的 Tool 操作 (`/confirm`)
- `REJECT`：拒绝具有副作用的 Tool 操作 (`/reject`)

---

## 断言与 Oracle (Hard Oracle Evaluator)

1. **`CONTAINS_TEXT`**：可见回复包含特定字符串。
2. **`NOT_CONTAINS_TEXT`**：可见回复不包含特定敏感或泄漏文本。
3. **`RESULT_STATUS`**：`AgentResultStatus` 匹配预期。
4. **`MEMORY_COUNT`**：Task 1 长期记忆条目总数符合预期。
5. **`CURRENT_MEMORY_COUNT`**：Task 1 中 `semantic_validity == 'CURRENT'` 的有效记忆条数符合预期。
6. **`SESSION_ID_SAME` / `SESSION_ID_DIFFERENT`**：Session 绑定与隔离断言。
7. **`TOOL_CALLED` / `TOOL_NOT_CALLED`**：工具调用断言。
8. **`EXECUTOR_COUNT`**：Side-effect 物理执行计数断言。
9. **`NO_UNGROUNDED_WEATHER_CLAIM`**：在无 Weather 工具时，严禁编造特定气温（如 `28°C`）或天气状态。
10. **`GLOBAL_INVARIANTS`**：
    - `session_id != task_id`
    - 每条 memory_id 至多 1 条 `CURRENT` Revision
    - 对话消息必须关联合法 Session ID
    - 未确认前 Side-effect 工具物理执行次数为 0

---

## 运行方式 (Usage)

```bash
# 1. 运行标准场景套件 (14 个场景 PASS)
python3 code/scripts/personal_agent_trial.py

# 2. 仅运行指定分类
python3 code/scripts/personal_agent_trial.py --category conversation
python3 code/scripts/personal_agent_trial.py --category memory

# 3. 运行 deterministic 随机 seeded fuzz
python3 code/scripts/personal_agent_trial.py --seed 123

# 4. 运行 pytest harness 自动化测试套件
python3 -m pytest -q code/tests/test_product_trial_harness.py
```
