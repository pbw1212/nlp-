import { useState } from 'react'
import type { TaskCycleState, TaskLifecycleStatus, TaskSummary } from '@/types'
import { useAppState } from '@/state/AppState'
import { useAsync } from '@/lib/useAsync'
import { formatDateTime, formatInt } from '@/lib/format'
import { Badge, Card, EmptyState, KeyValue, SectionLabel } from '@/components/ui/primitives'
import { Banner as SharedBanner, Hint as SharedHint } from '@/components/ui/feedback'
import type { Tone } from '@/components/ui/primitives'

const TASK_STATUS_TONE: Record<string, Tone> = {
  ACTIVE: 'verified',
  PAUSED: 'waiting',
  COMPLETED: 'completed',
  CANCELLED: 'cancelled',
}

const CYCLE_TONE: Record<TaskCycleState, Tone> = {
  ASSESS_PENDING: 'assess',
  REPAIR_PENDING: 'repair',
  WAITING_USER: 'waiting',
  COMPLETED: 'completed',
  FAILED: 'failed',
  CANCELLED: 'cancelled',
}

const VALIDITY_TONE: Record<string, Tone> = {
  VALID: 'valid',
  STALE: 'stale',
  NOT_COMPLETED: 'pending',
  NOT_CHECKED_WORKSPACE: 'notchecked',
}

const ENTRY_GROUPS = [
  { key: 'goals', label: '目标', tone: 'accent' as Tone },
  { key: 'constraints', label: '约束', tone: 'unknown' as Tone },
  { key: 'decisions', label: '决定', tone: 'produced' as Tone },
  { key: 'progress', label: '进展', tone: 'verified' as Tone },
  { key: 'open_questions', label: '待解决问题', tone: 'waiting' as Tone },
] as const

/**
 * 任务台账（P3）。
 *
 * 要证明的核心点：**任务状态与对话解耦**。对话会被压缩甚至换会话，
 * 但目标、约束、决定、进展、待解决问题这些持续状态留在台账里，且带检查点可恢复。
 *
 * 数据全部来自 Task 3 的真实方法：`get_task_status_summary` / `get_task_lifecycle_status` / `list_tasks`。
 */
export function TaskPage() {
  const { source } = useAppState()
  const [actionError, setActionError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const state = useAsync<{
    tasks: Record<string, unknown>[]
    summary: TaskSummary
    lifecycle: TaskLifecycleStatus
  }>(async () => {
    const [tasks, summary, lifecycle] = await Promise.all([
      source.listTasks(),
      source.getTaskSummary(),
      source.getTaskLifecycle(),
    ])
    return { tasks, summary, lifecycle }
  }, [source])

  const runAction = async (label: string, action: () => Promise<unknown>) => {
    setBusy(true)
    setActionError(null)
    try {
      await action()
      state.reload()
    } catch (cause) {
      setActionError(`${label}失败：` + (cause instanceof Error ? cause.message : String(cause)))
    } finally {
      setBusy(false)
    }
  }

  const summary = state.data?.summary ?? null
  const lifecycle = state.data?.lifecycle ?? null
  const tasks = state.data?.tasks ?? []
  const checkpoint = summary?.checkpoint ?? null

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <header style={{ display: 'grid', gap: 6 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <h1 style={{ fontSize: 20, fontWeight: 650, margin: 0 }}>任务台账</h1>
          <Badge tone="verified">已接入真实数据</Badge>
          <Badge tone="accent" plain>
            Task 3
          </Badge>
          {summary?.status !== null && summary?.status !== undefined && (
            <Badge tone={TASK_STATUS_TONE[summary.status] ?? 'pending'} filled>
              {summary.status}
            </Badge>
          )}
        </div>
        <p style={{ margin: 0, fontSize: 13, color: 'var(--fg-muted)', lineHeight: 1.7 }}>
          台账保存跨会话仍然有效的目标、约束、决定与进展；检查点记录"下一步做什么"；两者都与对话分离，
          所以换会话也不会丢。
        </p>
      </header>

      <Hint>目标、约束、进展这些状态独立于对话保存，所以换会话、隔几天都能接着做；检查点记录「下一步做什么」。</Hint>

      {actionError !== null && <Banner message={actionError} />}
      {state.error !== null && <Banner message={state.error} />}

      <Card
        title="任务操作"
        description="这些操作调用后端的真实方法；完成操作在 multi_fixed 模式下需要通过整体验收才会封存"
      >
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <ActionButton disabled={busy} onClick={() => void runAction('新建任务', () => source.createTask())}>
            新建任务
          </ActionButton>
          <ActionButton disabled={busy} onClick={() => void runAction('暂停', () => source.pauseTask())}>
            暂停
          </ActionButton>
          <ActionButton disabled={busy} onClick={() => void runAction('恢复', () => source.resumeTask())}>
            恢复
          </ActionButton>
          <ActionButton disabled={busy} onClick={() => void runAction('完成', () => source.completeTask())}>
            完成（需通过验收）
          </ActionButton>
          <ActionButton disabled={busy} onClick={() => void runAction('取消', () => source.cancelTask())}>
            取消
          </ActionButton>
          <ActionButton
            disabled={busy}
            onClick={() => void runAction('推进闭环', () => source.drainTaskLifecycle())}
          >
            推进待办作业
          </ActionButton>
          <button type="button" className="chip chip--button" onClick={state.reload}>
            刷新
          </button>
        </div>
      </Card>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1.4fr) minmax(0,1fr)', gap: 16 }}>
        <div style={{ display: 'grid', gap: 16, alignContent: 'start' }}>
          {summary === null && state.loading && <Card title="台账"><Hint>读取中…</Hint></Card>}
          {summary !== null && (summary.task_id === null || summary.task_id === undefined) ? (
            <Card title="台账">
              <EmptyState
                title="当前没有绑定任务"
                description="任务由 Provider 在对话中提议绑定（EXISTING / NEW / UNBOUND），也可以点上方「新建任务」手动创建。"
                items={[
                  '没有任务时，普通对话仍可进行，只是不产生台账与检查点',
                  '新建任务后，目标会写入 GOAL 条目',
                ]}
              />
            </Card>
          ) : null}

          {summary !== null &&
            ENTRY_GROUPS.map((group) => {
              const items = summary[group.key] ?? []
              return (
                <Card
                  key={group.key}
                  title={
                    <span style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}>
                      {group.label}
                      <Badge tone={group.tone} plain>
                        {items.length}
                      </Badge>
                    </span>
                  }
                >
                  {items.length === 0 ? (
                    <Hint>暂无条目。</Hint>
                  ) : (
                    <ul style={{ margin: 0, paddingLeft: 18, display: 'grid', gap: 6 }}>
                      {items.map((item, index) => (
                        <li key={`${group.key}-${index}`} style={{ fontSize: 13, lineHeight: 1.75 }}>
                          {item}
                        </li>
                      ))}
                    </ul>
                  )}
                </Card>
              )
            })}

          {summary !== null && (summary.summary ?? '') !== '' && (
            <Card collapsed title="台账摘要">
              <p style={{ margin: 0, fontSize: 13, lineHeight: 1.8, whiteSpace: 'pre-wrap' }}>
                {summary.summary}
              </p>
            </Card>
          )}
        </div>

        <div style={{ display: 'grid', gap: 16, alignContent: 'start' }}>
          <Card title="检查点（ContinuationCheckpoint）" description="恢复执行所需的当前位置与下一步">
            {checkpoint === null ? (
              <Hint>没有检查点：任务尚未产生可恢复的进度。</Hint>
            ) : (
              <KeyValue
                rows={[
                  { k: '焦点 focus', v: checkpoint.focus ?? '—' },
                  { k: '下一步 next_action', v: checkpoint.next_action ?? '—' },
                  { k: '世界状态 world_state', v: checkpoint.world_state ?? '—' },
                  { k: '当前步骤', v: checkpoint.current_step ?? '—' },
                  { k: '续接说明', v: checkpoint.continuation_note ?? '—' },
                  { k: '检查点时间', v: formatDateTime(checkpoint.created_at ?? null) },
                ]}
              />
            )}
          </Card>

          <Card title="任务级闭环" description="整体验收、修复与经验的调度状态">
            {lifecycle === null ? (
              <Hint>读取中…</Hint>
            ) : lifecycle.enabled !== true ? (
              <Hint>
                当前执行模式未启用任务闭环（需要 `multi_fixed`）。single 模式下这一块不会产生数据。
              </Hint>
            ) : (
              <div style={{ display: 'grid', gap: 10 }}>
                {(lifecycle.tasks ?? []).length === 0 && <Hint>没有闭环记录。</Hint>}
                {(lifecycle.tasks ?? []).map((task) => (
                  <div key={task.task_id} style={{ display: 'grid', gap: 4 }}>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                      <Badge tone={CYCLE_TONE[task.state] ?? 'pending'} filled>
                        {task.state}
                      </Badge>
                      <span className="mono" style={{ fontSize: 11, color: 'var(--fg-subtle)' }}>
                        {task.task_id.slice(0, 18)}
                      </span>
                    </div>
                    {task.current_result_validity !== undefined && (
                      <span style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 12 }}>
                        <span className="subtle">结果有效性</span>
                        <Badge tone={VALIDITY_TONE[task.current_result_validity] ?? 'pending'} plain>
                          {task.current_result_validity}
                        </Badge>
                      </span>
                    )}
                    {(task.open_questions ?? []).length > 0 && (
                      <div style={{ fontSize: 12, color: 'var(--sem-waiting)', lineHeight: 1.6 }}>
                        待问问题：{(task.open_questions ?? []).join('；')}
                      </div>
                    )}
                  </div>
                ))}
                <hr className="divider" />
                <SectionLabel>作业</SectionLabel>
                {(lifecycle.jobs ?? []).length === 0 && <Hint>没有待处理作业。</Hint>}
                {(lifecycle.jobs ?? []).map((job) => (
                  <div key={job.job_id} style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 12 }}>
                    <Badge tone={job.state === 'SUCCEEDED' ? 'verified' : job.state === 'FAILED' ? 'failed' : 'pending'} plain>
                      {job.kind}
                    </Badge>
                    <span className="mono" style={{ color: 'var(--fg-subtle)' }}>
                      {job.state}
                    </span>
                  </div>
                ))}
                {lifecycle.usage !== undefined && (
                  <p style={{ margin: 0, fontSize: 11, color: 'var(--fg-subtle)' }}>
                    已记录模型调用 {formatInt(lifecycle.usage.model_calls)} 次 · 核算单位{' '}
                    {formatInt(lifecycle.usage.charged_units)}
                  </p>
                )}
              </div>
            )}
          </Card>

          <Card collapsed title="全部任务" description={`共 ${tasks.length} 个`}>
            {tasks.length === 0 && <Hint>没有任务。</Hint>}
            <div style={{ display: 'grid', gap: 6 }}>
              {tasks.map((task, index) => {
                const taskId = String(task['task_id'] ?? task['id'] ?? `#${index}`)
                return (
                  <div
                    key={taskId}
                    style={{
                      display: 'flex',
                      gap: 8,
                      alignItems: 'center',
                      border: '1px solid var(--border)',
                      borderRadius: 'var(--radius-sm)',
                      padding: '6px 8px',
                    }}
                  >
                    <span className="mono" style={{ fontSize: 11, flex: 1, wordBreak: 'break-all' }}>
                      {taskId}
                    </span>
                    <span style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
                      {String(task['status'] ?? '')}
                    </span>
                    <button
                      type="button"
                      className="chip chip--button"
                      disabled={busy}
                      onClick={() => void runAction('切换任务', () => source.switchTask(taskId))}
                    >
                      切换
                    </button>
                  </div>
                )
              })}
            </div>
          </Card>
        </div>
      </div>
    </div>
  )
}

function ActionButton({
  children,
  disabled,
  onClick,
}: {
  children: React.ReactNode
  disabled: boolean
  onClick(): void
}) {
  return (
    <button type="button" className="chip chip--button" disabled={disabled} onClick={onClick}>
      {children}
    </button>
  )
}

function Banner({ message, tone = 'failed' }: { message: string; tone?: 'failed' | 'running' }) {
  return <SharedBanner tone={tone === 'running' ? 'info' : 'error'}>{message}</SharedBanner>
}

function Hint({ children }: { children: React.ReactNode }) {
  return <SharedHint>{children}</SharedHint>
}
