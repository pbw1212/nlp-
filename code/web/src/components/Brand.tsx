import { useId } from 'react'
import { useAppState } from '@/state/AppState'

/**
 * 产品标识（左上角）。
 *
 * 设计意图：不做"彩色方块 + 首字母"这种模板脸，而是用**一个几何纪念标 + 排版字标**：
 * - 方框：圆角渐变描边 + 极浅渐变底，像一枚实体徽标；
 * - M 折线：主笔 + 一层同形"残影"错位，暗示记忆的**版本层叠**；
 * - 地平线：底部渐隐横线，对应"长程任务"；
 * - 字标：`MAPAS` 宽字距 + 小字副标，层级清楚，像品牌而不是工具配置项；
 * - 悬停：整体轻微上浮、M 描边重绘一次，点击回到对话页。
 */
export function Brand() {
  const { view, setView } = useAppState()
  // useId 里含 ':'，直接拼进 SVG url(#...) 在部分浏览器会失效，先清洗成合法片段
  const gradientId = 'brand-grad-' + useId().replace(/[^a-zA-Z0-9_-]/g, '')
  const fadeId = gradientId + '-fade'

  return (
    <button
      type="button"
      className="brand"
      onClick={() => setView('chat')}
      title="MAPAS · 记忆工作台（点击回到对话）"
      aria-label="MAPAS 记忆工作台，回到对话页"
      style={view === 'chat' ? undefined : { cursor: 'pointer' }}
    >
      <span className="brand__mark" aria-hidden="true">
        <svg width="26" height="26" viewBox="0 0 28 28" fill="none">
          <defs>
            <linearGradient
              id={gradientId}
              x1="4"
              y1="3"
              x2="24"
              y2="25"
              gradientUnits="userSpaceOnUse"
            >
              <stop offset="0" style={{ stopColor: 'var(--accent)' }} />
              <stop offset="1" style={{ stopColor: 'var(--accent-strong)' }} />
            </linearGradient>
            <linearGradient
              id={fadeId}
              x1="6"
              y1="0"
              x2="22"
              y2="0"
              gradientUnits="userSpaceOnUse"
            >
              <stop offset="0" style={{ stopColor: 'var(--accent)', stopOpacity: 0.05 }} />
              <stop offset="0.5" style={{ stopColor: 'var(--accent)', stopOpacity: 0.55 }} />
              <stop offset="1" style={{ stopColor: 'var(--accent)', stopOpacity: 0.05 }} />
            </linearGradient>
          </defs>

          {/* 徽标底：圆角方框 */}
          <rect
            x="1"
            y="1"
            width="26"
            height="26"
            rx="8"
            fill={`url(#${gradientId})`}
            opacity="0.16"
          />
          <rect
            x="1"
            y="1"
            width="26"
            height="26"
            rx="8"
            stroke={`url(#${gradientId})`}
            strokeOpacity="0.6"
            strokeWidth="1"
          />

          {/* M 的残影：错位一层，暗示版本层叠 */}
          <path
            d="M8.2 18.6V9.4l5.8 5.9 5.8-5.9v9.2"
            transform="translate(0.9 0.7)"
            stroke={`url(#${gradientId})`}
            strokeOpacity="0.28"
            strokeWidth="2.2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />

          {/* 主 M 折线 */}
          <path
            className="brand__glyph"
            d="M8.2 18.6V9.4l5.8 5.9 5.8-5.9v9.2"
            stroke={`url(#${gradientId})`}
            strokeWidth="2.3"
            strokeLinecap="round"
            strokeLinejoin="round"
          />

          {/* 地平线：两端渐隐 */}
          <path
            className="brand__horizon"
            d="M6.6 22.2h14.8"
            stroke={`url(#${fadeId})`}
            strokeWidth="1.1"
            strokeLinecap="round"
          />
        </svg>
      </span>

      <span className="brand__text">
        <span className="brand__word">MAPAS</span>
        <span className="brand__tag">
          <i className="brand__tick" aria-hidden="true" />
          记忆工作台
        </span>
      </span>
    </button>
  )
}
