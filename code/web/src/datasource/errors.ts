/**
 * 数据源错误。
 *
 * 为什么要单独定义错误类型：本层有一条硬规则 —— **绝不用假数据假装成功**。
 * 桥接没启动、fixture 没录制、写操作在只读回放里被调用，都必须变成一个明确的、
 * 界面上能看懂的报错，而不是静默返回空数组。
 */
export type DataSourceErrorCode =
  | 'BRIDGE_NOT_RUNNING' // 桥接进程没起来（连接被拒/超时）
  | 'HTTP_ERROR' // 桥接返回了非 2xx
  | 'BAD_RESPONSE' // 返回体不是预期形状
  | 'NO_FIXTURE_RECORDED' // 回放模式里没有对应的录制
  | 'OFFLINE_FIXTURE_READ_ONLY' // 回放模式不支持写操作
  | 'NOT_SUPPORTED' // 该操作在当前数据源上尚未实现
  | 'AUTH_REQUIRED' // 桥接要求访问口令，但本机尚未填写（或填写错误）

export class DataSourceError extends Error {
  readonly code: DataSourceErrorCode
  readonly detail?: string

  constructor(code: DataSourceErrorCode, message: string, detail?: string) {
    super(message)
    this.name = 'DataSourceError'
    this.code = code
    this.detail = detail
  }
}

export function isDataSourceError(value: unknown): value is DataSourceError {
  return value instanceof DataSourceError
}

/** 把任意异常压成一句人类可读的说明（界面直接显示，不吞掉） */
export function describeError(value: unknown): string {
  if (isDataSourceError(value)) {
    return value.detail ? `${value.message}（${value.detail}）` : value.message
  }
  if (value instanceof Error) return value.message
  return String(value)
}
