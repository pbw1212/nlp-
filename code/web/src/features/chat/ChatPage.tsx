import { useCallback, useEffect, useRef, useState } from 'react'
import { withErrorTranslation } from '@/lib/errorText'
import { useAppState } from '@/state/AppState'
import { Badge, EmptyState } from '@/components/ui/primitives'
import { Composer } from './Composer'
import { ModeHint } from './ModeHint'
import { TokenFlame } from '@/components/FlameLoader'
import { ThreadList } from './ThreadList'
import { TurnTimeline } from './TurnTimeline'
import { useChatSession } from './useChatSession'

/**
 * 对话工作台（P2 主链路）。
 *
 * 数据全部来自真实后端（经 bridge）：
 * - 会话/消息：`thread/read`；
 * - 过程项（阶段、工具、审批、压缩）：事件流增量读取；
 * - 发送与审批：异步作业 + 轮询，失败如实显示。
 *
 * 没有任何前端编造的步骤或占位回答。
 */
export function ChatPage() {
  const { source, bridge, refreshBridge, openApiKeyDialog } = useAppState()
  const session = useChatSession(source)
  const bridgeDown = bridge.state === 'error'
  const needsApiKey = bridge.state === 'ok' && bridge.needsApiKey
  const noThread = session.activeThread === null

  // 默认滚到最新（像微信打开聊天）；用户往上翻时不被强制拉回
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const [atBottom, setAtBottom] = useState(true)

  const scrollToLatest = useCallback((smooth: boolean) => {
    const element = scrollRef.current
    if (element === null) return
    element.scrollTo({ top: element.scrollHeight, behavior: smooth ? 'smooth' : 'auto' })
    setAtBottom(true)
  }, [])

  const onScroll = useCallback(() => {
    const element = scrollRef.current
    if (element === null) return
    const distance = element.scrollHeight - element.scrollTop - element.clientHeight
    setAtBottom(distance < 96)
  }, [])

  // 切会话：直接落到最新一条
  useEffect(() => {
    const frame = window.requestAnimationFrame(() => scrollToLatest(false))
    return () => window.cancelAnimationFrame(frame)
  }, [session.activeThread?.threadId, scrollToLatest])

  // 新内容到达：只有本来就在底部时才跟随
  useEffect(() => {
    if (!atBottom) return
    const frame = window.requestAnimationFrame(() => scrollToLatest(true))
    return () => window.cancelAnimationFrame(frame)
  }, [
    session.timeline.length,
    session.messages.length,
    session.pendingMessage,
    session.busy,
    atBottom,
    scrollToLatest,
  ])

  // 未配置 API Key：显示引导模板，而不是一屏红色报错（也不去发必然失败的请求）
  if (needsApiKey) {
    return (
      <div style={{ height: '100%', display: 'grid', placeItems: 'center', padding: 24 }}>
        <div className="locked-panel">
          <div className="locked-panel__icon" aria-hidden="true">
            🔑
          </div>
          <h2 style={{ margin: 0, fontSize: 16 }}>您还未设置 DeepSeek API Key</h2>
          <p style={{ margin: 0, color: 'var(--fg-muted)', fontSize: 13, lineHeight: 1.8 }}>
            请点击右上角「<b style={{ color: 'var(--fg)' }}>API Key</b>」填入密钥。
            填入后会立即生效，不需要重启桥接。
          </p>
          <div style={{ display: 'flex', gap: 8, justifyContent: 'center' }}>
            <button type="button" className="chip chip--button" onClick={openApiKeyDialog}>
              去填写 API Key
            </button>
            <button type="button" className="chip chip--button" onClick={() => void refreshBridge()}>
              重新检测
            </button>
          </div>
          <p className="hint" style={{ textAlign: 'center' }}>
            密钥只写不读：页面不会显示你填入的内容；删除密钥后对话会立即被拒绝。
          </p>
        </div>
      </div>
    )
  }

  return (
    <div
      className="chat-layout"
      style={{
        height: '100%',
        display: 'grid',
        gridTemplateColumns: '216px minmax(0, 1fr)',
        gridTemplateRows: 'minmax(0, 1fr)',
        minHeight: 0,
      }}
    >
      <ThreadList
        threads={session.threads}
        activeThreadId={session.activeThread?.threadId ?? null}
        busy={session.busy}
        onSelect={(threadId) => void session.selectThread(threadId)}
        onCreate={() => void session.createThread()}
        onRename={session.renameThread}
        onExport={session.exportThread}
        onImport={session.importThread}
        onDelete={session.deleteThread}
      />

      <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0, height: '100%', minWidth: 0 }}>
        <header
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            padding: '10px 16px',
            borderBottom: '1px solid var(--border)',
            background: 'var(--bg-elev)',
            flex: 'none',
            minHeight: 46,
          }}
        >
          <span style={{ fontSize: 13, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {session.activeThread?.title ?? '未选择会话'}
          </span>
          {session.activeThread !== null && (
            <>
              <Badge tone="accent" plain>
                {session.activeThread.status}
              </Badge>
              <span className="mono" style={{ color: 'var(--fg-subtle)' }}>
                {session.activeThread.threadId.slice(0, 18)}
              </span>
            </>
          )}
          <div style={{ flex: 1 }} />
          {session.busy && (
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--sem-running)' }}>
              <TokenFlame size={16} title="正在执行" />
              执行中
            </span>
          )}
          <button
            type="button"
            className="chip chip--button"
            onClick={() => void session.reload()}
            disabled={session.busy}
          >
            刷新
          </button>
        </header>

        {bridge.state === 'ok' && bridge.executionMode === 'multi_fixed' && <ModeHint />}

        {(session.error !== null || bridgeDown) && (
          <div
            style={{
              padding: '8px 16px',
              borderBottom: '1px solid var(--border)',
              background: 'color-mix(in oklab, var(--sem-failed) 10%, transparent)',
              color: 'var(--sem-failed)',
              fontSize: 12,
              lineHeight: 1.7,
              display: 'flex',
              gap: 12,
              alignItems: 'flex-start',
            }}
          >
            <div style={{ flex: 1 }}>
              {bridgeDown && bridge.state === 'error' ? (
                <>
                  {bridge.code === 'AUTH_REQUIRED' ? (
                    <>
                      桥接要求<b>访问口令</b>：点顶栏「口令：需要填写」粘贴启动时终端打印的那串随机码
                      （不是 DeepSeek API Key，也不是模型 token）。
                      <br />
                      {withErrorTranslation(bridge.message)}
                    </>
                  ) : (
                    <>
                      后端未连接（{bridge.code}）：{withErrorTranslation(bridge.message)}
                      <br />
                      启动桥接后点右侧「重试」：<span className="mono">python code/web/bridge/web_bridge.py --execution-mode multi_fixed --port 8787</span>
                    </>
                  )}
                </>
              ) : (
                withErrorTranslation(session.error ?? '')
              )}
            </div>
            {bridgeDown && (
              <button type="button" className="chip chip--button" onClick={() => void refreshBridge()}>
                重试
              </button>
            )}
          </div>
        )}

        {session.notice !== null && (
          <div
            style={{
              padding: '6px 16px',
              borderBottom: '1px solid var(--border)',
              fontSize: 12,
              color: 'var(--sem-running)',
            }}
          >
            {session.notice}
          </div>
        )}

        {session.unfinishedTurns.length > 0 && (
          <div
            style={{
              padding: '8px 16px',
              borderBottom: '1px solid var(--border)',
              background:
                'color-mix(in oklab, var(--sem-warning, var(--sem-unknown)) 10%, transparent)',
              fontSize: 12,
              display: 'flex',
              gap: 12,
              alignItems: 'center',
              flexWrap: 'wrap',
            }}
          >
            <div style={{ flex: 1, minWidth: 200 }}>
              该会话有 <b>{session.unfinishedTurns.length}</b> 个未完成的轮次（上次执行被中断）。
              桥接可以从中断处继续执行。
            </div>
            {session.unfinishedTurns.map((turn) => (
              <button
                key={turn.turn_id}
                type="button"
                className="chip chip--button"
                disabled={session.busy}
                title={`轮次 ${turn.turn_id}，开始于 ${new Date(turn.started_at * 1000).toLocaleString('zh-CN')}`}
                onClick={() => void session.resumeUnfinished(turn.turn_id)}
              >
                继续执行 {turn.turn_id.slice(0, 12)}
              </button>
            ))}
            {!session.busy && (
              <button type="button" className="chip chip--button" onClick={() => void session.reload()}>
                刷新
              </button>
            )}
          </div>
        )}

        <div style={{ position: 'relative', flex: 1, minHeight: 0 }}>
        <div
          ref={scrollRef}
          className="scroll-thin"
          onScroll={onScroll}
          style={{ height: '100%', overflowY: 'auto' }}
        >
          {noThread ? (
            <div style={{ padding: 24 }}>
              <EmptyState
                title="还没有会话"
                description="对话页需要先有一个会话。点左侧「+ 新建」创建；如果桥接里已存在会话，会自动出现在列表里。"
                items={[
                  '新建会话本身不消耗模型调用（懒创建，首轮才落库）',
                  '发一条消息后才真正进入 Agent 执行',
                  '执行过程会在时间线里逐项出现：阶段、工具调用、审批、最终回答',
                ]}
                note="发送消息会真实调用后端（消耗 DeepSeek 调用）。"
              />
            </div>
          ) : (
            <TurnTimeline
              timeline={session.timeline}
              busy={session.busy}
              pendingMessage={session.pendingMessage}
              onDecide={(id, decision) => void session.decide(id, decision)}
            />
          )}
        </div>
          {!atBottom && (
            <button type="button" className="to-bottom" onClick={() => scrollToLatest(true)} title="回到最新">
              ↓ 最新
            </button>
          )}
        </div>

        <Composer
          busy={session.busy}
          disabled={noThread || bridgeDown}
          disabledReason={
            bridgeDown ? '后端未连接，无法发送' : noThread ? '请先新建或选择一个会话' : undefined
          }
          onSend={(input) => void session.send(input)}
        />
      </div>
    </div>
  )
}
