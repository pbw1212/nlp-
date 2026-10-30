# 协作中的需求驱动证据

状态：Implemented / experimental，更新于 2026-10-31。默认模式仍为 `single`。

## 执行流程

`multi_fixed` 中，每个子任务先由 Memory v2 提出具体 EvidenceNeed、选择当前有效记忆，
并从宿主授予的 source_catalog 请求原文；EvidenceCheck 按需求逐项检查。
不足时，EvidenceSearch Provider 提出与原需求绑定的 Task 2 查询或原文字符窗口，
宿主执行补查，再交给 Memory 和 EvidenceCheck。仍不足时阻断 Executor 并报告缺口。

语义选择全部由版本化结构化 Provider 提案承担。代码只校验 schema、ID/来源所有权、
当前 revision、权限、预算和生命周期。扩展过程中不得删除或放宽原始需求，
也不能把检索相似度、来源哈希一致或模型自信视为事实成立。

## 来源与窗口

SourceResolver 支持当前记忆 provenance 绑定的原始 USER 消息，以及显式授予的同任务成功工具回执。
缺失原文、角色错误、哈希不匹配和历史 provenance 不完整都会保留缺口，不退回摘要冒充原文。

SourceDescriptor 保存原始身份、完整内容哈希、offset 和可选 length。
SourcePacket 记录总原文字符数、返回窗口和截断状态。显式窗口可以完整解析为 RESOLVED，
但只证明该片段已绑定完整原文，不证明窗口覆盖整个需求；EvidenceCheck 仍须核查语义覆盖。
未解析完的截断片段不能满足 requires_original。
已读窗口在执行前、验证前、最终提交和结果重放时重新读取并比较。

read_file 的外部文件当前状态校验由工具适配器承担，区别于读取历史工具回执；
详见 [来源版本与修复](repair-execution.md)。

## 预算与恢复

- 每个子任务最多 8 个需求；初始读取最多 4 个来源、每个 4000 字符，总计 12000 字符。
- 最多 2 轮补查，每轮最多 2 个查询，每查询最多 8 条候选；合并记忆上限 24 条。
- 补查总计最多再读取 4 个来源窗口；窗口最多 4000 字符，所有轮次总读取字符上限不超过 28000。
- 原始目录上限 32，补查目录上限 40；超限不会扩大授权。
- 所有 Provider 调用（含 Task 2 的语义解释）共享当前 run 的持久预算；没有按角色重置额度。

查询、提案、原文包和审核通过不可变 handoff 保存；重启后阶段缓存验证输入和 payload hash，
已经接受的补查不重复请求模型。原文仍重新核对，旧版本不能通过缓存复活。
trace 保存来源与哈希，不另外复制完整原文库。

## 验收和边界

测试：`test_source_resolver.py`、`test_collaboration_evidence.py`、`test_evidence_expansion.py`。
覆盖原文尾部窗口、额外召回、无证据阻断、伪造 need/source 拒绝和无调用重放。

这不是通用文件/网页浏览器，也不是无限循环检索。来源修正改变规划输入时仍要求新请求；
来源充分性是 Provider 语义判断，不是形式化证明。见 [项目进展](../project-progress.md)。

真实 Provider 长原文尾部窗口验收见 [记录](../evaluations/live-evidence-expansion-20261031.json)；输入为隔离的合成用户记录，模型调用使用实际 Provider，非官方任务基准。
