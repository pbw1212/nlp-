import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import type { ApiKeyStatus, DataSource } from '@/types'
import type { BridgeProbe, SourceKind } from '@/datasource'
import { createDataSource, initialSourceKind, probeDataSource } from '@/datasource'
import type { ViewId } from '@/app/pages'
import { isViewId } from '@/app/pages'
import { THEME_STORAGE_KEY, readStoredThemeMode, resolveTheme } from '@/lib/theme'
import type { Theme, ThemeMode } from '@/lib/theme'

const VIEW_KEY = 'mapas.view'

interface AppStateValue {
  /** 当前页面 */
  view: ViewId
  setView(view: ViewId): void
  /** 主题选择（含 auto） */
  themeMode: ThemeMode
  /** 当前实际生效的主题（auto 时随北京时间解析） */
  theme: Theme
  setTheme(mode: ThemeMode): void
  /** 数据源：http（真实桥接）/ fixture（录制回放） */
  sourceKind: SourceKind
  changeSource(kind: SourceKind): void
  source: DataSource
  /** 桥接连接探针结果：界面必须如实显示通没通 */
  bridge: BridgeProbe
  refreshBridge(): Promise<void>
  /** 切换后端执行模式（single ⇄ multi_fixed）；切换成功后 epoch 递增，页面重挂载 */
  switchMode(mode: 'single' | 'multi_fixed'): Promise<void>
  /** 数据代次：模式切换等全局变化后递增，让页面重新挂载/取数 */
  epoch: number
  /** 协作模式横幅是否已被本次关闭（每次切换到协作模式都会重置为 false） */
  collabHintDismissed: boolean
  dismissCollabHint(): void
  /** API Key 弹窗（顶部/对话页都能打开） */
  apiKeyDialogOpen: boolean
  openApiKeyDialog(): void
  closeApiKeyDialog(): void
  /** 当前 API Key 状态（用于顶栏按钮着色；只含状态，不含 Key 内容） */
  apiKeyStatus: ApiKeyStatus | null
  refreshApiKeyStatus(refresh?: boolean): Promise<void>
  /** 跨页跳转用：证据页点某条记忆 → 切到记忆库并预选 */
  pendingMemoryFocus: string | null
  focusMemory(memoryId: string): void
  clearMemoryFocus(): void
}

const AppStateContext = createContext<AppStateValue | null>(null)



function readView(): ViewId {
  try {
    const stored = localStorage.getItem(VIEW_KEY)
    if (stored !== null && isViewId(stored)) return stored
  } catch {
    /* ignore */
  }
  return 'chat'
}

export function AppStateProvider({ children }: { children: ReactNode }) {
  const [view, setViewState] = useState<ViewId>(readView)
  const [themeMode, setThemeMode] = useState<ThemeMode>(readStoredThemeMode)
  const [sourceKind, setSourceKind] = useState<SourceKind>(initialSourceKind)
  const [bridge, setBridge] = useState<BridgeProbe>({ state: 'checking' })
  const [pendingMemoryFocus, setPendingMemoryFocus] = useState<string | null>(null)
  const [epoch, setEpoch] = useState(0)
  const [collabHintDismissed, setCollabHintDismissed] = useState(false)
  const [apiKeyDialogOpen, setApiKeyDialogOpen] = useState(false)
  const [apiKeyStatus, setApiKeyStatus] = useState<ApiKeyStatus | null>(null)

  const source = useMemo(() => createDataSource(sourceKind), [sourceKind])
  const activeSource = useRef(source)
  const probe = useRef({ sequence: 0, reloadOnSuccess: false, failed: false })

  const setView = useCallback((next: ViewId) => {
    setViewState(next)
    try {
      localStorage.setItem(VIEW_KEY, next)
    } catch {
      /* ignore */
    }
  }, [])

  const setTheme = useCallback((mode: ThemeMode) => {
    setThemeMode(mode)
    try {
      localStorage.setItem(THEME_STORAGE_KEY, mode)
    } catch {
      /* ignore */
    }
  }, [])

  /** auto 模式下的解析结果；非 auto 时不用它 */
  const [autoTheme, setAutoTheme] = useState<Theme>(() => resolveTheme('auto'))

  // auto 模式：每 30 秒按北京时间重算一次（跨越时段边界时自动换主题）
  useEffect(() => {
    if (themeMode !== 'auto') return
    setAutoTheme(resolveTheme('auto'))
    const timer = window.setInterval(() => setAutoTheme(resolveTheme('auto')), 30_000)
    return () => window.clearInterval(timer)
  }, [themeMode])

  const theme: Theme = themeMode === 'auto' ? autoTheme : themeMode

  const changeSource = useCallback((kind: SourceKind) => {
    // 换数据源必须整页重挂载：否则对话页会继续显示上一个数据源的会话/消息
    // （例如从"桥接"切到"回放"后，列表还是实时数据）。
    setSourceKind(kind)
    setEpoch((value) => value + 1)
  }, [])

  const checkBridge = useCallback(async (manual: boolean) => {
    if (activeSource.current !== source) return
    const current = probe.current
    const sequence = ++current.sequence
    if (manual) {
      current.reloadOnSuccess = true
      setBridge({ state: 'checking' })
    }
    const next = await probeDataSource(source)
    if (sequence !== current.sequence) return

    const reload = current.reloadOnSuccess || current.failed
    current.reloadOnSuccess = false
    current.failed = next.state === 'error'
    setBridge(next)
    if (next.state === 'ok' && reload) {
      setEpoch((value) => value + 1)
    }
  }, [source])

  const refreshBridge = useCallback(() => checkBridge(true), [checkBridge])

  /**
   * 切换执行模式。
   *
   * 后端模式是构造参数（无运行时切换 API），由桥接在同一数据目录上重建 app 实现。
   * 切换成功后递增 epoch，让当前页面重新挂载、重新取数（避免残留幽灵会话/旧能力状态）。
   */
  const switchMode = useCallback(
    async (mode: 'single' | 'multi_fixed') => {
      if (!source.writable) {
        throw new Error('回放模式不能切换后端执行模式')
      }
      probe.current.sequence += 1
      setBridge({ state: 'checking' })
      try {
        await source.switchMode(mode)
        // 切换后重新走一遍探针：鉴权模式/能力/needsApiKey 都可能有变化，避免手工拼装漂移
        await refreshBridge()
        // 每次切换模式都重新提醒（token 消耗是重要提醒，不该被永久关掉）
        setCollabHintDismissed(false)
      } catch (cause) {
        // 切换失败时保持旧状态可见：重新探测一次，把真实原因交给调用方
        await checkBridge(false)
        throw cause
      }
    },
    [source, checkBridge, refreshBridge],
  )

  /** 证据页点某条记忆 → 切到记忆库并预选（跨页跳转的最小实现） */
  const focusMemory = useCallback(
    (memoryId: string) => {
      setPendingMemoryFocus(memoryId)
      setView('memory')
    },
    [setView],
  )

  const clearMemoryFocus = useCallback(() => setPendingMemoryFocus(null), [])

  const dismissCollabHint = useCallback(() => setCollabHintDismissed(true), [])
  const refreshApiKeyStatus = useCallback(
    async (refresh = false) => {
      try {
        setApiKeyStatus(await source.getApiKeyStatus(refresh))
      } catch {
        // 回放模式或桥接异常：按钮保持中性色，不弹错
        setApiKeyStatus(null)
      }
    },
    [source],
  )

  const openApiKeyDialog = useCallback(() => setApiKeyDialogOpen(true), [])
  const closeApiKeyDialog = useCallback(() => setApiKeyDialogOpen(false), [])

  // 主题写到 <html data-theme>，tokens.css 靠它切换配色
  // 额外记录 mode，便于调试"自动模式当前命中哪个主题"
  //
  // 切换要"渐变"而不是"突变"：优先用 View Transitions 做整页交叉淡化；
  // 不支持时退化为 CSS 过渡（由 html.theme-transition 触发）。
  const firstThemeRef = useRef(true)
  useEffect(() => {
    const root = document.documentElement
    const apply = () => {
      root.dataset['theme'] = theme
      root.dataset['themeMode'] = themeMode
    }

    if (firstThemeRef.current) {
      firstThemeRef.current = false
      apply()
      return
    }

    const reduceMotion =
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const documentWithTransition = document as Document & {
      startViewTransition?: (callback: () => void) => { finished: Promise<void> }
    }

    if (!reduceMotion && typeof documentWithTransition.startViewTransition === 'function') {
      try {
        documentWithTransition.startViewTransition(apply)
        return
      } catch {
        // 过渡进行中被重复触发等极端情况：退化为直接应用
      }
    }

    apply()
    if (reduceMotion) return
    root.classList.add('theme-transition')
    const timer = window.setTimeout(() => root.classList.remove('theme-transition'), 480)
    return () => window.clearTimeout(timer)
  }, [theme, themeMode])

  // 启动、切换数据源和后台探活共用同一套连接状态。
  useEffect(() => {
    activeSource.current = source
    const current = probe.current
    current.reloadOnSuccess = false
    current.failed = false
    setBridge({ state: 'checking' })
    void checkBridge(false)
    const timer = window.setInterval(() => void checkBridge(false), 15_000)
    return () => {
      window.clearInterval(timer)
      current.sequence += 1
    }
  }, [source, checkBridge])

  // 桥接连上后拉一次 Key 状态（用于顶栏按钮着色）
  useEffect(() => {
    if (bridge.state !== 'ok') {
      setApiKeyStatus(null)
      return
    }
    void refreshApiKeyStatus()
  }, [bridge.state, refreshApiKeyStatus])

  const value = useMemo<AppStateValue>(
    () => ({
      view,
      setView,
      themeMode,
      theme,
      setTheme,
      sourceKind,
      changeSource,
      source,
      bridge,
      refreshBridge,
      switchMode,
      epoch,
      collabHintDismissed,
      dismissCollabHint,
      apiKeyDialogOpen,
      openApiKeyDialog,
      closeApiKeyDialog,
      apiKeyStatus,
      refreshApiKeyStatus,
      pendingMemoryFocus,
      focusMemory,
      clearMemoryFocus,
    }),
    [
      view,
      setView,
      themeMode,
      theme,
      setTheme,
      sourceKind,
      changeSource,
      source,
      bridge,
      refreshBridge,
      switchMode,
      epoch,
      collabHintDismissed,
      dismissCollabHint,
      apiKeyDialogOpen,
      openApiKeyDialog,
      closeApiKeyDialog,
      apiKeyStatus,
      refreshApiKeyStatus,
      pendingMemoryFocus,
      focusMemory,
      clearMemoryFocus,
    ],
  )

  return <AppStateContext.Provider value={value}>{children}</AppStateContext.Provider>
}

export function useAppState(): AppStateValue {
  const value = useContext(AppStateContext)
  if (value === null) {
    throw new Error('useAppState 必须在 <AppStateProvider> 内使用')
  }
  return value
}
