# 来源版本与有界修复执行

状态：Implemented / experimental，更新于 2026-10-31。

## 外部文件观察

read_file 一次读取至多 8 MiB UTF-8 文件，返回有界文本；同时把原定位路径、
实际解析路径、完整字节 SHA-256、字节数和截断标志写入真实工具回执。
内容哈希来自同一批实际读取的字节，不由模型推断或事后读取另一版本产生。

注册的 FileReadExecutor 在恢复、重放和成果复用前重新检查授权范围、定位路径及当前字节哈希。
文件修改、删除、路径重定向或权限收回会使相关成果和依赖后继失效。
工具回执内容一致与外部文件仍有效是两次独立核验。
工作者的 Task 3/记忆输入投影固定在准备快照；工具结果仍通过 Runtime 持久化，
但不会把其他分支刚写入的全局任务状态隐式加入当前工作者上下文。
没有显式核验适配器的工具保持 UNKNOWN；截断结果不能授权复用。

## 失效与修复

宿主撤销或适配器发现文件变化后，失效事件与 PENDING 队列项在 coordination SQLite
事务中记录，历史 attempt、artifact 和回执保留。会话恢复或显式 drain 扫描当前最近
最多 100 张终态图，跳过已经被成功修复替代的父图，默认处理至多 3 个待处理项。
长期未扫描的更早图仍在实际重放时核验，不将该有界扫描称为常驻全量监控。

修复流程：

1. 重验任务、记忆、对话、来源、原计划快照和执行条件。
2. 从有类型、带哈希的 JSON 准备快照恢复原输入，取得进程间锁。
3. 使用稳定 repair ID 建立独立子运行，沿依赖执行，保留原预算、开始时刻和阶段缓存。
4. 允许复用核验通过的独立根节点；其他节点重新取证和执行。
5. 分别核验子任务和整体目标，并在交付前重新检查来源和父快照。
6. 保存新结果与来源链接，写入独立对话 turn；同源图多个待处理事件共同绑定这次修复结果。

独立根节点复用要求准备上下文、完整对话投影、环境指纹与提示词一致，原文可核验，
记忆和来源引用仍有效，实际工具回执完整，所有用到的工具观察均经适配器确认 CURRENT，
验证证据 ID 属于允许集合。若准备时 Task 3 含未跟踪的工具/代理来源状态、外部观察或压缩世界状态，不授权根节点复用。无工具成果也适用这些约束。复用保留原观察身份和
reused_from 链接，后续仍能因原文件变化而失效。

修复产生的子图也保存准备快照，因此修复后的结果可以再次发生来源变化并继续修复。
同一图同时发生多个失效事件时选择同一规范事件和快照，队列记录 repair_resolutions；
逐个重放这些事件也不再次执行工具或调用模型。

预算耗尽安全终止，中断保留原 run/阶段；进程死亡由 OS 释放锁。
未知外部新鲜度会导致重跑。目标、约束或规划阶段来源变化仍使整图失效并要求新请求，
不会用局部修复绕过全局变化。跨数据库与外部文件之间没有分布式原子保证。

## API 和产品入口

Coordinator：`prepare_repair`、`plan_repair`、`execute_repair`、`execute_saved_repair`、
`repair_status`、`refresh_external_sources`。应用 API：`repair_collaboration`、
`revoke_and_repair_collaboration`、`process_pending_collaboration_repairs`。

CLI：`/repair-graphs`、`/repairs`、`/repair <run_id> <event_id>`、
`/revoke-repair <run_id> <artifact_id> <event_id>`。
JSONL：`repair/graphs`、`repair/list`、`repair/run`、`repair/revoke`、`repair/drain`。

持久化计划中的 NOT_STARTED 是创建时记录；当前执行状态以 repair_status 和队列为准。
成功队列项可指向同一个修复子运行。旧版本缺少完整快照或协议不兼容时失败关闭。

## 验证

离线：`test_file_source_repair.py`、`test_repair_execution.py`、
`test_repair_provenance.py`、`test_collaboration_repair.py`。
覆盖多分支真实文件、两次更新、重启、来源收回、事件合并、重复恢复、预算与锁。
真实 Provider 记录见 [文件/经验联调](../evaluations/live-source-repair-experience-20261031.json)。
历史单节点联调保留在 `../evaluations/live-*-20261029.json`。
这些是工程验收，均不是性能评测或任意工具安全复用证明。
