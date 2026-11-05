/**
 * 页面注册表：**唯一**定义"前端有哪几屏、每屏干什么、数据从哪来"的地方。
 *
 * 为什么单独抽出来：
 * 1. 侧栏、路由、检查器、空态提示都从这里取，避免四处硬编码导致不一致；
 * 2. 每屏都显式写出依赖的后端方法，方便对照 `docs/frontend-contract.md` §2 的映射表；
 * 3. 显式标注就绪度，让"现在没数据"变成可解释的状态，而不是一团空白。
 */

export const VIEW_IDS = [
  'chat',
  'memory',
  'retrieval',
  'tasks',
  'collaboration',
  'governance',
  'context',
] as const

export type ViewId = (typeof VIEW_IDS)[number]

/** 本屏"真实数据来源"的成熟度 */
export type Readiness =
  /** 界面已实现且接入真实数据源（P2 起逐屏进入此状态） */
  | 'ready'
  /** 后端已有确定方法，桥接进程起来就能显示真数据 */
  | 'waiting-bridge'
  /** 依赖待验证项（如 V1：检索证据字段是否被持久化），验证前不承诺能显示什么 */
  | 'blocked-v1'

export interface PageMeta {
  id: ViewId
  /** 侧栏文字（两字，保证侧栏整齐） */
  navLabel: string
  /** 页面标题 */
  title: string
  /** 一句话作用（页面副标题） */
  subtitle: string
  /** 这个界面解决什么问题：为什么需要它 */
  purpose: string
  /** 本屏将展示的内容，P2–P4 逐项落地 */
  willShow: string[]
  /** 依赖的后端能力（真实方法名，便于对照契约表） */
  upstream: string[]
  readiness: Readiness
}

export const PAGES: readonly PageMeta[] = [
  {
    id: 'chat',
    navLabel: '对话',
    title: '对话工作台',
    subtitle: '一轮对话到底发生了什么：阶段、工具、审批、最终回答',
    purpose:
      '这是整个产品的门面。MAPAS 的每一轮不只是"你说一句、模型回一句"，中间还有任务绑定、证据检索、工具调用、可能的审批等待。把这些阶段如实摆出来，用户才知道 Agent 在做什么、卡在哪一步。',
    willShow: [
      '消息时间线（用户 / 助手 / 工具回执）',
      '阶段卡：任务绑定 → Task 3 更新 → Task 2 取证 → 上下文组装',
      '工具调用卡：工具名、参数、回执 ID、耗时、状态',
      '审批卡：副作用工具的待确认动作（确认 / 拒绝）',
      '最终回答 + 耗时 / 模型调用数 / 安全警告',
      '未完成轮次提示与"继续执行"入口',
    ],
    upstream: ['process_chat_turn', 'list_events', 'list_pending_approvals', 'process_confirmation'],
    readiness: 'ready',
  },
  {
    id: 'memory',
    navLabel: '记忆',
    title: '长期记忆库',
    subtitle: '记忆有身份、有版本、有来源，删除是可恢复的',
    purpose:
      '普通聊天记录一改就覆盖，无法回答"这条信息什么时候改过、依据是什么、现在还算不算数"。Task 1 把一条记忆拆成稳定身份 + 版本序列 + 来源引用，这一屏就是让这套机制可见。',
    willShow: [
      '记忆卡片列表（类型 / 重要性 / 最近访问 / 是否置顶）',
      '当前有效版本徽章 + 版本时间轴（ADD / UPDATE / CORRECT / NOOP）',
      '来源溯源：来自哪一轮、哪条消息、权威等级（显式用户 / 推断 / 工具 / 未知）',
      '回收站与恢复（删除是可恢复的，不是彻底擦除）',
      '显式写入记忆 / 待重试的 ingestion 事件数',
    ],
    upstream: [
      'list_memories',
      'get_memory_detail',
      'get_memory_history',
      'get_memory_provenance',
      'list_recycled_memories',
      'forget_memory',
      'restore_memory',
    ],
    readiness: 'ready',
  },
  {
    id: 'retrieval',
    navLabel: '证据',
    title: '检索证据包',
    subtitle: '相关 ≠ 可用：四态适用性、冲突与证据充分性',
    purpose:
      '检索到语义相关的摘要，不代表它能用于当前任务。这一屏要呈现"为什么这条被保留、那条被排除"：召回排名与分数、适用性四态、冲突、去重与篇幅预算、以及够不够支撑当前子任务。',
    willShow: [
      '候选排行（原始排名 / 分数 / BM25 / Dense / RRF 分解）',
      '适用性四态色块：MATCH 适用 / MISMATCH 不适用 / UNKNOWN 信息不足 / AMBIGUOUS 存在歧义',
      '冲突标记与"被排除原因"（如 mismatch、conflict_loser）',
      '证据充分性：SUFFICIENT / PARTIAL / INSUFFICIENT',
      '来源与版本引用（provenance → 可跳转到记忆库对应版本）',
    ],
    upstream: ['get_turn_trace', 'EvidenceBundle（trace_metadata）'],
    readiness: 'ready',
  },
  {
    id: 'tasks',
    navLabel: '任务',
    title: '任务台账',
    subtitle: '目标、约束、决定、进展与检查点：与对话解耦',
    purpose:
      '对话会被压缩、会被遗忘，但任务的目标和约束不能丢。Task 3 用事件驱动的台账维护持续状态，并保存"下一步做什么"的检查点，这一屏让跨会话的连续性看得见。',
    willShow: [
      '台账条目：目标 / 约束 / 决定 / 进展 / 待解决问题（含优先级与来源）',
      '检查点：当前焦点、下一步动作、世界状态',
      '任务状态操作（暂停 / 恢复 / 完成 / 取消）',
      '任务级闭环：ASSESS_PENDING / REPAIR_PENDING / WAITING_USER / COMPLETED',
      '完成后的结果有效性：VALID / STALE / NOT_CHECKED_WORKSPACE（三者含义不同）',
      '待处理作业与"继续推进"入口',
    ],
    upstream: [
      'get_task_status_summary',
      'list_tasks',
      'create_new_task',
      'switch_task',
      'pause_current_task',
      'complete_current_task',
      'get_task_lifecycle_status',
      'drain_task_lifecycle',
    ],
    readiness: 'ready',
  },
  {
    id: 'collaboration',
    navLabel: '协作',
    title: '协作拓扑',
    subtitle: 'Planner / Memory / Executor / Verifier 的分工、依赖与核验',
    purpose:
      '多 Agent 的价值不在于"多"，而在于责任可追踪：哪个子任务由谁做、依赖谁、产物是否通过核验、预算花了多少。这一屏把 DAG 与交接记录画出来。',
    willShow: [
      '子任务 DAG（拓扑排序；边 = 依赖）',
      '节点状态 7 态配色：PENDING / RUNNING / PRODUCED / VERIFIED / FAILED / BLOCKED / STALE',
      '每个节点的目标、可用工具、完成标准',
      '产物（artifact）：hash、PRODUCED / VERIFIED、是否被撤销',
      '交接记录：发送角色 → 接收角色、payload hash',
      '共享预算仪表：模型调用数、核算单位、耗时',
    ],
    upstream: ['list_session_graphs', 'task_graphs.plan_json', 'task_nodes.spec_json', 'model_calls'],
    readiness: 'ready',
  },
  {
    id: 'governance',
    navLabel: '治理',
    title: '治理中心',
    subtitle: '修复、经验、审批：把"失败也真实"做成可见的界面',
    purpose:
      '这一屏是整个系统最"诚实"的部分：来源变化后哪些成果失效、影响范围多大、只重做了哪一部分；经验从候选到准入到隔离到撤销的完整状态机；以及所有需要人拍板的副作用操作。',
    willShow: [
      '修复队列（PENDING / SUCCEEDED / FAILED）与影响范围 S / Desc(S) / A',
      '修复计划：哪些节点重跑、哪些可复用及复用理由',
      '经验 7 态机：CANDIDATE / ACTIVE / QUARANTINED / NEEDS_CONFIRMATION / STALE / REVOKED / SUPERSEDED',
      '经验适用性筛选：APPLICABLE / INAPPLICABLE / UNCERTAIN（因果贡献恒为 UNKNOWN）',
      '审批列表与确认/拒绝',
      '工具目录（effect / 幂等能力）、MCP 状态、工作区范围、存储布局',
    ],
    upstream: [
      'list_repairs',
      'get_repair_plan',
      'repair_collaboration',
      'list_experiences',
      'process_confirmation',
      'get_tool_catalog',
      'get_workspace_status',
    ],
    readiness: 'ready',
  },
  {
    id: 'context',
    navLabel: '上下文',
    title: '上下文仪表盘',
    subtitle: '预算是真的在管：窗口、投影、压缩检查点与降级',
    purpose:
      '长程任务一定会撞上上下文上限。系统不是等它溢出，而是按预算保留近期消息、压缩旧上下文并留下可追踪摘要。这一屏展示"这一次调用到底准备交给模型多少内容"。',
    willShow: [
      '模型与窗口：上下文窗口、可用、预留、压缩比例',
      '当前投影：原始消息数 vs 投影消息数、估算 token',
      '压缩检查点：compaction id、来源、是否降级及原因',
      'Provider 最近一次用量',
      '手动压缩入口（只压缩会话，不改长期记忆）',
    ],
    upstream: ['get_conversation_context_status', 'compact_current_session'],
    readiness: 'ready',
  },
]

export const PAGE_BY_ID: Record<ViewId, PageMeta> = PAGES.reduce(
  (acc, page) => {
    acc[page.id] = page
    return acc
  },
  {} as Record<ViewId, PageMeta>,
)

export function isViewId(value: string): value is ViewId {
  return (VIEW_IDS as readonly string[]).includes(value)
}
