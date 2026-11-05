import { useState } from 'react'
import type {
  Experience,
  ExperienceState,
  GraphSummary,
  PendingApproval,
  RepairPlanView,
  RepairQueueRow,
  ToolSpec,
} from '@/types'
import { useAppState } from '@/state/AppState'
import { useAsync } from '@/lib/useAsync'
import { formatDateTime } from '@/lib/format'
import { Badge, Card, EmptyState, KeyValue, SectionLabel } from '@/components/ui/primitives'
import { Banner as SharedBanner, Hint as SharedHint } from '@/components/ui/feedback'
import type { Tone } from '@/components/ui/primitives'

const EXPERIENCE_TONE: Record<ExperienceState, Tone> = {
  CANDIDATE: 'pending',
  ACTIVE: 'verified',
  QUARANTINED: 'blocked',
  NEEDS_CONFIRMATION: 'waiting',
  STALE: 'stale',
  REVOKED: 'failed',
  SUPERSEDED: 'cancelled',
}

const REPAIR_TONE: Record<string, Tone> = {
  PENDING: 'waiting',
  SUCCEEDED: 'verified',
  FAILED: 'failed',
}

const NODE_TONE: Record<string, Tone> = {
  PENDING: 'pending',
  RUNNING: 'running',
  PRODUCED: 'produced',
  VERIFIED: 'verified',
  FAILED: 'failed',
  BLOCKED: 'blocked',
  STALE: 'stale',
}

const EFFECT_TONE: Record<string, Tone> = {
  READ_ONLY: 'verified',
  INTERNAL: 'produced',
  SIDE_EFFECT: 'unknown',
}

type TabId = 'approvals' | 'repairs' | 'experiences' | 'tools' | 'env'

const TABS: { id: TabId; label: string; hint: string }[] = [
  { id: 'approvals', label: '审批', hint: '有副作用的操作必须人工确认' },
  { id: 'repairs', label: '修复', hint: '来源失效后的影响范围与局部重跑' },
  { id: 'experiences', label: '经验', hint: '程序性经验的 7 态生命周期' },
  { id: 'tools', label: '工具目录', hint: '能力与副作用等级' },
  { id: 'env', label: '环境', hint: '工作区与存储布局' },
]

/**
 * 治理中心（P4）。
 *
 * 这一屏是整套系统里最"诚实"的部分：它展示的不是成功故事，而是
 * **失败如何处理**——哪些成果因来源变化而失效、影响范围多大、只重跑了哪一部分；
 * 经验从候选到准入到隔离到撤销的状态机；以及所有需要人来拍板的副作用操作。
 */
export function GovernancePage() {
  const [tab, setTab] = useState<TabId>('approvals')

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <header style={{ display: 'grid', gap: 6 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <h1 style={{ fontSize: 20, fontWeight: 650, margin: 0 }}>治理中心</h1>
          <Badge tone="verified">已接入真实数据</Badge>
          <Badge tone="accent" plain>
            修复 · 经验 · 审批
          </Badge>
        </div>
        <p style={{ margin: 0, fontSize: 13, color: 'var(--fg-muted)', lineHeight: 1.7 }}>
          系统把"失败"也当成一等公民：来源变化会让成果失效，但只重做受影响的部分；经验有准入与撤销；
          副作用操作必须人工确认。这一屏让这些机制可见、可操作。
        </p>
      </header>

      <Hint>系统把「失败」也当正常情况处理：来源变了会让成果失效并只重做受影响部分；经验要经过审核才能复用；有副作用的操作必须你确认。</Hint>

      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {TABS.map((item) => (
          <button
            key={item.id}
            type="button"
            className="chip chip--button"
            onClick={() => setTab(item.id)}
            style={
              tab === item.id
                ? {
                    borderColor: 'color-mix(in oklab, var(--accent) 45%, transparent)',
                    color: 'var(--fg)',
                    background: 'color-mix(in oklab, var(--accent) 12%, transparent)',
                  }
                : undefined
            }
            title={item.hint}
          >
            {item.label}
          </button>
        ))}
      </div>

      {tab === 'approvals' && <ApprovalsSection />}
      {tab === 'repairs' && <RepairsSection />}
      {tab === 'experiences' && <ExperiencesSection />}
      {tab === 'tools' && <ToolsSection />}
      {tab === 'env' && <EnvSection />}
    </div>
  )
}

/* ============================== 审批 ============================== */

function ApprovalsSection() {
  const { source } = useAppState()
  const state = useAsync<PendingApproval[]>(() => source.listApprovals(), [source])
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  const decide = async (id: string, decision: 'CONFIRM' | 'REJECT') => {
    setBusy(true)
    setMessage(null)
    try {
      const job = await source.decideApproval(id, decision)
      for (let i = 0; i < 80; i += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 700))
        const status = await source.getJob(job.jobId)
        if (status.state === 'SUCCEEDED') {
          setMessage(`已${decision === 'CONFIRM' ? '确认' : '拒绝'}并执行完成`)
          state.reload()
          break
        }
        if (status.state === 'FAILED') {
          setMessage(`执行失败：${status.error?.message ?? '未返回原因'}`)
          break
        }
      }
    } catch (cause) {
      setMessage('操作失败：' + (cause instanceof Error ? cause.message : String(cause)))
    } finally {
      setBusy(false)
    }
  }

  const approvals = state.data ?? []

  return (
    <div style={{ display: 'grid', gap: 12 }}>
      <Card
        title="待审批动作"
        description={`共 ${approvals.length} 条`}
        actions={
          <button type="button" className="chip chip--button" onClick={state.reload}>
            刷新
          </button>
        }
      >
        <div style={{ display: 'grid', gap: 10 }}>
          {state.error !== null && <Banner message={state.error} />}
          {message !== null && <Banner message={message} tone="running" />}
          {state.loading && <Hint>读取中…</Hint>}
          {!state.loading && approvals.length === 0 && (
            <EmptyState
              title="当前没有待审批动作"
              description="有副作用的工具（写文件、执行命令、apply_patch）会在这里等待确认。"
              items={[
                '确认/拒绝会立刻续跑该轮，并把结果写回对话与事件流',
                '注意：后端 list_pending_approvals() 只按「当前绑定任务」过滤——未绑定任务的轮次这里会为空，审批仍可在对话时间线里看到并处理',
              ]}
              note="这是后端既有行为，不是界面缺陷；对话页以事件流为准渲染审批卡。"
            />
          )}
          {approvals.map((item) => (
            <div
              key={item.pending_action_id}
              style={{
                border: '1px solid color-mix(in oklab, var(--sem-waiting) 45%, transparent)',
                background: 'color-mix(in oklab, var(--sem-waiting) 8%, var(--bg-elev))',
                borderRadius: 'var(--radius)',
                padding: 12,
                display: 'grid',
                gap: 8,
              }}
            >
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <Badge tone="waiting" filled>
                  {item.effect}
                </Badge>
                <span style={{ fontSize: 13, fontWeight: 600 }}>{item.tool_name}</span>
                <span className="mono" style={{ color: 'var(--fg-subtle)' }}>
                  {item.pending_action_id}
                </span>
                <span className="mono" style={{ color: 'var(--fg-subtle)', marginLeft: 'auto' }}>
                  {formatDateTime(item.created_at)}
                </span>
              </div>
              <pre className="mono" style={preStyle}>
                {JSON.stringify(item.validated_arguments, null, 2)}
              </pre>
              <div style={{ display: 'flex', gap: 8 }}>
                <button
                  type="button"
                  className="chip chip--button"
                  disabled={busy}
                  onClick={() => void decide(item.pending_action_id, 'CONFIRM')}
                  style={{ color: 'var(--sem-verified)' }}
                >
                  确认执行
                </button>
                <button
                  type="button"
                  className="chip chip--button"
                  disabled={busy}
                  onClick={() => void decide(item.pending_action_id, 'REJECT')}
                  style={{ color: 'var(--sem-failed)' }}
                >
                  拒绝
                </button>
              </div>
            </div>
          ))}
        </div>
      </Card>

      <Card collapsed title="为什么需要人工确认">
        <p style={{ margin: 0, fontSize: 12, color: 'var(--fg-subtle)', lineHeight: 1.8 }}>
          工具必须显式声明副作用等级（READ_ONLY / INTERNAL / SIDE_EFFECT，无默认兜底）。
          SIDE_EFFECT 一律进入 AWAITING_CONFIRMATION 并持久化为待确认动作，用户确认后才真正执行；
          执行前会在同一事务里冻结参数、效果与幂等能力快照，崩溃恢复时不依赖可能已漂移的注册表。
        </p>
      </Card>
    </div>
  )
}

/* ============================== 修复 ============================== */

function RepairsSection() {
  const { source, bridge, switchMode } = useAppState()
  // single 模式没有协作修复能力：提前判断，避免发出必然 400 的请求、也不要弹出无谓红错
  const supported =
    bridge.state !== 'ok' || bridge.capabilities.collaborationRepair
  const state = useAsync<{ repairs: RepairQueueRow[]; graphs: GraphSummary[] }>(async () => {
    if (!supported) return { repairs: [], graphs: [] }
    const [repairs, graphs] = await Promise.all([source.listRepairs(), source.listGraphs()])
    return { repairs, graphs }
  }, [source, supported])

  const [planId, setPlanId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  const plan = useAsync<RepairPlanView | null>(
    () => (planId === null ? Promise.resolve(null) : source.getRepairPlan(planId)),
    [source, planId],
  )

  const runJob = async (label: string, factory: () => Promise<unknown>) => {
    setBusy(true)
    setMessage(null)
    try {
      const response = await factory()
      const jobId = (response as { jobId?: string }).jobId
      // 有些操作是同步返回（例如批量 drain 若网关直接返回结果），没有 jobId 就当作已完成。
      if (jobId === undefined) {
        setMessage(`${label}完成`)
        state.reload()
        return
      }
      for (let i = 0; i < 120; i += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 800))
        const status = await source.getJob(jobId)
        if (status.state === 'SUCCEEDED') {
          setMessage(`${label}完成`)
          state.reload()
          break
        }
        if (status.state === 'FAILED') {
          setMessage(`${label}失败：${status.error?.message ?? '未返回原因'}`)
          state.reload()
          break
        }
      }
    } catch (cause) {
      setMessage(`${label}失败：` + (cause instanceof Error ? cause.message : String(cause)))
    } finally {
      setBusy(false)
    }
  }

  const repairs = state.data?.repairs ?? []
  const graphs = state.data?.graphs ?? []

  if (!supported) {
    return (
      <UnsupportedMode
        title="当前是 single 模式，没有协作修复数据"
        description="修复队列、协作图、修复计划都由多 Agent 协作产生。切到 multi_fixed 后这一页会自动出现真实数据。"
        onSwitch={() => void switchMode('multi_fixed').catch(() => undefined)}
      />
    )
  }

  return (
    <div style={{ display: 'grid', gap: 12 }}>
      {state.error !== null && <Banner message={state.error} />}
      {message !== null && <Banner message={message} tone="running" />}

      <Card
        title="修复队列"
        description={`共 ${repairs.length} 条 · 失效事件与待修复项在同一事务记账`}
        actions={
          <div style={{ display: 'flex', gap: 6 }}>
            <button type="button" className="chip chip--button" onClick={state.reload}>
              刷新
            </button>
            <button
              type="button"
              className="chip chip--button"
              disabled={busy}
              onClick={() => void runJob('批量处理待修复项', () => source.drainRepairs())}
            >
              批量 drain
            </button>
          </div>
        }
      >
        {repairs.length === 0 && (
          <EmptyState
            title="没有待修复项"
            description="当被引用的文件被修改、删除或权限收回时，相关成果会失效并进入这个队列。"
            items={[
              '影响范围按依赖图计算：S = 直接受影响，Desc(S) = 后继闭包，A = S ∪ Desc(S)',
              'A 之外仍然有效且已核验的成果会被复用，只重做受影响部分',
              '每次默认最多处理 3 个待修复项（有界，不是常驻监听）',
            ]}
          />
        )}
        <div style={{ display: 'grid', gap: 6 }}>
          {repairs.map((row) => (
            <div
              key={`${row.run_id}-${row.event_id}`}
              style={{
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-sm)',
                padding: '8px 10px',
                display: 'flex',
                gap: 10,
                alignItems: 'center',
                flexWrap: 'wrap',
              }}
            >
              <Badge tone={REPAIR_TONE[row.status] ?? 'pending'} filled>
                {row.status}
              </Badge>
              <span className="mono" style={{ fontSize: 11 }}>
                run {row.run_id.slice(0, 16)}
              </span>
              <span className="mono" style={{ fontSize: 11, color: 'var(--fg-subtle)' }}>
                event {row.event_id.slice(0, 16)}
              </span>
              {row.error_type !== null && (
                <span style={{ fontSize: 11, color: 'var(--sem-failed)' }}>{row.error_type}</span>
              )}
              <span className="mono" style={{ fontSize: 11, color: 'var(--fg-subtle)', marginLeft: 'auto' }}>
                {formatDateTime(row.updated)}
              </span>
              <button
                type="button"
                className="chip chip--button"
                disabled={busy}
                onClick={() => void runJob('执行修复', () => source.runRepair(row.run_id, row.event_id))}
              >
                执行修复
              </button>
            </div>
          ))}
        </div>
      </Card>

      <Card title="会话内的协作图" description={`共 ${graphs.length} 张`}>
        {graphs.length === 0 && <Hint>还没有协作运行记录（需要 multi_fixed 模式下成功发起过一轮）。</Hint>}
        <div style={{ display: 'grid', gap: 8 }}>
          {graphs.map((graph) => (
            <div
              key={graph.graph_id}
              style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', padding: '8px 10px', display: 'grid', gap: 6 }}
            >
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <Badge tone={REPAIR_TONE[graph.run_status] ?? 'pending'} filled>
                  {graph.run_status}
                </Badge>
                <span className="mono" style={{ fontSize: 11 }}>
                  run {graph.run_id.slice(0, 18)}
                </span>
                <span className="mono" style={{ fontSize: 11, color: 'var(--fg-subtle)' }}>
                  task {graph.task_id}
                </span>
              </div>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {graph.nodes.map((node) => (
                  <Badge key={node.node_key} tone={NODE_TONE[node.status] ?? 'pending'} plain>
                    {node.node_key} · {node.status}
                  </Badge>
                ))}
              </div>
            </div>
          ))}
        </div>
      </Card>

      <Card
        title="修复计划"
        description="输入 repair_id 查看：哪些节点要重跑、哪些允许复用、契约校验结果"
        actions={
          <div style={{ display: 'flex', gap: 6 }}>
            <input
              value={planId ?? ''}
              onChange={(event) => setPlanId(event.target.value.trim() === '' ? null : event.target.value.trim())}
              placeholder="repair_id"
              style={inputStyle}
            />
            <button type="button" className="chip chip--button" onClick={plan.reload} disabled={planId === null}>
              查询
            </button>
          </div>
        }
      >
        {plan.error !== null && <Banner message={plan.error} />}
        {planId === null && <Hint>从上方队列里拿 repair_id（或 run_id）填入后查询。</Hint>}
        {plan.data === null && planId !== null && !plan.loading && <Hint>没有该修复计划。</Hint>}
        {plan.data !== null && (
          <div style={{ display: 'grid', gap: 10 }}>
            <KeyValue
              rows={[
                { k: 'repair_id', v: <span className="mono">{plan.data.repair_id}</span> },
                { k: 'source_run', v: <span className="mono">{plan.data.source_run_id}</span> },
                { k: 'event_id', v: <span className="mono">{plan.data.event_id}</span> },
                { k: '计划状态', v: plan.data.execution_status },
                {
                  k: '契约校验',
                  v: <Badge tone="produced" plain>{plan.data.execution_contract_validation ?? '—'}</Badge>,
                },
              ]}
            />
            <SectionLabel>处理节点</SectionLabel>
            <div style={{ display: 'grid', gap: 6 }}>
              {(plan.data.nodes ?? []).map((node, index) => (
                <div key={index} style={{ fontSize: 12, color: 'var(--fg-muted)', lineHeight: 1.7 }}>
                  <span className="mono" style={{ color: 'var(--fg)' }}>
                    {node.spec?.key ?? `node-${index}`}
                  </span>{' '}
                  · {node.action} · 原因 <span className="mono">{node.reason}</span>
                  {node.spec?.objective !== undefined && <> · {node.spec.objective}</>}
                </div>
              ))}
            </div>
            {plan.data.affected !== undefined && (
              <>
                <SectionLabel>影响范围</SectionLabel>
                <div style={{ fontSize: 12, color: 'var(--fg-muted)', lineHeight: 1.8 }}>
                  S（直接受影响）= {plan.data.affected.direct.join(', ') || '—'}
                  <br />
                  Desc(S)（后继闭包）= {plan.data.affected.descendants.join(', ') || '—'}
                  <br />
                  A = S ∪ Desc(S) = <span className="mono">{plan.data.affected.all.join(', ') || '—'}</span>
                </div>
              </>
            )}
          </div>
        )}
      </Card>
    </div>
  )
}

/* ============================== 经验 ============================== */

function ExperiencesSection() {
  const { source, bridge, switchMode } = useAppState()
  const supported = bridge.state !== 'ok' || bridge.capabilities.proceduralExperiences
  const state = useAsync<Experience[]>(
    () => (supported ? source.listExperiences() : Promise.resolve([])),
    [source, supported],
  )
  const [selected, setSelected] = useState<string | null>(null)
  const [runId, setRunId] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  const experiences = state.data ?? []
  const current = experiences.find((item) => item.experience_id === selected) ?? null

  if (!supported) {
    return (
      <UnsupportedMode
        title="当前是 single 模式，没有程序性经验数据"
        description="经验来自「整个任务通过验收并封存」的协作运行。切到 multi_fixed 并完成任务后才会产生。"
        onSwitch={() => void switchMode('multi_fixed').catch(() => undefined)}
      />
    )
  }

  const act = async (label: string, factory: () => Promise<unknown>) => {
    setBusy(true)
    setMessage(null)
    try {
      await factory()
      setMessage(`${label}已完成`)
      state.reload()
    } catch (cause) {
      setMessage(`${label}失败：` + (cause instanceof Error ? cause.message : String(cause)))
    } finally {
      setBusy(false)
    }
  }

  /**
   * learn 有两个合法结果：产出经验，或 Provider 弃权（ABSTAIN，返回 `{decision, reason}`）。
   * 后者**不是失败**，必须如实区分，否则界面会把"弃权"说成"已完成"。
   */
  const learn = async () => {
    setBusy(true)
    setMessage(null)
    try {
      const raw = (await source.learnExperience(runId)) as unknown as Record<string, unknown> | null
      if (raw === null || raw === undefined) {
        setMessage('后端返回 null：本次没有产生经验')
      } else if (raw['decision'] === 'ABSTAIN') {
        setMessage(
          `Provider 弃权（ABSTAIN）：${String(raw['reason'] ?? '未给出原因')} —— 这是真实结果，不是写入失败`,
        )
      } else {
        setMessage(
          `已提炼经验 ${String(raw['experience_id'] ?? '')}（状态 ${String(raw['state'] ?? '未知')}）`,
        )
      }
      state.reload()
    } catch (cause) {
      setMessage('提炼失败：' + (cause instanceof Error ? cause.message : String(cause)))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ display: 'grid', gap: 12 }}>
      {state.error !== null && <Banner message={state.error} />}
      {message !== null && <Banner message={message} tone="running" />}

      <Card
        title="经验列表"
        description={`共 ${experiences.length} 条 · 7 态状态机`}
        actions={
          <button type="button" className="chip chip--button" onClick={state.reload}>
            刷新
          </button>
        }
      >
        {experiences.length === 0 && (
          <EmptyState
            title="还没有程序性经验"
            description="经验来自「整个任务通过验收并封存」的运行；也可以在下面用 run_id 显式提炼。"
            items={[
              '状态：CANDIDATE / ACTIVE / QUARANTINED / NEEDS_CONFIRMATION / STALE / REVOKED / SUPERSEDED',
              '两条独立通道：Provider 提案 + 独立审核（ADMIT / QUARANTINE / NEEDS_CONFIRMATION）',
              'causal_contribution 恒为 UNKNOWN —— 系统不声称"用了这条经验就变好了"',
            ]}
          />
        )}
        <div style={{ display: 'grid', gap: 6 }}>
          {experiences.map((item) => (
            <button
              key={item.experience_id}
              type="button"
              className="thread-item"
              aria-current={item.experience_id === selected ? 'true' : undefined}
              onClick={() => setSelected(item.experience_id)}
            >
              <span style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <Badge tone={EXPERIENCE_TONE[item.state] ?? 'pending'} filled>
                  {item.state}
                </Badge>
                <span style={{ fontSize: 13 }}>{item.payload?.task_type ?? item.experience_id}</span>
              </span>
              <span style={{ fontSize: 11, color: 'var(--fg-subtle)', marginTop: 4 }}>
                {item.payload?.action_summary ?? ''}
              </span>
            </button>
          ))}
        </div>
      </Card>

      {current !== null && (
        <Card title="经验详情" description={current.experience_id}>
          <div style={{ display: 'grid', gap: 10 }}>
            <KeyValue
              rows={[
                { k: '状态', v: <Badge tone={EXPERIENCE_TONE[current.state] ?? 'pending'}>{current.state}</Badge> },
                { k: 'revision_id', v: <span className="mono">{current.revision_id.slice(0, 24)}…</span> },
                { k: '任务类型', v: current.payload?.task_type ?? '—' },
                { k: '适用条件', v: (current.payload?.preconditions ?? []).join('；') || '—' },
                { k: '行动摘要', v: current.payload?.action_summary ?? '—' },
                { k: '观察结果', v: current.payload?.observed_outcome ?? '—' },
                { k: '适用范围', v: current.payload?.applicability_scope ?? '—' },
                { k: '来源 run', v: <span className="mono">{current.payload?.source_run_id ?? '—'}</span> },
              ]}
            />
            <SectionLabel>审计事件</SectionLabel>
            <div style={{ display: 'grid', gap: 6 }}>
              {(current.events ?? []).map((event) => (
                <div key={event.event_id} style={{ fontSize: 12, color: 'var(--fg-muted)', lineHeight: 1.7 }}>
                  <span className="mono">{event.kind}</span>
                  {event.state !== undefined && <> → {event.state}</>}
                  {event.review !== undefined && <> · 判定 {event.review.verdict}</>}
                  {event.outcome !== undefined && <> · 反馈 {event.outcome}</>}
                  {event.causal_contribution !== undefined && (
                    <> · 因果贡献 <span className="mono">{event.causal_contribution}</span></>
                  )}
                </div>
              ))}
            </div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              <button
                type="button"
                className="chip chip--button"
                disabled={busy}
                onClick={() =>
                  void act('复核经验', () =>
                    source.reviewExperience(current.experience_id, confirmation || '前端人工确认'),
                  )
                }
              >
                复核（review）
              </button>
              <button
                type="button"
                className="chip chip--button"
                disabled={busy}
                onClick={() => void act('撤销经验', () => source.revokeExperience(current.experience_id, `ui_${Date.now()}`))}
                style={{ color: 'var(--sem-failed)' }}
              >
                撤销（revoke）
              </button>
            </div>
          </div>
        </Card>
      )}

      <Card collapsed title="显式提炼 / 反馈" description="需要真实 run_id；提炼与审核各自使用独立持久预算">
        <div style={{ display: 'grid', gap: 8 }}>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
            <input
              value={runId}
              onChange={(event) => setRunId(event.target.value.trim())}
              placeholder="run_id（从协作图或修复队列里取）"
              style={{ ...inputStyle, minWidth: 320 }}
            />
            <button
              type="button"
              className="chip chip--button"
              disabled={busy || runId === ''}
              onClick={() => void learn()}
            >
              learn（提炼）
            </button>
          </div>
          <input
            value={confirmation}
            onChange={(event) => setConfirmation(event.target.value)}
            placeholder="复核说明（review 用，可留空）"
            style={inputStyle}
          />
          <p style={{ margin: 0, fontSize: 11, color: 'var(--fg-subtle)', lineHeight: 1.7 }}>
            提示：learn 会真实调用 Provider（消耗调用），且要求来源运行成功且可核验；失败会返回真实原因。
          </p>
        </div>
      </Card>
    </div>
  )
}

/* ============================== 工具目录 ============================== */

function ToolsSection() {
  const { source } = useAppState()
  const state = useAsync<ToolSpec[]>(() => source.getTools(), [source])
  const tools = state.data ?? []

  return (
    <div style={{ display: 'grid', gap: 12 }}>
      {state.error !== null && <Banner message={state.error} />}
      <Card title="工具目录" description={`共 ${tools.length} 个 · effect 必须显式声明（无默认兜底）`}>
        <div style={{ display: 'grid', gap: 6 }}>
          {tools.map((tool) => (
            <details
              key={tool.name}
              style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', padding: '8px 10px' }}
            >
              <summary style={{ cursor: 'pointer', display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <span className="mono" style={{ fontSize: 13 }}>
                  {tool.name}
                </span>
                <Badge tone={EFFECT_TONE[tool.effect] ?? 'pending'} filled>
                  {tool.effect}
                </Badge>
                <span style={{ fontSize: 11, color: 'var(--fg-subtle)' }}>
                  幂等 {tool.idempotencySupport}
                </span>
                {tool.effect === 'SIDE_EFFECT' && (
                  <span style={{ fontSize: 11, color: 'var(--sem-waiting)' }}>需人工确认</span>
                )}
              </summary>
              <p style={{ margin: '8px 0 6px', fontSize: 12, color: 'var(--fg-muted)', lineHeight: 1.7 }}>
                {tool.description}
              </p>
              <pre className="mono" style={preStyle}>
                {JSON.stringify(tool.inputSchema, null, 2)}
              </pre>
            </details>
          ))}
        </div>
      </Card>
    </div>
  )
}

/* ============================== 环境 ============================== */

function EnvSection() {
  const { source } = useAppState()
  const state = useAsync<{ workspace: Record<string, unknown>; storage: Record<string, string> }>(
    async () => {
      const [workspace, storage] = await Promise.all([source.getWorkspace(), source.getStorage()])
      return { workspace, storage }
    },
    [source],
  )

  const workspace = state.data?.workspace ?? {}
  const storage = state.data?.storage ?? {}
  const roots = Array.isArray(workspace['roots'])
    ? (workspace['roots'] as Record<string, unknown>[])
    : []

  return (
    <div style={{ display: 'grid', gap: 12 }}>
      {state.error !== null && <Banner message={state.error} />}
      <Card title="工作区范围" description="本地文件工具的读写范围与终端 cwd 由此决定">
        <div style={{ display: 'grid', gap: 8 }}>
          <KeyValue rows={[{ k: '当前根', v: <span className="mono">{String(workspace['current_root'] ?? '—')}</span> }]} />
          <div style={{ display: 'grid', gap: 4 }}>
            {roots.map((root, index) => (
              <div key={index} style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
                <span className="mono">{String(root['path'] ?? '')}</span>
                {root['current'] === true && <Badge tone="accent" plain>当前</Badge>}
                {root['exists'] === false && <Badge tone="failed" plain>不存在</Badge>}
              </div>
            ))}
          </div>
        </div>
      </Card>
      <Card title="存储布局" description="权威数据按职责分目录；SQLite 是权威存储">
        <KeyValue
          rows={Object.entries(storage)
            .slice(0, 24)
            .map(([key, value]) => ({
              k: key,
              v: <span className="mono" style={{ fontSize: 11 }}>{value}</span>,
            }))}
        />
      </Card>
    </div>
  )
}

/* ============================== 通用 ============================== */

/** 当前模式不支持该治理分区时，给出解释与一键切换入口（而不是抛红错） */
function UnsupportedMode({
  title,
  description,
  onSwitch,
}: {
  title: string
  description: string
  onSwitch(): void
}) {
  return (
    <Card title={title}>
      <div style={{ display: 'grid', gap: 10 }}>
        <p style={{ margin: 0, fontSize: 13, lineHeight: 1.8, color: 'var(--fg-muted)' }}>
          {description}
        </p>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <button type="button" className="chip chip--button" onClick={onSwitch}>
            切换到 multi_fixed 协作
          </button>
          <span style={{ fontSize: 11, color: 'var(--fg-subtle)' }}>
            也可以点顶栏「模式」切换；切换在下一轮生效，历史数据保留
          </span>
        </div>
      </div>
    </Card>
  )
}

const preStyle = {
  margin: 0,
  padding: '8px 10px',
  background: 'var(--bg-sunken)',
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius-sm)',
  color: 'var(--fg-muted)',
  fontSize: 11,
  lineHeight: 1.6,
  maxHeight: 240,
  overflow: 'auto',
  whiteSpace: 'pre-wrap' as const,
  wordBreak: 'break-all' as const,
}

const inputStyle = {
  padding: '6px 10px',
  borderRadius: 'var(--radius-sm)',
  border: '1px solid var(--border)',
  background: 'var(--bg)',
  color: 'var(--fg)',
  fontFamily: 'var(--font-mono)',
  fontSize: 12,
}

function Banner({ message, tone = 'failed' }: { message: string; tone?: 'failed' | 'running' }) {
  return <SharedBanner tone={tone === 'running' ? 'info' : 'error'}>{message}</SharedBanner>
}

function Hint({ children }: { children: React.ReactNode }) {
  return <SharedHint>{children}</SharedHint>
}
