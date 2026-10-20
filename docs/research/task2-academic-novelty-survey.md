# Task 2 Academic Novelty Collision Audit

Status: **Phase 2R complete; research positioning narrowed, production behavior unchanged**

> 本文记录 2026-08 的历史源码核查。`reference_repos/...` 是当时的本地检视路径；第三方源码不随 MAPAS 仓库分发。上游仓库与可复核 revision 见 [`reference-systems.md`](reference-systems.md)。

## 1. Problem definition and evidence boundary

本调查审计以下宽泛命题是否仍可作为研究 novelty：

> Semantic/lexical recall relevance does not imply that a recalled personal memory is applicable to the current context.

结论必须分成两层：

- **工程结论 KEEP**：Recall 与 applicability 分层仍是可解释、可测试且安全的系统边界。
- **宽泛 novelty claim DROP**：偏好是否应被使用、context-dependent apply/suppress、context-mismatched retrieval、retrieval-to-utilization failure decomposition 均已有直接工作。
- **研究方向 NARROW**：只保留尚需外部数据验证的窄问题；当前 schema、枚举和模块边界不自动构成论文贡献。

证据优先级：原始论文/正式 proceedings > 官方代码仓库 > 项目页。调查截至 2026-08-13。`YES/PARTIAL/NO/UNCLEAR` 只描述论文公开任务或实现，不推断作者未公开能力。

## 2. Search methodology

检索范围覆盖 2023--2026 personal memory、personalization、context-aware retrieval、preference selectivity、memory utilization、stale memory、uncertainty/clarification，以及更早的 context-aware/situation-aware recommender literature。重点全文核查：

- formal problem definition；
- candidate/memory-level decision 是否发生在 prompt injection 前；
- current context、scope、time、provenance、unknown/ambiguity 如何表示；
- gold label 与 response-level metric；
- retrieval failure、utilization failure和 state-update failure是否分开。

本阶段没有运行论文 benchmark、没有修改 Task 2 production behavior。仅对 RPEval 官方仓库做 targeted source inspection。

## 3. Closest-work deep dives

### 3.1 RPEval / RP-Reasoner

Source: Feng et al., *How Does Personalized Memory Shape LLM Behavior?* (2026), arXiv:2601.16621，§2--§4、Appendix；official repository commit `0f8bce80a03e6b86426485264b14ae7de49cc8f9`。

RPEval 将 preference memory 对 query intent 的作用标为 `Ignore / Support / Dominate`。Single-preference 与 multi-preference evaluation 都要求逐 preference 决定利用强度。RP-Reasoner以 pragmatic inference 产生并聚合策略 ranking。

源码证据：

- `reference_repos/rpeval/discrimation_task/eval_single.py:rpa_new`：对 MLE、CPE-recall、CPE-intent 发出三个模型请求并聚合 ranking；
- `reference_repos/rpeval/discrimation_task/eval_single.py:evaluate_single_item`：将 A/B/C 映射到 Ignore/Support/Dominate；
- `reference_repos/rpeval/discrimation_task/eval_multi.py:aggregate_policy_ranking_multi`：逐 preference/dimension 聚合 policy；
- `reference_repos/rpeval/discrimation_task/eval_multi.py:evaluate_single_item`：比较多 preference policy string。

`Ignore/Support/Dominate` 与本项目 `MATCH/MISMATCH/UNKNOWN/AMBIGUOUS` **不是同一轴**：前者是利用强度，后者是 scope/context epistemic assessment。一个 MATCH memory 仍可 Support 或 Dominate；MISMATCH 通常应 Ignore。但两者针对的上位问题相同：不要因 memory 与 query 相关就无条件个性化。因此宽泛 claim 发生直接碰撞；显式 UNKNOWN/AMBIGUOUS 仍未在该任务中出现。

可借鉴：L2 evaluation pattern（per-memory utilization decision、single/multi preference protocol）；不复制其 prompt/API implementation，也不采用解析失败回退 `ABC` 的错误语义。

### 3.2 BenchPreS

Source: Yoon et al., *BenchPreS* (2026), arXiv:2603.16557，§3--§5、Limitations。

BenchPreS 明确定义 context-aware preference selectivity：在 recipient--task context 中 apply appropriate preferences、suppress inappropriate preferences。它对 attribute--task pair 使用人工 binary gold `g(t,a)`，并以 response-level `Misapplication Rate` 与 `Appropriate Application Rate` 评价模型。数据集中主观或 ambiguous cases 被过滤。

直接碰撞：`context -> apply/suppress preference` 已被正式 benchmark。差异是 BenchPreS 面向 communication recipient/task、将全部 persistent profile 放入 prompt、主要评价生成结果；它不评估外部 retrieval pipeline，也没有 UNKNOWN/AMBIGUOUS 类别。这些差异只能支持一个更窄的系统/evaluation question，不能恢复宽泛 novelty。

可借鉴：L2 benchmark pattern（同时测 inappropriate use 和 appropriate use，人工 gold）；不复制其二元 clear-case 假设。

### 3.3 KnowAct

Source: Feng, Ma, and Chersoni, *Know It, Act on It* (2026), arXiv:2607.29433，§3--§5。

KnowAct 在同一 preference 上配对 `Know` 与 `Act` 测试，并定义 Utilization Rate。对 Know-pass/Act-fail case，oracle experiments 分解为 retrieval、comprehension、application failure；论文报告的 722 个失败中三类分别约 30.7%、53.0%、16.2%。

因此“memory 已被知道/召回但没有正确作用于行为”以及 retrieval 与 downstream utilization 分离，都是直接已知问题。本项目的 Recall→Applicability 是更细的工程分层，不是首次 failure decomposition。KnowAct 多数 case 假设 preference 应该应用，未重点覆盖不适用 memory 的 suppress/unknown。

可借鉴：L2 evaluation pattern（paired Know/Act、oracle-stage attribution）；未来 end-to-end benchmark 应加入，而不是只测 candidate classifier self-consistency。

### 3.4 STITCH / CAME-Bench

Source: Yang et al., *Grounding Agent Memory in Contextual Intent* (Findings of ACL 2026), §2--§5。

STITCH 是本调查中对 pre-injection candidate gating 最直接的碰撞。它为 trajectory step 建立 `(thematic scope, event type, key entity types)` contextual-intent tuple，在 query time 生成 filter configuration，并用 Label Density Ranking 按 intent compatibility 过滤/优先级排序 memory snippets。CAME-Bench 专门制造 recurring entities、interleaved goals 和 context-mismatched evidence。

这已覆盖：structured current intent、candidate-level indexing、pre-prompt filtering、semantically similar but context-incompatible history suppression。它主要面向 goal-oriented trajectory memory，而不是 persistent user preference scope；没有显式 UNKNOWN/AMBIGUOUS/EXPLICIT_GENERAL。结论仍是 C2/C5 strong collision，而非无碰撞。

可借鉴：L2/L3 candidate——contextual-intent retrieval 是 Phase 2 后续 strong baseline，且必须与本项目 deterministic gate 公平比较。

### 3.5 MemSyco-Bench

Source: Xiang et al., *MemSyco-Bench* (2026), arXiv:2607.01071v2，§3--§4。

其五类任务包括 `Contextual Scope Control`、`Valid Memory Selection`、`Memory-Evidence Conflict`、`Objective Fact Judgment` 与 `Personalized Memory Use`。它直接测试 preference 是否被应用在原适用 scope 之外、旧/新 memory 是否被正确选择，以及 memory 是否错误压过客观证据。

这使“memory scope respect + current validity + downstream use”成为直接碰撞。其主要 evaluator 是 response-level rubric，而不是公开的 typed pre-injection gate；未找到 UNKNOWN/AMBIGUOUS 类别。

可借鉴：L2 benchmark/task taxonomy；它比当前 Phase 2C 人工 equality fixture 更适合 future external evaluation。

### 3.6 STALE and HorizonBench

STALE (Chao et al., 2026, §3--§5) 用 implicit conflict 测 `State Resolution / Premise Resistance / Implicit Policy Adaptation`。CUPMem在 write-time进行 structured state consolidation，并保留 archived stale states、supporting evidence和 `unknown-current` marker。它直接削弱“lifecycle-aware applicability”作为宽泛新意的可能性。

HorizonBench (Li et al., 2026, §3--§6) 用 structured mental-state graph 生成六个月 preference evolution，并为每次变化保留 ground-truth provenance；错误分析显示模型经常回到最初 preference。它直接覆盖 temporal evolution、provenance 与 updated current state 的联合评价。

可借鉴：STALE 的三阶段 probe 与 HorizonBench 的 evolution provenance 属于 L2 evaluation pattern；本项目 Task 1 仍可作为不同工程 baseline，但不能宣称 revision/provenance/currentness 的组合本身是新问题。

### 3.7 CIMemories and the Severance Problem

CIMemories (Mireshghallah et al., 2025, §3--§5) 以 recipient/task context 将 profile attributes 标成 necessary 或 inappropriate，同时报告 disclosure violation 与 task completeness。它证明 context-dependent information gating 还涉及 privacy/utility，而不只是 recommendation relevance。

The Severance Problem (Litvak and Leqi, 2026, §2--§4) 显式建立 structured unknown person-context dimensions；缺信息时促使模型校准回答并提出 clarification。它与 Candidate A 的 `UNKNOWN != MATCH`、incomplete context、clarification 有强重叠，不过它不是逐 memory candidate applicability gate。

可借鉴：CIMemories 的 privacy/utility 双指标和 Severance 的 clarification/calibration protocol，均为 L2；`UNKNOWN` 状态本身不再是可主张 novelty。

## 4. Other relevant work

| Work | Observed contribution | Collision / reuse interpretation |
|---|---|---|
| PersonaMem (2025, §3--§5) | Dynamic profile、latest preference、full evolution、reason、new-scenario generalization；长历史与 external memory baselines | 时间/current situation 与 scenario generalization strong overlap；L2 benchmark candidate |
| PrefEval (2025, §2.5, §3.6) | 3,000 preference-query pairs；explicit/implicit、long-context、dynamic preference following | 主要假定 preference 应应用；适合作为 following baseline，不能测 suppress |
| PEARL (2023/ACL 2024, §3--§5) | generation-calibrated personalized document retriever，按 downstream generation benefit 学习选择 | L2/L3 future learned recall/ranking baseline；不是 explicit scope uncertainty |
| LongMemEval (2024) | extraction、multi-session、temporal、knowledge update与 abstention 等 long-memory ability | L2 general memory regression；scope applicability 不是主任务 |
| PersonaLens (Findings ACL 2025, §2--§4) | profile/history/situational context 下的 111 个 task-oriented personalization tasks | location/device/time current context 与 response-level personalization overlap |
| SARE (2024, §2--§5) | time/location/weather/activity/emotion 等 situation 作为 recommendation precondition，学习 `p(item|user,situation)` | 证明 multi-dimensional context-dependent preference 是 RecSys 已知问题；L1/L2 pattern |
| RF-Mem (2026, §2--§3) | familiarity mean/entropy 选择 direct recall 或 iterative recollection | L3 strong retrieval baseline；其 uncertainty 是 retrieval familiarity，不是 scope UNKNOWN |
| FluxMem (2026) | 根据 task/context 自适应 memory structure | L1/L2 adjacent system pattern；不是 candidate applicability |
| MemCon (2026, §3--§4) | contextual-bandit memory control：retrieve/inject/re-retrieve/consolidate/forget/noop | L2 future orchestration/control baseline；不是当前 symbolic scope evaluator |
| APOLLO (Findings ACL 2026, §3--§5) | preference-grounded tool selection/arguments，explicit/implicit与 proactive/reactive queries | downstream action grounding直接相关；通常假定 relevant preference 应应用 |
| SAMD (2026, §3--§5) | application-scene-aware write-time memory discrimination | L2 write-policy baseline；不要与 read-time applicability 混同 |

## 5. Novelty collision matrix

Notation: `Y=YES`, `P=PARTIAL`, `N=NO`, `U=UNCLEAR`。每格括号给出主要论文 section；`A` 表示 abstract/official record，`R` 表示 official repository source。`PM`=persistent memory，`Ctx`=current context，`App`=explicit applicability/selectivity，`MDec`=memory-level decision，`Resp`=response-level evaluation，`Dyn`=dynamic evolution，`Unk/Amb/Abs`=unknown/ambiguity/abstention。

### 5.1 Task and decision characteristics

| Work | PM | Retrieval | Preference | Ctx | App | MDec | Resp | Apply/suppress | Multi-memory | Dyn | Unk | Amb | Abs | Evidence |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| RPEval | P§2 | N§2 | Y§2 | Y§2 | Y§3 | Y§3 | Y§3 | Y§3 | Y§3.2 | N§3 | N§3 | N§3 | N§3 | §2--4; R |
| BenchPreS | P§3 | N§3 | Y§3 | Y§3 | Y§3 | P§3 | Y§3.3 | Y§3 | Y§3 | N§3 | N§3 | N§3 | N§3 | §3--5 |
| KnowAct | Y§3 | Y§4 | Y§3 | Y§3 | N§3 | N§3 | Y§3 | N§3 | P§4 | N§3 | N§3 | N§3 | N§3 | §3--5 |
| PersonaMem | Y§3 | Y§4.4 | Y§3 | Y§3 | P§3 | N§3 | Y§4 | N§3 | Y§3 | Y§3 | N§3 | N§3 | N§3 | §3--5 |
| PrefEval | P§2 | P§2.5 | Y§2 | Y§2 | N§2 | N§2 | Y§2.5 | N§2 | P§2 | Y§3.6 | N§2 | N§2 | N§2 | §2.5,3.6 |
| PEARL | P§3 | Y§3 | P§3 | Y§3 | P§4 | Y§4 | Y§5 | P§5.3 | Y§3 | N§3 | N§3 | N§3 | N§3 | §3--5 |
| LongMemEval | Y§3 | Y§4 | P§3 | Y§3 | N§3 | N§3 | Y§3 | N§3 | Y§3 | Y§3 | P§3 | P§3 | P§3 | §3--5 |
| PersonaLens | P§2 | N§2 | Y§2 | Y§2 | P§2 | N§2 | Y§3 | N§2 | Y§2 | P§2 | N§2 | N§2 | N§2 | §2--4 |
| SARE | N§3 | N§3 | Y§3 | Y§3 | Y§3 | P§4 | Y§5 | P§4 | Y§4 | N§3 | N§3 | N§3 | N§3 | §2--5 |
| RF-Mem | Y§2 | Y§2 | Y§3 | Y§2 | N§2 | Y§2 | Y§3 | N§2 | Y§2 | N§2 | P§2.1 | N§2 | P§2.1 | §2--3 |
| FluxMem | Y§2 | Y§3 | P§2 | Y§2 | N§2 | Y§3 | Y§4 | N§2 | Y§3 | P§3 | N§2 | N§2 | N§2 | §2--4 |
| MemCon | Y§3 | Y§3 | P§4 | Y§3 | P§3 | Y§3 | Y§4 | P§3 | Y§3 | P§3 | N§3 | N§3 | P§3 | §3--4 |
| APOLLO | P§3 | P§4 | Y§3 | Y§3 | N§3 | N§3 | Y§3 | N§3 | Y§3 | N§3 | N§3 | N§3 | N§3 | §3--5 |
| CIMemories | Y§3 | N§3 | P§3 | Y§3 | Y§3 | P§3 | Y§3 | Y§3 | Y§4 | N§3 | N§3 | N§3 | N§3 | §3--5 |
| Severance | P§2 | N§2 | P§2 | Y§2 | P§2 | N§2 | Y§3 | P§3 | Y§2 | P§2 | Y§2 | P§2 | Y§3 | §2--4 |
| STALE | Y§3 | Y§4 | P§3 | Y§3 | P§3 | Y§5 | Y§3 | Y§3 | Y§3 | Y§3 | Y§5 | P§5 | P§5 | §3--5 |
| HorizonBench | P§3 | P§6 | Y§3 | Y§3 | P§3 | N§3 | Y§5 | P§5 | Y§3 | Y§3 | N§3 | N§3 | N§3 | §3--6 |
| MemSyco-Bench | Y§3 | Y§4 | Y§3 | Y§3 | Y§3.2 | P§3 | Y§3.4 | Y§3.2 | Y§3 | Y§3.2 | N§3 | N§3 | N§3 | §3--4 |
| STITCH | Y§2 | Y§2.3 | N§2 | Y§2 | Y§2.3 | Y§2.3 | Y§3 | Y§2.3 | Y§2 | Y§2 | N§2 | P§2 | N§2 | §2--5 |

### 5.2 Scope, evidence and pipeline characteristics

| Work | General vs unspecified | Person | Location | Time | Task | Scenario | Constraint | Provenance | Current-safe | Uncertainty output | Pre-prompt gate | Downstream answer eval | Evidence |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| RPEval | N§3 | N§3 | N§3 | N§3 | P§2 | P§2 | P§2 | N§3 | N§3 | N§3 | Y§4 | Y§3 | §2--4; R |
| BenchPreS | N§3 | Y§3 | N§3 | N§3 | Y§3 | Y§3 | P§3 | N§3 | N§3 | N§3 | N§3 | Y§3.3 | §3--5 |
| KnowAct | N§3 | N§3 | N§3 | N§3 | P§3 | P§3 | N§3 | P§4 | N§3 | N§3 | N§3 | Y§3 | §3--5 |
| PersonaMem | N§3 | P§3 | P§3 | Y§3 | Y§3 | Y§3 | P§3 | P§3 | P§3 | N§3 | N§3 | Y§4 | §3--5 |
| PrefEval | N§2 | N§2 | N§2 | P§3.6 | Y§2 | P§2 | P§2 | N§2 | P§3.6 | N§2 | N§2 | Y§2.5 | §2.5,3.6 |
| PEARL | N§3 | N§3 | N§3 | N§3 | Y§3 | P§3 | N§3 | P§3 | N§3 | N§3 | Y§4 | Y§5 | §3--5 |
| LongMemEval | N§3 | P§3 | P§3 | Y§3 | Y§3 | Y§3 | P§3 | Y§3 | P§3 | P§3 | N§3 | Y§3 | §3--5 |
| PersonaLens | N§2 | Y§2 | Y§2 | Y§2 | Y§2 | Y§2 | P§2 | P§2 | N§2 | N§2 | N§2 | Y§3 | §2--4 |
| SARE | N§3 | Y§4 | Y§3 | Y§3 | P§3 | Y§3 | P§3 | N§3 | N§3 | N§3 | Y§4 | Y§5 | §2--5 |
| RF-Mem | N§2 | N§2 | N§2 | N§2 | P§2 | P§2 | N§2 | P§2 | N§2 | P§2.1 | Y§2 | Y§3 | §2--3 |
| FluxMem | N§2 | N§2 | N§2 | P§3 | Y§2 | Y§2 | P§2 | P§3 | P§3 | N§2 | Y§3 | Y§4 | §2--4 |
| MemCon | N§3 | N§3 | N§3 | P§3 | Y§3 | Y§3 | P§3 | P§3 | P§3 | N§3 | Y§3 | Y§4 | §3--4 |
| APOLLO | N§3 | P§3 | P§3 | N§3 | Y§3 | Y§3 | Y§3 | P§3 | N§3 | N§3 | N§3 | Y§3 | §3--5 |
| CIMemories | N§3 | Y§3 | N§3 | N§3 | Y§3 | Y§3 | Y§3 | P§4 | N§3 | N§3 | N§3 | Y§3 | §3--5 |
| Severance | N§2 | Y§2 | P§2 | Y§2 | P§2 | Y§2 | Y§2 | N§2 | N§2 | Y§3 | N§2 | Y§3 | §2--4 |
| STALE | N§3 | P§3 | P§3 | Y§3 | P§3 | Y§3 | Y§3 | Y§5 | Y§5 | Y§5 | Y§5 | Y§3 | §3--5 |
| HorizonBench | N§3 | Y§3 | P§3 | Y§3 | P§5 | Y§3 | P§3 | Y§3 | P§5 | N§3 | N§3 | Y§5 | §3--6 |
| MemSyco-Bench | N§3 | P§3 | P§3 | Y§3 | Y§3 | Y§3 | Y§3 | Y§3 | Y§3 | N§3 | N§3 | Y§3.4 | §3--4 |
| STITCH | N§2 | P§2 | P§2 | Y§2 | Y§2 | Y§2 | P§2 | P§2 | P§2 | N§2 | Y§2.3 | Y§3 | §2--5 |

Interpretation：`EXPLICIT_GENERAL vs UNSPECIFIED` 的精确 schema 未在调查对象中找到直接同构表示，但 conditional/situation-dependent scope 本身早已存在。缺少同名 enum 不是 novelty 证据。

## 6. Claim-by-claim verdict

| Claim | Verdict | Evidence-based rationale |
|---|---|---|
| C1 Semantic relevance != contextual applicability | **DIRECT COLLISION** | RPEval、BenchPreS、MemSyco-Bench、STITCH均直接描述 irrelevant/context-mismatched memory 的错误作用；SARE更早确立 situation-dependent preference |
| C2 recalled memory 在 prompt injection 前做独立 candidate-level gate | **STRONG OVERLAP** | RPEval逐 memory utilization policy；STITCH query-time intent filtering最直接；PEARL也做 generation-calibrated preselection |
| C3 EXPLICIT_CONSTRAINT / EXPLICIT_GENERAL / UNSPECIFIED | **POSSIBLE GAP** | 未找到完全同构三分法；但 preference scope、conditional preference和missing context均已知，需证明该三分法带来独立 empirical benefit |
| C4 UNKNOWN != MATCH/REJECT + AMBIGUOUS preservation | **STRONG OVERLAP / POSSIBLE NARROW GAP** | Severance直接表示 structured unknowns并触发 clarification；STALE有 unknown-current；candidate-level memory-scope ambiguity 的 calibrated policy仍未充分验证 |
| C5 person/location/time/task/scenario/constraint 多维 applicability | **STRONG OVERLAP** | SARE、PersonaLens、CIMemories、STITCH覆盖多维 situation/recipient/task/intent；组合字段不是新 problem formulation |
| C6 applicability + temporal revision/provenance/current-safe memory | **STRONG OVERLAP / POSSIBLE SYSTEM GAP** | STALE、HorizonBench、PersonaMem、MemSyco分别覆盖 current validity、provenance、evolution和scope；统一模块化 attribution 尚可作为系统 benchmark 问题，不是已证实 algorithm novelty |
| C7 retrieval contamination 与 downstream utilization failure 分离 | **DIRECT COLLISION** | KnowAct 的 retrieval/comprehension/application oracle decomposition最直接；RPEval也分 discriminative policy 与 generation error |
| C8 applicability -> EvidenceBundle 而非直接 answer | **ADJACENT / ENGINEERING ONLY** | 可解释 DTO 是合理接口，但没有独立 objective/algorithm/evaluation 就不构成 research claim |

## 7. What is definitely not novel

- “Top-K relevance 不等于应使用该 memory”。
- 根据 current recipient/task/situation apply 或 suppress preference。
- 多维 context-dependent preference 的一般概念。
- 检索成功与 downstream utilization 成功的区分。
- preference evolution、stale memory、current valid state、provenance 中任一单独概念。
- `ApplicabilityContext`、`DimensionAssessment`、`EvidenceBundle` 等 schema 名称。
- 在人工 profile/context equality rules 上实现 deterministic hard mismatch filter。

## 8. Phase 2C circularity and weakness audit

Phase 2C 的 gold/runtime **文件边界是正确的**，validator 也阻止 labels 直接进入 runtime input；但其证据性质必须重新命名为：

> controlled semantic self-consistency and regression evaluation.

原因：

1. `profiles.json`、`contexts.json`、`labels.json` 与 B2 使用同一组人工 dimension/value/state vocabulary；
2. 多数 gold label 是 explicit constraint 与 known context 的集合相等/冲突的直接结果；
3. B1 exact hard filter 已覆盖全部 supported explicit mismatch，所以 B1-permissive/B1-strict/B2 safety 相同；
4. B2 的 observable advantage（保留 UNKNOWN/AMBIGUOUS）由 fixture 直接按 `UNSPECIFIED/UNKNOWN/AMBIGUOUS` 构造，尚未证明来源于自然语言或真实 agent 行为；
5. candidate universe 与 expected recall targets 也由人工选择，不能代表开放世界 distribution。

因此 B1==B2 的主要解释是：**dataset too easy + B1 sufficient for supported subset + benchmark follows B2 rules**。不能据此判定 uncertainty 无价值，也不能据此宣称 B2 优于 strong baselines。

Phase 2C 仍有工程价值：current-safe regression、score/status separation、K sensitivity、high-rank hard negatives、metric formulas和 reproducible runner 均应保留。

## 9. Remaining candidate research questions

最多保留三个，均标为待证实，而不是 novelty claim。

### Candidate 1 — Calibrated memory-use control under incomplete context

- **Problem**：recalled candidate 的 scope 或 current user context 不完整时，在 `use / ignore / retain / clarify` 之间作 calibrated decision。
- **Why existing work may not fully solve it**：Severance处理 person-context unknown与 clarification，RPEval处理 utilization strength，BenchPreS处理 binary selectivity；尚未见把三者与 persistent candidate-level evidence统一并评测 risk/utility/cost。
- **Closest papers**：Severance、RPEval、BenchPreS、CIMemories、MemSyco-Bench。
- **Exact possible gap**：不是 UNKNOWN state，而是 candidate-level calibration与 clarification policy 的 end-to-end trade-off。
- **Required method**：natural-language context/profile interpreter + calibrated policy；必须允许 abstain/clarify，不能只做 equality rules。
- **Required benchmark**：人工标注 missing/ambiguous information、decision-flipping clarification、privacy/utility和 answer outcome。
- **Main risk**：可能退化为通用 clarification/selective prediction；需要证明 memory-specific failure。
- **Implementation cost**：高。
- **Publication potential**：中，取决于独立 dataset 与 strong baseline gains。

### Candidate 2 — Lifecycle-aware end-to-end failure attribution

- **Problem**：把 stale/current-state、candidate recall、scope applicability、comprehension、action failure分开归因。
- **Why existing work may not fully solve it**：KnowAct分 retrieval/comprehension/application；STALE/HorizonBench测 evolution；MemSyco测 scope/current valid use，但尚未见统一、可替换 pipeline attribution protocol。
- **Closest papers**：KnowAct、STALE、HorizonBench、PersonaMem、MemSyco-Bench。
- **Exact possible gap**：统一评估接口与 counterfactual oracle stages，而非 revision schema 本身。
- **Required method**：oracle current-memory、oracle retrieval、oracle applicability、oracle utilization的 controlled interventions。
- **Required benchmark**：同一 case 同时包含 source provenance、revision history、scope、current context和 downstream task gold。
- **Main risk**：更像 benchmark/system paper，且 dataset construction 成本高。
- **Implementation cost**：高。
- **Publication potential**：中高，若 error attribution 揭示强而稳定的新瓶颈。

### Candidate 3 — Strong-baseline memory control for a complete personal agent

- **Problem**：在质量、token/latency/privacy budget 下组合 recall、memory gate、generation-calibrated ranking与 downstream action。
- **Why existing work may not fully solve it**：PEARL、RF-Mem、MemCon、STITCH分别优化局部机制；完整 personal-agent lifecycle + task state + action evaluation仍需系统比较。
- **Closest papers**：PEARL、RF-Mem、MemCon、STITCH、APOLLO。
- **Exact possible gap**：不是新组件，而是可替换 strong baselines、统一 failure contracts和 end-to-end measured trade-off。
- **Required method**：先复现/适配 strong baselines，再考虑新 controller；保持 deterministic test baseline。
- **Required benchmark**：memory construction、retrieval、selection、answer/tool action、latency/cost/privacy 的共同 protocol。
- **Main risk**：工程量最大，若无清晰 empirical insight 可能只有系统整合价值。
- **Implementation cost**：很高。
- **Publication potential**：取决于 baseline completeness 和端到端提升；对项目长期目标最重要。

## 10. Strategic recommendation

**Original broad novelty hypothesis: DROP.**

**Project engineering architecture: KEEP.** Recall/applicability separation仍是可解释的 baseline 与 ablation boundary。

**Research positioning: NARROW.** 优先把 Phase 2C 视为测试基础设施，下一步不要继续扩充同构 symbolic fixture；应先选 external benchmark，复现 B1/RPEval/STITCH/KnowAct-style baselines，验证 Candidate 1 或 2 是否仍有独立 empirical gap。

这不妨碍完整 Personal Agent 的长期目标。相反，调查把“自行发明 applicability schema”转成“用成熟任务和 strong baselines检验系统真实增益”。

## 11. Required next experiments

1. **External-task feasibility pass**：将 RPEval、BenchPreS、MemSyco-Bench、STALE/STITCH 数据许可、输入形式和 evaluator 成本映射到本项目 pipeline；不先改算法。
2. **Oracle attribution experiment design**：定义 current-memory / recall / applicability / utilization 四级 oracle，明确每级输入输出与 failure label。
3. **Natural-language and human-label pilot**：小规模独立 annotator 数据，禁止从 B2 rules自动生成 gold；测 parser error 与 assessor error。
4. **Strong baseline plan**：至少包含 naive metadata filter、RPEval-style policy、STITCH intent filtering、generation-calibrated retriever或等价公开 baseline。
5. **Decision gate**：只有 external evaluation显示稳定 unmet gap 后，才决定继续 Candidate 1/2 的新方法；否则把 applicability保留为工程机制并转向完整系统性能。

## 12. Reuse candidates

| Source | Candidate | Level | Deliberately not copied |
|---|---|---|---|
| RPEval | per-memory utilization policy + multi-preference protocol | L2 | prompt-specific code、API fallback |
| BenchPreS | misapplication + appropriate application dual metrics | L2 | binary-only clear-case assumption |
| KnowAct | paired Know/Act + oracle failure attribution | L2 | system-specific memory wrappers |
| STITCH | contextual-intent indexing/filtering baseline | L2/L3 | production code未复用；需独立适配 |
| MemSyco-Bench | scope/current/evidence task taxonomy | L2 | response judge不直接当本项目 gate |
| Severance | structured ignorance + clarification evaluation | L1/L2 | schema categories不直接复制 |
| PEARL | generation-calibrated retrieval | L2/L3 | 训练 pipeline未引入 |
| RF-Mem | uncertainty-routed retrieval strong baseline | L3 candidate | 不把 familiarity uncertainty当 applicability uncertainty |
| MemCon | contextual memory-control action space | L1/L2 | RL/bandit当前 deferred |

No L4 direct code reuse is proposed in Phase 2R.

## 13. Source register

| Work | Primary source | Sections inspected |
|---|---|---|
| RPEval | [arXiv:2601.16621](https://arxiv.org/abs/2601.16621); official GitHub `XueyangFeng/RPEval` @ `0f8bce8` | §2--4, appendices; source symbols listed above |
| BenchPreS | [arXiv:2603.16557](https://arxiv.org/abs/2603.16557) | §3--5, limitations |
| KnowAct | [arXiv:2607.29433](https://arxiv.org/abs/2607.29433) | §3--5 |
| PersonaMem | [arXiv:2504.14225](https://arxiv.org/abs/2504.14225) | §3--5 |
| PrefEval | [arXiv:2502.09597](https://arxiv.org/abs/2502.09597) | §2.5, §3.6, appendices |
| PEARL | [arXiv:2311.09180](https://arxiv.org/abs/2311.09180) / ACL 2024 | §3--5 |
| LongMemEval | [arXiv:2410.10813](https://arxiv.org/abs/2410.10813) | benchmark/evaluation/memory-system sections |
| PersonaLens | [ACL Findings 2025](https://aclanthology.org/2025.findings-acl.927/), DOI 10.18653/v1/2025.findings-acl.927 | §2--4 |
| SARE | [arXiv:2403.18317](https://arxiv.org/abs/2403.18317) | §2--5 |
| RF-Mem | [arXiv:2603.09250](https://arxiv.org/abs/2603.09250) | §2--3 |
| FluxMem | [arXiv:2602.14038](https://arxiv.org/abs/2602.14038) | method/evaluation sections |
| MemCon | [arXiv:2607.13591](https://arxiv.org/abs/2607.13591) | §3--4 |
| APOLLO | [ACL Findings 2026](https://aclanthology.org/2026.findings-acl.1676/), DOI 10.18653/v1/2026.findings-acl.1676 | §3--5 |
| CIMemories | [arXiv:2511.14937](https://arxiv.org/abs/2511.14937) / ICLR 2026 OpenReview | §3--5 |
| Severance | [arXiv:2607.14250](https://arxiv.org/abs/2607.14250) | §2--4 |
| STALE | [arXiv:2605.06527](https://arxiv.org/abs/2605.06527) | §3--5 |
| HorizonBench | [arXiv:2604.17283](https://arxiv.org/abs/2604.17283) | §3--6 |
| MemSyco-Bench | [arXiv:2607.01071v2](https://arxiv.org/abs/2607.01071); official GitHub `XMUDeepLIT/MemSyco-Bench` metadata | §3--4, rubric appendix |
| STITCH/CAME-Bench | [Findings ACL 2026](https://aclanthology.org/2026.findings-acl.584/), DOI 10.18653/v1/2026.findings-acl.584 | §2--5, appendices |
| SAMD | [arXiv:2602.11607](https://arxiv.org/abs/2602.11607) / Knowledge-Based Systems 338 (2026), 115496 | method/evaluation sections |

## 14. Current boundary after Phase 2R

Implemented remains exactly Phase 2A/2B/2C deterministic code. No production algorithm, public DTO, database schema, root `README.md` or benchmark fixture was changed in Phase 2R. This document changes only research interpretation and the set of baselines that must be compared before further novelty claims.

