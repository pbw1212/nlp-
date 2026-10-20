# Task 2：Context-aware Retrieval

当前状态：**Phase 2A lexical、Phase 2B structured applicability、Phase 2C controlled evaluation、Phase 2D pluggable lexical/dense/hybrid recall、Phase 2E semantic interpretation foundation 与 Phase 2G bounded evidence pipeline 已实现；Phase 2F controlled DEV 不支持当前 staged field-first，one-shot 是默认语义基线；Phase 2R 已撤回宽泛 novelty claim**。

候选 pipeline：

```text
query
→ current-safe lexical / dense / hybrid candidate recall [Implemented]
→ natural-language current-context interpretation     [Implemented]
→ current-revision memory-scope interpretation        [Implemented]
→ deterministic context applicability assessment      [Implemented]
→ rerank + conflict/redundancy handling
→ query-conditioned evidence distillation
→ EvidenceBundle
```

## Phase 2A 当前实现

`MemoryRecallEngine` 只依赖 `MemoryReadRepository`，每次调用从 `list_current()` 获取 `ACTIVE + CURRENT` 的 `MemorySnapshot`，在内存中重建 ephemeral lexical corpus。它不读取 Task 1 SQL，不写 access/importance/revision/provenance。

```text
RecallRequest(query, top_k, optional memory_types)
    ↓
NFKC + casefold tokenizer
Latin/number words + CJK unigram/bigram
    ↓
content BM25 + bounded metadata lexical overlap
    ↓
RecallCandidate(snapshot, rank, score breakdown, lexical trace)
    ↓
RecallResult
```

默认参数：`k1=1.5`、`b=0.75`、`metadata_weight=0.35`。相同分数按 `memory_id ASC` 排序；总分 `<= 0` 不返回。metadata 仅提供 query 与显式 `persons/entities/locations/topics/keywords/scopes/conditions` 的 lexical overlap，不做 person/location/scenario applicability reasoning。

DTO 是 request-local view：`RecallCandidate` 引用 canonical `MemorySnapshot`，没有创建第二套 persistent memory schema。provenance 随 snapshot 保留，但 authority 当前不参与排序。

工程 fixture 位于 `benchmarks/task2/recall_engineering_v1.json`，只用于 deterministic regression。它不是论文 benchmark，也不能用于研究效果声明。

必须保持的边界：

- 输入只来自 `MemoryReadRepository` / `MemorySnapshot`，不修改长期记忆。
- `retrieval score`、MATCH/MISMATCH/UNKNOWN、query-time inferred metadata 不写回 Task 1。
- recall 与 context applicability 是不同阶段；字符/向量相似不能自动证明当前适用。
- person、location、time、task、scenario、constraint、preference scope 是待验证的 applicability 维度。
- 必须保留 provenance、冲突和 insufficient-evidence/abstention 信号。

Phase 2A 有意允许“lexically related but not applicable”的 candidate 进入结果，为 Phase 2B 的 `Recall only` vs `Recall + Applicability` ablation 提供基线。Phase 2R 确认该问题已有 RPEval、BenchPreS、STITCH、MemSyco-Bench、KnowAct与 situation-aware recommendation 等直接或强重叠工作。因此该分层继续作为工程 architecture与 baseline boundary，但不再作为宽泛 novelty claim；详见 ADR-0012 与 `docs/research/task2-academic-novelty-survey.md`。

## Phase 2B 当前实现

`ApplicabilityEngine` 接收原始 `RecallCandidate`、caller/fixture 提供的 `ApplicabilityContext` 与独立 Task 2 annotation `MemoryApplicabilityProfile`。它不解析自然语言、不改变 `recall_score/rank`，也不把 profile/assessment 写回 Task 1。

支持 dimension：`PERSON`、`LOCATION`、`TIME`、`TASK`、`SCENARIO`、`CONSTRAINT`。每个实际参与比较的 key 是 `(dimension, field)`；frozen engine 只比较 canonical symbolic values。Phase 2F 的 upstream `ApplicabilityFieldRegistry` 负责受控 field identity 与 deterministic aliases，engine 本身仍不拥有 ontology。

Candidate scope：

- `EXPLICIT_CONSTRAINT`：有明确适用值；known context 满足则 MATCH、冲突则 MISMATCH。
- `EXPLICIT_GENERAL`：有证据明确表示该 field 不限制适用范围；无论 context 值为何均 MATCH。
- `UNSPECIFIED`：没有证据证明 constrained 或 general；必须是 UNKNOWN。

Context knowledge：`KNOWN`、`UNKNOWN`、`AMBIGUOUS`。结果状态：`MATCH`、`MISMATCH`、`UNKNOWN`、`AMBIGUOUS`。一个明确 MISMATCH 聚合为 `REJECT`；UNKNOWN/AMBIGUOUS 使用 `RETAIN_WITH_UNCERTAINTY`，不自动视为 MATCH 或 reject。`CONSTRAINT` 另支持 `REQUIRES` / `EXCLUDES`。

每个 `DimensionAssessment` 保留 stable reason code、candidate/context state/value 和不含完整文本的 evidence refs（memory/revision/provenance/profile/context field）。batch `ApplicabilityResult` 保持 Recall 原始顺序，只分离 retained/rejected，不进行最终 rerank。

Engineering annotations 位于 `applicability_profiles_v1.json` 与 `applicability_contexts_v1.json`，它们是 Task 2 fixture，不是 Task 1 metadata 或 research benchmark。

## Phase 2C evaluation protocol

`benchmarks/task2/applicability_eval_v1/` 将 runtime inputs（memory/profile/query/context）与 `labels.json` 物理分开。每个 query 在显式 8-memory controlled candidate universe 内运行 frozen Phase 2A Recall，全部 8 个 pair 预先拥有独立 gold label；evaluator 不根据实际 Top-K 补标签。

比较四种 evaluator methods：

- B0：Recall only，全部 retain/预测 MATCH。
- B1-permissive：已知 explicit constraint 冲突才 reject；missing/unknown/ambiguous pass-through 且不显式建模 uncertainty。
- B1-strict：已知冲突或 missing/unknown/ambiguous 均 reject。
- B2：frozen `ApplicabilityEngine`，保留四类 status 与 uncertainty policy。

评估同时观察 retrieval、safety、utility 和 uncertainty；contamination 不能单独作为成功指标。Phase 2C implementation 全在 `code/scripts/`/benchmark/tests，production `code/src/memory/retrieval/` 不认识 dataset IDs 或 gold。

Controlled v1 的真实结果显示：B0@4 mismatch contamination 为 `0.4128`；B1-permissive 与 B2 对 explicit mismatch 都能降为 `0` 且不误杀 gold MATCH。因此该数据不能支持“独立 applicability 在 explicit mismatch safety 上必然优于 naive exact filter”。B2 的可验证增益是把 `38.53%` assessed candidates 显式保留为 UNKNOWN/AMBIGUOUS，而 permissive B1 把它们压成 definite MATCH、strict B1 将其全部删除。该结果是 engineering motivation，不是 academic novelty claim。

开放世界 semantic alias、complex conditional logic、cross-encoder/final rerank
和 answer generation 仍未实现。Phase 2G 已补齐 provider-independent 的
request-local evidence downstream：Applicability gate、显式 conflict
annotations/resolution baseline、deterministic selection/dedup/diversity、
bounded extractive distillation、sufficiency 与 `EvidenceBundle`。这些 DTO
均为 derived/read-only，不写回 Task 1；natural-language convenience path
仍可使用 Phase 2E one-shot interpreter，而 typed path 完全不需要 provider。

### Phase 2G evidence pipeline

```text
RecallResult + ApplicabilityResult
  → CandidateEvidence (rank/score/applicability/provenance trace)
  → MISMATCH reject; UNKNOWN/AMBIGUOUS retain with uncertainty
  → conflict assessment
  → bounded deterministic selection/dedup/diversity
  → extractive DistilledEvidence
  → EvidenceSufficiency
  → EvidenceBundle
```

`CandidateEvidence`保留原始 recall rank/score breakdown、current revision
identity、provenance refs、applicability reason与 conflict trace；任何 losing
conflict evidence只会从 answer-facing selection 中省略，且在 omitted trace
中保留 identity。第一版 `ConflictDetector` 只在相同结构化 scope
axis/value 出现相反 `REQUIRES`/`EXCLUDES` 时自动产生 unresolved
annotation；更复杂的冲突仍可由上游显式提供 `ConflictAnnotation`，不会凭
文本相似度偷偷生成语义冲突。bundle 有
`max_items`、per-item chars 与 total chars 三重 budget，输出只描述记忆
证据，不生成建议或最终回答。

Phase 2G 的测试入口是
`DeterministicEvidencePipeline.run_typed(ApplicabilityResult, ...)`；自然
语言 convenience 入口接收现有 `SemanticRetrievalPipeline`，provider 失败
仍以 typed error 向上暴露。见 `code/src/memory/retrieval/evidence.py` 与
`code/scripts/task2_evidence_acceptance.py`。

## Phase 2D strong recall stack

三个 recall backend 共享 `MemoryRetriever.recall_request(RecallRequest) -> RecallResult`：

- `MemoryRecallEngine` / `lexical_bm25_v1`：Phase 2A frozen BM25，仍使用 `k1=1.5`、`b=0.75`、`metadata_weight=0.35`。
- `DenseEmbeddingRetriever` / `dense_embedding_v1`：只 embedding current revision content，L2 normalize 后用 cosine similarity（normalized dot product），`score <= 0` 排除，相同分数按 `memory_id`。
- `HybridRRFMemoryRetriever` / `hybrid_rrf_v1`：独立运行 lexical/dense candidate generation，再以 Reciprocal Rank Fusion 合并：`RRF(d)=Σ 1/(60+rank_i(d))`。默认每路最多取 50 个候选；不存在某一路的 candidate 不获得该路贡献。

`RecallScoreBreakdown` 保持旧构造兼容，并新增 optional `dense_similarity`、`fusion_rrf`。Lexical 的 `total` 仍是 `content_bm25 + metadata_overlap`；Dense 使用 cosine；Hybrid 使用 RRF。原始 recall score、applicability status 与 Task 1 importance 是三个独立量。

Dense 只依赖 `EmbeddingProvider`：`embed_documents`、`embed_query` 与可核查的 `EmbeddingProviderIdentity(provider, provider_version, model, dimension, normalization, batch_size)`。当前真实可选 backend 是 `FastEmbedEmbeddingProvider`，默认 model 为 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`、384 维、L2 normalize、batch 32。它采用 lazy import；只有显式构造时才可能安装/下载失败，失败使用现有 typed provider exceptions，绝不 silent fallback 到 lexical。安装见 `requirements-retrieval.txt`。

所有 index 仍是 request-local、derived、rebuildable：

```text
MemoryReadRepository.list_current()
  → ACTIVE + CURRENT snapshots
  → ephemeral lexical/dense indices
  → RecallResult
```

Dense 不读取 metadata，避免重复 Phase 2A metadata lexical boost；Hybrid 只融合两个 rank list，不再次加 metadata。Applicability 不在 retriever 内运行。

`benchmarks/task2/retrieval_eval_v1/` 是 40 memories / 32 queries 的人工 engineering evaluation，覆盖 8 domains 与 lexical-exact / semantic-paraphrase / mixed subsets。默认 runner 的 `deterministic-test` provider 只验证接口、cosine、fusion 和 evaluator determinism，输出禁止作为 semantic performance。真实工程结果必须显式指定 `--provider fastembed` 并记录 provider/model。

## Phase 2E semantic interpretation

Phase 2E 补齐两个相互独立、可单独 attribution 的 derived stage：

```text
query + bounded recent ContextSource
  → ContextInterpreter
  → ContextInterpretation(ApplicabilityContext, evidence refs, warnings, identity)

MemorySnapshot(current revision + metadata + provenance identity)
  → MemoryScopeInterpreter
  → MemoryScopeInterpretation(MemoryApplicabilityProfile, evidence refs, warnings, identity)
```

二者共享 `SemanticInterpretationProvider`，但拥有不同 task、prompt version 和严格 output validator。当前真实实现通过 DeepSeek V4 Flash 的 Chat Completions、JSON Output、thinking 和 typed provider error 工作；生产 endpoint、model、provider label 与 credential 环境变量固定，不允许旧 API 选择变量改变路由。Semantic structured extraction在调用方没有显式配置时使用 `2048` completion budget，避免思考模型在输出完整对象前耗尽预算。只有 `REQUIRED` 可构造，deterministic test 测试必须显式注入 fake。Phase 2F 结果记录 `context_interpretation_v2` / `memory_scope_interpretation_v2`、schema v1与 ontology version。invalid JSON/enum/field/value 最终均是可见 `ProviderResponseError`，不会 silent all-UNKNOWN fallback。

本地开发可使用 ignored、权限受限的 `.env.local`；加载优先级是 `process environment > .env.local > defaults`，CI以 `MEMORY_LOAD_LOCAL_ENV=0` 明确禁用。该文件不是 package asset且不得进入 Git。DeepSeek 客户端使用 provider 支持的 `json_object`，本地 domain validator继续负责结构和 cross-field invariant。HTTP 429按同一 DeepSeek endpoint 的 `Retry-After` / token reset header有限等待，并可配置最小请求间隔；不会轮换 API。

`ContextInterpreter` 只接受有 supplied source 支持的 KNOWN/AMBIGUOUS fact；缺失信息保持 UNKNOWN/absent。evidence 只保存 `source:<id>` 或 bounded span reference，不在 DTO 中复制对话。当前继续使用 KNOWN/UNKNOWN/AMBIGUOUS：未被时序消解的冲突证据表达为 AMBIGUOUS，不新增语义重叠的 CONFLICTING。

`MemoryScopeInterpreter` 只看 selected current `MemorySnapshot`。revision text 是主要语义 evidence；semantic metadata 是 hint，不因出现 `locations=[上海]` 就自动推断 LOCATION constraint。provenance 只提供 ID/authority。Profile 是 sparse：没有 qualifier evidence 就没有 scope rule；这不是 `UNSPECIFIED`。`UNSPECIFIED` 只表示有正面 axis-relevance evidence、但 scope 未解析；只有明确“无论/任何/regardless”等证据才能输出 `EXPLICIT_GENERAL`。无法由现有 equality/REQUIRES/EXCLUDES schema 忠实表示的 complex conditional 返回 warning/UNSPECIFIED，而非发明 DSL。

### Controlled applicability ontology

`ApplicabilityFieldRegistry` 是 Task 2-owned、versioned、derived configuration，不是 Task 1 schema。它封闭 `(dimension, field_id)` vocabulary，并为每个 field声明：description、open/closed value semantics、closed values、deterministic field/value aliases与constraint support。JSON Schema只允许 registry field ID；本地 validator再检查 dimension-field pair、closed values与alias canonicalization。新增 field主要修改 registry，而不是给 `ApplicabilityEngine`增加 case-specific `if`。

Field closed、value部分开放：`payment_source` 等小集合使用 closed values；`destination`、预算和时间窗口等开放值只做 NFKC/casefold与明确 alias mapping。trace保留 provider raw values与 canonical values，便于 attribution。没有合适 field时必须 omit + warning，禁止 arbitrary fallback。

Context中的 absent 表示“未提及或与本次 applicability无关”；显式 `UNKNOWN` 表示该 field 对当前决策相关但值不可得；`AMBIGUOUS` 必须保留至少两个有证据候选。Query intent（例如“找酒店”）不自动成为 applicability fact。Scope中的 `EXPLICIT_GENERAL` 除模型判断外还经过明确 universal marker门禁；absence永远不能推出 generality。

`SemanticRetrievalPipeline` 组合任意 `MemoryRetriever`、两个 interpreter 与 frozen `ApplicabilityEngine`：

```text
natural-language query
→ recall (BM25/Dense/Hybrid)
→ independent context + per-candidate scope interpretation
→ MATCH/MISMATCH/UNKNOWN/AMBIGUOUS
```

它不修改 recall rank/score，不写 Task 1，也不生成 EvidenceBundle/回答。`benchmarks/task2/context_interpretation_eval_v1/` 的 runtime natural-language 与 human gold 物理分离，覆盖 30 context、30 memory scope、10 end-to-end pairs以及中/英/混合语言。默认 oracle fixture 只验证 orchestration、metrics与 A-D attribution，明确禁止作为模型语义效果；真实模型必须用 `--provider real` 显式运行。

### Historical Phase 2E Groq pilot

以下结果属于 2026-08 的历史工程试验，不代表当前 Provider：当时使用 Groq
OpenAI-compatible endpoint 与 `openai/gpt-oss-120b`，链路成功但 semantic performance
较弱：context F1 `0.1026`、false-known `0.7619`、scope-state accuracy `0.0141`、
9/30 scope case 在两次生成后仍违反本地 invariant；A/B/C/D 为 `1.0/0.4/0.4/0.4`。
它只证明当时 integration link 可运行，不能作为当前模型比较。

Phase 2F controlled ontology真实结果将context F1提高到`.7808`、scope-state提高到`.4138`、D提高到`.8`，但false-known仍为`.48`，Hard-v1 scope-state仅`.3333`。因此semantic transport/integration已可运行，automatic interpretation仍不是production-ready；下游finalization不得把oracle contract结果当作真实质量。

Phase 2F Round-2 domain audit将profile正式冻结为 sparse evidence-backed semantics。v1 fixtures保留为historical diagnostic；v2 fixtures移除plain-absence `UNSPECIFIED` axes、修正general/constraint边界，并附带migration manifests与scope evidence manifest。Round-1 checkpoint已测试重算v2：Frozen context F1=`.8321`、false-known=`.75`、scope-state=`.4528`、scope-axis precision/recall=`.7273/.5455`、D=`1.0`；Hard context F1=`.8182`、false-known=`.3333`、scope-state=`.4`、scope-axis precision/recall=`.6/.5455`、D=`1.0`。v2仍未达到production gate，且context false-known因删除无证据UNKNOWN后更高；未开始field-first stages。

Round-2 staged path is now opt-in and separately attributable: selectors choose evidence-backed registry fields/qualifier axes, then conditioned interpreters produce state/value/rule for only that field. Canonical context safety is `Unsupported-KNOWN Rate`; the legacy false-known rate remains historical only. The first real DEV attempt stopped at an HTTP 400 structured-output response from the selector request, so no holdout or promotion claim is made.
