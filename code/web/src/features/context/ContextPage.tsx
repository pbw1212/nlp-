import { useState } from 'react'
import type { ContextDiagnostics } from '@/types'
import { useAppState } from '@/state/AppState'
import { useAsync } from '@/lib/useAsync'
import { formatInt, formatRatio } from '@/lib/format'
import { Badge, Card, EmptyState, KeyValue, SectionLabel } from '@/components/ui/primitives'
import { Banner as SharedBanner, Hint as SharedHint } from '@/components/ui/feedback'

/**
 * 上下文仪表盘（P3）。
 *
 * 要证明的核心点：**预算真的在管**。长程任务一定会撞窗口上限，系统不是等它溢出，
 * 而是按预算压缩旧上下文、保留近期消息，并留下可追踪的压缩检查点（可能降级）。
 *
 * 数据全部来自 `get_conversation_context_status` / `compact_current_session`。
 */
export function ContextPage() {
  const { source } = useAppState()
  const state = useAsync<ContextDiagnostics>(() => source.getContext(), [source])
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const context = state.data

  const runCompact = async () => {
    setBusy(true)
    setError(null)
    setMessage('已提交压缩作业，等待完成…')
    try {
      const job = await source.compact()
      // 轮询作业直到终态：不假装"点一下就完成了"
      for (let attempt = 0; attempt < 120; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 700))
        const status = await source.getJob(job.jobId)
        if (status.state === 'SUCCEEDED') {
          setMessage('压缩完成（只压缩会话上下文，不修改长期记忆）')
          state.reload()
          break
        }
        if (status.state === 'FAILED') {
          setError('压缩失败：' + (status.error?.message ?? '未返回原因'))
          setMessage(null)
          break
        }
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
      setMessage(null)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <header style={{ display: 'grid', gap: 6 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <h1 style={{ fontSize: 20, fontWeight: 650, margin: 0 }}>上下文仪表盘</h1>
          <Badge tone="verified">已接入真实数据</Badge>
          <Badge tone="accent" plain>
            会话上下文
          </Badge>
          {context !== null && context.compaction_degraded && (
            <Badge tone="waiting" filled>
              摘要已降级
            </Badge>
          )}
        </div>
        <p style={{ margin: 0, fontSize: 13, color: 'var(--fg-muted)', lineHeight: 1.7 }}>
          完整 Transcript 仍然保留；「当前投影」才是这一次模型调用实际准备使用的上下文。
          压缩不会删除原始对话，只生成可追踪摘要。
        </p>
      </header>

      <Hint>这里显示模型这次实际能读多少上下文。上下文快满时系统会压缩旧对话成摘要，原文仍然保留。</Hint>

      {error !== null && <Banner message={error} tone="failed" />}
      {state.error !== null && <Banner message={state.error} tone="failed" />}
      {message !== null && <Banner message={message} tone="running" />}

      {context === null ? (
        <Card title="上下文诊断">
          {state.loading ? <Hint>读取中…</Hint> : <EmptyState title="没有可显示的诊断" description="后端未返回上下文诊断数据。" />}
        </Card>
      ) : (
        <>
          <Card title="窗口与预算" description={`模型：${context.model}`}>
            <div style={{ display: 'grid', gap: 14 }}>
              <Gauge
                label="上下文窗口"
                used={context.context_window_tokens - context.reserve_tokens}
                total={context.context_window_tokens}
                right={`${formatInt(context.context_window_tokens)} tokens（来源：${context.context_window_source}）`}
              />
              <Gauge
                label="本次投影占用"
                used={context.estimated_projected_tokens}
                total={context.usable_tokens}
                tone={context.estimated_projected_tokens / Math.max(context.usable_tokens, 1) > 0.85 ? 'failed' : 'verified'}
                right={`估算 ${formatInt(context.estimated_projected_tokens)} / 可用 ${formatInt(context.usable_tokens)}（${formatRatio(
                  context.estimated_projected_tokens / Math.max(context.usable_tokens, 1),
                )}）`}
              />
              <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', fontSize: 12, color: 'var(--fg-muted)' }}>
                <span>预留 reserve：{formatInt(context.reserve_tokens)}</span>
                <span>压缩比例：{formatRatio(context.compaction_ratio, 2)}</span>
                <span>保留近期：{formatInt(context.keep_recent_tokens)}</span>
              </div>
            </div>
          </Card>

          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) minmax(0,1fr)', gap: 16 }}>
            <Card collapsed title="消息与压缩检查点">
              <KeyValue
                rows={[
                  { k: 'Transcript 消息数', v: formatInt(context.transcript_message_count) },
                  { k: '投影保留消息数', v: formatInt(context.projected_tail_message_count) },
                  { k: '已摘要消息数', v: formatInt(context.summarized_message_count) },
                  { k: '压缩来源', v: context.compaction_source ?? '—' },
                  { k: 'compaction_id', v: <span className="mono">{context.compaction_id ?? '—'}</span> },
                  {
                    k: '是否降级',
                    v: (
                      <Badge tone={context.compaction_degraded ? 'waiting' : 'verified'} plain>
                        {context.compaction_degraded ? '降级' : '正常'}
                      </Badge>
                    ),
                  },
                  { k: '降级原因', v: context.compaction_degraded_reason ?? '—' },
                ]}
              />
            </Card>

            <Card
              title="手动压缩"
              description="只压缩当前会话上下文，不隐式修改长期记忆"
              actions={
                <button
                  type="button"
                  className="chip chip--button"
                  disabled={busy}
                  onClick={() => void runCompact()}
                >
                  {busy ? '压缩中…' : '执行压缩'}
                </button>
              }
            >
              <p style={{ margin: 0, fontSize: 12, color: 'var(--fg-subtle)', lineHeight: 1.8 }}>
                什么时候用：长对话接近窗口上限时。压缩会生成摘要并保留近期消息，
                原始 Transcript 不删除；如果摘要 Provider 失败，会标记为「降级」，原文仍然在。
              </p>
            </Card>
          </div>

          <Card collapsed title="Provider 最近一次用量" description="来自后端记录的 provider_last_usage">
            {context.provider_last_usage === null ? (
              <Hint>还没有 Provider 用量记录（本会话尚未成功调用模型）。</Hint>
            ) : (
              <pre
                className="mono"
                style={{
                  margin: 0,
                  padding: 10,
                  background: 'var(--bg-sunken)',
                  border: '1px solid var(--border)',
                  borderRadius: 'var(--radius-sm)',
                  fontSize: 11,
                  lineHeight: 1.7,
                  overflow: 'auto',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-all',
                  color: 'var(--fg-muted)',
                }}
              >
                {JSON.stringify(context.provider_last_usage, null, 2)}
              </pre>
            )}
          </Card>

          <Card title="刷新" description="诊断是请求时现查的，不是缓存">
            <SectionLabel>操作</SectionLabel>
            <button type="button" className="chip chip--button" onClick={state.reload}>
              重新读取诊断
            </button>
          </Card>
        </>
      )}
    </div>
  )
}

function Gauge({
  label,
  used,
  total,
  right,
  tone = 'accent',
}: {
  label: string
  used: number
  total: number
  right: string
  tone?: 'accent' | 'verified' | 'failed'
}) {
  const safeTotal = Math.max(total, 1)
  const ratio = Math.min(Math.max(used / safeTotal, 0), 1)
  const color =
    tone === 'failed' ? 'var(--sem-failed)' : tone === 'verified' ? 'var(--sem-verified)' : 'var(--accent)'
  return (
    <div style={{ display: 'grid', gap: 6 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, fontSize: 12 }}>
        <span style={{ color: 'var(--fg)' }}>{label}</span>
        <span className="mono" style={{ color: 'var(--fg-subtle)' }}>
          {right}
        </span>
      </div>
      <div
        style={{
          height: 8,
          borderRadius: 999,
          background: 'var(--bg-sunken)',
          border: '1px solid var(--border)',
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            width: `${ratio * 100}%`,
            height: '100%',
            background: color,
            transition: 'width 240ms ease',
          }}
        />
      </div>
    </div>
  )
}

function Banner({ message, tone = 'failed' }: { message: string; tone?: 'failed' | 'running' }) {
  return <SharedBanner tone={tone === 'running' ? 'info' : 'error'}>{message}</SharedBanner>
}

function Hint({ children }: { children: React.ReactNode }) {
  return <SharedHint>{children}</SharedHint>
}
