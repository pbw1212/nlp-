# 系统运行与调度审计（2026-10-31）

对象：`D:\MAPAS\MAPAS` 的 `main`，审计起点 `15012d7`。本次目标是验证运行链路、
持久化调度和故障恢复，修复可复现缺陷后同步唯一正式远端。

## 发现与修复

| 边界 | 原问题 | 修复 |
| --- | --- | --- |
| 结果提交 → 验收入队 | 两次提交之间崩溃会漏掉验收作业 | 来源终态与 lifecycle_result_outbox 同事务；恢复时只接纳最新且任务快照未变化的来源 |
| Task 3 完成 → coordination 提交 | Task 3 已完成后的普通异常会把作业永久标为失败 | 核验归属与完整性后保留待恢复作业，沿用原完成记录补齐经验作业 |
| 取消与晚到结果 | 更新已取消作业失败后仍会覆盖 task_cycles | 只有成功更新 PENDING 作业才推进投影；完成封存再次检查作业与取消状态 |
| 锁冲突 | 最新任务占锁会直接结束本次 drain | 在有界额度内跳过占锁任务，继续独立任务 |
| 跨会话诊断 | read/list 恢复会话并意外执行待办 | 生命周期、修复、经验查询不再恢复或切换会话，不触发模型或调度 |
| 跨目录诊断 | 当前目录的权限可能误判旧会话来源 | 不同工作目录标记 NOT_CHECKED_WORKSPACE，不据此使旧经验失效 |
| 运行与作业终态 | 作业已发布、模型运行尚未结束时崩溃会留下 RUNNING | 先持久化已接受验收判断，再发布作业结果；终止失败作业时关闭其未结束模型运行 |
| 终态不可变 | 相同结果 payload 可以被再次写成其他运行状态 | 状态与结果必须同时相同才允许终态幂等写入 |
| 历史完成与当前证据 | COMPLETED 详情可能仍显示已失效或已撤销经验为 ACTIVE | 保留历史完成，另报 current_result_validity、current_experience_state |

以上均有故障注入或接口回归。新增 `code/tests/test_system_audit.py`，覆盖真实 SQLite、
文件变化、抢锁、跨会话查询、原子回滚和重启，不使用实际服务密钥。

## 验收证据

原有基线：542 passed、1 skipped，见
[`system-audit-baseline-20261031.xml`](../evaluations/system-audit-baseline-20261031.xml)。
跳过项是 Windows 的可选 readline。

最终代码全量回归：**560 passed、1 skipped、0 failed、0 errors**，用时 190.31 秒；
相较原基线新增 18 个故障注入与边界用例。
结果与快照：[`system-audit-20261031.json`](../evaluations/system-audit-20261031.json)、
[`system-audit-20261031.xml`](../evaluations/system-audit-20261031.xml)。
产品场景：[`product-trial-system-audit-20261031.json`](../evaluations/product-trial-system-audit-20261031.json)，
14 个场景全部通过，50 步有种子的扰动通过。

真实 Provider：[`live-system-audit-20261031.json`](../evaluations/live-system-audit-20261031.json)。
以最终汇总的 `engineering_assertions_passed` 为验收依据，不将请求模型名视为已核验的服务端模型身份。
首次尝试在批准后的执行中连续遇到两个缺字段响应，按预算停止；完整失败证据保留于
[`live-system-audit-attempt1-20261031.json`](../evaluations/live-system-audit-attempt1-20261031.json)。
随后给已有的一次格式纠正请求增加 schema 路径和缺失字段名反馈，不拷贝模型值，
不补造语义内容，不放宽校验，不增加重试次数。对应回归同时检查两次调用均计费、模型值不进入系统指令。
第二次尝试没有格式错误，执行与任务封存成功，但经验提案未通过来源/前提门禁，
未生成有效经验，因此未完成后续复用断言；保留于 `live-system-audit-attempt2-20261031.json`。
进一步明确 Experience 角色的 ID 归属契约：PROPOSE 必须有非空前提和实际成果引用，
只能复制顶层 artifacts[].artifact_id，不能替换成运行、任务、完成记录或工具回执 ID。
最终联调还保留合成用例的原始经验提案，便于区分模型输出错误与门禁错误。
最终版本的六项闭环断言全部通过：等待真实批准、重启修复、自动完成与经验审核、
重复 drain 不新增调用、封存后的经验跨重启用于新任务、撤销后阻断旧消费者答案。
本轮一次 agent_action 缺少 tool_name/tool_arguments，既有单次纠正成功，调用仍计入预算。
最终联调记录的全部代码 SHA-256 与待提交文件一致；两次失败尝试保留，不能据单次通过推导成功率。

修改 Python 文件执行完整 Ruff 检查，全仓执行 E9/F821/F822/F823 严重错误检查、
compileall，以及 `git diff --check`，均通过。全仓完整 Ruff 仍有既有风格/兼容注解/未使用导入等债务，
不把严重错误检查通过表述为全仓所有 lint 均通过；本次未批量重写无关代码。

## 结论边界

这是一轮工程验收，不能证明任意输入下绝对无 bug，也不提供正式性能或优于单智能体的结论。
`multi_fixed` 仍是最多三个只读子任务的串行协作；调度发生在轮次前后、会话恢复与显式 drain，
没有常驻后台调度服务。Task 3 与 coordination 的恢复基于可核验记录，不宣称跨库分布式原子性。
未知外部工具的新鲜度仍不能由历史回执证明。真实模型可能拒绝或错误响应；校验失败必须停止，
不能以保存经验或任务完成来掩盖失败。

同步目标按 `docs/repository-policy.md` 核验为 `origin` / `main`，URL 必须精确等于
`https://github.com/pbw1212/nlp-`。凭据、本机产品数据库和临时测试状态不随本次提交发布。
