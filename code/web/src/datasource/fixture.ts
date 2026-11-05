import type { AgentEvent, CollaborationGraphView, ContextDiagnostics, DataSource, Experience, GraphSummary, JobHandle, JobResult, MemoryItem, MemoryRevision, Message, MetaInfo, PendingApproval, ProvenanceRef, RepairPlanView, RepairQueueRow, StatusSnapshot, TaskLifecycleStatus, TaskSummary, Thread, ToolSpec, UnfinishedTurn, ApiKeyStatus, CollaborationRunDetail, CollaborationRunSummary, ThreadExportPayload, ThreadImportResult } from '@/types'
import { DataSourceError } from './errors'

/**
 * 录制回放数据源（只读）。
 *
 * 为什么要有它：演示/截图时不一定能连上桥接进程；但**绝不允许手编假数据**。
 * 这里的 fixtures 必须是「真实跑过一次后录下来的响应」，页面代码对两个数据源完全无感。
 *
 * 录制方式（P5 实现）：对 bridge 的每个 GET 请求把响应体原样存成
 * `src/datasource/fixtures/<key>.json`，key 见下方注释。
 *
 * 找不到录制时抛 `NO_FIXTURE_RECORDED` —— 界面会显示"未录制"，而不是空列表。
 */
const modules = import.meta.glob('./fixtures/*.json', {
  eager: true,
  import: 'default',
}) as Record<string, unknown>

function load(key: string): unknown | null {
  const path = `./fixtures/${key}.json`
  return Object.prototype.hasOwnProperty.call(modules, path) ? modules[path] : null
}

export class FixtureSource implements DataSource {
  readonly kind = 'fixture' as const
  /** 回放永远是只读：写操作会明确报错，不会伪造成功 */
  readonly writable = false

  private read<T>(key: string, what: string): T {
    const value = load(key)
    if (value === null) {
      throw new DataSourceError(
        'NO_FIXTURE_RECORDED',
        `回放模式缺少录制：${key}.json`,
        `需要先按 P5 的录制流程抓取「${what}」的真实响应`,
      )
    }
    return value as T
  }

  private offline(operation: string): never {
    throw new DataSourceError(
      'OFFLINE_FIXTURE_READ_ONLY',
      `回放模式是只读的，无法执行「${operation}」`,
      '请切换到 HttpSource（启动 web_bridge.py）后重试',
    )
  }

  // ---------- 读：有录制就返回，没有就明确报错 ----------
  meta(): Promise<MetaInfo> {
    return Promise.resolve(this.read<MetaInfo>('meta', '系统元信息'))
  }

  checkAuth(): Promise<{ tokenRequired: boolean; protocolVersion: string }> {
    return Promise.resolve({ tokenRequired: false, protocolVersion: 'fixture' })
  }

  hasCredential(): boolean {
    return false
  }

  listCollaborationRuns(): Promise<CollaborationRunSummary[]> {
    return this.offline('协作运行列表')
  }

  getCollaborationRun(): Promise<CollaborationRunDetail> {
    return this.offline('协作运行详情')
  }

  getApiKeyStatus(): Promise<ApiKeyStatus> {
    return Promise.reject(
      new DataSourceError(
        'OFFLINE_FIXTURE_READ_ONLY',
        '回放模式不接触真实凭据',
        'API Key 管理只在桥接（http 数据源）可用',
      ),
    )
  }

  updateApiKey(): Promise<ApiKeyStatus> {
    return this.offline('更新 API Key')
  }

  deleteApiKey(): Promise<ApiKeyStatus> {
    return this.offline('删除 API Key')
  }

  switchMode(): Promise<MetaInfo> {
    return Promise.reject(
      new DataSourceError(
        'OFFLINE_FIXTURE_READ_ONLY',
        '回放模式是只读的，不能切换后端执行模式',
        '请用 http 数据源（启动 web_bridge.py）后切换',
      ),
    )
  }

  listThreads(): Promise<Thread[]> {
    return Promise.resolve(this.read<Thread[]>('threads', '会话列表'))
  }

  resumeThread(threadId: string): Promise<Thread> {
    return Promise.resolve(this.read<Thread>(`thread.${threadId}`, `会话 ${threadId}`))
  }

  exportThread(): Promise<ThreadExportPayload> {
    return this.offline('导出会话')
  }

  importThread(): Promise<ThreadImportResult> {
    return this.offline('导入会话')
  }

  renameThread(): Promise<Thread> {
    return this.offline('重命名会话')
  }

  deleteThread(): Promise<{ ok: boolean; deletedId: string; wasActive: boolean }> {
    return this.offline('删除会话')
  }

  readMessages(threadId: string): Promise<Message[]> {
    return Promise.resolve(this.read<Message[]>(`messages.${threadId}`, '会话消息'))
  }

  listUnfinishedTurns(threadId: string): Promise<UnfinishedTurn[]> {
    return Promise.resolve(
      this.read<UnfinishedTurn[]>(`unfinished.${threadId}`, '未完成轮次'),
    )
  }

  readEvents(opts: {
    afterSequence?: number
    threadId?: string
    turnId?: string
    limit?: number
  }): Promise<{ events: AgentEvent[]; nextSequence: number }> {
    const events = this.read<AgentEvent[]>(
      opts.threadId ? `events.${opts.threadId}` : 'events',
      '事件流',
    )
    const after = opts.afterSequence ?? 0
    const limit = opts.limit ?? 100
    const sliced = events.filter((event) => event.sequence > after).slice(0, limit)
    const nextSequence =
      sliced.length > 0 ? sliced[sliced.length - 1]!.sequence : after
    return Promise.resolve({ events: sliced, nextSequence })
  }

  streamEvents(): () => void {
    // 回放模式不做实时推送：页面应改用 readEvents 一次性读取
    return () => undefined
  }

  getStatus(): Promise<StatusSnapshot> {
    return Promise.resolve(this.read<StatusSnapshot>('status', '会话聚合状态'))
  }

  getContext(): Promise<ContextDiagnostics> {
    return Promise.resolve(this.read<ContextDiagnostics>('context', '上下文诊断'))
  }

  getTools(): Promise<ToolSpec[]> {
    return Promise.resolve(this.read<ToolSpec[]>('tools', '工具目录'))
  }

  getMcpStatus(): Promise<Record<string, unknown>> {
    return Promise.resolve(this.read<Record<string, unknown>>('mcp', 'MCP 状态'))
  }

  getWorkspace(): Promise<Record<string, unknown>> {
    return Promise.resolve(this.read<Record<string, unknown>>('workspace', '工作区范围'))
  }

  getStorage(): Promise<Record<string, string>> {
    return Promise.resolve(this.read<Record<string, string>>('storage', '存储布局'))
  }

  listMemories(): Promise<MemoryItem[]> {
    return Promise.resolve(this.read<MemoryItem[]>('memories', '长期记忆列表'))
  }

  getMemory(id: string): Promise<MemoryItem | null> {
    return Promise.resolve(this.read<MemoryItem>(`memory.${id}`, `记忆 ${id}`))
  }

  getMemoryHistory(id: string): Promise<MemoryRevision[]> {
    return Promise.resolve(
      this.read<MemoryRevision[]>(`memory-history.${id}`, `记忆 ${id} 的版本历史`),
    )
  }

  getMemoryProvenance(id: string): Promise<ProvenanceRef[]> {
    return Promise.resolve(
      this.read<ProvenanceRef[]>(`memory-provenance.${id}`, `记忆 ${id} 的来源`),
    )
  }

  listRecycleBin(): Promise<MemoryItem[]> {
    return Promise.resolve(this.read<MemoryItem[]>('recycle-bin', '回收站'))
  }

  getProfile(): Promise<Record<string, unknown>> {
    return Promise.resolve(this.read<Record<string, unknown>>('profile', '用户画像'))
  }

  listTasks(): Promise<Record<string, unknown>[]> {
    return Promise.resolve(this.read<Record<string, unknown>[]>('tasks', '任务列表'))
  }

  getTaskSummary(): Promise<TaskSummary> {
    return Promise.resolve(this.read<TaskSummary>('task-summary', '任务台账'))
  }

  getTaskLifecycle(): Promise<TaskLifecycleStatus> {
    return Promise.resolve(this.read<TaskLifecycleStatus>('task-lifecycle', '任务闭环状态'))
  }

  getTurnTrace(turnId: string): Promise<Record<string, unknown>> {
    return Promise.resolve(
      this.read<Record<string, unknown>>(`turn-trace.${turnId}`, `轮次 ${turnId} 的检索 trace`),
    )
  }

  listTurnTraces(): Promise<Record<string, unknown>[]> {
    return Promise.resolve(this.read<Record<string, unknown>[]>('traces', '检索 trace 列表'))
  }

  listGraphs(): Promise<GraphSummary[]> {
    return Promise.resolve(this.read<GraphSummary[]>('graphs', '协作图列表'))
  }

  getGraph(runId: string): Promise<CollaborationGraphView> {
    return Promise.resolve(
      this.read<CollaborationGraphView>(`graph.${runId}`, `协作图 ${runId}`),
    )
  }

  listApprovals(): Promise<PendingApproval[]> {
    return Promise.resolve(this.read<PendingApproval[]>('approvals', '待审批列表'))
  }

  listRepairs(): Promise<RepairQueueRow[]> {
    return Promise.resolve(this.read<RepairQueueRow[]>('repairs', '修复队列'))
  }

  getRepairPlan(repairId: string): Promise<RepairPlanView> {
    return Promise.resolve(
      this.read<RepairPlanView>(`repair.${repairId}`, `修复计划 ${repairId}`),
    )
  }

  listExperiences(): Promise<Experience[]> {
    return Promise.resolve(this.read<Experience[]>('experiences', '经验列表'))
  }

  getJob(): Promise<JobResult> {
    return Promise.reject(
      new DataSourceError('NOT_SUPPORTED', '回放模式没有后台作业，无法查询 jobId'),
    )
  }

  // ---------- 写：一律明确拒绝 ----------
  createThread(): Promise<Thread> {
    return this.offline('新建会话')
  }

  startTurn(): Promise<JobHandle> {
    return this.offline('发起一轮对话')
  }

  resumeTurn(): Promise<JobHandle> {
    return this.offline('恢复未完成轮次')
  }

  compact(): Promise<JobHandle> {
    return this.offline('压缩上下文')
  }

  remember(): Promise<Record<string, unknown>> {
    return this.offline('写入长期记忆')
  }

  forgetMemory(): Promise<boolean> {
    return this.offline('忘记记忆')
  }

  restoreMemory(): Promise<boolean> {
    return this.offline('恢复记忆')
  }

  createTask(): Promise<string> {
    return this.offline('新建任务')
  }

  switchTask(): Promise<boolean> {
    return this.offline('切换任务')
  }

  pauseTask(): Promise<void> {
    return this.offline('暂停任务')
  }

  resumeTask(): Promise<void> {
    return this.offline('恢复任务')
  }

  completeTask(): Promise<void> {
    return this.offline('完成并验收任务')
  }

  cancelTask(): Promise<void> {
    return this.offline('取消任务')
  }

  drainTaskLifecycle(): Promise<Record<string, unknown>> {
    return this.offline('推进任务闭环')
  }

  decideApproval(): Promise<JobHandle> {
    return this.offline('确认/拒绝审批')
  }

  runRepair(): Promise<JobHandle> {
    return this.offline('执行修复')
  }

  revokeAndRepair(): Promise<JobHandle> {
    return this.offline('撤销成果并修复')
  }

  drainRepairs(): Promise<Record<string, unknown>> {
    return this.offline('批量处理修复队列')
  }

  learnExperience(): Promise<Experience | null> {
    return this.offline('提炼经验')
  }

  reviewExperience(): Promise<Experience> {
    return this.offline('复核经验')
  }

  revokeExperience(): Promise<Experience> {
    return this.offline('撤销经验')
  }

  feedbackExperience(): Promise<Experience> {
    return this.offline('记录经验反馈')
  }
}
