# MAPAS 多智能体基础层：P1 当前实现

状态：Implemented / experimental opt-in。日期：2026-10-14。

## 入口与兼容性

默认仍为 `single`。CLI 与 JSONL host 均支持 `--execution-mode multi_fixed`：

```powershell
python code/scripts/personal_agent.py --execution-mode multi_fixed
python code/scripts/personal_agent_app_server.py --data-dir .runtime/my-agent --execution-mode multi_fixed
```

`PersonalAgentApp(..., execution_mode="multi_fixed")` 在执行目录新增独立 `coordination.db`。Task1、Task3、对话与工具 journal 的格式不变。全局配置中的 API 密钥继续由既有配置逻辑读取，不写入协作记录。

## 当前链路

```text
一次真实 USER 入库
  → 原有 AgentRuntime：任务绑定、任务更新、Task2 证据
  → MainAgent.prepared_runner
  → Planner：最多 3 个结构化子任务，校验 DAG/能力
  → 每个子任务：Memory 选择已有证据 → Executor 工具循环 → Verifier
  → Verifier 核查整体目标覆盖
  → 持久化终态 → 原有 TOOL/ASSISTANT 对话与 JSONL 事件
```

Executor 调用已有 `AgentLoopController.run_loop`，使用从外层上下文派生的稳定子任务 turn ID。它不再次调用 `process_user_turn`，不创建伪造 USER 消息。工具的真实执行事实仍由原 Journal 拥有，工具结果的 Task3 处理仍经原 Runtime。

P1 工作者只获得计划所列的 READ_ONLY 工具。INTERNAL 记忆工具和 SIDE_EFFECT 工具不进入工作者目录。显式自然语言记忆写入仍由 MainAgent 原路处理，只有实际 Task1 提交才确认成功。普通对话的自动长期沉淀在 multi_fixed 模式尚未移交给 Memory 角色，属于 P2；需要现有自动沉淀/外部写入能力时使用默认 single 模式。

四个角色通过独立 schema、角色输入和能力边界区分。P1 采用串行调用及加锁的 Provider 适配器保护现有客户端的可变 usage 字段，随后立即记录请求级用量；没有并行共享客户端的保证。未来并行角色必须先引入独立客户端或真正无状态的调用结果。

## 持久化、交接与恢复

- `runs`：session/turn 身份、输入 hash、任务所有权、阶段版本、终态。
- `role_tasks`：角色、输入 hash、PRODUCED/VERIFIED 状态。
- `handoffs`：稳定消息 ID、发送角色、接收者、不可变结构化结果及 hash。
- `artifacts`：Executor 产物与验证状态，不能把一条完成文字直接升级为 VERIFIED。
- `model_calls`：调用前持久化预算预约，完成后的状态、独立 usage 与耗时。

handoff、角色任务与 run 版本推进在同一 SQLite 事务中提交，检查 expected_version。重复 stage 读取时验证角色、输入和 payload hash。失败、未知与未经验证的成果不会作为成功依赖交给后继。

接收子任务可以引用其前置任务的已验证 artifact 与证据 ID；其他分支成果不自动进入该交接包。工具证据必须来自属于该 Executor run 的 Journal 回执。模型捏造 memory/tool/artifact ID 会失败，不会被补成猜测值。

完成 run 的重放可复用已保存结果，不重新进行用户任务解释和工具执行。结果引用的长期记忆在重放前重新检查当前 revision、保留状态及内容 hash；被修改或撤销时返回 `COLLABORATION_STALE_REPLAY` 并隐藏旧答案。历史数据库记录仍保持原样，这不是物理抹除历史。

未完成 run 可在准备后的输入保持一致时复用已有 stage。输入变化时失败关闭，要求新 turn；P1 尚未实现基于执行依赖的局部重新规划。进程死亡后尚未结算的调用保留预约费用，预算和开始时刻不会在重启时清零。

## 预算与错误

默认每个协作 chat turn 最多 24 次模型调用、200000 个核算单位、4096 completion tokens/请求、180 秒模型调用期限。预算类型必须合法、正数且有限。真实 Provider 禁用内部重试，解析/上游组件发起的额外调用也经过共享预算，而非每个角色获得一份系统额度。

调用前用 UTF-8 输入字节、schema/协议开销和输出上限作保守预约；已知用量按 Provider 的 input/output tokens 结算，失败/未知用量保留预约。核算单位不是精确 tokenizer 测量，不能当成论文 token 指标；实际 usage 单独保存。模型过期返回也不被接受。

预约覆盖 Runtime、对话压缩、四角色及工具后的 Task3 解释。显式旧 CLI 直达命令不属于 multi_fixed chat turn；其既有协议不变。工具仍使用原适配器的超时和原循环步数上限，P1 没有跨进程强制取消工具的保证。

Provider、schema、来源、权限、预算及整体覆盖错误均返回非 SUCCESS，保留真实工具调用 ID。错误日志记录边界和异常类型，不记录原始异常正文、模型 prompt 或 API 密钥。Task3 写入警告继续经原有真实性说明处理。

## 已实现与尚未实现

已实现：四角色串行协作、有限任务 DAG、现有工具执行、回执证据、当前记忆 revision 校验、事务交接、全局调用账本和稳定终态重放。

尚未实现：原文升级检索、程序性经验写入、向量缓存/远端 GPU、完整派生输入版本 frontier、执行分支局部修复、只读工作者并行、分布式通信、取消与多用户服务。Verifier 的模型语义判断也不是形式化正确性证明。

外部任务数据和真实验证见 [P0–P1 阶段报告](../phases/multi-agent-p0-p1-20261014.md)。本阶段只证明基础工程链路可运行，不证明协作优于强单 Agent。
