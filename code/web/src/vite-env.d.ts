/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 桥接进程地址；不设置则用默认 http://127.0.0.1:8787 */
  readonly VITE_BRIDGE_URL?: string
  /** 桥接进程的一次性本地 token */
  readonly VITE_BRIDGE_TOKEN?: string
  /** 默认数据源：http（真实）| fixture（录制回放） */
  readonly VITE_DATA_SOURCE?: 'http' | 'fixture'
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
