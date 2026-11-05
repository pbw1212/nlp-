import { useState } from 'react'
import { describeError } from '@/datasource'
import { useAppState } from '@/state/AppState'

/**
 * 协作模式提示条。
 *
 * 为什么需要：`multi_fixed` 连"你好"这类简单请求也会被 Planner 拆成子任务，token 消耗明显更高，
 * 且旧版本里回答可能变成"子任务执行自述"（已修复，见 docs/bug-report-verifier-receipts.md §2）。
 *
 * 显示规则（用户要求）：**每次切换到协作模式都重新提醒**，可以关掉，但只关到下一次切换为止。
 * 因此"是否关闭"存在 AppState 里，而不是 localStorage —— 切换模式时由 switchMode 重置。
 */
export function ModeHint() {
  const { switchMode, collabHintDismissed, dismissCollabHint } = useAppState()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (collabHintDismissed) return null

  const switchToSingle = async () => {
    setBusy(true)
    setError(null)
    try {
      await switchMode('single')
    } catch (cause) {
      setError(describeError(cause))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      style={{
        display: 'flex',
        gap: 10,
        alignItems: 'flex-start',
        padding: '8px 14px',
        borderBottom: '1px solid var(--border)',
        background: 'color-mix(in oklab, var(--sem-ambiguous) 8%, transparent)',
        fontSize: 12,
        lineHeight: 1.7,
        color: 'var(--fg-muted)',
      }}
    >
      <span style={{ color: 'var(--sem-ambiguous)', flex: 'none' }}>协作模式</span>
      <div style={{ display: 'grid', gap: 4, minWidth: 0 }}>
        <span>
          当前是 <span className="mono">multi_fixed</span>（多 Agent 协作）：适合复杂任务，
          但 <b style={{ color: 'var(--fg)' }}>token 消耗明显更高</b>。日常对话建议用
          <span className="mono"> single </span>。
        </span>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
          <button
            type="button"
            className="chip chip--button"
            disabled={busy}
            onClick={() => void switchToSingle()}
          >
            {busy ? '切换中…' : '一键切换到 single'}
          </button>
          <span style={{ fontSize: 11, color: 'var(--fg-subtle)' }}>
            切换在下一轮生效，当前会话与历史保留
          </span>
        </div>
        {error !== null && (
          <span style={{ fontSize: 11, color: 'var(--sem-failed)' }}>切换失败：{error}</span>
        )}
      </div>
      <div style={{ flex: 1 }} />
      <button
        type="button"
        className="chip chip--button"
        onClick={dismissCollabHint}
        title="关闭后，下次再切换到协作模式时会再次提醒"
      >
        知道了
      </button>
    </div>
  )
}
