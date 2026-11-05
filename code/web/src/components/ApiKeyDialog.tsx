import { useCallback, useEffect, useState } from 'react'
import type { ApiKeyStatus } from '@/types'
import { describeError } from '@/datasource'
import { useAppState } from '@/state/AppState'
import { withErrorTranslation } from '@/lib/errorText'
import { Badge } from '@/components/ui/primitives'
import type { Tone } from '@/components/ui/primitives'

const SHANGHAI_TIME = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
})

function formatTime(seconds: number | null): string {
  if (seconds === null) return '未知'
  return SHANGHAI_TIME.format(new Date(seconds * 1000)).replace(/\//g, '-') + '（UTC+8）'
}

/**
 * API Key 管理弹窗 + 顶栏按钮。
 *
 * 安全设计（用户要求）：**只写不读**。
 * - 状态接口不返回 Key 的任何片段，页面也无输入框回显历史值；
 * - 更新 = 直接覆盖配置文件里的 `DEEPSEEK_API_KEY`（原子写入、保留其它配置）；
 * - 删除 = 抹掉该行，并清理注释行里可能残留的旧 Key 副本；
 * - 可用性用 DeepSeek 的 `GET /models` 探活，不发推理请求、不消耗 token。
 */
export function ApiKeyButton() {
  const { openApiKeyDialog, apiKeyStatus } = useAppState()

  // 顶栏状态灯：可用=绿、不可用=红、未填入=黄（无法校验时也用黄色，但 title 说明不确定）
  const state = (() => {
    if (apiKeyStatus === null) {
      return { className: 'chip--warn', label: 'API Key 未知', hint: '正在读取 Key 状态…' }
    }
    if (!apiKeyStatus.configured) {
      return { className: 'chip--warn', label: 'API Key 未填入', hint: '未配置：点击填入密钥' }
    }
    if (apiKeyStatus.valid === true) {
      return { className: 'chip--ok', label: 'API Key 可用', hint: '凭据有效（探活通过）' }
    }
    if (apiKeyStatus.valid === false) {
      return {
        className: 'chip--bad',
        label: 'API Key 不可用',
        hint: '凭据被拒绝：点击更新或删除密钥',
      }
    }
    return { className: 'chip--warn', label: 'API Key 未校验', hint: '暂时无法完成探活（网络/未知原因）' }
  })()

  return (
    <button
      type="button"
      className={`chip chip--button ${state.className}`}
      onClick={openApiKeyDialog}
      title={`${state.hint}（只写不读）`}
    >
      <span className="dot" data-tone="verified" />
      {state.label}
    </button>
  )
}

export function ApiKeyDialog({ onClose }: { onClose(): void }) {
  const { source, sourceKind, refreshBridge, refreshApiKeyStatus } = useAppState()
  const [status, setStatus] = useState<ApiKeyStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [confirmingDelete, setConfirmingDelete] = useState(false)

  const readonly = sourceKind === 'fixture'

  const loadStatus = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setStatus(await source.getApiKeyStatus(true))
    } catch (cause) {
      setError(describeError(cause))
    } finally {
      setLoading(false)
    }
  }, [source])

  useEffect(() => {
    void loadStatus()
  }, [loadStatus])

  // Esc 关闭
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const submit = async () => {
    const key = input.trim()
    if (key === '') {
      setError('请先粘贴新的 API Key')
      return
    }
    setBusy(true)
    setError(null)
    setMessage(null)
    try {
      const next = await source.updateApiKey(key)
      setStatus(next)
      // 让顶栏的"后端状态"与按钮颜色立刻反映变化
      await refreshBridge()
      await refreshApiKeyStatus(true)
      setInput('') // 立刻清空输入框，避免停留在页面上
      setMessage(
        next.appReloaded === true
          ? '已更新并让新 Key 立即生效（Agent 已重建）'
          : `已写入配置，但重建 Agent 失败：${next.reloadError ?? '未知原因'}（重启桥接后生效）`,
      )
    } catch (cause) {
      setError(withErrorTranslation(describeError(cause)))
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    setBusy(true)
    setError(null)
    setMessage(null)
    try {
      const next = await source.deleteApiKey()
      setStatus(next)
      await refreshBridge()
      await refreshApiKeyStatus(true)
      setConfirmingDelete(false)
      setMessage(next.note ?? '已删除配置中的 API Key')
    } catch (cause) {
      setError(withErrorTranslation(describeError(cause)))
    } finally {
      setBusy(false)
    }
  }

  const validity: { tone: Tone; text: string } = (() => {
    if (status === null) return { tone: 'unknown', text: '读取中' }
    if (!status.configured) return { tone: 'failed', text: '未配置' }
    if (status.valid === true) return { tone: 'verified', text: '可用' }
    if (status.valid === false) return { tone: 'failed', text: '不可用（被拒绝）' }
    return { tone: 'unknown', text: '无法校验' }
  })()

  return (
    <div className="modal-overlay" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose()
    }}>
      <div className="modal" role="dialog" aria-modal="true" aria-label="API Key 管理">
        <div className="modal__head">
          <strong style={{ fontSize: 14 }}>API Key 管理</strong>
          <div style={{ flex: 1 }} />
          <button type="button" className="modal__close" onClick={onClose} aria-label="关闭">
            ×
          </button>
        </div>

        <div className="modal__body">
          <div className="modal__row">
            <span className="modal__label">当前状态</span>
            <span style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <Badge tone={validity.tone} filled>
                {validity.text}
              </Badge>
              {status !== null && status.probeError !== null && (
                <span className="hint">{withErrorTranslation(status.probeError)}</span>
              )}
            </span>
          </div>

          <div className="modal__row">
            <span className="modal__label">填入时间</span>
            <span className="mono" style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
              {loading ? '读取中…' : formatTime(status?.updatedAt ?? null)}
            </span>
          </div>

          <p className="hint">
            可用性通过 DeepSeek 的 <span className="mono">GET /models</span> 探活判断：只校验凭据，
            <b>不调用模型、不消耗 token</b>。填入时间取配置文件的最后修改时间。
          </p>

          <hr className="modal__sep" />

          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <label className="modal__label" htmlFor="api-key-input">
              新的 API Key
            </label>
            <span
              className="chip"
              style={{ fontSize: 10, height: 18, padding: '0 6px' }}
              title="只写不读：页面不会显示已保存的 Key，你也无法从界面读回它"
            >
              只写不读
            </span>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <input
              id="api-key-input"
              className="modal__input"
              type="password"
              autoComplete="off"
              spellCheck={false}
              placeholder="粘贴新的 sk-…（不会回显已有内容）"
              value={input}
              disabled={readonly || busy}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') void submit()
              }}
            />
            <button
              type="button"
              className="chip chip--button"
              disabled={readonly || busy || input.trim() === ''}
              onClick={() => void submit()}
            >
              {busy ? '处理中…' : '更新'}
            </button>
          </div>
          <p className="hint">
            仅支持 <b>DeepSeek API Key</b>（形如 <span className="mono">sk-…</span>），其它厂商的 Key 无法使用。
            更新会直接覆盖配置文件中的旧 Key，并<b>立即重建 Agent</b> 让新 Key 生效；
            页面任何时候都不会显示 Key 内容，也无法读取当前值。
          </p>

          {message !== null && (
            <div className="banner" data-tone="success" style={{ marginTop: 4 }}>
              <div style={{ flex: 1 }}>{message}</div>
            </div>
          )}
          {error !== null && (
            <div className="banner" data-tone="error" role="alert">
              <div style={{ flex: 1 }}>{error}</div>
            </div>
          )}

          <hr className="modal__sep" />

          <div className="modal__row">
            <span className="modal__label">删除凭据</span>
            {confirmingDelete ? (
              <span style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <span className="hint">确认删除？删除后 Agent 将无法调用模型。</span>
                <button
                  type="button"
                  className="chip chip--button"
                  style={{ color: 'var(--sem-failed)' }}
                  disabled={busy}
                  onClick={() => void remove()}
                >
                  {busy ? '删除中…' : '确认删除'}
                </button>
                <button type="button" className="chip chip--button" onClick={() => setConfirmingDelete(false)}>
                  取消
                </button>
              </span>
            ) : (
              <button
                type="button"
                className="chip chip--button"
                style={{ color: 'var(--sem-failed)' }}
                disabled={readonly || busy || status?.configured === false}
                onClick={() => setConfirmingDelete(true)}
              >
                删除现存 Key
              </button>
            )}
          </div>
          <p className="hint">
            删除会抹掉配置文件中的 Key 行，并清理注释里可能残留的旧 Key 副本。
            当前进程内存仍持有旧 Key，<b>重启桥接后彻底失效</b>。
          </p>

          {readonly && <p className="hint">回放模式（只读）下不能管理凭据。</p>}
        </div>
      </div>
    </div>
  )
}
