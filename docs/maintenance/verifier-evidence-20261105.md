# 下游 Verifier 原始证据漏传修复（2026-11-05）

基线：`c10fd0a`。用户报告 multi_fixed 已执行 read_file，但后续 Verifier 仍因缺少回执/原文而 BLOCKED。

## 代码缺口和复现

直接执行工具的子任务原本会将自己的真实回执交给自己的 Verifier。
但是 verified_dependencies/verified_subtasks 只携带答案、成果 ID、证据 ID 和输入哈希，
未携带这些 ID 对应的真实回执或原文包。没有自己调用工具的依赖子任务的 receipts=[]，
整体验证同样缺少可独立检查的原始内容。

source_reads 是记忆原文解析记录，不是 read_file 执行日志，不应把文件工具回执硬塞进它。
本次本机产品数据没有提供报告所指失败会话的 coordination 记录，因此代码缺口通过隔离场景重现，
不声称已经定位截图原会话的全部上下文。

修复前，严格检查原始证据的受控 Provider 使用真实 read_file 与临时 SQLite：

| 阻断点 | 结果 |
| --- | --- |
| left/right 都执行并保存真实回执，merge Verifier 要求原始证据 | FAILED / COLLABORATION_VERIFICATION_BLOCKED |
| 子任务通过，whole_user_goal Verifier 要求原始证据 | FAILED / COLLABORATION_GOAL_INCOMPLETE |

此前模拟 Verifier 多接受成果 ID/摘要，因此这些路径在旧回归中可以通过；缺少严格的下游原文要求覆盖。

## 修复

新增宿主组装的 supporting_evidence v1，保留实际工具回执、当前记忆 revision 与已解析原文窗口。
声明依赖只继承其已验证前驱的原始证据；多层依赖继续传递，整体阶段合并全部已验证子任务。
按 ID 去重，内容冲突时拒绝。原来的 receipts 仍表示当前 Executor 自己执行的工具。

通过 support:<subtask> / EvidenceHandoff 阶段持久化，不增加 Provider 调用。交接绑定当前成果 ID、
证据哈希和阶段输入哈希。复用时检查成果、node、attempt 均有效；缺失、篡改、归属不同、撤销均拒绝。
执行契约加入证据协议版本，旧协议不能被静默当作新协议。原有来源新鲜度、任务/消息版本和授权检查保持执行。

Verifier 提示词明确区分当前执行回执与完整支持证据，继续按内容作独立语义判断，
无工具观察的编造答案仍须阻断。没有通过补默认答案、改成无条件 PASS 或重复读取掩盖漏传。

## 验证

专项回归：`code/tests/test_verifier_evidence_handoff.py`。覆盖直接文件、依赖与整体、两层传递、
去重、无关分支隔离、无实际读取时阻断、重启修复复用、替换失效旧回执、原文窗口与记忆 revision，
以及证据包内容/哈希/归属和撤销。

全量回归及代码快照：
[`verifier-evidence-20261105.json`](../evaluations/verifier-evidence-20261105.json)、
[`verifier-evidence-20261105.xml`](../evaluations/verifier-evidence-20261105.xml)。
最终全量为 **571 passed、1 skipped、0 errors、0 failures**，用时 163.61 秒；
新增专项 11 项。跳过项仍为 Windows 可选 readline。修改 Python 的 Ruff 与全仓严重静态错误检查通过。

真实 API：[`live-verifier-evidence-20261105.json`](../evaluations/live-verifier-evidence-20261105.json)，
使用隔离临时目录与两份随机 token 文件。5 项断言通过：真实图成功、保留两个随机原值、
merge 收到两份真实回执、whole_user_goal 收到两份真实回执、merge 无重复文件读取。
四次 Verifier 均 PASS；直接文件子任务各有 1 条执行回执，merge 的执行回执为 0、支持回执为 2，整体支持回执为 2。
支持回执逐条与 Journal 原始 result_json 比较，不根据模型自述认定读取成功。
联调仅针对图执行，显式关闭自动任务验收/经验学习，结构化格式恢复使用生产默认相同的有界开关；
不把这项专项联调称为任务生命周期、任意文件长度或模型长期可靠性的全面验收。

不存在绝对无 bug 的结论；本次针对可复现的原始证据漏传及其恢复/隔离边界验证。
