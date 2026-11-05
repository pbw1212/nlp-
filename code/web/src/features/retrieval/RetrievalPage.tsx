import { useState } from 'react'
import { useAppState } from '@/state/AppState'
import { useAsync } from '@/lib/useAsync'
import { Badge, Card, EmptyState, KeyValue, SectionLabel } from '@/components/ui/primitives'
import { Hint as SharedHint, MoreInfo } from '@/components/ui/feedback'

/**
 * 检索证据（P3 收尾 · 降级视图）。
 *
 * 为什么是"降级"：后端只把检索的**聚合统计**落盘（`execution/retrieval-traces.jsonl`），
 * 逐条候选的排名 / 分数 / 适用性四态 / 冲突 / 充分性**没有持久化**，也没有接口回传。
 * 所以这一屏只用真实存在的字段，缺失项明确标成占位符「—（后端未持久化）」，
 * 绝不用编造的分数或颜色填充。
 *
 * 本页对"选中的记忆"做了**只读 join**：trace 里只有 `selected_memory_ids`，
 * 因此再用 `listMemories()` 把这些 ID 还原成**内容/类型/当前版本**，
 * 让界面显示人话而不是 `mem_xxxx` 编号；找不到的（已删除/回收）如实标注，不编造内容。
 *
 * 真实字段（逐条核对过）：
 *   request_id / turn_id / task_id / warnings
 *   trace.recall.stages              —— 本次用了哪些召回阶段
 *   trace.recall.query_variants      —— 模型把输入扩写成的检索查询
 *   trace.recall.candidate_counts    —— 每条查询召回多少候选
 *   trace.applicability.{assessment_count,rejected_count,warning_count}
 *   selected_memory_ids              —— 最终选中哪些记忆
 */

function rec(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : {}
}
function arrOf<T = unknown>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : []
}
function numOf(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}
function strOf(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

interface EvidenceCandidate {
  memoryId: string
  revisionId: string
  rank: number
  score: number
  scoreBreakdown: Record<string, number>
  applicability: string
  decision: string
  conflict: string
  conflictKey: string
  conflictLoser: boolean
  currentSafe: boolean | null
  selected: boolean
}

interface SufficiencyView {
  status: string
  reasonCodes: string[]
  missingAspectCount: number
  uncertainIds: string[]
  unresolvedConflictIds: string[]
}

interface TraceView {
  requestId: string
  turnId: string
  taskId: string
  /** 记录版本：1 = 旧聚合记录；2 = 含逐条候选与充分性（后端 53484dc 起） */
  schemaVersion: number
  selectedMemoryIds: string[]
  stages: string[]
  queryVariants: string[]
  candidateCounts: { query: string; count: number }[]
  assessmentCount: number | null
  rejectedCount: number | null
  warningCount: number | null
  warnings: string[]
  candidateCount: number | null
  candidates: EvidenceCandidate[]
  sufficiency: SufficiencyView | null
}

function toTraceView(raw: unknown): TraceView {
  const row = rec(raw)
  const trace = rec(row['trace'])
  const recall = rec(trace['recall'])
  const applicability = rec(trace['applicability'])
  const counts = rec(recall['candidate_counts'])
  const sufficiencyRaw = row['sufficiency']
  const sufficiency =
    typeof sufficiencyRaw === 'object' && sufficiencyRaw !== null
      ? {
          status: strOf(rec(sufficiencyRaw)['status']),
          reasonCodes: arrOf<string>(rec(sufficiencyRaw)['reason_codes']).filter(
            (item) => typeof item === 'string',
          ),
          missingAspectCount: numOf(rec(sufficiencyRaw)['missing_aspect_count']) ?? 0,
          uncertainIds: arrOf<string>(rec(sufficiencyRaw)['uncertain_memory_ids']).filter(
            (item) => typeof item === 'string',
          ),
          unresolvedConflictIds: arrOf<string>(
            rec(sufficiencyRaw)['unresolved_conflict_memory_ids'],
          ).filter((item) => typeof item === 'string'),
        }
      : null
  const candidates = arrOf<unknown>(trace['evidence_candidates'])
    .map((item) => {
      const candidate = rec(item)
      const memoryId = strOf(candidate['memory_id'])
      if (memoryId === '') return null
      return {
        memoryId,
        revisionId: strOf(candidate['revision_id']),
        rank: numOf(candidate['rank']) ?? 0,
        score: numOf(candidate['score']) ?? 0,
        scoreBreakdown: Object.fromEntries(
          Object.entries(rec(candidate['score_breakdown'])).map(([key, value]) => [
            key,
            numOf(value) ?? 0,
          ]),
        ),
        applicability: strOf(candidate['applicability_status']),
        decision: strOf(candidate['applicability_decision']),
        conflict: strOf(candidate['conflict_status']),
        conflictKey: strOf(candidate['conflict_key']),
        conflictLoser: candidate['conflict_loser'] === true,
        currentSafe:
          typeof candidate['current_safe'] === 'boolean'
            ? (candidate['current_safe'] as boolean)
            : null,
        selected: candidate['selected'] === true,
      }
    })
    .filter((item): item is EvidenceCandidate => item !== null)

  return {
    requestId: strOf(row['request_id']),
    turnId: strOf(row['turn_id']),
    taskId: strOf(row['task_id']),
    schemaVersion: numOf(row['schema_version']) ?? numOf(trace['schema_version']) ?? 1,
    selectedMemoryIds: arrOf<string>(row['selected_memory_ids']).filter(
      (item) => typeof item === 'string',
    ),
    stages: arrOf<string>(recall['stages']).filter((item) => typeof item === 'string'),
    queryVariants: arrOf<string>(recall['query_variants']).filter(
      (item) => typeof item === 'string',
    ),
    candidateCounts: Object.entries(counts).map(([query, count]) => ({
      query,
      count: numOf(count) ?? 0,
    })),
    assessmentCount: numOf(applicability['assessment_count']),
    rejectedCount: numOf(applicability['rejected_count']),
    warningCount: numOf(applicability['warning_count']),
    warnings: arrOf<string>(row['warnings']).filter((item) => typeof item === 'string'),
    candidateCount: numOf(trace['evidence_candidate_count']),
    candidates,
    sufficiency,
  }
}

export function RetrievalPage() {
  const { source, focusMemory } = useAppState()
  const state = useAsync<{ traces: TraceView[]; memories: Map<string, MemoryLite> }>(
    async () => {
      const [rows, rawMemories] = await Promise.all([
        source.listTurnTraces(100),
        // 只读 memory 列表，用于把 selected_memory_ids 还原成内容
        source.listMemories().catch(() => []),
      ])
      const lite = (rawMemories as unknown[])
        .map(toMemoryLite)
        .filter((item): item is MemoryLite => item !== null)
      return {
        traces: rows.map(toTraceView),
        memories: new Map(lite.map((memory) => [memory.id, memory])),
      }
    },
    [source],
  )
  const [selectedTurn, setSelectedTurn] = useState<string | null>(null)

  const traces = state.data?.traces ?? []
  const memoryMap = state.data?.memories ?? new Map<string, MemoryLite>()
  const current =
    traces.find((item) => item.turnId === selectedTurn || item.requestId === selectedTurn) ?? null

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <header style={{ display: 'grid', gap: 6 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <h1 style={{ fontSize: 20, fontWeight: 650, margin: 0 }}>检索证据</h1>
          <Badge tone="waiting" filled>
            降级视图
          </Badge>
          <Badge tone="accent" plain>
            Task 2
          </Badge>
        </div>
        <p style={{ margin: 0, fontSize: 13, color: 'var(--fg-muted)', lineHeight: 1.7 }}>
          这里回答一个问题：<b>这一轮到底用了哪些长期记忆，为什么用它们。</b>
          新记录会给出<b>逐条候选的排名、分数、适用性判定与冲突状态</b>，以及证据是否充分；
          旧记录只有聚合统计，页面会明确标注。
        </p>
      </header>

      <Hint>
        后端从 53484dc 起把逐条候选（排名 / 分数 / 适用性四态 / 冲突）与证据充分性一起落盘；
        本条是旧记录时页面会提示"无逐条字段"，不会补造。
      </Hint>

      <Card
        collapsed
        title="降级说明（为什么这一屏不完整）"
        description="项目规则：功能不造假 —— 缺字段就明说，不编数"
      >
        <div style={{ display: 'grid', gap: 8, fontSize: 12, lineHeight: 1.85, color: 'var(--fg-muted)' }}>
          <div>
            <strong style={{ color: 'var(--fg)' }}>能显示的（真实）：</strong>
            召回阶段、查询变体、候选数量、适用性评估计数、选中的记忆 ID。
          </div>
          <div>
            <strong style={{ color: 'var(--fg)' }}>显示不了的（后端未持久化）：</strong>
            逐条候选排名与分数（BM25 / Dense / RRF）、逐条适用性四态（MATCH/MISMATCH/UNKNOWN/AMBIGUOUS）、
            冲突状态（RESOLVED/UNRESOLVED）、证据充分性（SUFFICIENT/PARTIAL/INSUFFICIENT）。
          </div>
          <div style={{ color: 'var(--fg-subtle)' }}>
            根因：这些字段只存在于运行期内存的 `EvidenceBundle`，落盘文件
            `execution/retrieval-traces.jsonl` 只保存聚合统计。要恢复完整视图，需要后端在落盘时补
            `selected[]` 的 `applicability_status` 与 `conflict_status`（已在
            <span className="mono"> docs/frontend-contract.md §2.5 </span>
            记录为待办）。
          </div>
        </div>
      </Card>

      {state.error !== null && (
        <div
          style={{
            padding: '8px 12px',
            borderRadius: 'var(--radius-sm)',
            border: '1px solid color-mix(in oklab, var(--sem-failed) 40%, transparent)',
            background: 'color-mix(in oklab, var(--sem-failed) 10%, transparent)',
            color: 'var(--sem-failed)',
            fontSize: 12,
            lineHeight: 1.7,
          }}
        >
          {state.error}
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '300px minmax(0, 1fr)', gap: 16 }}>
        <Card
          title="检索记录"
          description={`共 ${traces.length} 次`}
          actions={
            <button type="button" className="chip chip--button" onClick={state.reload}>
              刷新
            </button>
          }
        >
          {state.loading && <Hint>读取中…</Hint>}
          {!state.loading && traces.length === 0 && (
            <Hint>
              还没有落盘的检索记录。发起一轮对话后，后端会往
              <span className="mono"> execution/retrieval-traces.jsonl </span>
              追加一条聚合记录。
            </Hint>
          )}
          <div style={{ display: 'grid', gap: 6, marginTop: traces.length > 0 ? 10 : 0 }}>
            {traces.map((trace) => {
              const key = trace.turnId || trace.requestId
              return (
                <button
                  key={key}
                  type="button"
                  className="thread-item"
                  aria-current={key === selectedTurn ? 'true' : undefined}
                  onClick={() => setSelectedTurn(key)}
                >
                  <span
                    style={{
                      fontSize: 12.5,
                      color: 'var(--fg)',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {trace.queryVariants[0] ?? '（本次没有查询扩写）'}
                  </span>
                  <span style={{ display: 'flex', gap: 6, marginTop: 4, flexWrap: 'wrap' }}>
                    <Badge tone="produced" plain>
                      {trace.selectedMemoryIds.length} 条记忆
                    </Badge>
                    <Badge tone="pending" plain>
                      {trace.stages.length} 个阶段
                    </Badge>
                  </span>
                  <span className="mono" style={{ fontSize: 9.5, color: 'var(--fg-subtle)', marginTop: 2 }}>
                    {(trace.turnId || trace.requestId).slice(0, 22)}
                  </span>
                </button>
              )
            })}
          </div>
        </Card>

        <div style={{ display: 'grid', gap: 16, alignContent: 'start' }}>
          {current === null ? (
            <EmptyState
              title="选择左侧一条检索记录"
              description="右侧会显示这一轮检索的真实过程：模型怎么理解你的话、经过哪些召回阶段、最终用了哪几条记忆。"
              items={[
                '列表标题是模型对这句话的检索式理解（查询扩写）',
                '「本次用到的记忆」显示记忆正文，点一下可跳到记忆库看版本与来源',
                '逐条排名与适用性判断后端暂未落盘，页面不显示这些数字',
              ]}
            />
          ) : (
            <>
              <Card
                title={current.queryVariants[0] ?? '本次检索'}
                description={
                  current.taskId === '' || current.taskId === '__unbound__'
                    ? '未绑定任务的一轮对话'
                    : '已在任务下执行'
                }
              >
                <div style={{ display: 'grid', gap: 6, fontSize: 12.5, lineHeight: 1.8 }}>
                  <span>
                    模型把这句话理解成了{' '}
                    <b style={{ color: 'var(--fg)' }}>{current.queryVariants.length}</b>{' '}
                    条检索查询，最终选用了{' '}
                    <b style={{ color: 'var(--fg)' }}>{current.selectedMemoryIds.length}</b>{' '}
                    条长期记忆。
                  </span>
                  {current.warnings.length > 0 && (
                    <span style={{ color: 'var(--sem-unknown)' }}>
                      过程中的提醒：{current.warnings.join('；')}
                    </span>
                  )}
                </div>
                <MoreInfo label="技术信息（轮次 / 请求 ID）">
                  <KeyValue
                    rows={[
                      { k: '轮次', v: <span className="mono">{current.turnId || '—'}</span> },
                      { k: '请求 ID', v: <span className="mono">{current.requestId || '—'}</span> },
                      { k: '任务 ID', v: <span className="mono">{current.taskId || '—'}</span> },
                    ]}
                  />
                </MoreInfo>
              </Card>

              {current.sufficiency !== null && (
                <Card
                  title="证据充分性"
                  description="后端判定：这些证据是否足以支撑本次回答"
                  actions={
                    <Badge tone={sufficiencyTone(current.sufficiency.status)} filled>
                      {current.sufficiency.status || '—'}
                    </Badge>
                  }
                >
                  <div style={{ display: 'grid', gap: 8 }}>
                    <KeyValue
                      rows={[
                        { k: '缺失方面', v: `${current.sufficiency.missingAspectCount} 个` },
                        { k: '不确定记忆', v: `${current.sufficiency.uncertainIds.length} 条` },
                        {
                          k: '未解决冲突',
                          v: `${current.sufficiency.unresolvedConflictIds.length} 条`,
                        },
                      ]}
                    />
                    {current.sufficiency.reasonCodes.length > 0 && (
                      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                        {current.sufficiency.reasonCodes.map((code) => (
                          <Badge key={code} tone="unknown" plain>
                            {code}
                          </Badge>
                        ))}
                      </div>
                    )}
                  </div>
                </Card>
              )}

              <Card
                title="候选与判定（逐条）"
                description={
                  current.candidates.length > 0
                    ? `共 ${current.candidateCount ?? current.candidates.length} 条候选，显示前 ${current.candidates.length} 条`
                    : '旧记录没有逐条候选字段'
                }
              >
                {current.candidates.length === 0 ? (
                  <div style={{ display: 'grid', gap: 6 }}>
                    <span style={{ fontSize: 12.5, color: 'var(--fg-muted)', lineHeight: 1.8 }}>
                      这条记录是旧版（schema v1），只保存了聚合统计 —— 逐条排名、分数、适用性与冲突
                      没有落盘，因此这里不显示。
                    </span>
                    <span style={{ fontSize: 11, color: 'var(--fg-subtle)', lineHeight: 1.7 }}>
                      发一轮新的对话后，后端会写入 schema v2 记录，这一屏就会显示完整候选表。
                    </span>
                  </div>
                ) : (
                  <div style={{ display: 'grid', gap: 6 }}>
                    {current.candidates.map((candidate) => {
                      const memory = memoryMap.get(candidate.memoryId) ?? null
                      const preview =
                        memory === null
                          ? '（记忆已不在库中，仅能显示编号）'
                          : memory.content.slice(0, 60)
                      const breakdown = Object.entries(candidate.scoreBreakdown)
                        .filter(([, value]) => value !== 0)
                        .map(([key, value]) => `${key} ${value.toFixed(2)}`)
                        .join(' · ')
                      return (
                        <div
                          key={`${candidate.memoryId}-${candidate.revisionId}`}
                          style={{
                            display: 'grid',
                            gap: 4,
                            padding: '8px 10px',
                            border: '1px solid var(--border)',
                            borderLeft:
                              '3px solid ' +
                              (candidate.selected ? 'var(--sem-verified)' : 'var(--border)'),
                            borderRadius: 'var(--radius-sm)',
                            background: candidate.selected
                              ? 'color-mix(in oklab, var(--sem-verified) 6%, transparent)'
                              : 'transparent',
                          }}
                        >
                          <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
                            <span className="mono" style={{ fontSize: 10.5, color: 'var(--fg-subtle)' }}>
                              #{candidate.rank}
                            </span>
                            <span style={{ fontSize: 12.5, flex: 1, minWidth: 120 }}>
                              {preview || '（无正文）'}
                            </span>
                            <span className="mono" style={{ fontSize: 11, color: 'var(--accent)' }}>
                              {candidate.score.toFixed(3)}
                            </span>
                          </div>
                          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                            <Badge tone={applicabilityTone(candidate.applicability)} plain>
                              {candidate.applicability || '—'}
                            </Badge>
                            {candidate.conflict !== '' && candidate.conflict !== 'NONE' && (
                              <Badge
                                tone={candidate.conflict === 'UNRESOLVED' ? 'failed' : 'verified'}
                                plain
                              >
                                {candidate.conflict}
                                {candidate.conflictLoser ? '（落败）' : ''}
                              </Badge>
                            )}
                            {candidate.currentSafe === false && (
                              <Badge tone="mismatch" plain>
                                非当前有效
                              </Badge>
                            )}
                            {candidate.selected && (
                              <Badge tone="verified" plain>
                                ✓ 已选用
                              </Badge>
                            )}
                            {breakdown !== '' && (
                              <span className="mono" style={{ fontSize: 10, color: 'var(--fg-subtle)' }}>
                                {breakdown}
                              </span>
                            )}
                          </div>
                        </div>
                      )
                    })}
                  </div>
                )}
              </Card>

              <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) minmax(0,1fr)', gap: 16 }}>
                <Card title="召回阶段" description="本次检索实际经过的阶段">
                  {current.stages.length === 0 ? (
                    <Placeholder label="stages" />
                  ) : (
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                      {current.stages.map((stage) => (
                        <Badge key={stage} tone="produced" filled>
                          {stage}
                        </Badge>
                      ))}
                    </div>
                  )}
                  <div style={{ marginTop: 12 }}>
                    <SectionLabel>候选数量（按查询）</SectionLabel>
                    {current.candidateCounts.length === 0 ? (
                      <Placeholder label="candidate_counts" />
                    ) : (
                      <div style={{ display: 'grid', gap: 6 }}>
                        {current.candidateCounts.map((item, index) => (
                          <div
                            key={`${item.query}-${index}`}
                            style={{ display: 'flex', gap: 10, fontSize: 12, alignItems: 'baseline' }}
                          >
                            <span className="mono" style={{ color: 'var(--accent)' }}>
                              {item.count}
                            </span>
                            <span style={{ color: 'var(--fg-muted)', wordBreak: 'break-word' }}>
                              {item.query}
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </Card>

                <Card title="适用性评估（聚合）" description="整体计数；逐条判定见上方候选表">
                  <KeyValue
                    rows={[
                      { k: '评估次数', v: fmtNum(current.assessmentCount) },
                      { k: '被拒次数', v: fmtNum(current.rejectedCount) },
                      { k: '警告次数', v: fmtNum(current.warningCount) },
                    ]}
                  />
                  <div style={{ marginTop: 12 }}>
                    <SectionLabel>逐条适用性四态</SectionLabel>
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                      <Badge tone="match">MATCH</Badge>
                      <Badge tone="mismatch">MISMATCH</Badge>
                      <Badge tone="unknown">UNKNOWN</Badge>
                      <Badge tone="ambiguous">AMBIGUOUS</Badge>
                      <span className="mono" style={{ fontSize: 11, color: 'var(--fg-subtle)' }}>
                        —（后端未持久化）
                      </span>
                    </div>
                    <p style={{ margin: '8px 0 0', fontSize: 11, color: 'var(--fg-subtle)', lineHeight: 1.7 }}>
                      {current.candidates.length > 0
                        ? '上排四态是图例；本次每条的判定见上方「候选与判定（逐条）」表。'
                        : '上排四态是图例，不是本次数据；这条旧记录没有落盘逐条判定。'}
                    </p>
                  </div>
                </Card>
              </div>

              <Card title="查询扩写" description="模型把用户输入解释成的检索查询">
                {current.queryVariants.length === 0 ? (
                  <Placeholder label="query_variants" />
                ) : (
                  <ol style={{ margin: 0, paddingLeft: 20, display: 'grid', gap: 6 }}>
                    {current.queryVariants.map((query, index) => (
                      <li key={`${query}-${index}`} style={{ fontSize: 12, color: 'var(--fg-muted)', lineHeight: 1.7 }}>
                        {query}
                      </li>
                    ))}
                  </ol>
                )}
              </Card>

              <Card
                title="本次用到的记忆"
                description="这些是检索真正选中的内容；点一条可跳到记忆库看版本与来源"
              >
                {current.selectedMemoryIds.length === 0 ? (
                  <div style={{ display: 'grid', gap: 6 }}>
                    <span style={{ fontSize: 12.5, color: 'var(--fg-muted)', lineHeight: 1.8 }}>
                      这一轮没有用到长期记忆（可能没绑定任务，或这次检索没有选中任何记忆）。
                    </span>
                  </div>
                ) : (
                  <div style={{ display: 'grid', gap: 8 }}>
                    {current.selectedMemoryIds.map((memoryId) => {
                      const memory = memoryMap.get(memoryId) ?? null
                      if (memory === null) {
                        return (
                          <div
                            key={memoryId}
                            style={{
                              padding: '8px 10px',
                              border: '1px dashed var(--border)',
                              borderRadius: 'var(--radius-sm)',
                              fontSize: 12,
                              color: 'var(--fg-subtle)',
                              lineHeight: 1.7,
                            }}
                          >
                            这条记忆已经不在记忆库中（可能被删除或进了回收站），因此无法展示内容。
                            <span className="mono" style={{ marginLeft: 6, fontSize: 10 }}>
                              {memoryId}
                            </span>
                          </div>
                        )
                      }
                      const content = memory.content
                      const version = memory.version
                      return (
                        <button
                          key={memoryId}
                          type="button"
                          onClick={() => focusMemory(memoryId)}
                          title="在记忆库中查看版本与来源"
                          style={{
                            display: 'grid',
                            gap: 4,
                            textAlign: 'left',
                            padding: '10px 12px',
                            border: '1px solid var(--border)',
                            borderRadius: 'var(--radius-sm)',
                            background: 'var(--bg-elev)',
                            color: 'var(--fg)',
                            cursor: 'pointer',
                          }}
                        >
                          <span
                            style={{
                              fontSize: 13,
                              lineHeight: 1.7,
                              display: '-webkit-box',
                              WebkitLineClamp: 3,
                              WebkitBoxOrient: 'vertical',
                              overflow: 'hidden',
                            }}
                          >
                            {content === '' ? '（这条记忆没有可读正文）' : content}
                          </span>
                          <span style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                            <Badge tone="produced" plain>
                              {MEMORY_TYPE_LABEL[memory.type] ?? (memory.type || '记忆')}
                            </Badge>
                            <Badge tone="pending" plain>
                              {GRANULARITY_LABEL[memory.granularity] ?? (memory.granularity || '—')}
                            </Badge>
                            {version !== null && (
                              <span className="mono" style={{ fontSize: 10, color: 'var(--fg-subtle)' }}>
                                当前 v{version}
                              </span>
                            )}
                            <span style={{ fontSize: 10.5, color: 'var(--fg-subtle)' }}>
                              点击查看版本与来源 →
                            </span>
                          </span>
                        </button>
                      )
                    })}
                    <span style={{ fontSize: 11, color: 'var(--fg-subtle)', lineHeight: 1.7 }}>
                      说明：检索记录本身只保存了记忆编号，这里的内容是用编号到记忆库只读 join 出来的；
                      已经不存在或已回收的记忆不会被补造内容。
                    </span>
                  </div>
                )}
              </Card>

              <Card collapsed title="字段可用性说明" flat>
                <div style={{ display: 'grid', gap: 6, fontSize: 12, color: 'var(--fg-subtle)', lineHeight: 1.8 }}>
                  <div>本记录版本：schema v{current.schemaVersion}</div>
                  <div>逐条候选排名 / 分数分解 / 适用性四态 / 冲突状态：schema v2 起落盘</div>
                  <div>证据充分性（sufficiency）：schema v2 起落盘</div>
                  <div>说明：旧记录（v1）没有上述字段，页面只展示聚合统计，不编造。</div>
                </div>
              </Card>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function Placeholder({ label }: { label: string }) {
  return (
    <span className="mono" style={{ fontSize: 12, color: 'var(--fg-subtle)' }}>
      {label} —（后端未持久化）
    </span>
  )
}

const MEMORY_TYPE_LABEL: Record<string, string> = {
  CORE_PROFILE: '核心资料',
  PREFERENCE_RULES: '偏好规则',
  EPISODIC_GRAPH: '经历事件',
}

const GRANULARITY_LABEL: Record<string, string> = {
  FINE_EPISODE: '细节片段',
  SEMANTIC_INSIGHT: '语义结论',
  HIGH_ABSTRACT: '高度概括',
}


/** 记忆列表的真实形状是扁平的（memory_id / type / content / granularity） */
interface MemoryLite {
  id: string
  type: string
  granularity: string
  content: string
  version: number | null
}

function toMemoryLite(raw: unknown): MemoryLite | null {
  if (typeof raw !== 'object' || raw === null) return null
  const row = raw as Record<string, unknown>
  const id = String(row['memory_id'] ?? row['memoryId'] ?? '')
  if (id === '') return null
  const revision =
    typeof row['current_revision'] === 'object' && row['current_revision'] !== null
      ? (row['current_revision'] as Record<string, unknown>)
      : {}
  const content = String(
    row['content'] ?? row['current_content'] ?? revision['content'] ?? '',
  )
  const rawVersion = revision['revision_no']
  return {
    id,
    type: String(row['type'] ?? row['memory_type'] ?? ''),
    granularity: String(row['granularity'] ?? ''),
    content,
    version: typeof rawVersion === 'number' ? rawVersion : null,
  }
}

function fmtNum(value: number | null): string {
  return value === null ? '—（后端未提供）' : String(value)
}

function Hint({ children }: { children: React.ReactNode }) {
  return <SharedHint>{children}</SharedHint>
}

function applicabilityTone(status: string): 'match' | 'mismatch' | 'unknown' | 'ambiguous' {
  if (status === 'MATCH') return 'match'
  if (status === 'MISMATCH') return 'mismatch'
  if (status === 'AMBIGUOUS') return 'ambiguous'
  return 'unknown'
}

function sufficiencyTone(status: string): 'verified' | 'waiting' | 'failed' | 'neutral' {
  if (status === 'SUFFICIENT') return 'verified'
  if (status === 'PARTIAL') return 'waiting'
  if (status === 'INSUFFICIENT') return 'failed'
  return 'neutral'
}
