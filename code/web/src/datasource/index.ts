import type { DataSource, MetaInfo } from '@/types'
import { describeError, isDataSourceError } from './errors'
import { HttpSource } from './http'
import { FixtureSource } from './fixture'

export { DataSourceError, describeError, isDataSourceError } from './errors'
export { bridgeBaseUrl, bridgeToken, setBridgeToken } from './http'
export { FixtureSource } from './fixture'
export { HttpSource } from './http'

export type SourceKind = 'http' | 'fixture'

/** 默认数据源：URL 参数 > 构建时环境变量 > 真实（http） */
export function initialSourceKind(): SourceKind {
  try {
    const fromQuery = new URLSearchParams(window.location.search).get('src')
    if (fromQuery === 'fixture' || fromQuery === 'http') return fromQuery
  } catch {
    /* 非浏览器环境忽略 */
  }
  return import.meta.env.VITE_DATA_SOURCE === 'fixture' ? 'fixture' : 'http'
}

export function createDataSource(kind: SourceKind): DataSource {
  return kind === 'fixture' ? new FixtureSource() : new HttpSource()
}

export type BridgeProbe =
  | { state: 'checking' }
  | {
      state: 'ok'
      executionMode: 'single' | 'multi_fixed'
      agentHome: string
      protocolVersion: string
      capabilities: MetaInfo['capabilities']
      /** 后端未配置 API Key：界面应显示"去填写密钥"的引导，而不是报错 */
      needsApiKey: boolean
      /** 桥接是否要求访问口令（false = 以 --no-token 启动，本机免校验） */
      authRequired: boolean
    }
  | { state: 'error'; message: string; code: string }

/**
 * 连接探针：只调 `meta()`。
 *
 * 为什么单独一个函数：界面启动时要**如实**显示"后端通没通"，
 * 不能等到用户点了某个面板才报错。失败原因原样呈现，不做兜底美化。
 */
export async function probeDataSource(source: DataSource): Promise<BridgeProbe> {
  // 先问桥接"你要求口令吗"（该探针免鉴权、不含数据）。
  // 这样"本机免校验"与"需要填口令"能区分开，而不是都显示成"未设置"。
  let tokenRequired = false
  try {
    const auth = await source.checkAuth()
    tokenRequired = auth.tokenRequired
  } catch {
    // 老版本桥接没有 /api/auth：退化为直接试 meta
    tokenRequired = false
  }

  if (tokenRequired && !source.hasCredential()) {
    return {
      state: 'error',
      code: 'AUTH_REQUIRED',
      message: '桥接要求访问口令，但本机浏览器还没有填写',
    }
  }

  try {
    const meta = await source.meta()
    return {
      state: 'ok',
      executionMode: meta.executionMode,
      agentHome: meta.agentHome,
      protocolVersion: meta.protocolVersion,
      capabilities: meta.capabilities,
      needsApiKey: meta.needsApiKey === true,
      authRequired: tokenRequired,
    }
  } catch (error) {
    const code = isDataSourceError(error) ? error.code : 'UNKNOWN'
    if (code === 'HTTP_ERROR' && describeError(error).includes('401')) {
      return {
        state: 'error',
        code: 'AUTH_REQUIRED',
        message: '访问口令无效或已过期（桥接重启后口令会变）',
      }
    }
    return { state: 'error', message: describeError(error), code }
  }
}
