# ADR-0012：Reposition Applicability After Academic Collision Audit

Status: **Accepted**

## Context

ADR-0007/0010 将 recall 与 applicability 分层，并把 scenario-conditioned applicability 记录为最强 research hypothesis。Phase 2C 随后发现简单 B1 exact filter 与 B2 在 explicit mismatch safety 上相同，B2 可观察差异仅是 UNKNOWN/AMBIGUOUS semantics。

Phase 2R 全文调查 RPEval、BenchPreS、KnowAct、STITCH/CAME-Bench、MemSyco-Bench、STALE、HorizonBench、CIMemories、Severance 及相关 personalization/RecSys work。

## Problem

必须区分：

1. Recall/applicability 分层是否仍是合理系统 architecture；
2. “relevance != applicability” 是否仍能作为宽泛 research novelty；
3. 下一步应该继续扩展自定义 symbolic benchmark，还是先对齐公开 strong baselines。

## Options considered

1. 维持原 novelty positioning；
2. 删除 applicability layer 并退回 retrieval-only；
3. 保留工程边界，撤回宽泛 novelty，把研究问题收窄并优先外部 benchmark/strong baseline comparison；
4. 立即实现 LLM applicability judge。

## Decision

采用选项 3。

- **工程 architecture KEEP**：Recall 与 applicability 继续独立，request-local state不写回 Task 1。
- **原宽泛 novelty DROP**：semantic relevance 与 contextual applicability不同、apply/suppress preference、context-aware prefiltering、Know→Act failure decomposition均有直接先行工作。
- **研究方向 NARROW**：只调查 incomplete-context calibrated memory-use policy、lifecycle-aware end-to-end attribution等尚未充分统一验证的窄问题。
- Phase 2C 定位为 controlled semantic self-consistency/regression evaluation，不是独立 effectiveness 或 novelty evidence。
- 下一研究动作必须先比较 RPEval/STITCH/KnowAct/MemSyco/naive filter 等 strong/closest baselines。

## Why

- RPEval 已对每条 preference 预测 Ignore/Support/Dominate；
- BenchPreS 已 formalize context-aware apply/suppress；
- STITCH 已在 prompt injection 前按 contextual intent 过滤/排序 memory snippets；
- KnowAct 已分解 retrieval/comprehension/application failure；
- MemSyco-Bench 已覆盖 contextual scope、valid memory selection与 downstream use；
- SARE 等 RecSys work 说明 situation-dependent preference 不是 LLM memory 首创；
- Severance、STALE分别覆盖 structured unknowns/clarification 与 unknown-current/state adjudication。

## Rejected alternatives

- 维持原 claim：与直接文献证据冲突。
- 删除 applicability：研究 novelty 不成立不等于工程 safety boundary 无价值。
- 立即上 LLM：在 external dataset、gold protocol与 strong baselines未确定前会混入 provider/parser confound。
- 用新的 enum/schema 名称维持 novelty：工程表示不等于研究贡献。

## Reference systems / research evidence

Detailed evidence: `docs/research/task2-academic-novelty-survey.md`。

Borrowing level: L1/L2；STITCH/PEARL/RF-Mem future baseline可评估为 L3 reimplementation candidate。Phase 2R 无 L4 code reuse。

## Consequences

- ADR-0007/0010 的 production boundaries 继续有效；其早期 novelty interpretation由本 ADR supersede。
- `docs/research/research-gaps.md` 不再称 scenario-conditioned applicability 为 strongest open gap。
- 后续 benchmark不能只从 B2 typed rules自动生成 gold；应加入自然语言、人类标注、external task和 oracle attribution。
- `EvidenceBundle` 仍可作为工程接口，但不能单独作为 research contribution。
- Phase 2R 不修改 Task 1、Task 2A/2B/2C behavior。

## Implementation mapping

Research/documentation only：

- `docs/research/task2-academic-novelty-survey.md`
- `docs/research/research-gaps.md`
- `docs/traceability.md`

Existing implementation remains:

- `code/src/memory/retrieval/`
- `code/scripts/task2_applicability_eval.py`
- `benchmarks/task2/applicability_eval_v1/`

## Test mapping

No new algorithm tests。Existing Task 1/2A/2B/2C regression suite verifies that repositioning did not change behavior。

Introduced in phase: Phase 2R。

Supersedes: ADR-0007/ADR-0010 **only for research novelty positioning**；不 supersede 其 module boundary 与 deterministic semantics。

Superseded by: None。
