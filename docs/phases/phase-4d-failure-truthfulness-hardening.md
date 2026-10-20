# Phase 4D：Failure Truthfulness Hardening

状态：**Implemented / Acceptance Complete**

## 1. 目标与边界

本阶段在 Phase 4C 冻结语义基线之上，对 `code/src/` 与运行/评估脚本做地毯式错误语义扫描。
目标不是调整已通过验收的检索、适用性或任务状态算法，而是消除静默异常、占位成功、
不完整幂等身份、错误分类掩盖以及可能泄漏敏感信息的 raw exception 输出。

## 2. 已实现改进

- 删除 `mem_unknown`、`unknown_turn` 和虚构 assistant response 等占位路径；生命周期写入
  没有返回 durable identity 时显式失败。
- 自动记忆提升改为结构化状态；失败不会中断主回答，但会进入 `AgentResult.warnings` 和
  安全日志，不再与“未启用/不符合规则”混为一谈。
- Journal 对参数 JSON、父 Run、pending action identity、终态 receipt 和实际 UPDATE
  row count 做严格验证；损坏回执返回 `JOURNAL_RECEIPT_CORRUPT`，绝不改成 `{}` 继续。
- `ToolCallResult` 明确 SUCCESS/FAILED payload 互斥；工具异常保存安全错误类型，原始异常
  文本不进入模型 observation、持久化 receipt 或用户输出。
- Task/ingestion event replay 使用完整来源身份；Task 3 旧工具事件从已有 canonical content
  确定性回填 status metadata，保证升级后仍可严格对账。
- Provider、Runtime、TaskState、Maintenance、CLI 和评估脚本统一通过
  `core.diagnostics` 输出结构化安全诊断；CLI 使用 1 MB、3 个备份的 rotating log。
- 增加 runtime/provider/tool DTO 参数边界，并防止存储异常被 Task Router 误报为“任务不存在”。
- Product Trial observer 遇到已有数据库查询失败时抛出 `TrialObservationError`，不再用零计数
  生成可能的假通过结果。

## 3. 验证结果

- `pytest`：284/284 PASS。
- `coverage run --source=code/src/memory -m pytest`：statement coverage 84%（6581 statements，
  1039 missed；主要未覆盖区为交互式 CLI 分支、可选 embedding backend 与极端恢复分支）。
- 新增 failure-truthfulness 回归：18/18 PASS，覆盖无身份写入、source collision、损坏 JSON、
  伪成功工具 payload、自动 promotion 失败、路由误分类、参数边界和日志脱敏。
- `mypy src --explicit-package-bases --ignore-missing-imports`：73 个源码文件零错误。
- Ruff `F821/F811/F822/E9`：PASS；本次变更文件 `F401/F841`：PASS。
- 六组既有 acceptance（Task 2 Recall、Applicability、Evidence、Task 3D、Runtime、
  Main Agent）：全部 PASS。Task 3D 输出中保留其 B0 诊断对照项的预期 `FAIL` 标记，
  但该套件最终 acceptance 为 PASS，未将诊断项伪装成通过。
- Product Trial：14/14 场景 PASS，随后 50 步确定性 fuzz PASS；runner 会把任何意外的
  `FAILED`/`STEP_LIMIT` 等 Agent 状态自动判为失败。本次修复也实际捕获了试跑替身返回错误
  `memory_id` 所导致、此前被局部断言漏掉的 provider response failure。
- `compileall` 与 import smoke：PASS；`pip wheel . --no-deps`：PASS。当前环境中的
  `python -m build` 入口因同名非可执行包不可用，因此没有把它记录成成功。

上述门禁使用确定性 test doubles 验证流程、事务和错误契约，不把它们伪装成真实付费模型
的语义质量证明。接入付费 Provider 后仍需执行独立 provider smoke 与语义评估。

设计决定见 [ADR-0020](../decisions/ADR-0020-failure-truthfulness-and-journal-integrity.md)。
