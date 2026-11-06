import { PAGES } from '@/app/pages'
import type { PageMeta } from '@/app/pages'
import { Icon } from '@/components/Icon'
import type { IconName } from '@/components/Icon'
import { useAppState } from '@/state/AppState'
import { StatusDot } from '@/components/ui/primitives'
import { Clock } from './Clock'

const NAV_ICON: Record<PageMeta['id'], IconName> = {
  chat: 'chat',
  memory: 'memory',
  retrieval: 'retrieval',
  tasks: 'tasks',
  collaboration: 'collaboration',
  governance: 'governance',
  context: 'context',
}

/**
 * 左侧导航：七个界面。
 *
 * 每个条目右侧的小圆点显示"就绪度"：
 * - 灰点 = 等桥接进程（后端方法已确定，只是没连上）
 * - 橙点 = 依赖待验证项 V1（验证前不承诺能显示什么）
 * 这样"某屏没数据"永远是**有原因、可解释**的，而不是空白。
 */
export function SideNav() {
  const { view, setView } = useAppState()

  return (
    <nav
      aria-label="主导航"
      style={{
        borderRight: '1px solid var(--border)',
        background: 'var(--bg)',
        padding: '12px 10px',
        display: 'flex',
        flexDirection: 'column',
        gap: 4,
        overflowY: 'auto',
      }}
      className="scroll-thin app-side"
    >
      <div
        className="side-label"
        style={{
          fontSize: 11,
          letterSpacing: '0.08em',
          textTransform: 'uppercase',
          color: 'var(--fg-subtle)',
          padding: '4px 10px 8px',
        }}
      >
        工作台
      </div>

      {PAGES.map((page) => {
        const active = page.id === view
        return (
          <button
            key={page.id}
            type="button"
            className="nav-item"
            aria-current={active ? 'page' : undefined}
            onClick={() => setView(page.id)}
            title={page.subtitle}
          >
            <Icon name={NAV_ICON[page.id]} size={15} />
            <span style={{ flex: 1 }}>{page.navLabel}</span>
            <StatusDot
              tone={
                page.readiness === 'ready'
                  ? 'verified'
                  : page.readiness === 'blocked-v1'
                    ? 'waiting'
                    : 'pending'
              }
              title={
                page.readiness === 'ready'
                  ? '已实现并接入真实数据源'
                  : page.readiness === 'blocked-v1'
                    ? '依赖待验证项 V1（检索证据字段是否被持久化）'
                    : '后端方法已确定，等桥接连接'
              }
            />
          </button>
        )
      })}

      <div className="side-spacer" style={{ flex: 1 }} />

      <Clock />

      <div
        className="side-foot"
        style={{
          padding: '10px',
          borderTop: '1px solid var(--border)',
          display: 'grid',
          gap: 4,
          fontSize: 11,
          color: 'var(--fg-subtle)',
          lineHeight: 1.6,
        }}
      >
        <div>阶段 P1 · 骨架与设计系统</div>
        <div>契约 docs/frontend-contract.md</div>
      </div>
    </nav>
  )
}
