import { useEffect, useRef, useState } from 'react'
import { bridgeToken, setBridgeToken } from '@/datasource'
import { useAppState } from '@/state/AppState'

/**
 * 本机桥接「访问口令」设置。
 *
 * 为什么叫"口令"而不叫 token：在这个项目里 token 已经指**模型的词元**
 * （token 消耗、token 燃烧中）。而浏览器 ↔ 本机桥接之间的这串随机码是**校验码**，
 * 两者混用会让人误解。所以界面统一叫「访问口令」，协议头仍是 `X-MAPAS-Token`（保持兼容）。
 *
 * 四种状态，如实显示：
 * - 本机免校验：桥接用 `--no-token` 启动，根本不需要口令（不是"未设置"）
 * - 已验证：桥接要求口令，且本机已保存正确的口令
 * - 需要填写：桥接要求口令，但本机还没填（或填错/过期）
 * - 检测中：正在探测
 */
export function TokenButton() {
  const { bridge, refreshBridge } = useAppState()
  const [open, setOpen] = useState(false)
  const [value, setValue] = useState('')
  const [hasToken, setHasToken] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const boxRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    setHasToken(bridgeToken() !== null)
  }, [bridge.state])

  useEffect(() => {
    if (!open) return
    const onDown = (event: MouseEvent) => {
      if (boxRef.current !== null && !boxRef.current.contains(event.target as Node)) setOpen(false)
    }
    window.addEventListener('mousedown', onDown)
    return () => window.removeEventListener('mousedown', onDown)
  }, [open])

  const save = () => {
    const trimmed = value.trim()
    if (trimmed === '') {
      setMessage('请粘贴桥接启动时打印的口令（形如 token=xxxx）')
      return
    }
    setBridgeToken(trimmed)
    setHasToken(true)
    setValue('')
    setMessage('已保存到本机浏览器，正在重连…')
    void refreshBridge()
  }

  const clear = () => {
    setBridgeToken(null)
    setHasToken(false)
    setMessage('已清除本机口令')
    void refreshBridge()
  }

  const view = (() => {
    if (bridge.state === 'checking') {
      return { label: '口令：检测中', tone: '', hint: '正在探测桥接是否需要口令' }
    }
    if (bridge.state === 'ok') {
      return bridge.authRequired
        ? { label: '口令：已验证', tone: 'chip--ok', hint: '桥接要求访问口令，本机已保存且校验通过' }
        : {
            label: '口令：本机免校验',
            tone: 'chip--ok',
            hint: '桥接以 --no-token 启动：本机访问无需口令（不是"未设置"）',
          }
    }
    if (bridge.code === 'AUTH_REQUIRED') {
      return {
        label: '口令：需要填写',
        tone: 'chip--warn',
        hint: '桥接要求访问口令，点此粘贴启动时打印的那串随机码',
      }
    }
    return { label: '口令：不可用', tone: '', hint: '桥接未连接，口令状态未知' }
  })()

  return (
    <div style={{ position: 'relative' }} ref={boxRef}>
      <button
        type="button"
        className={`chip chip--button ${view.tone}`}
        onClick={() => setOpen((prev) => !prev)}
        title={view.hint}
      >
        {view.label}
      </button>

      {open && (
        <div
          style={{
            position: 'absolute',
            top: 'calc(100% + 8px)',
            right: 0,
            width: 330,
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
          <div style={{ fontSize: 12, color: 'var(--fg-muted)', lineHeight: 1.7 }}>
            这里的<b>口令</b>是本机桥接的访问校验码（启动时终端打印的{' '}
            <span className="mono">token=…</span>），
            <b>不是</b>模型的 token/词元，也不是 DeepSeek API Key。
          </div>

          {bridge.state === 'ok' && !bridge.authRequired && (
            <div className="banner" data-tone="success">
              <div style={{ flex: 1 }}>
                当前桥接以 <span className="mono">--no-token</span> 启动，<b>本机免校验</b>，
                无需填写口令。
              </div>
            </div>
          )}

          <input
            value={value}
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') save()
            }}
            placeholder="粘贴启动时打印的口令"
            style={{
              padding: '6px 10px',
              borderRadius: 'var(--radius-sm)',
              border: '1px solid var(--border)',
              background: 'var(--bg)',
              color: 'var(--fg)',
              fontFamily: 'var(--font-mono)',
              fontSize: 12,
            }}
          />
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            <button type="button" className="chip chip--button" onClick={save}>
              保存并重连
            </button>
            <button type="button" className="chip chip--button" onClick={clear} disabled={!hasToken}>
              清除本机口令
            </button>
          </div>
          <div style={{ fontSize: 11, color: 'var(--fg-subtle)', lineHeight: 1.6 }}>
            口令只存在本机浏览器 localStorage，不进仓库、不进构建产物。
            {hasToken ? '当前本机已保存口令。' : '当前本机未保存口令。'}
          </div>
          {message !== null && (
            <span style={{ fontSize: 11, color: 'var(--fg-subtle)', lineHeight: 1.6 }}>{message}</span>
          )}
        </div>
      )}
    </div>
  )
}
