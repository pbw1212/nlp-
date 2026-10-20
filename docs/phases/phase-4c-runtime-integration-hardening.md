# Phase 4C：Runtime Integration Hardening

状态：**Implemented / Acceptance Complete**

## 1. 目标与范围

本阶段以 `066525fe710313a15c6e5fbe616015e26c83606f` 为审计起点，对项目计划书所述
Task 1 长期记忆、Task 2 检索、Task 3 工作状态、对话层、AgentRuntime、MainAgent
和 CLI 集成路径做边界审计。原则是修复能够被失败用例或不变量证明的问题，同时不改动
已经通过验收的 Task 2 排序/适用性参数和 Task 3 领域语义。

## 2. 已实现改进

- 修复 `EvidenceBundle` 到 Prompt 的字段契约，并对记忆、历史和工具输出做 XML/HTML
  转义；运行时 warning 不再出现在当前用户请求之后。
- 自动记忆开关启用时，候选项通过规范生命周期管道写入，来源标记为 `INFERRED`，并
  保留 promotion event、session 和 message 身份；默认仍为 OFF。
- `NOOP` ingestion 持久化命中的 `memory_id`，事件重放不再次请求 Provider。
- Conversation append 使用单个 `BEGIN IMMEDIATE` 事务完成 identity check + insert，
  相同身份不同内容显式拒绝；上下文的零预算、负预算和字符上限获得严格语义。
- 生命周期配置在入口拒绝无效算法参数；时钟回拨时遗忘强度保持在 `[0, s0]`。
- 非暂态 HTTP 4xx 不再进行无意义重试；schema 降级与暂态状态仍保持既有有界重试。
- 修复 TaskState provider 异常导入、生命周期错误参数、Product Trial 注解导入、重复 DTO
  和连续副作用确认状态等集成缺陷。

## 3. 验证门禁

最终门禁结果：

- `pytest`：266/266 PASS；statement coverage 88%（审计前 87%）。
- `mypy src --explicit-package-bases --ignore-missing-imports`：72 个源码文件零错误
  （审计前 52 个错误）。
- Ruff `F821/F811/F822`：零未定义、重复定义或无效导出错误。
- Python bytecode 编译、wheel build 和核心 import smoke：PASS。
- Task 2 recall/applicability/evidence、Task 3D、AgentRuntime、MainAgent 六组独立验收：PASS。
  Recall@4=1.0、MRR=1.0、Applicability Accuracy=1.0，mismatch contamination
  从 0.25 降至 0；这些结果与审计前冻结基线一致。
- Product Trial：14/14 场景 PASS；50 步 seeded fuzz PASS。

上述门禁不包含真实付费 Provider 的语义质量测试，因此不把确定性 test double 的结果外推为
真实模型质量结论。

## 4. 明确保留的边界

- 不凭主观判断调整已经达到既定验收指标的检索、Applicability 或 EvidenceBundle 参数。
- 历史阶段记录中的自动 promotion 当时默认关闭；当前发布实现已改为默认开启的批量后台 buffer，并已删除单轮规则晋升路径。本文保留当时的阶段结论，不覆盖当前产品状态。
- 不自动修改 `.agent_data` 中的用户数据库或历史冲突记录。
- 不在缺少真实工具、数据集和失败策略时仓促实现动态事实工具、长会话语义压缩或 Task 4。

设计依据与兼容性决定见 [ADR-0019](../decisions/ADR-0019-runtime-contract-idempotency-and-provenance-hardening.md)。
