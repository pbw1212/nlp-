import type { ReactNode } from 'react'
import { Inspector } from './Inspector'
import { SideNav } from './SideNav'
import { TopBar } from './TopBar'

/**
 * 应用骨架：顶栏（全局状态）+ 三栏。
 *
 * 为什么是"三栏 + 顶栏"而不是单列聊天流：
 * MAPAS 的信息是"一条主线 + 大量旁证"。中栏保证时序可读（发生了什么），
 * 右栏保证依据可查（为什么这么判断）—— 正好对应系统的两条原则：
 * 当前有效（current-safe）与来源可核验（provenance）。
 *
 * `flush`：对话页需要占满整个中栏并自己管理滚动（输入框固定底部），
 * 所以取消主区默认内边距，而不是用负 margin 去抵。
 */
export function AppShell({ children, flush = false }: { children: ReactNode; flush?: boolean }) {
  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <TopBar />
      <div className="app-grid">
        <SideNav />
        <main className={flush ? 'app-main app-main--flush' : 'app-main scroll-thin'}>{children}</main>
        <Inspector />
      </div>
    </div>
  )
}
