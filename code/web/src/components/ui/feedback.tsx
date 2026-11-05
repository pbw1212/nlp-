import type { ReactNode } from 'react'
import { withErrorTranslation } from '@/lib/errorText'

/**
 * 反馈类原子组件（横幅 / 灰色解释 / 可折叠的「更多信息」）。
 *
 * 抽出来的原因：之前 5 个业务页面各写一份 Banner/Hint，文案与配色逐渐漂移。
 * 现在统一在这里定义，页面只关心"放什么信息"。
 */

export type BannerTone = 'error' | 'info' | 'success'

export function Banner({
  children,
  tone = 'error',
  action,
}: {
  children: ReactNode
  tone?: BannerTone
  action?: ReactNode
}) {
  // 字符串错误自动补中文解释（原文保留，排查仍可用）
  const content = typeof children === 'string' ? withErrorTranslation(children) : children
  return (
    <div className="banner" data-tone={tone} role={tone === 'error' ? 'alert' : undefined}>
      <div style={{ flex: 1, minWidth: 0 }}>{content}</div>
      {action}
    </div>
  )
}

/** 一行灰色小字解释：放在标题下或区块内，帮助非技术读者理解术语 */
export function Hint({ children }: { children: ReactNode }) {
  return <p className="hint">{children}</p>
}

/**
 * 可折叠的「更多信息」：默认收起，点开才显示次要细节。
 * 用原生 <details>，无状态、可键盘操作、无需额外 JS。
 */
export function MoreInfo({
  children,
  label = '更多信息',
  open = false,
}: {
  children: ReactNode
  label?: string
  open?: boolean
}) {
  return (
    <details className="moreinfo" open={open}>
      <summary>{label}</summary>
      <div className="moreinfo__body">{children}</div>
    </details>
  )
}
