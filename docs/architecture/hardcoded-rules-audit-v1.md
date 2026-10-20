# 硬编码规则审计与记忆路线调整

## 本次结论

普通对话不再经过单轮自动晋升，也不再用正则、关键词和长度门禁判断“是否值得记忆”。
普通成功轮次只写入 durable ingestion buffer；达到配置阈值后，NEMORI 读取完整的多轮用户/助手轮次，
由 Provider 完成 episode partition、候选事实判断和 `ADD/UPDATE/CORRECT/NOOP` 生命周期提案。`/remember` 是唯一的
立即写入入口。

已删除：

- `MemoryPromotionGate` 的临时词、个人偏好词、问句词、最小长度等语义正则规则。
- `MemoryPromotionService` 及 `LongTermMemoryManager.process_promoted_instruction()` 单轮写入路径。
- Task 3 updater 中按问号、假设词、完成词和“已/成功”等字符串过滤模型提案的逻辑。

## 发现的问题

| 区域 | 原规则 | 判断 | 处理 |
|---|---|---|---|
| 单轮隐式记忆 | 用关键词把一轮用户输入判为长期偏好/事实 | 语义误判、跨轮信息丢失、每轮额外 Provider 写入 | 删除，改为批量 NEMORI |
| Task 3 updater | 用字符串识别问题、假设和完成状态 | 语言覆盖不完整，容易误删模型正确提案，也会随语言变化失效 | 删除语义过滤，仅保留来源、证据、目标存在性和生命周期不变量 |
| NEMORI 预测残差 | Provider 返回残差后，运行时用 `prediction_error_threshold` 强制决定是否 ADD | 仍是本地语义门禁，而且会把更正错误地变成新增记忆 | 删除阈值；自动批量路径改为 Provider 提议 `ADD/UPDATE/CORRECT/NOOP`，本地只做结构、身份、来源和原子事务校验 |
| 批量触发 | 默认 10 个前台轮次或时间阈值 | 调度策略，不负责判断内容语义 | 保留并配置化；事件先落盘，失败可重试 |
| 生命周期/数据库校验 | event identity、support span、ACTIVE target、tool success、来源 authority | 防止伪造、越权和不可恢复状态，不是语义沉淀分类器 | 保留 |
| 上下文预算 | 最大消息、字符、证据和 compaction 大小 | 工程资源边界，不是内容价值规则 | 保留，后续根据压力实验调参 |

## 当前仍需实验验证的风险

1. Provider 是否能在十轮窗口内把跨轮偏好、修正和暂时信息区分开。
2. `ADD/UPDATE/CORRECT/NOOP` 提案是否重复、是否把助手建议误当用户事实。
3. batch provider 失败后重试是否保持同一 `consolidation_run_id` 和事件幂等性。
4. 新会话能否通过长期记忆召回已沉淀内容，而不会把历史事件原文泄漏进当前上下文。

这些问题应由真实模型和确定性假模型的正反实验回答，不应继续添加领域关键词补丁。
