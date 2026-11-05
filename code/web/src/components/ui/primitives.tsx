import type { ReactNode } from 'react'

/**
 * 语义色调：与 CSS `data-tone` 一一对应。
 * 取值直接沿用 Python 侧枚举的小写形式（match/mismatch/...），方便对照。
 */
export type Tone =
  | 'match'
  | 'mismatch'
  | 'unknown'
  | 'ambiguous'
  | 'pending'
  | 'running'
  | 'produced'
  | 'verified'
  | 'failed'
  | 'blocked'
  | 'stale'
  | 'waiting'
  | 'valid'
  | 'notchecked'
  | 'completed'
  | 'cancelled'
  | 'repair'
  | 'assess'
  | 'accent'
  | 'neutral'

export function Badge({
  tone = 'neutral',
  filled = false,
  plain = false,
  title,
  children,
}: {
  tone?: Tone
  filled?: boolean
  plain?: boolean
  title?: string
  children: ReactNode
}) {
  const className = ['badge', filled ? 'badge--filled' : '', plain ? 'badge--plain' : '']
    .filter(Boolean)
    .join(' ')
  return (
    <span className={className} data-tone={tone} title={title}>
      {children}
    </span>
  )
}

export function StatusDot({ tone = 'neutral', title }: { tone?: Tone; title?: string }) {
  return <span className="dot" data-tone={tone} title={title} aria-hidden="true" />
}

export function Card({
  title,
  description,
  actions,
  flat = false,
  collapsed = false,
  children,
}: {
  title?: ReactNode
  description?: ReactNode
  actions?: ReactNode
  flat?: boolean
  /** 次要卡片默认折叠：标题常显，内容放在「更多信息」里 */
  collapsed?: boolean
  children?: ReactNode
}) {
  return (
    <section className={flat ? 'card card--flat' : 'card'}>
      {(title !== undefined || actions !== undefined) && (
        <header className="card__head">
          <div style={{ display: 'grid', gap: 2 }}>
            {title !== undefined && (
              <span style={{ fontSize: 13, fontWeight: 600 }}>{title}</span>
            )}
            {description !== undefined && (
              <span style={{ fontSize: 12, color: 'var(--fg-subtle)' }}>{description}</span>
            )}
          </div>
          {actions}
        </header>
      )}
      {children !== undefined &&
        (collapsed ? (
          <details className="card__fold">
            <summary>更多信息</summary>
            <div className="card__body">{children}</div>
          </details>
        ) : (
          <div className="card__body">{children}</div>
        ))}
    </section>
  )
}

export function EmptyState({
  title,
  description,
  items,
  note,
}: {
  title: string
  description: string
  items?: string[]
  note?: string
}) {
  return (
    <div className="empty">
      <div className="empty__title">{title}</div>
      <p className="empty__desc">{description}</p>
      {items !== undefined && items.length > 0 && (
        <ul className="empty__list">
          {items.map((item) => (
            <li key={item} className="empty__item">
              <span>{item}</span>
            </li>
          ))}
        </ul>
      )}
      {note !== undefined && (
        <p className="empty__desc" style={{ color: 'var(--fg-subtle)' }}>
          {note}
        </p>
      )}
    </div>
  )
}

export function KeyValue({ rows }: { rows: { k: ReactNode; v: ReactNode }[] }) {
  return (
    <div className="kv">
      {rows.map((row, index) => (
        <div key={index} style={{ display: 'contents' }}>
          <div className="kv__k">{row.k}</div>
          <div className="kv__v">{row.v}</div>
        </div>
      ))}
    </div>
  )
}

export function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <div
      style={{
        fontSize: 11,
        letterSpacing: '0.08em',
        textTransform: 'uppercase',
        color: 'var(--fg-subtle)',
        marginBottom: 8,
      }}
    >
      {children}
    </div>
  )
}
