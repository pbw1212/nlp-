import type { Tone } from '@/components/ui/primitives'

/** 协作阶段状态 → 语义色调（与 tokens.css 的 data-tone 对齐） */
export const STATUS_TONE: Record<string, Tone> = {
  VERIFIED: 'verified',
  PRODUCED: 'produced',
  RUNNING: 'running',
  PENDING: 'pending',
  FAILED: 'failed',
  BLOCKED: 'blocked',
  STALE: 'stale',
  SUCCEEDED: 'verified',
  REVOKED: 'stale',
}

/** 运行级状态 → 语义色调 */
export const RUN_STATUS_TONE: Record<string, Tone> = {
  SUCCEEDED: 'verified',
  FAILED: 'failed',
  RUNNING: 'running',
  PENDING: 'pending',
}

export function toneOf(status: string | null | undefined): Tone {
  if (status === null || status === undefined) return 'neutral'
  return STATUS_TONE[status] ?? 'neutral'
}
