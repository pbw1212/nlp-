import { useEffect, useRef, useState } from 'react'
import { describeError } from '@/datasource'
import { useAppState } from '@/state/AppState'
import { Icon } from '@/components/Icon'

/**
 * 执行模式切换器（P5 方案 B）。
 *
 * 后端模式是 `PersonalAgentApp` 的构造参数、没有运行时 API；
 * 切换由桥接在同一数据目录上重建 app 完成（token 不变、数据不丢）。
 * 因此这里只是发一个 POST /api/mode，然后把真实结果（成功/409 忙/失败原因）如实显示。
 */
export function ModeSwitcher() {
  const { bridge, switchMode, sourceKind } = useAppState()
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const boxRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!open) return
    const onDown = (event: MouseEvent) => {
      if (boxRef.current !== null && !boxRef.current.contains(event.target as Node)) setOpen(false)
    }
    window.addEventListener('mousedown', onDown)
    return () => window.removeEventListener('mousedown', onDown)
  }, [open])

  if (bridge.state !== 'ok') return null

  const current = bridge.executionMode
  const needsKey = bridge.needsApiKey
  const readonly = sourceKind === 'fixture'

  const change = async (mode: 'single' | 'multi_fixed') => {
    if (mode === current) {
      setOpen(false)
      return
    }
    setBusy(true)
    setMessage(null)
    try {
      await switchMode(mode)
      setMessage(`已切换到 ${mode === 'single' ? 'single（单 Agent）' : 'multi_fixed（协作）'}，页面已重新加载`)
      setOpen(false)
    } catch (cause) {
      setMessage('切换失败：' + describeError(cause))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ position: 'relative' }} ref={boxRef}>
      <button
        type="button"
        className="chip chip--button"
        disabled={readonly || busy || needsKey}
        onClick={() => setOpen((value) => !value)}
        title={
          readonly
            ? '回放模式不能切换后端执行模式'
            : needsKey
              ? '未配置 API Key：填入密钥后才能切换模式'
              : '切换执行模式（每次切换在下一轮对话生效，当前会话与历史保留）'
        }
      >
        <Icon name="refresh" size={12} />
        {busy ? '切换中…' : current === 'single' ? '模式：single' : '模式：协作'}
      </button>

      {open && (
        <div
          style={{
            position: 'absolute',
            top: 'calc(100% + 8px)',
            right: 0,
            width: 300,
            padding: 12,
            borderRadius: 'var(--radius)',
            border: '1px solid var(--border-strong)',
            background: 'var(--bg-elev)',
            boxShadow: '0 12px 32px rgba(0,0,0,0.35)',
            display: 'grid',
            gap: 8,
            zIndex: 20,
          }}
        >
          <div style={{ fontSize: 12, color: 'var(--fg-subtle)', lineHeight: 1.7 }}>
            切换在<b>下一轮对话</b>生效；当前会话、记忆与任务历史都保留。切换期间不能有正在执行的作业。
          </div>
          <ModeOption
            active={current === 'single'}
            title="single · 单 Agent"
            desc="直接对话，回答是自然语言；记忆/任务照常维护，但没有协作、修复、经验。"
            onClick={() => void change('single')}
            disabled={busy}
          />
          <ModeOption
            active={current === 'multi_fixed'}
            title="multi_fixed · 四角色协作"
            desc="Planner/Memory/Executor/Verifier 串行协作，产 DAG、证据与经验；简单请求也会被拆子任务。"
            onClick={() => void change('multi_fixed')}
            disabled={busy}
          />
          {message !== null && (
            <span style={{ fontSize: 11, color: 'var(--fg-subtle)', lineHeight: 1.6 }}>{message}</span>
          )}
        </div>
      )}
    </div>
  )
}

function ModeOption({
  active,
  title,
  desc,
  onClick,
  disabled,
}: {
  active: boolean
  title: string
  desc: string
  onClick(): void
  disabled: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || active}
      style={{
        textAlign: 'left',
        display: 'grid',
        gap: 2,
        padding: '8px 10px',
        borderRadius: 'var(--radius-sm)',
        border:
          '1px solid ' +
          (active ? 'color-mix(in oklab, var(--accent) 45%, transparent)' : 'var(--border)'),
        background: active ? 'color-mix(in oklab, var(--accent) 10%, transparent)' : 'transparent',
        color: 'var(--fg)',
        cursor: active || disabled ? 'default' : 'pointer',
      }}
    >
      <span style={{ fontSize: 12.5, fontWeight: 600 }}>
        {title}
        {active ? '（当前）' : ''}
      </span>
      <span style={{ fontSize: 11, color: 'var(--fg-subtle)', lineHeight: 1.6 }}>{desc}</span>
    </button>
  )
}
