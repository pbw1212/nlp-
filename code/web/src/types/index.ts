/**
 * MAPAS Web — P0 契约类型（字段名与 Python 侧逐字对齐，禁止改名）
 *
 * 来源：
 *  - code/src/memory/core/schema.py                (记忆/来源枚举)
 *  - code/src/memory/retrieval/applicability_models.py / evidence.py (检索四态)
 *  - code/src/memory/task_state/models.py           (Task 3)
 *  - code/src/memory/collaboration/models.py / experience.py (协作/经验)
 *  - code/src/memory/agent/models.py / journal.py   (AgentResult / 审批)
 *  - code/src/memory/runtime/events.py              (事件)
 *  - code/scripts/personal_agent_app_server.py      (JSONL 协议)
 *
 * 命名约定：后端同时使用 camelCase（JSONL 响应）与 snake_case（内部 dataclass / AgentResult）。
 * 两套都保留 —— 桥接层不重新投影字段，前端在 selector 里做唯一一次归一化。
 */

/* ============================================================
 * 1. 枚举（值必须与 Python Enum 完全一致）
 * ============================================================ */

export const MemoryType = ['CORE_PROFILE', 'PREFERENCE_RULES', 'EPISODIC_GRAPH'] as const;
export type MemoryType = (typeof MemoryType)[number];

export const Granularity = ['FINE_EPISODE', 'SEMANTIC_INSIGHT', 'HIGH_ABSTRACT'] as const;
export type Granularity = (typeof Granularity)[number];

/** 单个 revision 的语义有效性 */
export const SemanticValidity = ['CURRENT', 'SUPERSEDED', 'CONTRADICTED', 'EXPIRED'] as const;
export type SemanticValidity = (typeof SemanticValidity)[number];

/** 记忆的保留处置（回收站） */
export const RetentionDisposition = ['ACTIVE', 'ARCHIVED', 'RECYCLED'] as const;
export type RetentionDisposition = (typeof RetentionDisposition)[number];

export const ActiveStatus = ['ACTIVE', 'IN_RECYCLE_BIN', 'DEPRECATED', 'CONTRADICTED'] as const;
export type ActiveStatus = (typeof ActiveStatus)[number];

/** 版本变更类型；RISE 与 LEGACY_IMPORT 仅历史 |
 *  UI: ADD(新增) UPDATE(更新) CORRECT(更正) RISE(提升) LEGACY_IMPORT(旧数据导入) */
export const ChangeKind = ['ADD', 'UPDATE', 'CORRECT', 'RISE', 'LEGACY_IMPORT'] as const;
export type ChangeKind = (typeof ChangeKind)[number];

/** 来源权威等级 —— UI 必须显示，不能一律画成"已确认" */
export const ProvenanceAuthority = ['EXPLICIT_USER', 'INFERRED', 'SYSTEM_OR_TOOL', 'UNKNOWN'] as const;
export type ProvenanceAuthority = (typeof ProvenanceAuthority)[number];

/** 显式记忆写入的生命周期操作（PPT 第 4 页） */
export const LifecycleOperation = ['ADD', 'UPDATE', 'CORRECT', 'REMOVE', 'NOOP'] as const;
export type LifecycleOperation = (typeof LifecycleOperation)[number];

export const RelationType = ['DERIVED_TO', 'CORRECTED_FROM', 'QUALIFIES', 'DEPENDS_ON'] as const;
export type RelationType = (typeof RelationType)[number];

export const IngestionState = ['PENDING', 'APPLIED'] as const;
export type IngestionState = (typeof IngestionState)[number];

/* ---------- Task 2 检索：四态是核心卖点，UI 禁止合并 ---------- */

export const ApplicabilityDimension = ['PERSON', 'LOCATION', 'TIME', 'TASK', 'SCENARIO', 'CONSTRAINT'] as const;
export type ApplicabilityDimension = (typeof ApplicabilityDimension)[number];

/** 适用性四态：MATCH 适用 / MISMATCH 不适用 / UNKNOWN 信息不足 / AMBIGUOUS 存在歧义 */
export const ApplicabilityStatus = ['MATCH', 'MISMATCH', 'UNKNOWN', 'AMBIGUOUS'] as const;
export type ApplicabilityStatus = (typeof ApplicabilityStatus)[number];

export const ApplicabilityDecision = ['RETAIN', 'REJECT', 'RETAIN_WITH_UNCERTAINTY'] as const;
export type ApplicabilityDecision = (typeof ApplicabilityDecision)[number];

export const ApplicabilityReasonCode = [
  'EXACT_MATCH', 'EXPLICIT_CONFLICT', 'CONTEXT_UNKNOWN', 'CONTEXT_AMBIGUOUS',
  'CANDIDATE_SCOPE_UNSPECIFIED', 'EXPLICIT_GENERAL_SCOPE', 'REQUIRED_CONSTRAINT_MISSING',
] as const;
export type ApplicabilityReasonCode = (typeof ApplicabilityReasonCode)[number];

export const ConflictStatus = ['NO_CONFLICT', 'RESOLVED', 'UNRESOLVED'] as const;
export type ConflictStatus = (typeof ConflictStatus)[number];

export const EvidenceSufficiencyStatus = ['SUFFICIENT', 'PARTIAL', 'INSUFFICIENT'] as const;
export type EvidenceSufficiencyStatus = (typeof EvidenceSufficiencyStatus)[number];

export const ContextKnowledgeState = ['KNOWN', 'UNKNOWN', 'AMBIGUOUS'] as const;
export type ContextKnowledgeState = (typeof ContextKnowledgeState)[number];

/* ---------- Task 3 任务状态 ---------- */

export const TaskStatus = ['ACTIVE', 'PAUSED', 'COMPLETED', 'CANCELLED'] as const;
export type TaskStatus = (typeof TaskStatus)[number];

export const EntryKind = [
  'GOAL', 'CONSTRAINT', 'DECISION', 'PROGRESS', 'OPEN_QUESTION', 'DEPENDENCY', 'BACKGROUND',
] as const;
export type EntryKind = (typeof EntryKind)[number];

export const EntryStatus = ['ACTIVE', 'RESOLVED', 'SUPERSEDED', 'REMOVED'] as const;
export type EntryStatus = (typeof EntryStatus)[number];

export const Priority = { LOW: 1, NORMAL: 2, HIGH: 3, CRITICAL: 4 } as const;
export type Priority = (typeof Priority)[keyof typeof Priority];

export const SourceKind = ['USER', 'AGENT', 'TOOL', 'EXTERNAL'] as const;
export type SourceKind = (typeof SourceKind)[number];

export const ProposalBasis = ['EXPLICIT', 'OBSERVED', 'INFERRED'] as const;
export type ProposalBasis = (typeof ProposalBasis)[number];

export const TaskStateScope = ['TASK', 'SESSION'] as const;
export type TaskStateScope = (typeof TaskStateScope)[number];

/* ---------- 任务级自动闭环 ---------- */

export const TaskCycleState = [
  'ASSESS_PENDING', 'WAITING_USER', 'REPAIR_PENDING', 'COMPLETED', 'FAILED', 'CANCELLED',
] as const;
export type TaskCycleState = (typeof TaskCycleState)[number];

export const LifecycleJobState = [
  'PENDING', 'WAITING_USER', 'SUCCEEDED', 'FAILED', 'SUPERSEDED', 'CANCELLED',
] as const;
export type LifecycleJobState = (typeof LifecycleJobState)[number];

export const LifecycleJobKind = ['ASSESS', 'LEARN'] as const;
export type LifecycleJobKind = (typeof LifecycleJobKind)[number];

/** 完成后结果是否仍然有效 —— 三个值含义不同，UI 文案必须区分 */
export const ResultValidity = ['VALID', 'STALE', 'NOT_COMPLETED', 'NOT_CHECKED_WORKSPACE'] as const;
export type ResultValidity = (typeof ResultValidity)[number];

/* ---------- 协作 / 修复 ---------- */

export const TaskNodeStatus = [
  'PENDING', 'RUNNING', 'PRODUCED', 'VERIFIED', 'FAILED', 'BLOCKED', 'STALE',
] as const;
export type TaskNodeStatus = (typeof TaskNodeStatus)[number];

export const RunStatus = ['RUNNING', 'SUCCEEDED', 'FAILED'] as const;
export type RunStatus = (typeof RunStatus)[number];

/** 核验状态：只有 VERIFIED 才允许作为后继依赖 */
export const VerificationStatus = ['PRODUCED', 'VERIFIED'] as const;
export type VerificationStatus = (typeof VerificationStatus)[number];

export const ModelCallStatus = ['RESERVED', 'SUCCEEDED', 'FAILED'] as const;
export type ModelCallStatus = (typeof ModelCallStatus)[number];

export const RepairQueueStatus = ['PENDING', 'SUCCEEDED', 'FAILED'] as const;
export type RepairQueueStatus = (typeof RepairQueueStatus)[number];

export const RepairAction = ['RERUN'] as const;
export type RepairAction = (typeof RepairAction)[number];

export const RepairReason = ['INVALIDATED', 'REUSE_NOT_AUTHORIZED'] as const;
export type RepairReason = (typeof RepairReason)[number];

export const InvalidationReason = [
  'TASK_FRONTIER', 'SOURCE_FRONTIER', 'STALE_REPLAY', 'ARTIFACT_REVOKED',
] as const;
export type InvalidationReason = (typeof InvalidationReason)[number];

export const ExecutionContractValidation = ['LEGACY_UNTRACKED', 'PARTIAL_MATCH', 'CHANGED'] as const;
export type ExecutionContractValidation = (typeof ExecutionContractValidation)[number];

export const VerifierVerdict = ['PASS', 'BLOCKED'] as const;
export type VerifierVerdict = (typeof VerifierVerdict)[number];

export const TaskReviewVerdict = ['COMPLETE', 'WAITING_USER'] as const;
export type TaskReviewVerdict = (typeof TaskReviewVerdict)[number];

export const TaskReviewEntryVerdict = ['SATISFIED', 'UNSATISFIED', 'UNCERTAIN'] as const;
export type TaskReviewEntryVerdict = (typeof TaskReviewEntryVerdict)[number];

export const CollaborationRole = ['Planner', 'Memory', 'Executor', 'Verifier', 'Coordinator', 'Review'] as const;
export type CollaborationRole = (typeof CollaborationRole)[number];

/* ---------- 程序性经验 ---------- */

export const ExperienceState = [
  'CANDIDATE', 'ACTIVE', 'QUARANTINED', 'NEEDS_CONFIRMATION', 'STALE', 'REVOKED', 'SUPERSEDED',
] as const;
export type ExperienceState = (typeof ExperienceState)[number];

export const ExperienceEventKind = ['REVIEW', 'SUPERSEDED', 'SOURCE_STALE', 'REVOKED', 'FEEDBACK'] as const;
export type ExperienceEventKind = (typeof ExperienceEventKind)[number];

export const ExperienceVerdict = ['ADMIT', 'QUARANTINE', 'NEEDS_CONFIRMATION'] as const;
export type ExperienceVerdict = (typeof ExperienceVerdict)[number];

/** 筛选判定不是持久状态，只用于当次选择 */
export const ExperienceSelectVerdict = ['APPLICABLE', 'INAPPLICABLE', 'UNCERTAIN'] as const;
export type ExperienceSelectVerdict = (typeof ExperienceSelectVerdict)[number];

/* ---------- 审批 / 工具 / Agent ---------- */

export const PendingActionStatus = ['PENDING', 'CONFIRMED', 'REJECTED', 'EXECUTED'] as const;
export type PendingActionStatus = (typeof PendingActionStatus)[number];

export const ConfirmationDecision = ['CONFIRM', 'REJECT'] as const;
export type ConfirmationDecision = (typeof ConfirmationDecision)[number];

export const AgentResultStatus = [
  'SUCCESS', 'FAILED', 'AWAITING_CONFIRMATION', 'STEP_LIMIT_REACHED',
  'TASK_SELECTION_REQUIRED', 'EXECUTION_STATUS_UNKNOWN', 'CANCELLED',
] as const;
export type AgentResultStatus = (typeof AgentResultStatus)[number];

export const ToolEffect = ['READ_ONLY', 'INTERNAL', 'SIDE_EFFECT'] as const;
export type ToolEffect = (typeof ToolEffect)[number];

export const IdempotencySupport = ['NONE', 'SUPPORTED'] as const;
export type IdempotencySupport = (typeof IdempotencySupport)[number];

export const ToolCallStatus = ['SUCCESS', 'FAILED'] as const;
export type ToolCallStatus = (typeof ToolCallStatus)[number];

/** 事件类型（源码实际写出的 21 种；turn.interrupted 无发射点，前端仍需容错） */
export const AgentEventType = [
  'thread.started', 'thread.resumed',
  'turn.started', 'turn.resumed', 'turn.completed', 'turn.failed', 'turn.usage',
  'item.completed',
  'tool.started', 'tool.completed', 'tool.call.completed',
  'approval.requested', 'approval.decision', 'approval.completed',
  'stage.completed',
  'compaction.started', 'compaction.completed', 'compaction.failed',
  'task.lifecycle.updated',
  'repair.completed', 'repair.failed',
] as const;
export type AgentEventType = (typeof AgentEventType)[number] | (string & {});

/* ============================================================
 * 2. 会话 / 消息 / 事件（JSONL 既有形状，字段名不改）
 * ============================================================ */

export interface Thread {
  threadId: string;
  title: string | null;
  taskId: string | null;
  /** 'ACTIVE' | 'PENDING'（PENDING = 尚未落库的新会话） */
  status: string;
  createdAt: number | null;
  updatedAt: number | null;
}

export type MessageRole = 'USER' | 'ASSISTANT' | 'SYSTEM' | 'TOOL';

export interface Message {
  messageId: string;
  turnId: string;
  role: MessageRole;
  content: string;
  createdAt: number;
  /** 仅 TOOL 消息：content 是 JSON 字符串
   *  { kind:'tool_exchange', exchanges:[{tool_call_id,tool_name,arguments,result,status}] } */
}

/** 注意：这个对象是 snake_case（宿主直出，不转换） */
export interface UnfinishedTurn {
  turn_id: string;
  session_id: string;
  started_at: number;
  input_available: boolean;
  status: 'INTERRUPTED';
}

export interface AgentEvent {
  event_id: string;
  /** 全局自增游标，SSE / events/read 的续读键 */
  sequence: number;
  event_type: AgentEventType;
  session_id: string | null;
  turn_id: string | null;
  payload: Record<string, unknown>;
  created_at: number;
  schema_version: number;
}

/* ---------- turn 结果（snake_case，来自 AgentResult serialization） ---------- */

export interface TurnErrorInfo {
  stage?: string;
  category: string;
  code: string;
  safe_message: string;
  retryable: boolean;
  details?: Record<string, unknown>;
}

export interface ToolDetail {
  tool_name?: string;
  tool_call_id?: string;
  status?: string;
  arguments?: unknown;
  duration_seconds?: number;
  result_size?: number;
  [k: string]: unknown;
}

export interface DeveloperDetails {
  summary?: string;
  elapsed_seconds?: number;
  status?: AgentResultStatus;
  step_count?: number;
  runtime_stage_traces?: unknown[];
  tool_details?: ToolDetail[];
  provider_usage_delta?: Record<string, number>;
  provider_reasoning_content?: string | null;
  warnings?: string[];
  error?: TurnErrorInfo | null;
  [k: string]: unknown;
}

export interface AgentResult {
  root_turn_id: string;
  request_turn_id: string;
  task_id: string | null;
  agent_run_id: string | null;
  status: AgentResultStatus;
  final_response: string | null;
  executed_tool_call_ids: string[];
  step_count: number;
  warnings: string[];
  /** 自由结构：task_lifecycle / long_term_memory_update / tool_stage_traces /
   *  turn_elapsed_seconds / event_sequence / retrieval(详见 V1) */
  trace_metadata: Record<string, unknown>;
  user_response: string;
  developer_details: DeveloperDetails;
  pending_action?: PendingApproval | null;
  error_info?: TurnErrorInfo | null;
}

/* ============================================================
 * 3. 长期记忆（bridge 新增：PersonalAgentApp 方法直出）
 * ============================================================ */

export interface ProvenanceRef {
  provenance_id?: string;
  source_event_id?: string;
  session_id?: string | null;
  message_id?: string | null;
  source_type?: string;
  observed_at?: number | null;
  authority?: ProvenanceAuthority;
  content_hash?: string | null;
  /** 受控摘录（长度由 PROVENANCE_EXCERPT_MAX_CHARS 限制，不是原文全文） */
  controlled_excerpt?: string | null;
}

export interface MemoryRevision {
  revision_id: string;
  /** 第几版：UI 显示为 v{revision_no} */
  revision_no: number;
  content: string;
  semantic_validity: SemanticValidity;
  created_at: number;
  event_time?: number | null;
  valid_from?: number | null;
  valid_until?: number | null;
  /** 0–1 */
  confidence: number;
  change_kind: ChangeKind;
  supersedes_revision_id?: string | null;
  semantic_metadata?: {
    persons?: string[];
    entities?: string[];
    locations?: string[];
    topics?: string[];
    keywords?: string[];
    scopes?: string[];
    conditions?: string[];
  };
}

export interface GraphRelation {
  target_id: string;
  relation_type: RelationType;
  weight: number;
}

export interface MemoryItem {
  memory_id: string;
  memory_type: MemoryType;
  granularity: Granularity;
  created_at: number;
  updated_at: number;
  current_revision_id: string | null;
  /** 0–1 */
  importance_score: number;
  access_count: number;
  last_accessed: number;
  decay_strength: number;
  retention_disposition: RetentionDisposition;
  is_pinned: boolean;
  enable_time_decay: boolean;
  graph_relations: GraphRelation[];
  /** 当前语义快照；回收站条目可能缺失 */
  current_revision: MemoryRevision | null;
  /** 兼容投影，不是权威来源；UI 以 current_revision 为准 */
  content?: string;
  status?: ActiveStatus;
}

export interface MemoryUpdateStatus {
  mode: string;
  tool_name: string;
  /** ingestion journal 待重试事件数 */
  pending_events: number;
  update_is_agent_selected: boolean;
  threshold_trigger_enabled: boolean;
  time_trigger_enabled: boolean;
}

/* ============================================================
 * 4. Task 3：台账 / 检查点 / 摘要
 * ============================================================ */

export interface EvidenceRef {
  bundle_id?: string;
  memory_id?: string;
  revision_id?: string;
  evidence_item_id?: string;
}

export interface LedgerEntry {
  entry_id: string;
  task_id: string;
  kind: EntryKind;
  content: string;
  status: EntryStatus;
  created_at: number;
  updated_at: number;
  priority: Priority;
  source_kind: SourceKind;
  basis: ProposalBasis;
  source_ref?: string | null;
  evidence_refs?: EvidenceRef[];
  supersedes_entry_id?: string | null;
}

export interface ContinuationCheckpoint {
  task_id: string;
  checkpoint_id: string;
  last_processed_event?: string | null;
  goal_entry_ids?: string[];
  current_step?: string | null;
  continuation_note?: string | null;
  critical_constraint_ids?: string[];
  open_question_ids?: string[];
  decision_ids?: string[];
  evidence_refs?: EvidenceRef[];
  created_at: number;
  focus?: string | null;
  next_action?: string | null;
  world_state?: string | null;
}

export interface TaskSummary {
  task_id: string | null;
  status: TaskStatus | null;
  summary?: string | null;
  goals: string[];
  constraints: string[];
  decisions: string[];
  progress: string[];
  open_questions: string[];
  checkpoint: Partial<ContinuationCheckpoint> | null;
  workspace?: unknown;
}

/** 闭环状态（get_task_lifecycle_status） */
export interface TaskLifecycleStatus {
  enabled: boolean;
  tasks: TaskCycleView[];
  jobs: LifecycleJobView[];
  usage?: { model_calls: number; charged_units: number };
  [k: string]: unknown;
}

export interface TaskCycleView {
  task_id: string;
  state: TaskCycleState;
  /** 完成后来源是否仍有效 */
  current_result_validity?: ResultValidity;
  current_experience_state?: ExperienceState | null;
  completion_id?: string | null;
  open_questions?: string[];
  remaining_requirements?: string[];
  [k: string]: unknown;
}

export interface LifecycleJobView {
  job_id: string;
  kind: LifecycleJobKind;
  task_id: string;
  state: LifecycleJobState;
  result?: unknown;
  [k: string]: unknown;
}

/* ============================================================
 * 5. Task 2：证据包（V1 待验证字段，先按 schema 定义）
 * ============================================================ */

export interface ScoreBreakdown {
  content_bm25?: number;
  metadata_overlap?: number;
  dense_similarity?: number;
  /** RRF(d) = Σ 1/(60 + rank_i(d)) */
  fusion_rrf?: number;
  [k: string]: number | undefined;
}

export interface DistilledEvidence {
  memory_id: string;
  revision_id: string;
  text: string;
  original_rank: number;
  original_score: number;
  score_breakdown?: ScoreBreakdown;
  /** 形如 "provenance:<id>"，不含来源正文 */
  provenance_refs: string[];
  applicability_status: ApplicabilityStatus;
  applicability_decision: ApplicabilityDecision;
  applicability_reasons: ApplicabilityReasonCode[];
  conflict_status: ConflictStatus;
  conflict_key?: string | null;
  conflict_losing_memory_ids?: string[];
  source_trace?: Record<string, unknown>;
  /** 是否来自"当前有效"快照 */
  current_safe: boolean;
}

export interface EvidenceSufficiency {
  status: EvidenceSufficiencyStatus;
  reason_codes: string[];
  missing_aspects: string[];
  uncertainties: string[];
  unresolved_conflicts: string[];
}

export interface EvidenceBundle {
  schema_version: string; // "evidence_bundle_v1"
  request_id: string;
  query: string;
  selected: DistilledEvidence[];
  sufficiency: EvidenceSufficiency;
  /** 形如 "mismatch:mem_xxx"、"conflict_loser:..." */
  rejected_or_omitted: string[];
  warnings: string[];
  pipeline_identity: string[];
  retrieval_trace?: Record<string, unknown>;
  budgets?: { max_items: number; max_chars_per_item: number; max_total_chars: number };
}

/* ============================================================
 * 6. 协作 DAG / artifact / handoff / 预算（V3 待验证列名）
 * ============================================================ */

export interface SubtaskSpec {
  key: string;
  objective: string;
  /** 上游 key 列表 —— DAG 的边 */
  dependencies: string[];
  tool_names: string[];
  completion_criteria: string;
}

export interface TaskNodeView {
  node_key: string;
  node_id?: string;
  status: TaskNodeStatus;
  artifact_id?: string | null;
  attempt_id?: string | null;
  spec?: SubtaskSpec;
  /** 前端派生：dependencies 中有节点失效/未核验时置灰 */
  blocked_by?: string[];
}

export interface ArtifactView {
  artifact_id: string;
  run_id?: string;
  stage_key?: string;
  payload_hash?: string;
  verification_status: VerificationStatus;
  /** 撤销后 verification_status 不回退，只能看节点是否 STALE */
  revoked?: boolean;
}

export interface HandoffView {
  message_id: string;
  run_id: string;
  stage_key: string;
  sender: CollaborationRole | string;
  receiver: CollaborationRole | string;
  payload_hash: string;
  payload?: Record<string, unknown>;
}

export interface ModelCallView {
  call_id: string;
  role?: CollaborationRole | string;
  operation?: string;
  status: ModelCallStatus;
  charged_units: number;
  usage_json?: Record<string, unknown>;
  elapsed?: number;
}

export interface CollaborationGraphView {
  graph_id: string;
  run_id: string;
  task_id: string;
  run_status: RunStatus;
  plan_hash?: string;
  nodes: TaskNodeView[];
  artifacts?: ArtifactView[];
  handoffs?: HandoffView[];
  model_calls?: ModelCallView[];
  budget?: { model_calls: number; charged_units: number };
}

/* ============================================================
 * 7. 修复
 * ============================================================ */

export interface RepairQueueRow {
  event_id: string;
  run_id: string;
  status: RepairQueueStatus;
  error_type: string | null;
  updated: number;
}

export interface RepairPlanView {
  repair_id: string;
  source_run_id: string;
  event_id: string;
  /** 计划里的 execution_status 恒为 NOT_STARTED；真实状态以 repair_status/队列为准 */
  execution_status: 'NOT_STARTED';
  execution_contract_validation?: ExecutionContractValidation;
  nodes: { spec: SubtaskSpec; action: RepairAction; reason: RepairReason }[];
  reuse_candidates?: Record<string, unknown>[];
  /** 受影响集合：S = 直接失效，Desc(S) = 后继闭包，A = S ∪ Desc(S) */
  affected?: { direct: string[]; descendants: string[]; all: string[] };
}

export interface GraphSummary {
  graph_id: string;
  run_id: string;
  task_id: string;
  run_status: RunStatus;
  nodes: { node_key: string; status: TaskNodeStatus; artifact_id: string | null }[];
}

/* ============================================================
 * 8. 程序性经验
 * ============================================================ */

export interface ExperiencePayload {
  schema_version: string;
  decision: 'PROPOSE' | 'ABSTAIN';
  task_type: string;
  preconditions: string[];
  action_summary: string;
  observed_outcome: string;
  artifact_ids: string[];
  applicability_scope: string;
  reason?: string;
  source_run_id: string;
  source_frontier_hash: string;
  environment_hash: string;
  revision_of?: string | null;
}

export interface ExperienceAuditEvent {
  event_id: string;
  kind: ExperienceEventKind;
  state?: ExperienceState;
  review?: { verdict: ExperienceVerdict; artifact_ids: string[]; reason?: string };
  replacement?: string;
  source_event?: string;
  consumer_run_id?: string;
  outcome?: 'SUCCEEDED' | 'FAILED';
  /** 恒为 'UNKNOWN'：系统不声称因果贡献 */
  causal_contribution?: 'UNKNOWN';
}

export interface Experience {
  experience_id: string;
  /** 内容哈希；修订即新 ID */
  revision_id: string;
  state: ExperienceState;
  payload: ExperiencePayload;
  events: ExperienceAuditEvent[];
}

/* ============================================================
 * 9. 审批 / 工具 / 运行环境
 * ============================================================ */

export interface PendingApproval {
  pending_action_id: string;
  agent_run_id: string;
  task_id: string;
  agent_step_id?: string;
  tool_call_id: string;
  tool_name: string;
  validated_arguments: Record<string, unknown>;
  effect: 'SIDE_EFFECT';
  created_at: number;
  arguments?: Record<string, unknown>;
  arguments_truncated?: boolean;
}

export interface ToolSpec {
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
  effect: ToolEffect;
  idempotencySupport: IdempotencySupport;
  returnsUserFacingResult: boolean;
}

export interface ContextDiagnostics {
  model: string;
  context_window_tokens: number;
  context_window_source: string;
  usable_tokens: number;
  reserve_tokens: number;
  compaction_ratio: number;
  keep_recent_tokens: number;
  transcript_message_count: number;
  projected_tail_message_count: number;
  estimated_projected_tokens: number;
  compaction_id: string | null;
  summarized_message_count: number;
  compaction_source: string | null;
  compaction_degraded: boolean;
  compaction_degraded_reason: string | null;
  provider_last_usage: Record<string, unknown> | null;
}

/* ============================================================
 * 10. Bridge HTTP 契约（新增后端，前端唯一依赖）
 * ============================================================ */

export interface MetaInfo {
  /** 后端未配置 API Key（降级模式）：除状态/Key 管理外，其它接口会返回 503 */
  needsApiKey?: boolean;
  protocolVersion: string;
  /** 'single' | 'multi_fixed'；只有 multi_fixed 才有 DAG/修复/经验/闭环 */
  executionMode: 'single' | 'multi_fixed';
  agentHome: string;
  capabilities: {
    collaborationRepair: boolean;
    proceduralExperiences: boolean;
    taskLifecycle: boolean;
    eventReplay: boolean;
    contextDiagnostics: boolean;
    approvals: boolean;
    toolCatalog: boolean;
    interrupt: false;
  };
}

export interface StatusSnapshot {
  taskLifecycle: TaskLifecycleStatus;
  status: TaskSummary;
  memory: MemoryUpdateStatus;
  unfinishedTurns: UnfinishedTurn[];
  pendingApprovals: PendingApproval[];
}

/** 长任务提交后的句柄：POST 立即返回，SSE/轮询取结果 */
export interface JobHandle {
  jobId: string;
  kind: 'turn' | 'turnResume' | 'repair' | 'drain' | 'compact' | 'confirmation';
  threadId?: string;
  turnId?: string;
  acceptedAt: number;
}

export interface JobResult {
  jobId: string;
  state: 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED';
  error?: { code: string; message: string };
  result?: AgentResult;
}

/* ============================================================
 * 11. DataSource：唯一数据入口（HttpSource 真实 / FixtureSource 回放）
 * ============================================================ */

export interface ApiKeyStatus {
  /** 是否已配置 Key（不回传任何 Key 内容） */
  configured: boolean
  /** 探活结果：true=可用，false=被拒绝，null=无法校验（网络/未知） */
  valid: boolean | null
  /** 探活失败原因（已中文化的短句由前端再加解释） */
  probeError: string | null
  /** 配置文件最后更新时间（秒级时间戳） */
  updatedAt: number | null
  /** 当前后端执行模式 */
  mode: string
  /** 当前进程是否已装载可用凭据 */
  appReady: boolean
  /** 配置文件路径（不含敏感内容） */
  configPath: string
  /** 本次可用性结果是否来自缓存（30s 内复用，避免每次打开页面都等外网） */
  probeCached?: boolean
  probeAgeSeconds?: number
  /** 更新后是否已重建 Agent 使新 Key 生效 */
  appReloaded?: boolean
  reloadError?: string | null
  /** 删除操作返回 */
  deleted?: boolean
  note?: string
}

export interface ThreadExportMessage {
  turnId: string
  role: 'USER' | 'ASSISTANT' | 'TOOL' | 'SYSTEM'
  content: string
  createdAt: number
}

export interface ThreadExportPayload {
  format: 'mapas.session/v1'
  exportedAt: number
  thread: {
    title: string
    activeTaskId: string | null
    createdAt: number
    updatedAt: number
    status: string
  }
  messages: ThreadExportMessage[]
}

export interface ThreadImportResult {
  sessionId: string
  title: string
  messageCount: number
}

export interface CollaborationNode {
  node_key: string
  status: string | null
  node_id: string
  artifact_id: string | null
}

export interface CollaborationRunSummary {
  graph_id: string
  run_id: string
  task_id: string | null
  run_status: string
  session_id?: string
  turn_id?: string
  started?: number
  nodes: CollaborationNode[]
}

export interface CollaborationSubtask {
  key: string
  objective?: string
  completion_criteria?: string
  dependencies?: string[]
  tool_names?: string[]
}

export interface CollaborationStage {
  stage_key: string
  role: string
  status: string
}

export interface CollaborationArtifact {
  artifact_id: string
  stage_key: string
  verification_status: string
}

export interface CollaborationModelCall {
  call_id: string
  role: string
  operation: string
  status: string
  charged_units: number
  usage: Record<string, number>
  /**
   * 计费口径：
   * - `provider`：Provider 回传了真实 usage
   * - `reservation`：没有回传 usage，按预留额度估算（后端 53484dc 起显式标记）
   */
  usage_source?: 'provider' | 'reservation' | null
  elapsed: number | null
}

export interface CollaborationUsageByRole {
  role: string
  calls: number
  failed: number
  chargedUnits: number
  inputTokens: number
  outputTokens: number
  /** 其中按预留额度估算的次数（无 provider usage） */
  estimatedCalls?: number
}

export interface CollaborationRunDetail {
  runId: string
  sessionId: string
  turnId: string
  taskId: string | null
  status: string
  started: number
  limits: Record<string, number>
  graphId: string
  plan: { schema_version?: number; subtasks: CollaborationSubtask[] }
  nodes: CollaborationNode[]
  roles: CollaborationStage[]
  artifacts: CollaborationArtifact[]
  handoffs: { stage_key: string; sender: string; receiver: string }[]
  modelCalls: {
    total: number
    failed: number
    chargedUnits: number
    failedWithEmptyUsage: number
    byRole: CollaborationUsageByRole[]
    list: CollaborationModelCall[]
  }
  /** 后端 usage() 的预算口径拆分（53484dc 起提供） */
  usage?: {
    modelCalls: number | null
    chargedTokenUnits: number | null
    providerReportedTokenUnits: number | null
    estimatedTokenUnits: number | null
  }
  result: {
    status: string | null
    errorInfo: Record<string, unknown> | null
    warnings: string[]
    finalResponse: string
    executedToolCalls: string[]
    verifiedSubtasks: string[]
    sourceReads: unknown[]
    collaborationUsage: Record<string, unknown> | null
    executionMode: string | null
  }
}

export interface DataSource {
  readonly kind: 'http' | 'fixture';
  readonly writable: boolean;

  meta(): Promise<MetaInfo>
  /** 桥接是否要求访问口令（免鉴权探针，不返回任何数据） */
  checkAuth(): Promise<{ tokenRequired: boolean; protocolVersion: string }>
  /** 本机浏览器是否已存访问口令 */
  hasCredential(): boolean;
  /** 协作运行列表（默认当前会话；all=true 时跨会话） */
  listCollaborationRuns(all?: boolean): Promise<CollaborationRunSummary[]>
  /** 单次协作运行的完整只读快照 */
  getCollaborationRun(runId: string): Promise<CollaborationRunDetail>

  /** 读取 API Key 状态（只写不读：响应里没有 Key 内容） */
  getApiKeyStatus(refresh?: boolean): Promise<ApiKeyStatus>
  /** 覆盖写入新的 API Key（只写，不回显） */
  updateApiKey(key: string): Promise<ApiKeyStatus>
  /** 删除配置文件中的 API Key */
  deleteApiKey(): Promise<ApiKeyStatus>

  /** 切换后端执行模式（single ⇄ multi_fixed）。回放模式不支持。 */
  switchMode(mode: 'single' | 'multi_fixed'): Promise<MetaInfo>;

  // chat
  listThreads(): Promise<Thread[]>;
  createThread(title?: string): Promise<Thread>;
  resumeThread(threadId: string): Promise<Thread>;
  /** 导出会话为可移植 JSON（消息 + 元信息） */
  exportThread(threadId: string): Promise<ThreadExportPayload>
  /** 导入会话（生成新会话，不覆盖已有） */
  importThread(payload: ThreadExportPayload): Promise<ThreadImportResult>
  /** 重命名会话（后端无正式接口，由桥接层受控写库实现） */
  renameThread(threadId: string, title: string): Promise<Thread>;
  /** 删除会话（连同其消息与压缩记录） */
  deleteThread(threadId: string): Promise<{ ok: boolean; deletedId: string; wasActive: boolean }>;
  readMessages(threadId: string): Promise<Message[]>;
  listUnfinishedTurns(threadId: string): Promise<UnfinishedTurn[]>;
  startTurn(threadId: string, input: string): Promise<JobHandle>;
  resumeTurn(threadId: string, turnId: string): Promise<JobHandle>;
  getJob(jobId: string): Promise<JobResult>;

  // events（SSE；fixture 模式为一次性回放）
  streamEvents(cb: (e: AgentEvent) => void, opts?: { afterSequence?: number; threadId?: string }): () => void;
  readEvents(opts: { afterSequence?: number; threadId?: string; turnId?: string; limit?: number }): Promise<{ events: AgentEvent[]; nextSequence: number }>;

  // 诊断
  getStatus(): Promise<StatusSnapshot>;
  getContext(): Promise<ContextDiagnostics>;
  compact(): Promise<JobHandle>;
  getTools(): Promise<ToolSpec[]>;
  getMcpStatus(): Promise<Record<string, unknown>>;
  getWorkspace(): Promise<Record<string, unknown>>;
  getStorage(): Promise<Record<string, string>>;

  // memory
  listMemories(): Promise<MemoryItem[]>;
  getMemory(id: string): Promise<MemoryItem | null>;
  getMemoryHistory(id: string): Promise<MemoryRevision[]>;
  getMemoryProvenance(id: string): Promise<ProvenanceRef[]>;
  listRecycleBin(): Promise<MemoryItem[]>;
  remember(content: string): Promise<Record<string, unknown>>;
  forgetMemory(id: string): Promise<boolean>;
  restoreMemory(id: string): Promise<boolean>;
  getProfile(): Promise<Record<string, unknown>>;

  // tasks
  listTasks(): Promise<Record<string, unknown>[]>;
  createTask(title?: string): Promise<string>;
  switchTask(taskId: string): Promise<boolean>;
  getTaskSummary(): Promise<TaskSummary>;
  pauseTask(note?: string): Promise<void>;
  resumeTask(): Promise<void>;
  completeTask(): Promise<void>;
  cancelTask(): Promise<void>;
  getTaskLifecycle(): Promise<TaskLifecycleStatus>;
  drainTaskLifecycle(): Promise<Record<string, unknown>>;

  // retrieval（V1 已确认：只有聚合字段，逐条证据未持久化）
  getTurnTrace(turnId: string): Promise<Record<string, unknown>>;
  /** 已落盘的检索 trace 列表（降级证据视图；字段见 README §2.10） */
  listTurnTraces(limit?: number): Promise<Record<string, unknown>[]>;

  // collaboration
  listGraphs(): Promise<GraphSummary[]>;
  getGraph(runId: string): Promise<CollaborationGraphView>;

  // governance
  listApprovals(threadId?: string): Promise<PendingApproval[]>;
  decideApproval(pendingActionId: string, decision: ConfirmationDecision): Promise<JobHandle>;
  listRepairs(): Promise<RepairQueueRow[]>;
  getRepairPlan(repairId: string): Promise<RepairPlanView>;
  runRepair(runId: string, eventId: string): Promise<JobHandle>;
  revokeAndRepair(runId: string, artifactId: string, eventId: string): Promise<JobHandle>;
  drainRepairs(): Promise<Record<string, unknown>>;
  listExperiences(): Promise<Experience[]>;
  learnExperience(runId: string, revisionOf?: string): Promise<Experience | null>;
  reviewExperience(experienceId: string, confirmation: string): Promise<Experience>;
  revokeExperience(experienceId: string, eventId: string): Promise<Experience>;
  feedbackExperience(experienceId: string, runId: string, eventId: string): Promise<Experience>;
}
