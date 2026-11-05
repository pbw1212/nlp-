import { useEffect, useState } from 'react'
import type { ProvenanceRef } from '@/types'
import { useAppState } from '@/state/AppState'
import { useAsync } from '@/lib/useAsync'
import { formatDateTime, formatRatio } from '@/lib/format'
import { Badge, Card, EmptyState, KeyValue, SectionLabel } from '@/components/ui/primitives'
import { Banner as SharedBanner, Hint as SharedHint, MoreInfo } from '@/components/ui/feedback'
import type { Tone } from '@/components/ui/primitives'
import {
  buildRevisions,
  toHistoryRows,
  toMemoryDetail,
  toMemoryListRows,
  toProvenanceGroups,
} from './memoryModel'
import type { MemoryDetailRow, MemoryListRow, RevisionView } from './memoryModel'

const VALIDITY_TONE: Record<string, Tone> = {
  CURRENT: 'verified',
  SUPERSEDED: 'stale',
  CONTRADICTED: 'failed',
  EXPIRED: 'stale',
  UNKNOWN: 'pending',
}

const RETENTION_TONE: Record<string, Tone> = {
  ACTIVE: 'verified',
  ARCHIVED: 'pending',
  RECYCLED: 'cancelled',
  UNKNOWN: 'pending',
}

const CHANGE_TONE: Record<string, Tone> = {
  ADD: 'accent',
  UPDATE: 'produced',
  CORRECT: 'unknown',
  REMOVE: 'failed',
  NOOP: 'pending',
  RISE: 'produced',
  LEGACY_IMPORT: 'stale',
  UNKNOWN: 'pending',
}

/** 来源权威：显式用户 > 工具/系统 > 模型推断 > 未知。必须区分，不能一律画成"可信" */
const AUTHORITY_TONE: Record<string, Tone> = {
  EXPLICIT_USER: 'verified',
  SYSTEM_OR_TOOL: 'produced',
  INFERRED: 'waiting',
  UNKNOWN: 'stale',
}

const TYPE_LABEL: Record<string, string> = {
  CORE_PROFILE: '核心画像',
  PREFERENCE_RULES: '偏好规则',
  EPISODIC_GRAPH: '情景图',
  UNKNOWN: '未知类型',
}

const GRANULARITY_LABEL: Record<string, string> = {
  FINE_EPISODE: '细粒度情景',
  SEMANTIC_INSIGHT: '语义洞察',
  HIGH_ABSTRACT: '高抽象',
  UNKNOWN: '未知粒度',
}

/**
 * 记忆库（P3）。
 *
 * 要证明三件事，全部来自 Task 1 真实数据：
 * 1. 记忆有**版本**：历史版本都保留，当前版本有明确标记；
 * 2. 记忆有**来源**：每版来自哪条消息、什么权威等级；
 * 3. 删除是**可恢复**的：忘记 → 回收站 → 恢复，历史不丢。
 */
export function MemoryPage() {
  const { source, pendingMemoryFocus, clearMemoryFocus } = useAppState()
  const [showRecycle, setShowRecycle] = useState(false)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // 跨页跳转：证据页点了某条记忆 → 这里预选它并清除焦点标记
  useEffect(() => {
    if (pendingMemoryFocus !== null) {
      setSelectedId(pendingMemoryFocus)
      clearMemoryFocus()
    }
  }, [pendingMemoryFocus, clearMemoryFocus])

  const list = useAsync<MemoryListRow[]>(async () => {
    const raw = showRecycle ? await source.listRecycleBin() : await source.listMemories()
    return toMemoryListRows(raw)
  }, [source, showRecycle])

  const detail = useAsync<{
    detail: MemoryDetailRow | null
    revisions: RevisionView[]
    provenanceCount: number
  }>(async () => {
    if (selectedId === null) {
      return { detail: null, revisions: [], provenanceCount: 0 }
    }
    const [rawDetail, rawHistory, rawProvenance] = await Promise.all([
      source.getMemory(selectedId),
      source.getMemoryHistory(selectedId),
      source.getMemoryProvenance(selectedId),
    ])
    const history = toHistoryRows(rawHistory)
    const groups = toProvenanceGroups(rawProvenance)
    return {
      detail: toMemoryDetail(rawDetail),
      revisions: buildRevisions(history, groups),
      provenanceCount: groups.reduce((sum, group) => sum + group.refs.length, 0),
    }
  }, [source, selectedId])

  const memories = list.data ?? []

  const runAction = async (action: () => Promise<unknown>) => {
    setBusy(true)
    setActionError(null)
    try {
      await action()
      list.reload()
      detail.reload()
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <header style={{ display: 'grid', gap: 6 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <h1 style={{ fontSize: 20, fontWeight: 650, margin: 0 }}>长期记忆库</h1>
          <Badge tone="verified">已接入真实数据</Badge>
          <Badge tone="accent" plain>
            Task 1
          </Badge>
        </div>
        <p style={{ margin: 0, fontSize: 13, color: 'var(--fg-muted)', lineHeight: 1.7 }}>
          一条记忆 = 稳定身份 + 版本序列 + 来源引用。当前有效版本可变，历史版本不可变；删除进入回收站，可恢复。
        </p>
      </header>

      <Hint>每条记忆是一条稳定的「身份」，内容按版本更新；删除是可恢复的回收。右侧展示当前版本与每一版的来源。</Hint>

      <RememberForm disabled={busy} onSubmit={(content) => runAction(() => source.remember(content))} />

      {actionError !== null && <Banner message={actionError} />}
      {list.error !== null && <Banner message={list.error} />}

      <div style={{ display: 'grid', gridTemplateColumns: '320px minmax(0, 1fr)', gap: 16 }}>
        <Card
          title={showRecycle ? '回收站' : '当前有效记忆'}
          description={`共 ${memories.length} 条`}
          actions={
            <div style={{ display: 'flex', gap: 6 }}>
              <button
                type="button"
                className="chip chip--button"
                onClick={() => {
                  setShowRecycle((value) => !value)
                  setSelectedId(null)
                }}
              >
                {showRecycle ? '看当前有效' : '看回收站'}
              </button>
              <button type="button" className="chip chip--button" onClick={list.reload}>
                刷新
              </button>
            </div>
          }
        >
          {list.loading && <Hint>读取中…</Hint>}
          {!list.loading && memories.length === 0 && (
            <Hint>
              暂无记忆。可以在上方「显式写入」添加一条；或等 Agent 在对话中自主调用记忆工具后产生。
            </Hint>
          )}
          <div style={{ display: 'grid', gap: 6, marginTop: memories.length > 0 ? 10 : 0 }}>
            {memories.map((item) => (
              <button
                key={item.memory_id}
                type="button"
                className="thread-item"
                aria-current={item.memory_id === selectedId ? 'true' : undefined}
                onClick={() => setSelectedId(item.memory_id)}
              >
                <span
                  style={{
                    fontSize: 13,
                    overflow: 'hidden',
                    display: '-webkit-box',
                    WebkitLineClamp: 2,
                    WebkitBoxOrient: 'vertical',
                  }}
                >
                  {item.content === '' ? '(内容为空)' : item.content}
                </span>
                <span style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 6, alignItems: 'center' }}>
                  <Badge tone="accent" plain>
                    {TYPE_LABEL[item.type] ?? item.type}
                  </Badge>
                  <span className="mono" style={{ fontSize: 10, color: 'var(--fg-subtle)' }}>
                    {GRANULARITY_LABEL[item.granularity] ?? item.granularity}
                  </span>
                </span>
              </button>
            ))}
          </div>
        </Card>

        <div style={{ display: 'grid', gap: 16, alignContent: 'start' }}>
          {selectedId === null ? (
            <EmptyState
              title="选择左侧一条记忆"
              description="右侧会显示它的当前状态、完整版本时间轴、每一版的来源，以及忘记/恢复操作。"
              items={[
                '当前状态来自 get_memory_detail（扁平字段：版本号、置信度、语义有效性、来源数）',
                '版本时间轴 = get_memory_history（正文+时间）× get_memory_provenance（版本号+变更类型+来源）按 revision_id 关联',
                '忘记 = 移入回收站（可恢复），不是物理删除',
              ]}
              note="列表接口只返回 4 个字段（id/类型/内容/粒度），版本与来源信息需要点开详情才能取到——这是后端接口的现状，界面如实体现。"
            />
          ) : detail.loading ? (
            <Card title="详情">
              <Hint>读取中…</Hint>
            </Card>
          ) : detail.data?.detail === null || detail.data?.detail === undefined ? (
            <Card title="详情">
              <Hint>没有取到这条记忆的详情（可能已不存在或没有当前版本）。</Hint>
            </Card>
          ) : (
            <MemoryDetail
              detail={detail.data.detail}
              revisions={detail.data.revisions}
              provenanceCount={detail.data.provenanceCount}
              busy={busy}
              onForget={() => void runAction(() => source.forgetMemory(detail.data!.detail!.memory_id))}
              onRestore={() => void runAction(() => source.restoreMemory(detail.data!.detail!.memory_id))}
            />
          )}
        </div>
      </div>
    </div>
  )
}

function MemoryDetail({
  detail,
  revisions,
  provenanceCount,
  busy,
  onForget,
  onRestore,
}: {
  detail: MemoryDetailRow
  revisions: RevisionView[]
  provenanceCount: number
  busy: boolean
  onForget(): void
  onRestore(): void
}) {
  const recycled = detail.retention === 'RECYCLED'

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <Card
        title="当前状态"
        actions={
          <div style={{ display: 'flex', gap: 6 }}>
            {recycled ? (
              <button type="button" className="chip chip--button" disabled={busy} onClick={onRestore}>
                从回收站恢复
              </button>
            ) : (
              <button
                type="button"
                className="chip chip--button"
                disabled={busy}
                onClick={onForget}
                style={{ color: 'var(--sem-failed)' }}
              >
                忘记（移入回收站）
              </button>
            )}
          </div>
        }
      >
        <MoreInfo label="更多信息（ID / 类型 / 置信度 / 来源数）">
        <KeyValue
          rows={[
            { k: 'memory_id', v: <span className="mono">{detail.memory_id}</span> },
            {
              k: '类型 / 粒度',
              v: `${TYPE_LABEL[detail.type] ?? detail.type} · ${GRANULARITY_LABEL[detail.granularity] ?? detail.granularity}`,
            },
            {
              k: '语义有效性',
              v: (
                <Badge tone={VALIDITY_TONE[detail.semantic_validity] ?? 'pending'}>
                  {detail.semantic_validity}
                </Badge>
              ),
            },
            {
              k: '保留处置',
              v: (
                <Badge tone={RETENTION_TONE[detail.retention] ?? 'pending'}>{detail.retention}</Badge>
              ),
            },
            { k: '置信度', v: formatRatio(detail.confidence, 2) },
            { k: '当前 revision', v: <span className="mono">{detail.revision_id}</span> },
            { k: '来源条数', v: `${detail.provenance_count}（下方按版本列出）` },
          ]}
        />
        </MoreInfo>
        <div style={{ marginTop: 12 }}>
          <SectionLabel>当前版本正文</SectionLabel>
          <p style={{ margin: 0, fontSize: 13, lineHeight: 1.85, whiteSpace: 'pre-wrap' }}>
            {detail.content === '' ? '(内容为空)' : detail.content}
          </p>
        </div>
      </Card>

      <Card
        title="版本时间轴"
        description={`共 ${revisions.length} 版 · 来源引用 ${provenanceCount} 条`}
      >
        {revisions.length === 0 && <Hint>没有版本记录。</Hint>}
        <div style={{ display: 'grid', gap: 12 }}>
          {revisions.map((revision) => (
            <RevisionCard key={revision.revision_id} revision={revision} currentRevisionId={detail.revision_id} />
          ))}
        </div>
        <p style={{ margin: '10px 0 0', fontSize: 11, color: 'var(--fg-subtle)', lineHeight: 1.6 }}>
          版本号与变更类型来自 provenance 接口；若某版没有来源记录，则只显示正文与时间，不做编号猜测。
        </p>
      </Card>
    </div>
  )
}

function RevisionCard({
  revision,
  currentRevisionId,
}: {
  revision: RevisionView
  currentRevisionId: string
}) {
  const isCurrent = revision.revision_id === currentRevisionId
  return (
    <div
      style={{
        borderLeft: '2px solid ' + (isCurrent ? 'var(--sem-verified)' : 'var(--border-strong)'),
        paddingLeft: 12,
        display: 'grid',
        gap: 6,
      }}
    >
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        {revision.revision_no !== null ? (
          <span className="mono" style={{ fontSize: 12 }}>
            v{revision.revision_no}
          </span>
        ) : (
          <Badge tone="pending" plain>
            无版本号
          </Badge>
        )}
        {revision.change_kind !== null && (
          <Badge tone={CHANGE_TONE[revision.change_kind] ?? 'pending'} plain>
            {revision.change_kind}
          </Badge>
        )}
        {isCurrent && (
          <Badge tone="verified" filled>
            当前版本
          </Badge>
        )}
        <span className="mono" style={{ fontSize: 11, color: 'var(--fg-subtle)', marginLeft: 'auto' }}>
          {formatDateTime(revision.created_at)}
        </span>
      </div>

      {revision.content !== '' && (
        <p style={{ margin: 0, fontSize: 13, lineHeight: 1.75, color: 'var(--fg-muted)', whiteSpace: 'pre-wrap' }}>
          {revision.content}
        </p>
      )}

      {revision.refs.length === 0 ? (
        <span style={{ fontSize: 11, color: 'var(--fg-subtle)' }}>本版没有来源记录。</span>
      ) : (
        <div style={{ display: 'grid', gap: 6 }}>
          {revision.refs.map((ref: ProvenanceRef, index: number) => (
            <div
              key={ref.provenance_id ?? index}
              style={{
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-sm)',
                padding: '6px 8px',
                display: 'grid',
                gap: 4,
              }}
            >
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <Badge tone={AUTHORITY_TONE[ref.authority ?? 'UNKNOWN'] ?? 'stale'} filled>
                  {ref.authority ?? 'UNKNOWN'}
                </Badge>
                <span className="mono" style={{ fontSize: 11, color: 'var(--fg-subtle)' }}>
                  {ref.source_type ?? 'unknown'}
                </span>
                <span className="mono" style={{ fontSize: 11, color: 'var(--fg-subtle)', marginLeft: 'auto' }}>
                  {formatDateTime(ref.observed_at ?? null)}
                </span>
              </div>
              {(ref.controlled_excerpt ?? '') !== '' && (
                <p style={{ margin: 0, fontSize: 12, lineHeight: 1.7, color: 'var(--fg-muted)' }}>
                  “{ref.controlled_excerpt}”
                </p>
              )}
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                {ref.session_id != null && (
                  <span className="mono" style={{ fontSize: 10, color: 'var(--fg-subtle)' }}>
                    session {String(ref.session_id).slice(0, 16)}
                  </span>
                )}
                {ref.message_id != null && (
                  <span className="mono" style={{ fontSize: 10, color: 'var(--fg-subtle)' }}>
                    msg {String(ref.message_id).slice(0, 16)}
                  </span>
                )}
                {ref.content_hash != null && (
                  <span className="mono" style={{ fontSize: 10, color: 'var(--fg-subtle)' }}>
                    hash {String(ref.content_hash).slice(0, 12)}…
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function RememberForm({
  disabled,
  onSubmit,
}: {
  disabled: boolean
  onSubmit(content: string): Promise<void>
}) {
  const [value, setValue] = useState('')
  const [message, setMessage] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  return (
    <Card
      title="显式写入记忆"
      description="调用 remember_explicit：走 Provider 的显式记忆请求分析，只有真实持久化成功才算成功"
    >
      <div style={{ display: 'grid', gap: 8 }}>
        <textarea
          value={value}
          onChange={(event) => setValue(event.target.value)}
          rows={2}
          placeholder="例如：我不吃香菜（写入后会在左侧列表出现，并带来源）"
          style={{
            width: '100%',
            padding: '8px 10px',
            borderRadius: 'var(--radius-sm)',
            border: '1px solid var(--border)',
            background: 'var(--bg)',
            color: 'var(--fg)',
            fontFamily: 'var(--font-sans)',
            fontSize: 13,
            lineHeight: 1.7,
            resize: 'vertical',
          }}
        />
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <button
            type="button"
            className="chip chip--button"
            disabled={disabled || submitting || value.trim().length === 0}
            onClick={() => {
              void (async () => {
                setSubmitting(true)
                setMessage(null)
                try {
                  await onSubmit(value.trim())
                  setMessage('已提交。成功与否以左侧列表是否出现新条目为准（接口返回真实结果）。')
                  setValue('')
                } catch (cause) {
                  setMessage('写入失败：' + (cause instanceof Error ? cause.message : String(cause)))
                } finally {
                  setSubmitting(false)
                }
              })()
            }}
          >
            {submitting ? '写入中…' : '写入记忆'}
          </button>
          {message !== null && (
            <span style={{ fontSize: 12, color: 'var(--fg-muted)', lineHeight: 1.6 }}>{message}</span>
          )}
        </div>
      </div>
    </Card>
  )
}

function Banner({ message, tone = 'failed' }: { message: string; tone?: 'failed' | 'running' }) {
  return <SharedBanner tone={tone === 'running' ? 'info' : 'error'}>{message}</SharedBanner>
}

function Hint({ children }: { children: React.ReactNode }) {
  return <SharedHint>{children}</SharedHint>
}
