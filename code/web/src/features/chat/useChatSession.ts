import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type {
  AgentEvent,
  Message,
  PendingApproval,
  Thread,
  ThreadExportPayload,
  UnfinishedTurn,
} from '@/types'
import { downloadJson, sanitizeFileName } from '@/lib/download'
import type { DataSource } from '@/types'
import { describeError } from '@/datasource'
import { buildTimeline } from './eventModel'
import type { TurnBlock } from './eventModel'

const JOB_POLL_MS = 700
const EVENT_PAGE_LIMIT = 1000

export interface ChatSession {
  threads: Thread[]
  activeThread: Thread | null
  messages: Message[]
  events: AgentEvent[]
  approvals: PendingApproval[]
  /** 该会话里「上次进程中断、后端仍可恢复」的轮次 */
  unfinishedTurns: UnfinishedTurn[]
  timeline: TurnBlock[]
  /** 刚发出、后端还没回显的用户消息（单独渲染在时间线末尾） */
  pendingMessage: Message | null
  /** 是否有已提交但未结束的作业（对话/审批） */
  busy: boolean
  error: string | null
  /** 中性提示（例如"已提交，正在执行"），与 error 分开 */
  notice: string | null
  selectThread(threadId: string): Promise<void>
  createThread(title?: string): Promise<void>
  /** 重命名会话（标题写回后端 conversation 库） */
  renameThread(threadId: string, title: string): Promise<void>
  /** 导出会话为 JSON 并触发下载 */
  exportThread(threadId: string): Promise<void>
  /** 从 JSON 文件导入会话 */
  importThread(file: File): Promise<void>
  /** 删除会话；若删的是当前会话则清空选中态 */
  deleteThread(threadId: string): Promise<void>
  send(input: string): Promise<void>
  decide(pendingActionId: string, decision: 'CONFIRM' | 'REJECT'): Promise<void>
  /** 继续执行一个未完成的轮次（桥接侧的 resume 接口） */
  resumeUnfinished(turnId: string): Promise<void>
  reload(): Promise<void>
}

/**
 * 对话会话状态机。
 *
 * 关键点：
 * 1. **写操作不阻塞界面**：`turn/start` 与审批决策都返回 jobId，界面进入 busy 态；
 * 2. **进度靠增量读事件**：轮询 `readEvents(afterSequence)`，把新事件追加进时间线，
 *    所以用户能看到"阶段卡逐个出现"的过程（而不是转圈等到最后一次性出现）；
 * 3. **失败如实显示**：作业状态 FAILED 时把 `error.message` 原样抛给界面，绝不假装成功。
 */
export function useChatSession(source: DataSource): ChatSession {
  const [threads, setThreads] = useState<Thread[]>([])
  const [activeThread, setActiveThread] = useState<Thread | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [events, setEvents] = useState<AgentEvent[]>([])
  const [approvals, setApprovals] = useState<PendingApproval[]>([])
  const [unfinishedTurns, setUnfinishedTurns] = useState<UnfinishedTurn[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // 乐观显示：用户按下 Enter 的瞬间就要看见自己这句话（不等后端轮询）
  // 只存"最后一条尚未回显的发送"，不做假轮次；后端一落库就被真实消息替换。
  const [pendingMessage, setPendingMessage] = useState<Message | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const cursorRef = useRef(0)
  const pollTimerRef = useRef<number | null>(null)
  const activeThreadIdRef = useRef<string | null>(null)
  const sourceRef = useRef(source)
  sourceRef.current = source

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current !== null) {
      window.clearInterval(pollTimerRef.current)
      pollTimerRef.current = null
    }
  }, [])

  const loadThreadData = useCallback(
    async (threadId: string, opts?: { resetEvents?: boolean }) => {
      const ds = sourceRef.current
      const [nextMessages, nextApprovals, nextUnfinished, eventPage] = await Promise.all([
        ds.readMessages(threadId),
        ds.listApprovals(threadId),
        ds.listUnfinishedTurns(threadId),
        ds.readEvents({
          afterSequence: opts?.resetEvents === true ? 0 : cursorRef.current,
          threadId,
          limit: EVENT_PAGE_LIMIT,
        }),
      ])
      setMessages(nextMessages)
      setApprovals(nextApprovals)
      setUnfinishedTurns(nextUnfinished)
      setEvents((prev) => (opts?.resetEvents === true ? eventPage.events : [...prev, ...eventPage.events]))
      cursorRef.current = eventPage.nextSequence
    },
    [],
  )

  const loadThreads = useCallback(async () => {
    const list = await sourceRef.current.listThreads()
    setThreads(list)
    // 幽灵会话清理：后端已不存在的会话不能继续占着选中态
    const activeId = activeThreadIdRef.current
    if (activeId !== null && !list.some((item) => item.threadId === activeId)) {
      activeThreadIdRef.current = null
      setActiveThread(null)
      setMessages([])
      setEvents([])
      cursorRef.current = 0
    } else if (activeId !== null) {
      const refreshed = list.find((item) => item.threadId === activeId)
      if (refreshed !== undefined) setActiveThread(refreshed)
    }
    return list
  }, [])

  /** 首次挂载：拉会话列表；如果已有会话，自动选中第一个（不擅自 resume，只读消息） */
  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const list = await loadThreads()
        if (cancelled || list.length === 0) return
        const first = list[0]!
        setActiveThread(first)
        activeThreadIdRef.current = first.threadId
        await loadThreadData(first.threadId, { resetEvents: true })
      } catch (cause) {
        if (!cancelled) setError(describeError(cause))
      }
    })()
    return () => {
      cancelled = true
      stopPolling()
    }
  }, [loadThreads, loadThreadData, stopPolling])

  const selectThread = useCallback(
    async (threadId: string) => {
      setError(null)
      setNotice(null)
      const target = threads.find((item) => item.threadId === threadId)
      // 惰性会话（还没落库的新会话）没有可 resume 的东西：
      // 直接切过去空着即可，避免后端返回 404 造成"未找到会话"的无谓报错。
      setPendingMessage(null)
      if (target !== undefined && target.status === 'PENDING') {
        setActiveThread(target)
        activeThreadIdRef.current = target.threadId
        setMessages([])
        setEvents([])
        setUnfinishedTurns([])
        cursorRef.current = 0
        return
      }
      try {
        // resume 有副作用（多 Agent 模式会推进修复/闭环），但这是"切换会话"的既有语义
        const thread = await sourceRef.current.resumeThread(threadId)
        setPendingMessage(null)
        setActiveThread(thread)
        activeThreadIdRef.current = thread.threadId
        setMessages([])
        setEvents([])
        setUnfinishedTurns([])
        cursorRef.current = 0
        await loadThreadData(thread.threadId, { resetEvents: true })
      } catch (cause) {
        // 会话可能已被清掉：刷新列表让幽灵消失，并给一句人能看懂的话
        await loadThreads().catch(() => undefined)
        setError(describeError(cause))
      }
    },
    [loadThreadData, loadThreads, threads],
  )

  const createThread = useCallback(
    async (title?: string) => {
      setError(null)
      setNotice(null)
      try {
        const thread = await sourceRef.current.createThread(title)
        await loadThreads()
        setActiveThread(thread)
        activeThreadIdRef.current = thread.threadId
        setMessages([])
        setEvents([])
        setUnfinishedTurns([])
        cursorRef.current = 0
      } catch (cause) {
        setError(describeError(cause))
      }
    },
    [loadThreads],
  )

  /** 导出当前会话：从后端读取完整消息快照，下载成 JSON 文件 */
  const exportThread = useCallback(
    async (threadId: string) => {
      setError(null)
      setNotice(null)
      try {
        const payload = await sourceRef.current.exportThread(threadId)
        const current = threads.find((item) => item.threadId === threadId)
        const name = sanitizeFileName(current?.title ?? '会话')
        downloadJson(`${name}.json`, payload)
        setNotice(`已导出会话（${payload.messages.length} 条消息）`)
      } catch (cause) {
        setError(describeError(cause))
        throw cause
      }
    },
    [threads],
  )

  /** 导入会话 JSON：生成新会话，不覆盖任何已有会话 */
  const importThread = useCallback(
    async (file: File) => {
      setError(null)
      setNotice(null)
      try {
        const text = await file.text()
        let payload: ThreadExportPayload
        try {
          payload = JSON.parse(text) as ThreadExportPayload
        } catch {
          throw new Error('文件不是合法的 JSON')
        }
        const result = await sourceRef.current.importThread(payload)
        await loadThreads()
        await selectThread(result.sessionId)
        setNotice(`已导入「${result.title}」，共 ${result.messageCount} 条消息`)
      } catch (cause) {
        setError(describeError(cause))
        throw cause
      }
    },
    [loadThreads, selectThread],
  )

  /** 重命名会话：真实写回后端，然后刷新列表（标题随之更新） */
  const renameThread = useCallback(
    async (threadId: string, title: string) => {
      setError(null)
      try {
        await sourceRef.current.renameThread(threadId, title)
        await loadThreads()
      } catch (cause) {
        setError(describeError(cause))
        throw cause
      }
    },
    [loadThreads],
  )

  /** 删除会话；如果删的是当前会话，清空选中态与本地数据 */
  const deleteThread = useCallback(
    async (threadId: string) => {
      setError(null)
      try {
        const result = await sourceRef.current.deleteThread(threadId)
        if (result.wasActive || activeThreadIdRef.current === threadId) {
          activeThreadIdRef.current = null
          setActiveThread(null)
          setMessages([])
          setEvents([])
          cursorRef.current = 0
        }
        await loadThreads()
      } catch (cause) {
        setError(describeError(cause))
        throw cause
      }
    },
    [loadThreads],
  )

  /** 轮询作业：同时增量读事件，并把终态写回界面 */  const watchJob = useCallback(
    (jobId: string) => {
      stopPolling()
      const ds = () => sourceRef.current
      pollTimerRef.current = window.setInterval(() => {
        void (async () => {
          const threadId = activeThreadIdRef.current
          try {
            if (threadId !== null) {
              // 同时拉事件与消息：消息让"用户提问/助手回答"尽早出现，
              // 事件让"阶段/工具/审批"逐项出现。两者都来自后端，不做本地臆造。
              const [page, latestMessages] = await Promise.all([
                ds().readEvents({
                  afterSequence: cursorRef.current,
                  threadId,
                  limit: EVENT_PAGE_LIMIT,
                }),
                ds().readMessages(threadId),
              ])
              if (page.events.length > 0) {
                cursorRef.current = page.nextSequence
                setEvents((prev) => [...prev, ...page.events])
              }
              setMessages(latestMessages)
            }
            const job = await ds().getJob(jobId)
            if (job.state === 'SUCCEEDED' || job.state === 'FAILED') {
              stopPolling()
              setBusy(false)
              if (job.state === 'FAILED') {
                setError(job.error?.message ?? '作业失败（未返回原因）')
              } else {
                setNotice(null)
              }
              setPendingMessage(null)
              if (threadId !== null) {
                await loadThreadData(threadId)
              }
              const list = await loadThreads()
              const current = list.find((item) => item.threadId === threadId)
              if (current !== undefined) setActiveThread(current)
            }
          } catch (cause) {
            stopPolling()
            setBusy(false)
            setError(describeError(cause))
          }
        })()
      }, JOB_POLL_MS)
    },
    [loadThreadData, loadThreads, stopPolling],
  )

  const send = useCallback(
    async (input: string) => {
      const threadId = activeThreadIdRef.current
      if (threadId === null) {
        setError('还没有选中的会话，请先新建一个会话')
        return
      }
      const trimmed = input.trim()
      if (trimmed.length === 0) return

      // 1) 立即本地显示用户消息（不等 startTurn 返回，更不等轮询）
      const optimisticMessage: Message = {
        messageId: `local_${Date.now()}`,
        turnId: '',
        role: 'USER',
        content: trimmed,
        createdAt: Date.now() / 1000,
      }
      setPendingMessage(optimisticMessage)
      setError(null)
      setNotice('已提交，正在执行…')
      setBusy(true)

      try {
        const job = await sourceRef.current.startTurn(threadId, trimmed)
        watchJob(job.jobId)
      } catch (cause) {
        setPendingMessage(null)
        setBusy(false)
        setNotice(null)
        setError(describeError(cause))
      }
    },
    [watchJob],
  )

  const decide = useCallback(
    async (pendingActionId: string, decision: 'CONFIRM' | 'REJECT') => {
      setError(null)
      setNotice(decision === 'CONFIRM' ? '已确认，正在执行…' : '已拒绝，正在处理…')
      setBusy(true)
      try {
        const job = await sourceRef.current.decideApproval(pendingActionId, decision)
        watchJob(job.jobId)
      } catch (cause) {
        setBusy(false)
        setNotice(null)
        setError(describeError(cause))
      }
    },
    [watchJob],
  )

  /** 继续执行一个未完成的轮次：桥接恢复作业，随后与普通作业一样轮询 */
  const resumeUnfinished = useCallback(
    async (turnId: string) => {
      const threadId = activeThreadIdRef.current
      if (threadId === null) {
        setError('还没有选中的会话')
        return
      }
      setError(null)
      setNotice('正在恢复未完成的轮次…')
      setBusy(true)
      try {
        const job = await sourceRef.current.resumeTurn(threadId, turnId)
        watchJob(job.jobId)
      } catch (cause) {
        setBusy(false)
        setNotice(null)
        setError(describeError(cause))
      }
    },
    [watchJob],
  )

  const reload = useCallback(async () => {
    const threadId = activeThreadIdRef.current
    setError(null)
    try {
      const list = await loadThreads()
      if (threadId === null) return
      const current = list.find((item) => item.threadId === threadId)
      if (current !== undefined) setActiveThread(current)
      await loadThreadData(threadId)
    } catch (cause) {
      setError(describeError(cause))
    }
  }, [loadThreadData, loadThreads])

  /**
   * 待回显消息：后端一旦落库同内容的用户消息，就交还给时间线渲染（避免重复显示）。
   * 用"内容 + 时间窗"匹配，而不是猜轮次 id —— 普通轮次的 turnId 只有作业跑起来才有。
   */
  const pendingVisible = useMemo(() => {
    if (pendingMessage === null) return null
    const echoed = messages.some(
      (message) =>
        message.role === 'USER' &&
        message.content.trim() === pendingMessage.content.trim() &&
        Math.abs(message.createdAt - pendingMessage.createdAt) < 30,
    )
    return echoed ? null : pendingMessage
  }, [pendingMessage, messages])

  const timeline = useMemo(() => buildTimeline(messages, events), [messages, events])

  return {
    threads,
    activeThread,
    messages,
    events,
    approvals,
    unfinishedTurns,
    timeline,
    /** 刚发出、后端还没回显的用户消息（时间线末尾单独渲染） */
    pendingMessage: pendingVisible,
    busy,
    error,
    notice,
    selectThread,
    createThread,
    renameThread,
    exportThread,
    importThread,
    deleteThread,
    send,
    decide,
    resumeUnfinished,
    reload,
  }
}
