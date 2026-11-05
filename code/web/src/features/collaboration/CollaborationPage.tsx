import { useEffect, useState } from 'react'
import type {
  CollaborationRunDetail,
  CollaborationRunSummary,
  CollaborationStage,
} from '@/types'
import { useAppState } from '@/state/AppState'
import { useAsync } from '@/lib/useAsync'
import { withErrorTranslation } from '@/lib/errorText'
import { Badge, Card, EmptyState, KeyValue, SectionLabel } from '@/components/ui/primitives'
import { DagView } from './dagView'
import { RUN_STATUS_TONE, toneOf } from './tone'
import { Banner, Hint, MoreInfo } from '@/components/ui/feedback'

const TIME_FORMAT = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
})

function formatTime(seconds: number | undefined): string {
  if (!seconds) return '—'
  return TIME_FORMAT.format(new Date(seconds * 1000)).replace(/\//g, '-')
}

/**
 * 协作拓扑（P4）。
 *
 * 数据全部来自真实落盘：`coordination.db` 的 runs / task_graphs / task_nodes / role_tasks /
 * artifacts / model_calls / handoffs，经桥接只读接口读出，前端不做任何推断或补造。
 * 页面回答三个问题：**谁做了什么、依赖谁、是否真的通过核验、花了多少**。
 */
export function CollaborationPage() {
  const { source, bridge, switchMode } = useAppState()
  const singleMode = bridge.state === 'ok' && bridge.executionMode !== 'multi_fixed'
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [selectedStage, setSelectedStage] = useState<string | null>(null)

  const runs = useAsync<CollaborationRunSummary[]>(
    () => (singleMode ? Promise.resolve([]) : source.listCollaborationRuns(true)),
    [source, singleMode],
  )

  const list = runs.data ?? []

  // 默认选中最近一次运行
  useEffect(() => {
    if (selectedRunId === null && list.length > 0) {
      setSelectedRunId(list[0]?.run_id ?? null)
    }
  }, [list, selectedRunId])

  const detail = useAsync<CollaborationRunDetail | null>(
    () =>
      selectedRunId === null
        ? Promise.resolve(null)
        : source.getCollaborationRun(selectedRunId),
    [source, selectedRunId],
  )

  if (singleMode) {
    return (
      <div style={{ display: 'grid', gap: 14 }}>
        <PageHead />
        <Card title="当前是 single 模式，没有协作拓扑">
          <div style={{ display: 'grid', gap: 10 }}>
            <p style={{ margin: 0, fontSize: 13, lineHeight: 1.8, color: 'var(--fg-muted)' }}>
              协作拓扑只在 <span className="mono">multi_fixed</span> 模式下产生：Planner 拆子任务、
              Memory 选证据、Executor 执行、Verifier 独立核验。切到协作模式后发一条消息即可看到真实 DAG。
            </p>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <button
                type="button"
                className="chip chip--button"
                onClick={() => void switchMode('multi_fixed').catch(() => undefined)}
              >
                切换到 multi_fixed
              </button>
              <Hint>切换在下一轮生效；协作模式 token 消耗明显更高。</Hint>
            </div>
          </div>
        </Card>
      </div>
    )
  }

  return (
    <div style={{ display: 'grid', gap: 14 }}>
      <PageHead />

      {runs.error !== null && <Banner tone="error">{withErrorTranslation(runs.error)}</Banner>}

      <Card
        title="协作运行"
        description="按时间倒序的最近运行（跨会话）"
        actions={
          <button type="button" className="chip chip--button" onClick={runs.reload} disabled={runs.loading}>
            {runs.loading ? '读取中…' : '刷新'}
          </button>
        }
      >
        {runs.loading && list.length === 0 ? (
          <Hint>正在读取 coordination 数据库…</Hint>
        ) : list.length === 0 ? (
          <EmptyState
            title="还没有协作运行"
            description="在 multi_fixed 模式下发一条消息，Planner 会拆出子任务并产生一次协作运行。"
            items={[
              '运行记录保存在 coordination.db（仓库之外）',
              '每个子任务都会走 Memory → Executor → 证据交接 → Verifier',
              '核验不通过时运行会 FAILED，并保留失败原因与回执',
            ]}
          />
        ) : (
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {list.map((run) => {
              const active = run.run_id === selectedRunId
              return (
                <button
                  key={run.run_id}
                  type="button"
                  onClick={() => {
                    setSelectedRunId(run.run_id)
                    setSelectedStage(null)
                  }}
                  style={{
                    display: 'grid',
                    gap: 3,
                    textAlign: 'left',
                    minWidth: 210,
                    padding: '8px 10px',
                    borderRadius: 'var(--radius-sm)',
                    border:
                      '1px solid ' +
                      (active
                        ? 'color-mix(in oklab, var(--accent) 50%, transparent)'
                        : 'var(--border)'),
                    background: active
                      ? 'color-mix(in oklab, var(--accent) 10%, transparent)'
                      : 'transparent',
                    color: 'var(--fg)',
                    cursor: 'pointer',
                  }}
                >
                  <span style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                    <Badge tone={RUN_STATUS_TONE[run.run_status] ?? 'neutral'} filled>
                      {run.run_status}
                    </Badge>
                    <span className="mono" style={{ fontSize: 10.5, color: 'var(--fg-subtle)' }}>
                      {run.run_id.slice(0, 16)}…
                    </span>
                  </span>
                  <span style={{ fontSize: 11.5, color: 'var(--fg-muted)' }}>
                    {run.nodes.length} 个子任务 · {formatTime(run.started)}
                  </span>
                  <span className="mono" style={{ fontSize: 10, color: 'var(--fg-subtle)' }}>
                    task={run.task_id ?? '—'}
                  </span>
                </button>
              )
            })}
          </div>
        )}
      </Card>

      {selectedRunId !== null && (
        <>
          {detail.error !== null && (
            <Banner tone="error">{withErrorTranslation(detail.error)}</Banner>
          )}
          {detail.data !== null ? (
            <RunDetail
              detail={detail.data}
              selectedStage={selectedStage}
              onSelectStage={setSelectedStage}
            />
          ) : (
            !detail.loading && <Hint>未找到该运行的详情</Hint>
          )}
        </>
      )}
    </div>
  )
}

function PageHead() {
  return (
    <header style={{ display: 'grid', gap: 6 }}>
      <h1 style={{ margin: 0, fontSize: 18, fontWeight: 650 }}>协作拓扑</h1>
      <Hint>
        Planner / Memory / Executor / Verifier 的分工、依赖与核验结果；数据来自
        coordination.db 的真实记录（只读读取，不补造任何节点）。
      </Hint>
    </header>
  )
}

/* ============================== 详情 ============================== */

function RunDetail({
  detail,
  selectedStage,
  onSelectStage,
}: {
  detail: CollaborationRunDetail
  selectedStage: string | null
  onSelectStage(stageKey: string | null): void
}) {
  const maxUnits = Math.max(
    1,
    ...detail.modelCalls.byRole.map((bucket) => bucket.chargedUnits),
  )
  const callsLimit = detail.limits.max_model_calls ?? null
  const unitsLimit = detail.limits.max_token_units ?? null
  const activeStage = detail.roles.find((stage) => stage.stage_key === selectedStage) ?? null

  return (
    <div style={{ display: 'grid', gap: 14 }}>
      <Card
        title="这次运行"
        actions={
          <Badge tone={RUN_STATUS_TONE[detail.status] ?? 'neutral'} filled>
            {detail.status}
          </Badge>
        }
      >
        <KeyValue
          rows={[
            {
              k: '运行 / 轮次',
              v: (
                <span className="mono" style={{ fontSize: 11 }}>
                  {detail.runId} · {detail.turnId}
                </span>
              ),
            },
            { k: '开始时间', v: formatTime(detail.started) },
            {
              k: '预算',
              v: (
                <span className="mono" style={{ fontSize: 11 }}>
                  调用 {detail.modelCalls.total}/{callsLimit ?? '—'} · 核算单位{' '}
                  {detail.modelCalls.chargedUnits.toLocaleString()}/{unitsLimit?.toLocaleString() ?? '—'}
                  {detail.usage?.providerReportedTokenUnits !== null &&
                    detail.usage?.providerReportedTokenUnits !== undefined && (
                      <span style={{ color: 'var(--fg-subtle)' }}>
                        {' '}
                        （Provider 实报{' '}
                        {detail.usage.providerReportedTokenUnits.toLocaleString()} · 预留估算{' '}
                        {(detail.usage.estimatedTokenUnits ?? 0).toLocaleString()}）
                      </span>
                    )}
                </span>
              ),
            },
            {
              k: '子任务',
              v:
                detail.plan.subtasks.length === 0
                  ? '—'
                  : detail.plan.subtasks.map((item) => item.key).join(' → '),
            },
            {
              k: '核验通过',
              v:
                detail.result.verifiedSubtasks.length === 0
                  ? '无'
                  : detail.result.verifiedSubtasks.join(', '),
            },
          ]}
        />
      </Card>

      <Card
        title="任务 DAG"
        description="左 → 右：计划 → 每个子任务的记忆选择 / 执行 / 证据交接 / 核验 → 整体核验；点节点看细节"
        actions={
          <button
            type="button"
            className="chip chip--button"
            onClick={() => onSelectStage(null)}
            disabled={activeStage === null}
          >
            清除选中
          </button>
        }
      >
        <DagView detail={detail} selectedStage={selectedStage} onSelectStage={onSelectStage} />
        {activeStage !== null && <StageDetail detail={detail} stage={activeStage} />}
      </Card>

      {detail.status === 'FAILED' && (
        <Card title="为什么失败" description="后端记录的阻断原因与回执证据（原样展示，不美化）">
          <div style={{ display: 'grid', gap: 8 }}>
            {detail.result.errorInfo !== null ? (
              <pre
                className="mono"
                style={{
                  margin: 0,
                  padding: '8px 10px',
                  background: 'var(--bg-sunken)',
                  border: '1px solid var(--border)',
                  borderRadius: 'var(--radius-sm)',
                  fontSize: 11,
                  lineHeight: 1.7,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                }}
              >
                {JSON.stringify(detail.result.errorInfo, null, 2)}
              </pre>
            ) : (
              <Hint>没有 error_info 记录（运行状态为 FAILED，但未写入结构化错误）。</Hint>
            )}
            {detail.result.warnings.length > 0 && (
              <MoreInfo label={`警告 ${detail.result.warnings.length} 条`}>
                <ul style={{ margin: 0, paddingLeft: 18, fontSize: 11.5, color: 'var(--sem-unknown)' }}>
                  {detail.result.warnings.map((warning) => (
                    <li key={warning}>{withErrorTranslation(warning)}</li>
                  ))}
                </ul>
              </MoreInfo>
            )}
            <Hint>
              计费口径：本次有 {detail.modelCalls.failedWithEmptyUsage} 次调用没有 Provider 回传
              usage，后端按预留额度估算（usage_source=reservation）。真实 token 数见「模型调用分布」。
            </Hint>
          </div>
        </Card>
      )}

      <Card title="模型调用分布" description="按角色统计调用次数、失败次数与核算单位">
        <div style={{ display: 'grid', gap: 8 }}>
          {detail.usage?.providerReportedTokenUnits !== null &&
            detail.usage?.providerReportedTokenUnits !== undefined && (
              <Hint>
                预算口径：Provider 实报 {detail.usage.providerReportedTokenUnits.toLocaleString()}{' '}
                token 单位，另有 {(detail.usage.estimatedTokenUnits ?? 0).toLocaleString()}{' '}
                单位按预留额度估算（无 usage 回传）。
              </Hint>
            )}
          {detail.modelCalls.byRole.map((bucket) => (
            <div key={bucket.role} style={{ display: 'grid', gap: 3 }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 12 }}>
                <span style={{ width: 84, color: 'var(--fg-muted)' }}>{bucket.role}</span>
                <span className="mono" style={{ fontSize: 11, color: 'var(--fg-subtle)' }}>
                  {bucket.calls} 次
                  {bucket.failed > 0 ? ` · 失败 ${bucket.failed}` : ''}
                  {(bucket.estimatedCalls ?? 0) > 0
                    ? ` · 估算 ${bucket.estimatedCalls} 次`
                    : ''}{' '}
                  · {bucket.chargedUnits.toLocaleString()} 单位
                </span>
              </div>
              <div
                style={{
                  height: 6,
                  borderRadius: 999,
                  background: 'var(--bg-sunken)',
                  overflow: 'hidden',
                }}
              >
                <div
                  style={{
                    width: `${Math.max(2, Math.round((bucket.chargedUnits / maxUnits) * 100))}%`,
                    height: '100%',
                    background: bucket.failed > 0 ? 'var(--sem-failed)' : 'var(--accent)',
                    opacity: 0.8,
                  }}
                />
              </div>
            </div>
          ))}
          {detail.modelCalls.failedWithEmptyUsage > 0 && (
            <Banner tone="info">
              有 {detail.modelCalls.failedWithEmptyUsage} 次调用没有 Provider 回传 usage，
              按预留额度估算计费（usage_source=reservation）。
            </Banner>
          )}
        </div>
      </Card>

      <Card title="证据与交接" collapsed>
        <div style={{ display: 'grid', gap: 10 }}>
          <KeyValue
            rows={[
              { k: '产物', v: `${detail.artifacts.length} 个` },
              { k: '交接记录', v: `${detail.handoffs.length} 条` },
              { k: '来源读取', v: `${detail.result.sourceReads.length} 条` },
              { k: '已执行工具调用', v: `${detail.result.executedToolCalls.length} 个` },
            ]}
          />
          {detail.artifacts.map((artifact) => (
            <div key={artifact.artifact_id} style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <Badge tone={toneOf(artifact.verification_status)} plain>
                {artifact.verification_status}
              </Badge>
              <span className="mono" style={{ fontSize: 10.5, color: 'var(--fg-subtle)' }}>
                {artifact.stage_key} · {artifact.artifact_id}
              </span>
            </div>
          ))}
          {detail.handoffs.length > 0 && (
            <MoreInfo label={`交接明细 ${detail.handoffs.length} 条`}>
              <ul style={{ margin: 0, paddingLeft: 18, fontSize: 11, lineHeight: 1.8, color: 'var(--fg-muted)' }}>
                {detail.handoffs.map((handoff, index) => (
                  <li key={`${handoff.stage_key}-${index}`}>
                    {handoff.stage_key}：{handoff.sender} → {handoff.receiver}
                  </li>
                ))}
              </ul>
            </MoreInfo>
          )}
        </div>
      </Card>

      {detail.result.finalResponse !== '' && (
        <Card title="本轮最终回答" collapsed>
          <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.8, whiteSpace: 'pre-wrap' }}>
            {detail.result.finalResponse}
          </p>
        </Card>
      )}
    </div>
  )
}

function StageDetail({ detail, stage }: { detail: CollaborationRunDetail; stage: CollaborationStage }) {
  const artifact = detail.artifacts.find((item) => item.stage_key === stage.stage_key) ?? null
  const subtaskKey = stage.stage_key.includes(':')
    ? stage.stage_key.split(':').slice(-1)[0]
    : null
  const subtask =
    subtaskKey === null ? null : detail.plan.subtasks.find((item) => item.key === subtaskKey) ?? null

  return (
    <div style={{ marginTop: 10, display: 'grid', gap: 8 }}>
      <SectionLabel>节点细节</SectionLabel>
      <KeyValue
        rows={[
          { k: '阶段', v: <span className="mono">{stage.stage_key}</span> },
          { k: '角色', v: stage.role },
          { k: '状态', v: <Badge tone={toneOf(stage.status)} plain>{stage.status}</Badge> },
          {
            k: '产物',
            v: artifact === null ? (
              '—'
            ) : (
              <span className="mono" style={{ fontSize: 11 }}>
                {artifact.artifact_id}（{artifact.verification_status}）
              </span>
            ),
          },
        ]}
      />
      {subtask !== null && (subtask.objective || subtask.completion_criteria) && (
        <MoreInfo label="子任务目标与完成标准" open>
          <div style={{ display: 'grid', gap: 4, fontSize: 12, lineHeight: 1.8 }}>
            <span>
              <b>目标：</b>
              {subtask.objective ?? '—'}
            </span>
            <span>
              <b>完成标准：</b>
              {subtask.completion_criteria ?? '—'}
            </span>
            <span className="mono" style={{ fontSize: 10.5, color: 'var(--fg-subtle)' }}>
              工具：{(subtask.tool_names ?? []).join(', ') || '无'}
            </span>
          </div>
        </MoreInfo>
      )}
    </div>
  )
}
