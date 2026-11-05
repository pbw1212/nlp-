import { useCallback, useEffect, useRef, useState } from 'react'
import { describeError } from '@/datasource'

export interface AsyncState<T> {
  data: T | null
  loading: boolean
  error: string | null
  reload(): void
}

/**
 * 极小的异步数据加载 hook。
 *
 * 为什么不引 react-query：本层只有"读一次 + 手动刷新"的需求，
 * 引一个状态库会多一份依赖和心智负担。这里把三件事做对就够了：
 * 1. 组件卸载后不再 setState；2. 竞态时只认最后一次请求；3. 错误原样暴露（不吞）。
 */
export function useAsync<T>(loader: () => Promise<T>, deps: readonly unknown[]): AsyncState<T> {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)
  const requestIdRef = useRef(0)
  const loaderRef = useRef(loader)
  loaderRef.current = loader

  const reload = useCallback(() => setNonce((value) => value + 1), [])

  useEffect(() => {
    const requestId = ++requestIdRef.current
    let cancelled = false
    setLoading(true)
    setError(null)

    void (async () => {
      try {
        const result = await loaderRef.current()
        if (cancelled || requestId !== requestIdRef.current) return
        setData(result)
      } catch (cause) {
        if (cancelled || requestId !== requestIdRef.current) return
        setError(describeError(cause))
      } finally {
        if (!cancelled && requestId === requestIdRef.current) setLoading(false)
      }
    })()

    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce])

  return { data, loading, error, reload }
}
