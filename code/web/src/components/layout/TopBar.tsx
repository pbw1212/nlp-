import type { BridgeProbe } from '@/datasource'
import { useAppState } from '@/state/AppState'
import { withErrorTranslation } from '@/lib/errorText'
import { Brand } from '@/components/Brand'
import { Icon } from '@/components/Icon'
import { TokenButton } from './TokenButton'
import { ModeSwitcher } from './ModeSwitcher'
import { ThemeSwitcher } from './ThemeSwitcher'
import { ApiKeyButton } from '@/components/ApiKeyDialog'

/**
 * 顶栏：始终显示"当前系统的真实状态"。
 *
 * 为什么顶栏要放这些：这几项决定了下面所有面板的数据是不是可信 ——
 * 执行模式（single 没有协作/修复/经验）、桥接是否连通、数据源是真实还是回放、当前主题。
 * 用户在任何一屏都能一眼看到，不用去设置页翻。
 */
export function TopBar() {
  const { sourceKind, changeSource, bridge, refreshBridge } = useAppState()

  return (
    <header
      className="topbar"
      style={{
        height: 'var(--topbar-h)',
        display: 'flex',
        alignItems: 'center',
        gap: 16,
        padding: '0 16px',
        borderBottom: '1px solid var(--border)',
        background: 'var(--bg-elev)',
        flex: 'none',
      }}
    >
      <Brand />

      <div style={{ flex: 1 }} />

      <BridgeChip bridge={bridge} onRetry={() => void refreshBridge()} />

      <TokenButton />

      <button
        type="button"
        className="chip chip--button"
        onClick={() => changeSource(sourceKind === 'http' ? 'fixture' : 'http')}
        title={
          sourceKind === 'http'
            ? '当前：真实桥接。点击切换到录制回放（只读）'
            : '当前：录制回放（只读）。点击切换到真实桥接'
        }
      >
        <Icon name="link" size={13} />
        数据源：{sourceKind === 'http' ? '桥接' : '回放'}
      </button>

      <ThemeSwitcher />

      <ApiKeyButton />
    </header>
  )
}

function BridgeChip({ bridge, onRetry }: { bridge: BridgeProbe; onRetry: () => void }) {
  if (bridge.state === 'checking') {
    return (
      <span className="chip">
        <span className="dot" data-tone="running" />
        正在探测桥接…
      </span>
    )
  }

  if (bridge.state === 'error') {
    return (
      <button
        type="button"
        className="chip chip--button"
        onClick={onRetry}
        title={`${bridge.code}：${withErrorTranslation(bridge.message)}（点击重试）`}
        style={{ color: 'var(--sem-mismatch)', maxWidth: 320 }}
      >
        <span className="dot" data-tone="mismatch" />
        <span
          style={{
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          后端未连接
        </span>
        <Icon name="refresh" size={12} />
      </button>
    )
  }

  return (
    <>
      <ModeSwitcher />
      <span className="chip" title={bridge.agentHome}>
        协议 {bridge.protocolVersion}
      </span>
    </>
  )
}
