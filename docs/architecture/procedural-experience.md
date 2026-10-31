# 程序性经验生命周期

状态：Implemented / experimental，更新于 2026-10-31。

## 存储与来源

ExperienceService 使用 coordination.db 中独立的 experiences / experience_events 表，
不写 Task 1 用户事实。每个候选保存任务类型、适用前提、操作摘要、观察到的结果、
适用范围、支持 artifact ID、来源运行、来源前沿哈希、环境指纹与可选 revision_of。
候选内容不可变，revision_id 是内容哈希；状态由带哈希的追加事件维护和核验。

只有成功完成且来源可核验的运行可以成为提炼来源。源记忆 revision、对话消息、
工具观察和任务图在提炼、审核、检索与消费时重验；来源成果撤销与经验停用在同一
coordination 事务中处理。更早的源记忆或对话变化通过读前重验发现。
派生经验的来源链也会递归核对，循环或超过 64 层保守拒绝。

## 准入与适用性

任务完成作业或显式 learn 操作先由 Experience Provider 提出候选或 ABSTAIN，再由独立
ExperienceReview Provider 根据真实成果及回执判断 ADMIT、QUARANTINE 或
NEEDS_CONFIRMATION。只有 ADMIT 且来源、支持 ID 与快照均通过宿主校验才进入 ACTIVE。
不确定候选保持待澄清，通过显式 review 提供确认说明后仍须重新审核，不直接改成成功。

状态：CANDIDATE、ACTIVE、QUARANTINED、NEEDS_CONFIRMATION、STALE、REVOKED、SUPERSEDED。
失效/撤销经验不能重新激活；修正通过新的来源运行生成 revision_of 版本，准入后旧版停用。
同一提炼/确认动作的调用预算与已接受阶段持久化，重复成功提炼不重复请求模型。

后续协作最多读取最近 100 条经验，再从环境兼容且当前有效的记录中提供至多 16 条，
ExperienceSelect 对每条判断 APPLICABLE、INAPPLICABLE 或 UNCERTAIN。
只有明确适用的经验进入 Planner/Memory/Executor 数据包；其身份和 revision 写入 trace，
执行及重放前重新核验。经验不授予工具权限，也不作为新的当前事实或工具回执。
这是有界候选筛选，没有额外的语义索引或相似度阈值捷径。

## 反馈与撤销

消费运行完成后自动尝试写入幂等反馈；CLI/JSONL 同时保留显式反馈入口。
反馈必须引用实际消费了相同经验 revision 的终态运行；成功结果还需核验当前来源。
失败反馈将 ACTIVE 经验隔离，成功反馈仅记录观察，causal_contribution 始终为 UNKNOWN。
反馈校验失败记安全边界日志，可在来源核验后显式重试，不伪造效用改善结论。

宿主可显式撤销经验。源图失效会使关联候选/有效经验 STALE；消费过该经验的结果在
再次重放前会被阻断。没有把一次任务成功当成因果贡献、强化学习奖励或跨任务通用结论。

## 操作入口

CLI：

```text
/experience list
/experience learn <run_id> [revision_of]
/experience review <experience_id> <确认说明>
/experience revoke <experience_id> <event_id>
/experience feedback <experience_id> <consumer_run_id> <event_id>
```

JSONL：experience/list、experience/learn（runId、可选 revisionOf）、
experience/review（experienceId、confirmation）、experience/revoke（experienceId、eventId）、
experience/feedback（experienceId、runId、eventId）。可选 threadId 选择当前会话；
learn 来源和 feedback 消费运行必须属于当前会话。initialize 提供 proceduralExperiences 能力标志。

提炼与复核使用独立的持久预算运行，筛选使用当前协作预算，反馈不调用模型。
整个任务通过验收并封存后自动处理提炼作业；不为每轮对话无条件存入经验。调度在轮次边界和会话恢复执行，没有常驻后台服务。默认 single 模式的用户事实机制不变。

## 验证

`test_experience_lifecycle.py` 覆盖准入、弃权、待澄清、错误来源、重启、
适用性不确定、来源撤销/变化、内容篡改、失败隔离、修订与 API。
最新回归与真实联调见 [项目进展](../project-progress.md)。

完成任务的来源资格由 [任务级完成记录](task-lifecycle.md) 保留，正常结束不再直接使经验失效；目标、证据、原始消息或封存记录变化仍受校验。

经验提案和独立审核现在还获得来源绑定的原始 USER 请求及正常完成记录，避免仅凭同伴的“用户已批准”描述推断批准事实。无法提供原始请求身份的旧来源拒绝准入。
