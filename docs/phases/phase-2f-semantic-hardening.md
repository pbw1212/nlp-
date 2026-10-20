# Phase 2F — Controlled Ontology & Semantic Interpretation Hardening

> **历史阶段记录**：本文只描述 Phase 2F 的受控实验与当时的 Groq 评估；其中的模型、
> 配额和指标不代表当前 main 的 Provider 或产品质量。

## Starting state

Phase 2E engineering integration通过，但Groq `openai/gpt-oss-120b`真实结果只有 context F1 `0.1026`、false-known `0.7619`、scope-state accuracy `0.0141`、A/B/C/D=`1.0/.4/.4/.4`。`ApplicabilityEngine` oracle正确，两个semantic interpreter均不是production-ready。

## Goal

在固定模型、frozen Phase 2E gold和frozen retrieval/applicability policy下，引入受控ontology与conservative extraction，使automatic semantic decisions达到明确promotion gate；另用独立hard set防止只适配旧fixture。

## Error audit

Phase 2E保存了aggregate metrics与代表性case，但没有保存全部structured predictions。Phase 2F开始时尝试以旧prompt重新capture全量prediction；provider在第5个context前触发每日token配额，capture不完整且未用于正式指标。已验证的Phase 2E错误包括：

- field ontology alias drift：`travel_party -> persons`、`activity -> tasks`、`payment_source -> expense_responsibility`；
- value/field alignment drift：canonical gold value被改写为另一个自由symbol；
- false-known：bare hotel query产生KNOWN `hotel_search`；
- ambiguity loss：AMBIGUOUS `payment_source`降为UNKNOWN且field改名；
- scope output有9/30 case在两次尝试后仍违反domain validation。

因此Phase 2E exact low score既包含ontology mismatch，也包含真实unsupported inference/state/scope错误。由于旧raw predictions没有完整保留，不能诚实给出全量manual-equivalence提升数字；人工equivalence只用于diagnosis，没有改gold或正式metric。

从frozen gold规模与Phase 2E P/R可精确还原context raw field counts：76 gold fields中exact TP=8、FN=68；80 predicted fields中FP=72。False-known为64/84个gold-UNKNOWN或gold-absent key。Scope有49个gold rules；只有1/37个`EXPLICIT_CONSTRAINT` state正确，`EXPLICIT_GENERAL`与`UNSPECIFIED`均为0正确，且9/30 scope case validation失败。Manual audit确认alias drift能解释部分FP/FN，但`hotel_search` false-known与AMBIGUOUS loss属于真实semantic error；由于缺少全部old prediction payload，semantic-equivalence-after-audit只能标记UNVERIFIED，不能生成伪精确分数。

## Implementation

- `ApplicabilityFieldRegistry`：versioned `(dimension, field_id)` registry；45+ personal-agent fields，支持closed/open values与deterministic aliases。
- prompt/schema：field enum封闭；payload携带compact registry；context/scope prompt version升级v2。
- normalization：NFKC/casefold + explicit alias；closed values严格拒绝；trace保留raw values。
- safety：missing != UNKNOWN；AMBIGUOUS至少两个values；query intent不自动成为fact；`EXPLICIT_GENERAL`需要明确universal marker。
- provider efficiency：通用 `reasoning_effort` 与 `max_completion_tokens`配置；semantic structured extraction在调用方未显式配置时使用provider-agnostic `2048` completion默认值。本轮固定同一Groq/model并使用low reasoning。
- evaluation：frozen-v1不改；新增12 context + 12 scope + 6 pair的`context_interpretation_hard_v1`。

## Promotion gate

Frozen-v1 gates：context F1 >= `.75`；false-known <= `.15`；scope-state >= `.70`；validation failure rate <= `.05`；D >= `.70`。Hard-v1要求context F1、scope-state与D均不低于`.60`。未通过则Phase FAIL，不能进入Task 2 finalization。

## Tests / acceptance

119个deterministic test pytest覆盖registry uniqueness、dimension mismatch、closed/open value、aliases、field invention、missing/UNKNOWN/AMBIGUOUS、query intent、general evidence、metadata mention、multilingual、provider config、structured completion默认/显式override、read-only、checkpoint resume与frozen regressions。Task 1/2A/2B/2C/2D/2E oracle/2F oracle runners均PASS。

## Real evaluation status

2026-08-13使用同一Groq `openai/gpt-oss-120b`、low reasoning、frozen-v1与hard-v1完成真实评估。Transport审计确认Chat Completions实际发送`json_schema.strict=true`（boolean），两套schema递归满足所有object字段required且`additionalProperties=false`。原本quota诊断遗留的`640` completion budget会产生完整但缺少required `warnings`的JSON并被Groq返回`json_validate_failed`；仅将budget提高到`2048`后，同一frozen context请求3/3 strict PASS，随后context/scope/end-to-end smoke全部PASS。由此将阻断归因于reasoning-model generation budget与strict completion的交互，而不是通过删除required字段或降级strict绕过。

完整Frozen-v1真实结果：context precision/recall/F1=`.8143/.7500/.7808`，state accuracy=`.6180`，false-known=`.4800`；scope-state=`.4138`，explicit-constraint=`.6486`，explicit-general=`0`，unspecified=`0`，value=`.3276`，operator=`.4138`；A/B/C/D=`1.0/.8/.9/.8`。3/60 frozen interpretation case最终domain validation失败，failure rate=`.05`。Hard-v1 context F1=`.8182`、false-known=`.3333`、scope-state=`.3333`、D=`1.0`。

Diagnostic显示主要瓶颈是field selection而非given-correct-field state：Frozen context field selection=`.75`、false invention=`.1857`、given-field state=`.9649`；scope field selection=`.4898`、false invention=`.2727`、given-field state=`1.0`。Hard scope field selection=`.4286`、false invention=`.4`。因此controlled ontology显著改善Phase 2E exact alignment与E2E，但仍不能安全晋级production。

Promotion gate结果：**FAIL / SEMANTIC QUALITY NOT READY**。通过context F1、validation failure和D；未通过false-known、scope-state及hard-set scope门槛。没有修改gold、threshold、retrieval或`ApplicabilityEngine`，也没有创建Phase 2F正式tag。

Round-2开始前的metric/domain audit见`phase-2f-round2-domain-audit.md`。审计修正了Frozen unsupported-abstention的空denominator显示（`0/0`应为`N/A`），并发现Frozen/Hard gold把部分“文本完全未提及的field”标成应主动输出的`UNSPECIFIED` applicability axis。该语义与conservative field activation目标冲突，因此Round-2 staged interpreter在实现前以**DOMAIN MODEL ISSUE**停止；没有创建holdout、修改prompt或发起额外provider prediction。

### Sparse scope contract and corrected v2

经过架构裁决，`MemoryApplicabilityProfile`正式采用 sparse evidence-backed qualifier semantics：ABSENT不产生rule；`UNSPECIFIED`必须有axis relevance evidence；`EXPLICIT_GENERAL`必须有universal evidence；preference target不自动成为scope axis。v1 gold保持immutable historical，v2全量复核30/30与12/12 scope cases，并提供scope/context/pair migration manifests及每条v2 scope rule的source evidence reference。

Round-1 prediction测试重算v2（无新LLM请求）结果：Frozen context F1=`.8321`、false-known=`.75`、scope-state=`.4528`、scope-axis F1=`.6234`、given-field state=`1.0`、D=`1.0`；Hard context F1=`.8182`、false-known=`.3333`、scope-state=`.4`、scope-axis F1=`.5714`、given-field state=`1.0`、D=`1.0`。v2仍不是promotion pass；结果表明Context false-known和scope axis selection仍需后续决策，但当前没有足够理由在正确target冻结前实现field-first architecture。

### Round-2 staged architecture

根据 sparse-v2 rescore，field selection 与 field-conditioned extraction 已实现为可选的 derived path：`ContextFieldSelector → ContextFactInterpreter` 与 `ScopeFieldSelector → ScopeRuleInterpreter`。公共 interpreter DTO 保持不变，独立 LLM verifier 暂未加入。新的 canonical safety metric 为 `Unsupported-KNOWN Rate`；Round1-on-v2 测试重算为 `21/59 = .3559`，高于 `<=.15` gate。首次 DEV real run 在 selector structured-output 请求处返回 HTTP 400，未打开 holdout；因此 Round-2 semantic promotion 尚未评估。

## Controlled GPT-OSS-20B DEV decision

The same-model `Groq / openai/gpt-oss-20b` comparison on
`semantic_calibration_dev_v1` is the Phase 2F architecture checkpoint. The
one-shot baseline scored Context F1 `.7273`, Unsupported-KNOWN `.3750`,
scope-axis F1 `.6154`, scope-state `.4444`, and D `.2000` with 44 requests and
76592 total tokens. The staged path scored Context F1 `.6774`,
Unsupported-KNOWN `.3214`, scope-axis F1 `.5957`, scope-state `.4242`, and D
`.1000` with 109 requests, 111263 total tokens, and 5/20 scope validation
failures.

Conclusion: Context field-first and Scope field-first are **NOT SUPPORTED** by
this controlled DEV. One-shot remains the default operational path;
`staged=True` is experimental/opt-in only. This is not a claim that the 20B
model or one-shot interpretation is production-optimal, and it does not
permanently reject field-first research. The holdout, Frozen-v2 and Hard-v2
sets remain sealed; no Phase 2F promotion tag is created.

Phase 2F status is therefore split: engineering implementation, controlled
ontology and sparse scope contract are complete; semantic quality remains a
known limitation and is not a production promotion.

## Known limitations / deferred

Ontology不是开放世界semantic resolver；complex conditional DSL、persistent
interpretation cache、Task 3继续deferred。Conflict handling、deterministic
evidence selection、bounded extractive distillation、sufficiency与
`EvidenceBundle`在 Phase 2G 作为 provider-independent downstream pipeline
实现；它们不把 Phase 2F semantic quality提升误称为已解决。

## Architecture changes

ADR-0015定义受控ontology；ADR-0017记录 staged field-first 的 controlled
DEV 结果与 one-shot 默认决策。Task 1 schema、Task 2A/2B/2C/2D semantics
均未改变。Phase 2G 的下游证据输出另由 ADR-0018 约束。

## Git archive

Local checkpoint commits：`57a02fa`、`93261a3`、`9e6859b`、`aefd6a8`。Completion-budget修复与本次真实结果由后续独立commit归档。Phase tag：未创建；`phase-2f-semantic-hardening`仅在真实promotion gate与完整回归通过后创建。
