# Phase 2E：Natural-Language Context & Memory-Scope Interpretation

> **历史阶段记录**：本文只描述 Phase 2E 的实现与当时的 Groq 评估；当前 Provider、模型
> 和发布状态以 `docs/project-progress.md` 与 `docs/architecture/` 为准。

## Starting state / Goal

Task 1与 Phase 2A-D frozen。缺口是 natural-language request到 `ApplicabilityContext`、以及 current `MemorySnapshot`到 `MemoryApplicabilityProfile` 的 production link。本阶段只补 semantic interpretation与 attribution，不做 final rerank、EvidenceBundle、answer generation或 Task 3。

## Architecture / implementation

- `ContextInterpreter` 与 `MemoryScopeInterpreter` 是独立组件，分别拥有 versioned prompt与 strict parser。
- `SemanticInterpretationProvider` 是 provider-neutral typed protocol；真实 adapter复用项目 `OpenAILLMClient` / `MemoryConfig` / provider errors。
- evidence result保存 source/revision/metadata/provenance reference与短 reason，不复制整段对话。
- `SemanticRetrievalPipeline` 组合 Phase 2D `MemoryRetriever`、两个 interpreter和 frozen `ApplicabilityEngine`，终点是 typed applicability candidates。
- 没有新增 `QueryFrame`、persistent profile/cache、CONFLICTING enum或 conditional DSL。

## Reference implementation decision

Borrow：STITCH structured intent、RPEval per-memory judgment、Graphiti typed/source-grounded extraction、Letta strict JSON schema、KnowAct oracle attribution。Adapt：两个独立 output schema、现有 applicability DTO、evidence identity与A-D interventions。Reject：loose JSON repair、doubtful fact extraction、metadata mention即constraint、reference runtime/code复制。

## Evaluation

`context_interpretation_eval_v1` 将 runtime natural-language与gold分文件保存，包含30条 context、30条 memory scope、10个 end-to-end pair，覆盖中文、英文、混合语言、explicit/unknown/ambiguous/negation/general/unspecified/REQUIRES/EXCLUDES。

默认 `oracle-fixture` 使用人工 gold驱动同一 interpreter orchestration，只验证 schema、metrics、pipeline与A-D attribution，不是 semantic model性能。真实 backend使用 `code/scripts/task2_interpretation_eval.py --provider real`；必须显式 `MEMORY_PROVIDER_MODE=REQUIRED`并配置 provider。结果记录 provider/model/prompt/schema和 field/state/scope/operator/A-D metrics。

## Real semantic pilot

Backend：Groq OpenAI-compatible `https://api.groq.com/openai/v1`，model=`openai/gpt-oss-120b`，prompt=`context_interpretation_v1` / `memory_scope_interpretation_v1`，schema v1。Credential只在 ignored `.env.local`，未进入Git。

- smoke：1 context + 1 scope + 1 end-to-end，HTTP/strict JSON/evidence/pipeline均PASS；最终 applicability为UNKNOWN，显示field alignment问题但链路真实运行。
- context P/R/F1=`0.1000/0.1053/0.1026`；state accuracy=`0.0541`；false-known=`0.7619`。
- scope state/value/operator=`0.0141/0.0000/0.0141`；constraint/general/unspecified accuracy=`0.0270/0/0`；两次生成后仍有9个scope validation failure。
- oracle attribution A/B/C/D=`1.0/0.4/0.4/0.4`；end-to-end applicability accuracy=`0.4`。
- usage：77 HTTP attempts，successful-response usage合计 input=`49,160`、output=`51,594` tokens。API未返回cost；按运行时公开单价只能另行估算，不能作为provider账单。

Representative context errors：`c01`把gold canonical fields拆成`persons/location/time/tasks/accommodation_constraint`，造成field/value mismatch；`c04`在gold unknown fields之外生成KNOWN `hotel_search`；`c10`把gold AMBIGUOUS payment_source压成UNKNOWN `expense_responsibility`。主要风险是field ontology drift、false-known与ambiguity loss，不是ApplicabilityEngine本身（A=1.0）。

## Tests / acceptance

deterministic test tests覆盖 evidence refs、UNKNOWN保留、AMBIGUOUS、多语言、negation、scope三态、constraints、malformed output/enum/value shape/retry exhaustion、no silent fallback、Hybrid recall集成、score不变、SQLite read-only和default no-network。

## Known limitations / deferred

- 当前 prompt/schema对自由field/value vocabulary约束不足，real semantic quality不能用于production决策。
- 没有 semantic alias、ontology、full conditional reasoning、persistent cache或 model calibration。
- recent-context contradiction当前映射 AMBIGUOUS，尚无 time-aware evidence resolver。
- 每个candidate在线解释scope带来显著延迟/token成本；需要derived revision+interpreter identity cache或batch设计。
- 没有 conflict resolution、final rerank、distillation、EvidenceBundle、Task 3/Main Agent。

## Architecture changes

ADR-0014。Task 1、Recall与ApplicabilityEngine semantics未改变。

## Git archive

- Implementation commit: `cbce2f0 feat(retrieval): add semantic context and scope interpretation`。
- Real-provider hardening commit: `bcb55ae feat(llm): harden real semantic provider integration`。
- deterministic test status: 106 pytest + Task 1/2A/2B/2C/2D/2E oracle acceptance、compileall与wheel import通过。
- Real backend status: Groq / `openai/gpt-oss-120b` smoke与完整30+30 evaluation已执行；低分如实记录。
- Phase tag: `phase-2e-semantic-interpretation`（完整回归后创建）。
