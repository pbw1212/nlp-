import type { AgentEvent, CollaborationGraphView, ContextDiagnostics, DataSource, Experience, GraphSummary, JobHandle, JobResult, MemoryItem, MemoryRevision, Message, MetaInfo, PendingApproval, ProvenanceRef, RepairPlanView, RepairQueueRow, StatusSnapshot, TaskLifecycleStatus, TaskSummary, Thread, ToolSpec, UnfinishedTurn, ApiKeyStatus, CollaborationRunDetail, CollaborationRunSummary, ThreadExportPayload, ThreadImportResult } from '@/types'
import { DataSourceError } from './errors'

const DEFAULT_BASE = 'http://127.0.0.1:8787'
const TOKEN_STORAGE_KEY = 'mapas.bridge.token'

export function bridgeBaseUrl(): string {
  return import.meta.env.VITE_BRIDGE_URL ?? DEFAULT_BASE
}

export function bridgeToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_STORAGE_KEY) ?? import.meta.env.VITE_BRIDGE_TOKEN ?? null
  } catch {
    return import.meta.env.VITE_BRIDGE_TOKEN ?? null
  }
}

export function setBridgeToken(token: string | null): void {
  try {
    if (token === null) localStorage.removeItem(TOKEN_STORAGE_KEY)
    else localStorage.setItem(TOKEN_STORAGE_KEY, token)
  } catch {
    /* 隐私模式下 localStorage 可能不可用：忽略，不影响主流程 */
  }
}

type Json = Record<string, unknown>

/**
 * 桥接 HTTP 客户端。
 *
 * 设计要点：
 * - 所有读写都走 `PersonalAgentApp` 的既有方法（由 bridge 暴露），本层不直连数据库；
 * - 长任务（对话/修复/drain）返回 JobHandle，不阻塞界面；
 * - 错误统一转成 DataSourceError，界面必须显示真实原因。
 */
export class HttpSource implements DataSource {
  readonly kind = 'http' as const
  readonly writable = true

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const token = bridgeToken()
    const headers: Record<string, string> = {
      Accept: 'application/json',
      ...(init?.body === undefined ? {} : { 'Content-Type': 'application/json' }),
      ...(token === null ? {} : { 'X-MAPAS-Token': token }),
    }

    let response: Response
    try {
      response = await fetch(`${bridgeBaseUrl()}${path}`, { ...init, headers })
    } catch (cause) {
      throw new DataSourceError(
        'BRIDGE_NOT_RUNNING',
        '连不上桥接进程，请先启动 web_bridge.py',
        cause instanceof Error ? cause.message : String(cause),
      )
    }

    const text = await response.text()
    let body: unknown = null
    if (text.length > 0) {
      try {
        body = JSON.parse(text)
      } catch {
        body = null
      }
    }

    if (!response.ok) {
      const record = body as Json | null
      const message =
        typeof record?.['error'] === 'string'
          ? String(record['error'])
          : typeof record?.['message'] === 'string'
            ? String(record['message'])
            : `桥接返回 HTTP ${response.status}`
      throw new DataSourceError('HTTP_ERROR', message, `${response.status} ${path}`)
    }

    if (body === null) {
      throw new DataSourceError('BAD_RESPONSE', `桥接返回体不是 JSON：${path}`)
    }
    return body as T
  }

  private get<T>(path: string): Promise<T> {
    return this.request<T>(path)
  }

  private del<T>(path: string): Promise<T> {
    return this.request<T>(path, { method: 'DELETE' })
  }

  private post<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>(path, {
      method: 'POST',
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  }

  checkAuth(): Promise<{ tokenRequired: boolean; protocolVersion: string }> {
    return this.get<{ tokenRequired: boolean; protocolVersion: string }>('/api/auth')
  }

  hasCredential(): boolean {
    return bridgeToken() !== null
  }

  // ---------- 协作运行 ----------
  async listCollaborationRuns(all = false): Promise<CollaborationRunSummary[]> {
    const r = await this.get<{ graphs: CollaborationRunSummary[] }>(
      `/api/collaboration/runs?all=${all ? 1 : 0}&limit=30`,
    )
    return r.graphs
  }

  getCollaborationRun(runId: string): Promise<CollaborationRunDetail> {
    return this.get<CollaborationRunDetail>(
      `/api/collaboration/runs/${encodeURIComponent(runId)}`,
    )
  }

  // ---------- API Key（只写不读）----------
  getApiKeyStatus(refresh = false): Promise<ApiKeyStatus> {
    return this.get<ApiKeyStatus>(
      refresh ? '/api/api-key?refresh=1' : '/api/api-key',
    )
  }

  updateApiKey(key: string): Promise<ApiKeyStatus> {
    return this.post<ApiKeyStatus>('/api/api-key', { key })
  }

  deleteApiKey(): Promise<ApiKeyStatus> {
    return this.del<ApiKeyStatus>('/api/api-key')
  }

  // ---------- 元信息 ----------
  meta(): Promise<MetaInfo> {
    return this.get<MetaInfo>('/api/meta')
  }

  /**
   * 切换执行模式：桥接会在同一个数据目录上重建 PersonalAgentApp（token 不变）。
   * 有作业在跑时桥接返回 409，前端把原因原样显示。
   */
  async switchMode(mode: 'single' | 'multi_fixed'): Promise<MetaInfo> {
    const r = await this.post<{ mode: string; meta: MetaInfo }>('/api/mode', { mode })
    return r.meta
  }

  // ---------- 会话 ----------
  async listThreads(): Promise<Thread[]> {
    const r = await this.get<{ threads: Thread[] }>('/api/threads')
    return r.threads ?? []
  }

  async createThread(title?: string): Promise<Thread> {
    const r = await this.post<{ thread: Thread }>('/api/threads', { title })
    return r.thread
  }

  async resumeThread(threadId: string): Promise<Thread> {
    const r = await this.post<{ thread: Thread }>(
      `/api/threads/${encodeURIComponent(threadId)}/resume`,
    )
    return r.thread
  }

  exportThread(threadId: string): Promise<ThreadExportPayload> {
    return this.get<ThreadExportPayload>(
      `/api/threads/${encodeURIComponent(threadId)}/export`,
    )
  }

  importThread(payload: ThreadExportPayload): Promise<ThreadImportResult> {
    return this.post<ThreadImportResult>('/api/threads/import', payload)
  }

  async renameThread(threadId: string, title: string): Promise<Thread> {
    const r = await this.post<{ thread: Thread }>(
      `/api/threads/${encodeURIComponent(threadId)}/rename`,
      { title },
    )
    return r.thread
  }

  deleteThread(
    threadId: string,
  ): Promise<{ ok: boolean; deletedId: string; wasActive: boolean }> {
    return this.post<{ ok: boolean; deletedId: string; wasActive: boolean }>(
      `/api/threads/${encodeURIComponent(threadId)}/delete`,
    )
  }

  async readMessages(threadId: string): Promise<Message[]> {
    const r = await this.get<{ messages: Message[] }>(
      `/api/threads/${encodeURIComponent(threadId)}/messages`,
    )
    return r.messages ?? []
  }

  async listUnfinishedTurns(threadId: string): Promise<UnfinishedTurn[]> {
    const r = await this.get<{ unfinishedTurns: UnfinishedTurn[] }>(
      `/api/threads/${encodeURIComponent(threadId)}/unfinished`,
    )
    return r.unfinishedTurns ?? []
  }

  startTurn(threadId: string, input: string): Promise<JobHandle> {
    return this.post<JobHandle>(`/api/threads/${encodeURIComponent(threadId)}/turns`, { input })
  }

  resumeTurn(threadId: string, turnId: string): Promise<JobHandle> {
    return this.post<JobHandle>(
      `/api/threads/${encodeURIComponent(threadId)}/turns/${encodeURIComponent(turnId)}/resume`,
    )
  }

  getJob(jobId: string): Promise<JobResult> {
    return this.get<JobResult>(`/api/jobs/${encodeURIComponent(jobId)}`)
  }

  // ---------- 事件 ----------
  async readEvents(opts: {
    afterSequence?: number
    threadId?: string
    turnId?: string
    limit?: number
  }): Promise<{ events: AgentEvent[]; nextSequence: number }> {
    const query = new URLSearchParams()
    query.set('afterSequence', String(opts.afterSequence ?? 0))
    if (opts.threadId) query.set('threadId', opts.threadId)
    if (opts.turnId) query.set('turnId', opts.turnId)
    if (opts.limit !== undefined) query.set('limit', String(opts.limit))
    return this.get<{ events: AgentEvent[]; nextSequence: number }>(
      `/api/events?${query.toString()}`,
    )
  }

  streamEvents(
    cb: (event: AgentEvent) => void,
    opts?: { afterSequence?: number; threadId?: string },
  ): () => void {
    const query = new URLSearchParams()
    query.set('afterSequence', String(opts?.afterSequence ?? 0))
    if (opts?.threadId) query.set('threadId', opts.threadId)
    const token = bridgeToken()
    if (token !== null) query.set('token', token)

    const source = new EventSource(`${bridgeBaseUrl()}/api/events/stream?${query.toString()}`)
    source.onmessage = (message) => {
      try {
        cb(JSON.parse(message.data) as AgentEvent)
      } catch {
        /* 单条事件解析失败不影响后续事件 */
      }
    }
    return () => source.close()
  }

  // ---------- 诊断 ----------
  getStatus(): Promise<StatusSnapshot> {
    return this.get<StatusSnapshot>('/api/status')
  }

  getContext(): Promise<ContextDiagnostics> {
    return this.get<ContextDiagnostics>('/api/context')
  }

  compact(): Promise<JobHandle> {
    return this.post<JobHandle>('/api/context/compact')
  }

  async getTools(): Promise<ToolSpec[]> {
    const r = await this.get<{ tools: ToolSpec[] }>('/api/tools')
    return r.tools ?? []
  }

  getMcpStatus(): Promise<Record<string, unknown>> {
    return this.get<Record<string, unknown>>('/api/mcp')
  }

  getWorkspace(): Promise<Record<string, unknown>> {
    return this.get<Record<string, unknown>>('/api/workspace')
  }

  getStorage(): Promise<Record<string, string>> {
    return this.get<Record<string, string>>('/api/storage')
  }

  // ---------- 长期记忆 ----------
  async listMemories(): Promise<MemoryItem[]> {
    const r = await this.get<{ memories: MemoryItem[] }>('/api/memories')
    return r.memories ?? []
  }

  async getMemory(id: string): Promise<MemoryItem | null> {
    const r = await this.get<{ memory: MemoryItem | null }>(
      `/api/memories/${encodeURIComponent(id)}`,
    )
    return r.memory ?? null
  }

  async getMemoryHistory(id: string): Promise<MemoryRevision[]> {
    const r = await this.get<{ revisions: MemoryRevision[] }>(
      `/api/memories/${encodeURIComponent(id)}/history`,
    )
    return r.revisions ?? []
  }

  async getMemoryProvenance(id: string): Promise<ProvenanceRef[]> {
    const r = await this.get<{ provenance: ProvenanceRef[] }>(
      `/api/memories/${encodeURIComponent(id)}/provenance`,
    )
    return r.provenance ?? []
  }

  async listRecycleBin(): Promise<MemoryItem[]> {
    const r = await this.get<{ memories: MemoryItem[] }>('/api/memories/recycle-bin')
    return r.memories ?? []
  }

  remember(content: string): Promise<Record<string, unknown>> {
    return this.post<Record<string, unknown>>('/api/memories', { content })
  }

  async forgetMemory(id: string): Promise<boolean> {
    const r = await this.post<{ ok: boolean }>(`/api/memories/${encodeURIComponent(id)}/forget`)
    return r.ok === true
  }

  async restoreMemory(id: string): Promise<boolean> {
    const r = await this.post<{ ok: boolean }>(`/api/memories/${encodeURIComponent(id)}/restore`)
    return r.ok === true
  }

  getProfile(): Promise<Record<string, unknown>> {
    return this.get<Record<string, unknown>>('/api/memories/profile')
  }

  // ---------- 任务 ----------
  async listTasks(): Promise<Record<string, unknown>[]> {
    const r = await this.get<{ tasks: Record<string, unknown>[] }>('/api/tasks')
    return r.tasks ?? []
  }

  async createTask(title?: string): Promise<string> {
    const r = await this.post<{ taskId: string }>('/api/tasks', { title })
    return r.taskId
  }

  async switchTask(taskId: string): Promise<boolean> {
    const r = await this.post<{ ok: boolean }>(`/api/tasks/${encodeURIComponent(taskId)}/switch`)
    return r.ok === true
  }

  async getTaskSummary(): Promise<TaskSummary> {
    const r = await this.get<{ status: TaskSummary }>('/api/tasks/status')
    return r.status
  }

  async pauseTask(note?: string): Promise<void> {
    await this.post<Json>('/api/tasks/current/pause', { note })
  }

  async resumeTask(): Promise<void> {
    await this.post<Json>('/api/tasks/current/resume')
  }

  async completeTask(): Promise<void> {
    await this.post<Json>('/api/tasks/current/complete')
  }

  async cancelTask(): Promise<void> {
    await this.post<Json>('/api/tasks/current/cancel')
  }

  getTaskLifecycle(): Promise<TaskLifecycleStatus> {
    return this.get<TaskLifecycleStatus>('/api/task-lifecycle')
  }

  drainTaskLifecycle(): Promise<Record<string, unknown>> {
    return this.post<Record<string, unknown>>('/api/task-lifecycle/drain')
  }

  // ---------- 检索 trace（降级证据视图：只有聚合字段） ----------
  getTurnTrace(turnId: string): Promise<Record<string, unknown>> {
    return this.get<Record<string, unknown>>(`/api/turns/${encodeURIComponent(turnId)}/trace`)
  }

  async listTurnTraces(limit = 50): Promise<Record<string, unknown>[]> {
    const r = await this.get<{ traces: Record<string, unknown>[] }>(
      `/api/traces?limit=${encodeURIComponent(String(limit))}`,
    )
    return r.traces ?? []
  }

  // ---------- 协作 ----------
  async listGraphs(): Promise<GraphSummary[]> {
    const r = await this.get<{ graphs: GraphSummary[] }>('/api/collaboration/runs')
    return r.graphs ?? []
  }

  getGraph(runId: string): Promise<CollaborationGraphView> {
    return this.get<CollaborationGraphView>(
      `/api/collaboration/runs/${encodeURIComponent(runId)}/graph`,
    )
  }

  // ---------- 审批 ----------
  async listApprovals(threadId?: string): Promise<PendingApproval[]> {
    const query = threadId === undefined ? '' : `?threadId=${encodeURIComponent(threadId)}`
    const r = await this.get<{ approvals: PendingApproval[] }>(`/api/approvals${query}`)
    return r.approvals ?? []
  }

  decideApproval(
    pendingActionId: string,
    decision: 'CONFIRM' | 'REJECT',
  ): Promise<JobHandle> {
    return this.post<JobHandle>(
      `/api/approvals/${encodeURIComponent(pendingActionId)}/decision`,
      { decision },
    )
  }

  // ---------- 修复 ----------
  async listRepairs(): Promise<RepairQueueRow[]> {
    const r = await this.get<{ repairs: RepairQueueRow[] }>('/api/repairs')
    return r.repairs ?? []
  }

  getRepairPlan(repairId: string): Promise<RepairPlanView> {
    return this.get<RepairPlanView>(`/api/repairs/${encodeURIComponent(repairId)}`)
  }

  runRepair(runId: string, eventId: string): Promise<JobHandle> {
    return this.post<JobHandle>(
      `/api/repairs/${encodeURIComponent(runId)}/${encodeURIComponent(eventId)}/run`,
    )
  }

  revokeAndRepair(runId: string, artifactId: string, eventId: string): Promise<JobHandle> {
    return this.post<JobHandle>('/api/repairs/revoke', { runId, artifactId, eventId })
  }

  drainRepairs(): Promise<Record<string, unknown>> {
    return this.post<Record<string, unknown>>('/api/repairs/drain')
  }

  // ---------- 经验 ----------
  async listExperiences(): Promise<Experience[]> {
    const r = await this.get<{ experiences: Experience[] }>('/api/experiences')
    return r.experiences ?? []
  }

  async learnExperience(runId: string, revisionOf?: string): Promise<Experience | null> {
    const r = await this.post<{ experience?: Experience | null }>('/api/experiences/learn', {
      runId,
      revisionOf,
    })
    return r.experience ?? null
  }

  async reviewExperience(id: string, confirmation: string): Promise<Experience> {
    const r = await this.post<{ experience: Experience }>('/api/experiences/review', {
      experienceId: id,
      confirmation,
    })
    return r.experience
  }

  async revokeExperience(id: string, eventId: string): Promise<Experience> {
    const r = await this.post<{ experience: Experience }>('/api/experiences/revoke', {
      experienceId: id,
      eventId,
    })
    return r.experience
  }

  async feedbackExperience(id: string, runId: string, eventId: string): Promise<Experience> {
    const r = await this.post<{ experience: Experience }>('/api/experiences/feedback', {
      experienceId: id,
      runId,
      eventId,
    })
    return r.experience
  }
}
