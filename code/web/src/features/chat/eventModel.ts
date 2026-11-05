import type { AgentEvent, Message } from '@/types'

/**
 * 事件流 → 界面时间线（归约层）。
 *
 * 为什么需要这一层：后端只给"追加型事件"（turn.started / tool.completed / approval.requested …），
 * 前端要把它折成"一轮对话里发生了什么"。归约必须是**纯函数**，方便测试与回放。
 *
 * 一条重要设计：时间线以 **turn（轮次）** 为单位分块，每块内部按"用户消息 → 过程项 → 助手消息"
 * 排列。过程项保留真实顺序（工具调用与阶段交替出现时不能乱）。
 */

export type ProcessItem =
  | {
      kind: 'tool'
      id: string
      toolCallId: string
      toolName: string
      /** started 表示已发出、尚未收到回执；completed/failed 为终态 */
      status: 'started' | 'completed' | 'failed'
      arguments?: unknown
      durationSeconds?: number
      resultSize?: number
      at: number
    }
  | {
      kind: 'approval'
      id: string
      pendingActionId: string
      toolName: string
      arguments?: unknown
      status: 'requested' | 'decided' | 'completed'
      decision?: 'CONFIRM' | 'REJECT'
      executed?: boolean
      resultStatus?: string
      at: number
    }
  | {
      kind: 'stage'
      id: string
      stage: string
      source?: string
      status: string
      durationSeconds?: number
      warnings: string[]
      at: number
    }
  | {
      kind: 'compaction'
      id: string
      trigger: string
      status: 'started' | 'completed' | 'failed'
      degraded?: boolean
      at: number
    }
  | {
      kind: 'unknown'
      id: string
      eventType: string
      /** 保留原始 payload，界面上以"未知事件"显示而不是丢掉 */
      payload: Record<string, unknown>
      at: number
    }

export interface TurnBlock {
  turnId: string
  /** 该轮第一个事件的时间；用于排序 */
  startedAt: number
  status: 'RUNNING' | 'SUCCESS' | 'FAILED' | 'UNKNOWN'
  statusDetail?: string
  errorType?: string
  durationSeconds?: number
  stepCount?: number
  warnings: string[]
  userMessages: Message[]
  assistantMessages: Message[]
  items: ProcessItem[]
  /** 该轮是否由多 Agent 协作产生（来自 turn.completed.trace_metadata.collaboration_run_id） */
  collaborationRunId?: string
  /** 该轮的产出文件：从 create_file / apply_patch 的真实工具参数里提取，不猜测 */
  outputs: { path: string; tool: string }[]
}

function num(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

function str(value: unknown): string | undefined {
  return typeof value === 'string' && value.length > 0 ? value : undefined
}

function record(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : {}
}

function arr(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

/**
 * 把消息与事件合成时间线。
 * @param messages 权威消息（来自 thread/read），按仓库顺序
 * @param events   事件流（可能只取到一部分，按 sequence 升序）
 */
export function buildTimeline(messages: Message[], events: AgentEvent[]): TurnBlock[] {
  const blocks = new Map<string, TurnBlock>()

  const ensure = (turnId: string, at: number): TurnBlock => {
    const existing = blocks.get(turnId)
    if (existing !== undefined) return existing
    const block: TurnBlock = {
      turnId,
      startedAt: at,
      status: 'UNKNOWN',
      warnings: [],
      userMessages: [],
      assistantMessages: [],
      items: [],
      outputs: [],
    }
    blocks.set(turnId, block)
    return block
  }

  for (const message of messages) {
    if (message.role === 'USER') {
      ensure(message.turnId, message.createdAt).userMessages.push(message)
    } else if (message.role === 'ASSISTANT') {
      ensure(message.turnId, message.createdAt).assistantMessages.push(message)
    }
    // TOOL / SYSTEM 消息不直接展示：工具过程已由事件流表达，避免重复
  }

  const ordered = [...events].sort((a, b) => a.sequence - b.sequence)

  // 跨轮索引：审批与工具的真实事件可能跨越 turn 边界（例如"确认"发生在一个新的 confirmation turn 里），
  // 所以查找必须跨 block，按稳定 id 定位，而不是只在当前轮里 find。
  const toolIndex = new Map<string, Extract<ProcessItem, { kind: 'tool' }>>()
  const approvalIndex = new Map<string, Extract<ProcessItem, { kind: 'approval' }>>()

  // 会话级事件（thread.started / thread.resumed）没有 turn_id：
  // 它们代表"打开/新建会话"，不是一轮对话，不能因此凭空造出 "#1 UNKNOWN" 空轮次。
  // 若个别无 turn 的事件需要展示（例如压缩），归到它之前最近的一轮过程里。
  let lastTurnId: string | undefined

  for (const event of ordered) {
    const payload = record(event.payload)
    const at = event.created_at
    const turnId = event.turn_id ?? lastTurnId
    if (turnId === undefined) continue
    lastTurnId = event.turn_id ?? lastTurnId ?? turnId
    const block = ensure(turnId, at)

    switch (event.event_type) {
      case 'turn.started':
      case 'turn.resumed': {
        block.status = 'RUNNING'
        break
      }
      case 'turn.completed': {
        const status = str(payload['status']) ?? 'SUCCESS'
        block.status = status === 'SUCCESS' ? 'SUCCESS' : 'FAILED'
        block.statusDetail = status
        block.durationSeconds = num(payload['duration_seconds'])
        block.stepCount = num(payload['step_count'])
        block.warnings = arr(payload['warnings'])
        // 结构化字段判断是否是协作轮（不做任何语义猜测）
        const traceMetadata = record(payload['trace_metadata'])
        const collaborationRunId = str(traceMetadata['collaboration_run_id'])
        if (collaborationRunId !== undefined) block.collaborationRunId = collaborationRunId
        break
      }
      case 'turn.failed': {
        block.status = 'FAILED'
        block.errorType = str(payload['error_type']) ?? 'UNKNOWN'
        block.durationSeconds = num(payload['duration_seconds'])
        break
      }
      case 'turn.usage': {
        // 用量事件只补充耗时，不单独成项
        if (block.durationSeconds === undefined) {
          block.durationSeconds = num(payload['elapsed_seconds'])
        }
        break
      }
      case 'tool.started': {
        const toolCallId = str(payload['tool_call_id']) ?? `tool-${event.sequence}`
        // 真实事件流里 tool.call.completed 可能先于 tool.started 到达（同一 tool_call_id），
        // 因此这里必须按 id 去重，否则界面会出现两张重复的工具卡。
        const existingTool = toolIndex.get(toolCallId)
        if (existingTool !== undefined) {
          if (existingTool.arguments === undefined) existingTool.arguments = payload['arguments']
          break
        }
        const item: Extract<ProcessItem, { kind: 'tool' }> = {
          kind: 'tool',
          id: `tool-${toolCallId}`,
          toolCallId,
          toolName: str(payload['tool_name']) ?? 'unknown',
          status: 'started',
          arguments: payload['arguments'],
          at,
        }
        block.items.push(item)
        toolIndex.set(toolCallId, item)
        break
      }
      case 'tool.completed':
      case 'tool.call.completed': {
        const toolCallId = str(payload['tool_call_id']) ?? `tool-${event.sequence}`
        const existing = toolIndex.get(toolCallId)
        const status = str(payload['status'])
        const nextStatus = status === 'SUCCESS' || status === 'SUCCEEDED' ? 'completed' : 'failed'
        if (existing !== undefined) {
          existing.status = nextStatus
          existing.durationSeconds = num(payload['duration_seconds']) ?? existing.durationSeconds
          existing.resultSize = num(payload['result_size']) ?? existing.resultSize
        } else {
          // 只拿到 completed（例如从游标中间开始读），补一条完整记录
          const item: Extract<ProcessItem, { kind: 'tool' }> = {
            kind: 'tool',
            id: `tool-${toolCallId}`,
            toolCallId,
            toolName: str(payload['tool_name']) ?? 'unknown',
            status: nextStatus,
            arguments: payload['arguments'],
            durationSeconds: num(payload['duration_seconds']),
            resultSize: num(payload['result_size']),
            at,
          }
          block.items.push(item)
          toolIndex.set(toolCallId, item)
        }
        break
      }
      case 'approval.requested': {
        const pendingActionId = str(payload['pending_action_id']) ?? `approval-${event.sequence}`
        const existingApproval = approvalIndex.get(pendingActionId)
        if (existingApproval !== undefined) {
          if (existingApproval.arguments === undefined) {
            existingApproval.arguments = payload['arguments'] ?? payload['validated_arguments']
          }
          break
        }
        const item: Extract<ProcessItem, { kind: 'approval' }> = {
          kind: 'approval',
          id: `approval-${pendingActionId}`,
          pendingActionId,
          toolName: str(payload['tool_name']) ?? 'unknown',
          arguments: payload['arguments'] ?? payload['validated_arguments'],
          status: 'requested',
          at,
        }
        block.items.push(item)
        approvalIndex.set(pendingActionId, item)
        break
      }
      case 'approval.decision':
      case 'approval.completed': {
        const pendingActionId = str(payload['pending_action_id']) ?? ''
        const rawDecision = str(payload['decision'])
        const decision: 'CONFIRM' | 'REJECT' | undefined =
          rawDecision === 'CONFIRM' ? 'CONFIRM' : rawDecision === 'REJECT' ? 'REJECT' : undefined
        const patch = {
          status:
            event.event_type === 'approval.completed'
              ? ('completed' as const)
              : ('decided' as const),
          decision,
          executed: typeof payload['executed'] === 'boolean' ? payload['executed'] : undefined,
          resultStatus: str(payload['result_status']),
        }
        // 跨轮查找：确认/拒绝通常发生在一条新的 confirmation turn 里，
        // 原卡片在更早的 turn block 中，因此必须用索引定位并原地更新。
        const existing = approvalIndex.get(pendingActionId)
        if (existing !== undefined) {
          Object.assign(existing, patch)
          break
        }
        const item: Extract<ProcessItem, { kind: 'approval' }> = {
          kind: 'approval',
          id: `approval-${pendingActionId || event.sequence}`,
          pendingActionId,
          toolName: str(payload['tool_name']) ?? 'unknown',
          arguments: payload['arguments'] ?? payload['validated_arguments'],
          at,
          ...patch,
        }
        block.items.push(item)
        if (pendingActionId !== '') approvalIndex.set(pendingActionId, item)
        break
      }
      case 'stage.completed': {
        block.items.push({
          kind: 'stage',
          id: `stage-${event.sequence}`,
          stage: str(payload['stage']) ?? 'unknown',
          source: str(payload['source']),
          status: str(payload['status']) ?? 'UNKNOWN',
          durationSeconds: num(payload['duration_seconds']),
          warnings: arr(payload['warnings']),
          at,
        })
        break
      }
      case 'compaction.started':
      case 'compaction.completed':
      case 'compaction.failed': {
        const status =
          event.event_type === 'compaction.started'
            ? ('started' as const)
            : event.event_type === 'compaction.completed'
              ? ('completed' as const)
              : ('failed' as const)
        block.items.push({
          kind: 'compaction',
          id: `compaction-${event.sequence}`,
          trigger: str(payload['trigger']) ?? 'unknown',
          status,
          degraded: typeof payload['degraded'] === 'boolean' ? payload['degraded'] : undefined,
          at,
        })
        break
      }
      case 'thread.started':
      case 'thread.resumed':
      case 'item.completed':
        // 会话边界与消息回执：消息已由 thread/read 提供，这里不重复展示
        break
      default: {
        block.items.push({
          kind: 'unknown',
          id: `evt-${event.event_id}`,
          eventType: event.event_type,
          payload,
          at,
        })
      }
    }
  }

  // 结束后从真实工具参数里提取"产出文件"（只取成功的写类工具）
  for (const block of blocks.values()) {
    const outputs: { path: string; tool: string }[] = []
    const seen = new Set<string>()
    for (const item of block.items) {
      if (item.kind !== 'tool') continue
      if (item.status !== 'completed') continue
      if (item.toolName !== 'create_file' && item.toolName !== 'apply_patch') continue
      const path = outputPath(item.arguments)
      if (path === null || seen.has(path)) continue
      seen.add(path)
      outputs.push({ path, tool: item.toolName })
    }
    block.outputs = outputs
  }

  // 过滤掉没有任何内容的空轮次（防御性：正常路径不应产生）
  return [...blocks.values()]
    .filter(
      (block) =>
        block.userMessages.length > 0 ||
        block.assistantMessages.length > 0 ||
        block.items.length > 0 ||
        block.status !== 'UNKNOWN',
    )
    .sort((a, b) => a.startedAt - b.startedAt)
}

/** 从工具参数里取目标文件路径（字段名不假设唯一，按常见键名依次尝试） */
function outputPath(argumentsValue: unknown): string | null {
  const args = record(argumentsValue)
  for (const key of ['filename', 'path', 'file_path', 'target']) {
    const value = str(args[key])
    if (value !== undefined) return value
  }
  return null
}

/** 时间戳 → 本地时分秒（时间线用短格式） */
export function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return '--:--:--'
  const date = new Date(seconds * 1000)
  return date.toLocaleTimeString('zh-CN', { hour12: false })
}

/** 耗时 → 人类可读 */
export function formatDuration(seconds?: number): string {
  if (seconds === undefined || !Number.isFinite(seconds)) return ''
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`
  if (seconds < 60) return `${seconds.toFixed(1)} s`
  const minutes = Math.floor(seconds / 60)
  return `${minutes}m ${Math.round(seconds % 60)}s`
}
