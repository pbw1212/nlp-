import { useEffect, useRef, useState } from 'react'
import type { Thread } from '@/types'
import { Icon } from '@/components/Icon'
import { Badge } from '@/components/ui/primitives'

const THREAD_STATUS_TONE: Record<string, 'verified' | 'pending' | 'cancelled'> = {
  ACTIVE: 'verified',
  PENDING: 'pending',
}

/**
 * 会话列表（对话页内部左列）。
 *
 * 每个会话右侧有「⋯」更多操作：重命名 / 删除 / 导出（暂未实现）。
 * 重命名与删除都是真实写回后端的操作（后端无正式接口，由桥接层受控写库实现），
 * 失败会显示真实原因，不做本地假改名。
 */
export function ThreadList({
  threads,
  activeThreadId,
  busy,
  onSelect,
  onCreate,
  onRename,
  onExport,
  onImport,
  onDelete,
}: {
  threads: Thread[]
  activeThreadId: string | null
  busy: boolean
  onSelect(threadId: string): void
  onCreate(): void
  onRename(threadId: string, title: string): Promise<void>
  onExport(threadId: string): Promise<void>
  onImport(file: File): Promise<void>
  onDelete(threadId: string): Promise<void>
}) {
  const [menuFor, setMenuFor] = useState<string | null>(null)
  const [menuAnchor, setMenuAnchor] = useState<{ top: number; left: number } | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editValue, setEditValue] = useState('')
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const menuRef = useRef<HTMLDivElement | null>(null)
  const fileRef = useRef<HTMLInputElement | null>(null)

  useEffect(() => {
    if (menuFor === null) return
    const onDown = (event: MouseEvent) => {
      if (menuRef.current !== null && !menuRef.current.contains(event.target as Node)) {
        closeMenu()
      }
    }
    const onScrollOrResize = () => closeMenu()
    window.addEventListener('mousedown', onDown)
    window.addEventListener('resize', onScrollOrResize)
    window.addEventListener('scroll', onScrollOrResize, true)
    return () => {
      window.removeEventListener('mousedown', onDown)
      window.removeEventListener('resize', onScrollOrResize)
      window.removeEventListener('scroll', onScrollOrResize, true)
    }
  }, [menuFor])

  const closeMenu = () => {
    setMenuFor(null)
    setMenuAnchor(null)
    setEditingId(null)
    setConfirmDeleteId(null)
    setError(null)
  }

  const startRename = (thread: Thread) => {
    setEditingId(thread.threadId)
    setEditValue(thread.title ?? '')
    setConfirmDeleteId(null)
    setError(null)
  }

  const submitRename = async (threadId: string) => {
    const title = editValue.trim()
    if (title === '') {
      setError('标题不能为空')
      return
    }
    setPending(true)
    try {
      await onRename(threadId, title)
      closeMenu()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setPending(false)
    }
  }

  const submitExport = async (threadId: string) => {
    setPending(true)
    try {
      await onExport(threadId)
      closeMenu()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setPending(false)
    }
  }

  const submitDelete = async (threadId: string) => {
    setPending(true)
    try {
      await onDelete(threadId)
      closeMenu()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setPending(false)
    }
  }

  return (
    <div
      className="thread-side"
      style={{
        borderRight: '1px solid var(--border)',
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        minHeight: 0,
        overflow: 'hidden',
        background: 'var(--bg)',
      }}
    >
      <div className="thread-head">
        <div className="thread-head__row">
          <span className="thread-head__title">会话</span>
          <span className="thread-head__count">{threads.length}</span>
          <span className="thread-head__spacer" />
          <input
            ref={fileRef}
            type="file"
            accept="application/json,.json"
            style={{ display: 'none' }}
            onChange={(event) => {
              const file = event.target.files?.[0]
              event.target.value = ''
              if (file) void onImport(file)
            }}
          />
          <button
            type="button"
            className="icon-btn"
            onClick={() => fileRef.current?.click()}
            disabled={busy}
            title="导入会话（选择导出的 JSON 文件）"
            aria-label="导入会话"
          >
            <Icon name="download" size={13} />
          </button>
          <button
            type="button"
            className="icon-btn icon-btn--accent"
            onClick={onCreate}
            disabled={busy}
            title="新建会话"
            aria-label="新建会话"
          >
            <Icon name="plus" size={14} />
          </button>
        </div>
        <div className="thread-head__hint">每轮可在单 Agent / 协作模式间切换</div>
      </div>

      <div
        className="scroll-thin"
        style={{
          flex: 1,
          minHeight: 0,
          overflowY: 'auto',
          padding: 8,
          display: 'grid',
          gap: 4,
          alignContent: 'start',
        }}
      >
        {threads.length === 0 && (
          <p style={{ fontSize: 12, color: 'var(--fg-subtle)', padding: 8, lineHeight: 1.6 }}>
            还没有会话。点「+ 新建」创建一个，或在桥接里已有会话时会自动出现。
          </p>
        )}

        {threads.map((thread) => {
          const active = thread.threadId === activeThreadId
          const editing = editingId === thread.threadId
          const confirming = confirmDeleteId === thread.threadId
          const menuOpen = menuFor === thread.threadId
          return (
            <div key={thread.threadId} className="thread-row">
              <button
                type="button"
                className="thread-item"
                aria-current={active ? 'true' : undefined}
                onClick={() => {
                  closeMenu()
                  onSelect(thread.threadId)
                }}
                disabled={busy}
                title={thread.threadId}
              >
                <span
                  style={{
                    fontSize: 13,
                    color: active ? 'var(--fg)' : 'var(--fg-muted)',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {thread.title ?? '未命名会话'}
                </span>
                <span style={{ display: 'flex', gap: 4, alignItems: 'center', marginTop: 4 }}>
                  <Badge tone={THREAD_STATUS_TONE[thread.status] ?? 'pending'} plain>
                    {thread.status}
                  </Badge>
                  {thread.taskId !== null && (
                    <span className="mono" style={{ color: 'var(--fg-subtle)', fontSize: 10 }}>
                      task:{thread.taskId.slice(0, 10)}
                    </span>
                  )}
                </span>
              </button>

              <button
                type="button"
                className="thread-more"
                aria-label={`${thread.title ?? '会话'} 的更多操作`}
                aria-expanded={menuOpen}
                onClick={(event) => {
                  if (menuOpen) {
                    closeMenu()
                    return
                  }
                  const rect = event.currentTarget.getBoundingClientRect()
                  const width = 184
                  const height = 190
                  const left = Math.min(rect.left, window.innerWidth - width - 8)
                  const top =
                    rect.bottom + 4 + height > window.innerHeight
                      ? Math.max(8, rect.top - height - 4)
                      : rect.bottom + 4
                  setMenuAnchor({ top, left })
                  setMenuFor(thread.threadId)
                }}
                disabled={busy}
                title="更多操作"
              >
                ⋯
              </button>

              {menuOpen && (
                <div
                  className="thread-menu thread-menu--fixed"
                  ref={menuRef}
                  style={menuAnchor !== null ? { top: menuAnchor.top, left: menuAnchor.left } : undefined}
                >
                  {confirming ? (
                    <>
                      <div className="thread-menu__hint">
                        确认删除？会话与其消息会被移除，不可恢复。
                      </div>
                      <button
                        type="button"
                        className="thread-menu__danger"
                        disabled={pending}
                        onClick={() => void submitDelete(thread.threadId)}
                      >
                        {pending ? '删除中…' : '确认删除'}
                      </button>
                      <button type="button" onClick={() => setConfirmDeleteId(null)}>
                        取消
                      </button>
                    </>
                  ) : editing ? (
                    <>
                      <input
                        value={editValue}
                        autoFocus
                        onChange={(event) => setEditValue(event.target.value)}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter') void submitRename(thread.threadId)
                          if (event.key === 'Escape') closeMenu()
                        }}
                        placeholder="新标题"
                      />
                      <button
                        type="button"
                        disabled={pending}
                        onClick={() => void submitRename(thread.threadId)}
                      >
                        {pending ? '保存中…' : '保存'}
                      </button>
                      <button type="button" onClick={() => setEditingId(null)}>
                        取消
                      </button>
                    </>
                  ) : (
                    <>
                      <button type="button" onClick={() => startRename(thread)}>
                        重命名
                      </button>
                      <button
                        type="button"
                        className="thread-menu__danger"
                        onClick={() => setConfirmDeleteId(thread.threadId)}
                      >
                        删除会话
                      </button>
                      <button
                        type="button"
                        disabled={pending}
                        onClick={() => void submitExport(thread.threadId)}
                        title="导出一个 JSON 文件（可在别的机器导入）"
                      >
                        {pending ? '导出中…' : '导出会话'}
                      </button>
                    </>
                  )}
                  {error !== null && <div className="thread-menu__hint" style={{ color: 'var(--sem-failed)' }}>{error}</div>}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
