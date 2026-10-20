# Reference systems：源码证据与设计借鉴

本文件记录用于架构判断的 source-level pointers 和可复核的上游仓库与 commit。第三方源码快照已移出 MAPAS 工作仓库并归档；它们不进入安装包、测试输入或运行时。需要复核时，应按下列上游仓库和 commit 单独 checkout。Claude Code 内部实现不可公开验证，不能用第三方仓库冒充官方源码。

## OpenAI Codex

- Repository: `openai/codex`
- Commit: `16fbfe557446`
- Inspected subsystem:
  - `codex-rs/core/src/session/mod.rs:replace_compacted_history`
  - `codex-rs/core/src/session/turn.rs`（pre/mid-turn compaction triggers）
  - `codex-rs/core/src/tasks/compact.rs`
  - `codex-rs/protocol/src/items.rs:ContextCompactionItem`
  - rollout reconstruction tests
- Finding: session/rollout events、replacement compacted history、turn context 与 world-state baseline 分开；compaction 是 history transformation，不是 universal typed task state。
- Borrowing: L1/L2，支持 transcript/compaction/external-state separation。
- Not copied: Rust event model、remote compaction protocol、product-specific analytics。
- Uncertainty: 当前 checkout 在原 survey 后可能演化；这里的 paths/commit 是可重复核查 snapshot。

## Anthropic Claude Code

- Official core source: **not publicly verifiable**。
- Evidence used: Anthropic 官方公开文档中的 resume、compaction、`CLAUDE.md`/memory、Todo/plan 行为。
- Finding: 可验证的是产品行为，不可声称内部使用某个 class/schema/algorithm。
- Borrowing: L1 only。
- Deliberately not used: `claude-cookbooks` 或第三方 clone 作为产品核心源码证据。

## Claw Code

- Repository: `ultraworkers/claw-code`
- Commit: `b71afddae100`
- Evidence level: 公开 Rust Claude-Code-style parity harness，不是 Anthropic 官方源码。
- Inspected subsystem:
  - `rust/crates/runtime/src/session.rs:Session,SessionCompaction,record_compaction`
  - `rust/crates/runtime/src/summary_compression.rs:compress_summary`
  - `rust/crates/runtime/src/task_registry.rs:TaskRegistry`
  - `rust/crates/tools/src/lib.rs:TodoWrite,TodoItem,TodoStatus`
  - `rust/PARITY.md`、`rust/MOCK_PARITY_HARNESS.md`
- Finding: session persistence/compaction、Todo、background/runtime task registry 是不同结构；summary 可重复压缩且是有损 representation；未发现 universal six-role continuity object。
- Borrowing: L1/L2，支持 ADR-0006。
- Not copied: parity-specific tools/session JSONL implementation。
- Uncertainty: parity 文档只能证明 harness 目标/测试，不能证明 Anthropic 内部设计。

## OpenClaw

- Repository: `openclaw/openclaw`
- Commit: `3e6aec1d55ae`
- Inspected subsystem:
  - `src/state/openclaw-agent-schema.sql`（session windows、transcript events/identities、active event projection）
  - `src/state/openclaw-agent-db-session-migrations.ts`
  - `src/memory/root-memory-files.ts`
  - `src/cron/types-shared.ts` 与 cron/heartbeat tests
- Finding: persistent transcript/event identities、active projections、workspace memory files、cron/heartbeat/session targeting 分层；跨 channel continuity 依赖 durable state + session identity，而非单一 working-set object。
- Borrowing: L1/L2。
- Not copied: TypeScript service/database schema、cron infrastructure。

## Mem0

- Repository: `mem0ai/mem0`
- Phase 0B snapshot: `d70cc00ab39e`（另保留 current checkout `4a0a9a92a641`）。
- Inspected subsystem:
  - `mem0/memory/main.py:Memory.add,search,update,delete,history`
  - `mem0/memory/storage.py:SQLiteManager,add_history,get_history`
- Finding: LLM extraction/comparison 驱动 ADD/UPDATE/DELETE，stable IDs 与 operation history 存在；主要 content/vector payload 更新不等同于本项目不可变 canonical revisions。
- Borrowing: L1/L2，stable identity/history 动机。
- Not copied: provider prompts、vector-store adapters、SDK code。
- Limitation relevant here: 未发现完整 person/location/task/scenario/constraint applicability validator。
- Phase 2D retrieval evidence（current checkout `4a0a9a92a641`）：`mem0/memory/main.py:search` 将 scope filters、vector search 与 optional reranker 分层；`mem0/utils/scoring.py` 的 hybrid additive score 需要 BM25 normalization、semantic threshold 与 weight。Phase 2D 借鉴 provider/vector-store separation（L2），但未复制其 tuned additive formula，也未复制 reranker failure 后静默返回原结果的行为。

## Letta

- Repository: `letta-ai/letta`
- Commit: `ff19ffeafeb5`
- Inspected subsystem:
  - `letta/schemas/agent.py:AgentState`
  - `letta/schemas/block.py:Block`
  - `letta/schemas/passage.py:Passage`
  - `letta/agents/letta_agent.py`（summary block/message buffer/archival search）
  - `letta/agents/ephemeral_summary_agent.py`
- Finding: in-context blocks、message buffer/summary 与 archival passages 分层；retrieval 主要通过 archival passage query，不等同于显式 scenario applicability validation。
- Borrowing: L1/L2，支持 active context 与 archival memory separation。
- Not copied: agent server、Pydantic schemas、tool/runtime framework。
- Phase 2D retrieval evidence（`ff19ffeafeb5`）：`letta/helpers/tpuf_client.py:_reciprocal_rank_fusion` 将 vector ANN 与 FTS 分开执行后用标准 `k=60` RRF，`agent_manager.py:query_agent_passages_async` 选择 hybrid mode。借鉴为 L2/L3 RRF pattern；本项目独立实现且不依赖 Turbopuffer。

## Graphiti

- Repository: `getzep/graphiti`
- Commit: `401c59a65bde`
- Inspected subsystem:
  - `graphiti_core/nodes.py:EpisodicNode`
  - `graphiti_core/edges.py:EntityEdge`（`episodes`,`valid_at`,`invalid_at`）
  - `graphiti_core/graphiti.py:search,search_`
  - `graphiti_core/search/search.py:search`
  - `graphiti_core/search/search_config_recipes.py`（hybrid/RRF/MMR/cross-encoder recipes）
- Finding: Episode→edge provenance、valid/invalid temporal facts 与 hybrid graph retrieval 较成熟；temporal storage/filtering仍不自动回答所有 current-scenario applicability 问题。
- Borrowing: L1/L2，启发 revision provenance 与 current-safe default。
- Not copied: graph DB schema、graph traversal、reranker implementation。
- Phase 2D retrieval evidence（`401c59a65bde`）：`graphiti_core/search/search_config.py` 分离 cosine/BM25/BFS search methods 与 rerankers；`graphiti_core/graphiti.py:search` 默认使用 `EDGE_HYBRID_SEARCH_RRF`。借鉴为 L2 pluggable candidate generation + rank fusion；未复制 graph traversal。

## PEARL / STITCH / RPEval 在 retrieval stack 中的位置

- PEARL：generation-calibrated retrieval 是 future paper-derived baseline candidate；它超出 Phase 2D 的纯 recall contract，未实现。
- STITCH/CAME-Bench：官方 `Seattleyrz/contextual-intent` 将 thematic scope、event type、entity type 作为结构 cue，并在 semantic score 前按 intent compatibility 过滤。它是 future `PAPER_DERIVED_REIMPLEMENTATION` / external benchmark candidate，不被冒充为 Phase 2D hybrid recall。
- RPEval（local snapshot `0f8bce80a03e`）：重点是 recalled preference 的 pragmatic utilization，不是 lexical+dense fusion recipe；本阶段只把其 public dataset/evaluator列为外部 evaluation feasibility 对象。

## Phase 2E semantic interpretation decision

Targeted inspection 只用于实现选择，没有重新做 novelty survey：

- **Borrow**：STITCH 将 current intent 拆为 structured cue 后再做 candidate compatibility；RPEval `prompts_528.py` 对单条 preference 与 query 分开判断 utilization；Graphiti `401c59a65bde` 的 extraction prompts使用 typed result models、episode/source linkage，并在 temporal extraction中明确禁止编造时间；Letta `ff19ffeafeb5` 的 strict tool schema要求封闭字段。采用 L1/L2 的“结构输出 + 独立 stage + evidence identity + strict validation”。
- **Adapt**：本项目不直接输出 answer/utilization policy，而是分别生成现有 `ApplicabilityContext` 与 `MemoryApplicabilityProfile`，保留 provider/model/prompt/schema identity，并通过 A-D oracle intervention定位 context 与 scope interpreter error。
- **Reject**：不采用 RPEval 脚本中的宽松文本/label parsing；不采用 Graphiti prompt中“when in doubt, extract”的 recall-oriented策略；不把 Mem0/Graphiti metadata/entity mention 自动解释为 applicability constraint；不复制 Letta/Pydantic runtime或任何 reference code。
- **Evidence limitation**：PersonaMem、LongMemEval、MemSyco-Bench在 Phase 2R 中提供 task taxonomy/evaluation启发，但当前本地没有其 implementation checkout；因此 Phase 2E 不声称复现其 parser。所有借鉴为 L1/L2，无 L4 reuse。

## External benchmark feasibility（Phase 2D）

| Benchmark | 结论 | 依据与边界 |
|---|---|---|
| RPEval | PARTIAL | dataset/code public；license 含 non-commercial dataset 条款，且主要评估生成时 preference utilization，需 system-output adapter 与模型 judge |
| BenchPreS | NOT CURRENTLY VIABLE | 论文完整公开，但未确认官方 public evaluator/repository；response-level recipient-task selectivity，不能无失真映射成 recall-only |
| STITCH / CAME-Bench | VIABLE | official code、MIT dataset、retriever method guide公开；长轨迹规模较重且 answer evaluation 需要模型，适合作为 secondary retrieval benchmark |
| MemSyco-Bench | VIABLE | MIT repo、1,550 samples、data/evaluator/unified baseline interface公开；覆盖 current selection、scope control、conflict与use，适合作为 primary full-system benchmark |
| STALE | PARTIAL | 论文定义 400 scenarios / 1,200 queries；当前未确认可直接运行的官方 code/dataset license，且重点为 implicit stale-state update/use而非纯 recall |

推荐：Primary=`MemSyco-Bench`；Secondary=`CAME-Bench`。推荐表示后续适配优先级，不表示本阶段已经运行或复现其官方结果。

## Future full-system baseline adapters

Mem0、Graphiti、Letta 必须通过 evaluator-side `SYSTEM_ADAPTER` 接入，固定相同输入事件、query、top-k、embedding/model与可见 context；保留各自 canonical write/retrieval behavior，不把内部对象强行映射成 Task 1 schema。adapter 输出至少包含 external IDs、rank/score、source/provenance（若系统提供）、latency、provider/model和失败类型。reference checkout 不成为 runtime dependency；每个系统使用隔离环境。

## Direct code reuse

当前所有借鉴均为 L1/L2；没有 L4 code reuse，因此主项目没有新增 reference license、attribution runtime dependency 或 maintenance coupling。
