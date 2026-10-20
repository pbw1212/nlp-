# ADR-0022：在现有 Runtime 之后接入可选串行协作

Status: **Accepted / P1 implemented**。日期：2026-10-14。

## 问题

复制四个 PersonalAgentApp 或对子角色调用整个 chat turn，会重复用户输入入库、任务绑定和记忆来源处理，同时共享活动会话与可变 Provider 用量。自由互聊也会失去工具授权、实际完成证据和统一预算。

## 决定

保留默认 single，新增显式 multi_fixed。MainAgent 在一次 Runtime 用户处理完成后委托 PreparedTurnRunner；显式记忆请求和确认恢复保留原路径。四角色先串行，Executor 使用独立工具目录和稳定子任务身份，复用原工具循环/journal。Coordinator 用独立 SQLite 持久化阶段及成果，不接管 Task1/Task3/Conversation/Journal 的权威数据。

P1 只给工作者 READ_ONLY 能力；内部记忆更新的角色化提案/提交和外部写入协作后置。每个模型调用先预约共享预算，真实 Provider 内部重试关闭，返回后记录独立 usage。现有可变客户端只在串行锁内借用；不据此承诺并行安全。

角色判断、任务分解和记忆选择使用 typed/versioned Provider schema；确定性层只检查图、类型、授权、身份、来源 revision、事务和资源限制。交接不是数据库事实写入；工具 journal 回执也不等于所有语义结论都已正确。

## 取舍与后果

避免替换成熟 Runtime、数据格式与工具执行边界，先建立能运行的协作闭环。代价是 experimental 模式暂不具备普通自动记忆沉淀、写工具协作和局部 repair；用户仍可用 single 获得既有功能。模型核验可能错误，外部基准与预算匹配对照在 P4 前不能宣称研究收益。

完成重放仍重新检查引用的长期记忆，禁止撤销事实借缓存返回；不改变历史 retention/软删除含义。未完成运行遇到准备输入改变则停止，完整派生版本检查/局部修复由后续阶段补齐。

## 证据与映射

- 批准设计：[多 Agent 方案](../research/mapas-multi-agent-plan-2026-10-14.md)。
- 实现：`collaboration/`、`MainAgent.prepared_runner`、CLI/JSONL execution-mode。
- 验证：`code/tests/test_collaboration.py`；阶段结果：[P0–P1](../phases/multi-agent-p0-p1-20261014.md)。
- 外部借鉴为思路/协议适配，没有复制论文算法源码进入生产模块；MemoryArena CitySearch 在指定外部 checkout 原位执行。

Supersedes: None。保持 ADR-0001/0008/0019/0020 的所有权、正式文档与失败真实性约束。
