import { useState } from 'react'
import type { KeyboardEvent } from 'react'

/**
 * 输入框。
 *
 * 细节：Enter 发送、Shift+Enter 换行；busy 时禁用并说明原因
 * （因为后端是"整轮跑完才结束"的同步语义，不能真的取消 —— 这一点必须让用户知道）。
 */
export function Composer({
  busy,
  disabled,
  disabledReason,
  onSend,
}: {
  busy: boolean
  disabled: boolean
  disabledReason?: string
  onSend(input: string): void
}) {
  const [value, setValue] = useState('')

  const submit = () => {
    const trimmed = value.trim()
    if (trimmed.length === 0 || busy || disabled) return
    onSend(trimmed)
    setValue('')
  }

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      submit()
    }
  }

  return (
    <div
      style={{
        borderTop: '1px solid var(--border)',
        padding: 12,
        display: 'grid',
        gap: 8,
        background: 'var(--bg-elev)',
      }}
    >
      <textarea
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={onKeyDown}
        placeholder={
          disabled
            ? (disabledReason ?? '当前不可发送')
            : busy
              ? '上一轮还在执行…（后端不支持中途取消，本轮结束后可继续）'
              : '输入消息，Enter 发送，Shift+Enter 换行'
        }
        rows={3}
        disabled={disabled || busy}
        style={{
          width: '100%',
          resize: 'vertical',
          minHeight: 64,
          maxHeight: 220,
          padding: '10px 12px',
          borderRadius: 'var(--radius)',
          border: '1px solid var(--border)',
          background: 'var(--bg)',
          color: 'var(--fg)',
          fontFamily: 'var(--font-sans)',
          fontSize: 13,
          lineHeight: 1.7,
        }}
      />
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <button
          type="button"
          onClick={submit}
          disabled={disabled || busy || value.trim().length === 0}
          style={{
            padding: '6px 16px',
            borderRadius: 999,
            border: '1px solid transparent',
            background: 'var(--accent)',
            color: 'var(--accent-fg)',
            fontSize: 13,
            fontWeight: 600,
            cursor: disabled || busy || value.trim().length === 0 ? 'not-allowed' : 'pointer',
            opacity: disabled || busy || value.trim().length === 0 ? 0.45 : 1,
          }}
        >
          {busy ? '执行中…' : '发送'}
        </button>
        <span style={{ fontSize: 11, color: 'var(--fg-subtle)' }}>
          每轮会真实调用后端（消耗 DeepSeek 调用）；执行过程在时间线里逐项显示
        </span>
      </div>
    </div>
  )
}
