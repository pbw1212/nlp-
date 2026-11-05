import { PAGE_BY_ID } from '@/app/pages'
import { bridgeBaseUrl, bridgeToken } from '@/datasource'
import { useAppState } from '@/state/AppState'
import { Badge, KeyValue, SectionLabel, StatusDot } from '@/components/ui/primitives'
import { withErrorTranslation } from '@/lib/errorText'

/**
 * 右侧检查器：随当前页面切换，显示"这一屏的依据"。
 *
 * P1 阶段它承担两件真实职责：
 * 1. 数据来源与连接状态（连不上时把真实原因显示出来，而不是假装有数据）；
 * 2. 语义图例 —— 把系统里的状态枚举和颜色对应关系固定下来，
 *    保证后面每一屏用的是同一套视觉语言（答辩时不会出现"同一个 UNKNOWN 两种颜色"）。
 */
export function Inspector() {
  const { view, sourceKind, bridge, source } = useAppState()
  const page = PAGE_BY_ID[view]
  const token = bridgeToken()

  return (
    <aside
      aria-label="检查器"
      className="scroll-thin app-inspector"
      style={{
        borderLeft: '1px solid var(--border)',
        background: 'var(--bg)',
        padding: 14,
        display: 'grid',
        gap: 18,
        alignContent: 'start',
        overflowY: 'auto',
      }}
    >
      <section style={{ display: 'grid', gap: 8 }}>
        <SectionLabel>当前界面</SectionLabel>
        <div style={{ fontSize: 13, fontWeight: 600 }}>{page.title}</div>
        <div style={{ fontSize: 12, color: 'var(--fg-muted)', lineHeight: 1.65 }}>
          {page.subtitle}
        </div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          <Badge
            tone={
              page.readiness === 'ready'
                ? 'verified'
                : page.readiness === 'blocked-v1'
                  ? 'waiting'
                  : 'pending'
            }
          >
            {page.readiness === 'ready'
              ? '已实现'
              : page.readiness === 'blocked-v1'
                ? '待验证 V1'
                : '等桥接'}
          </Badge>
          <Badge tone={source.writable ? 'verified' : 'stale'} plain>
            {source.writable ? '可写' : '只读'}
          </Badge>
        </div>
      </section>

      <hr className="divider" />

      <section style={{ display: 'grid', gap: 8 }}>
        <SectionLabel>数据来源</SectionLabel>
        <KeyValue
          rows={[
            { k: '数据源', v: sourceKind === 'http' ? 'HttpSource（真实）' : 'FixtureSource（回放）' },
            {
              k: '桥接地址',
              v: <span className="mono">{bridgeBaseUrl()}</span>,
            },
            {
              k: '本地 token',
              v: token === null ? <span className="subtle">未设置</span> : <span className="mono">已注入</span>,
            },
            {
              k: '连接状态',
              v:
                bridge.state === 'checking' ? (
                  <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
                    <StatusDot tone="running" />
                    探测中
                  </span>
                ) : bridge.state === 'ok' ? (
                  <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
                    <StatusDot tone="verified" />
                    已连通
                  </span>
                ) : (
                  <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
                    <StatusDot tone="mismatch" />
                    {bridge.code}
                  </span>
                ),
            },
            ...(bridge.state === 'ok'
              ? [{ k: 'Agent 主目录', v: <span className="mono">{bridge.agentHome}</span> }]
              : []),
          ]}
        />
        {bridge.state === 'error' && (
          <p style={{ fontSize: 12, color: 'var(--sem-mismatch)', lineHeight: 1.6, margin: 0 }}>
            {withErrorTranslation(bridge.message)}
          </p>
        )}
      </section>

      <hr className="divider" />

      <section style={{ display: 'grid', gap: 8 }}>
        <SectionLabel>本屏后端依赖</SectionLabel>
        <div style={{ display: 'grid', gap: 6 }}>
          {page.upstream.map((item) => (
            <div
              key={item}
              className="mono"
              style={{ color: 'var(--fg-muted)', display: 'flex', gap: 6, alignItems: 'baseline' }}
            >
              <span style={{ color: 'var(--accent)' }}>·</span>
              <span style={{ wordBreak: 'break-word' }}>{item}</span>
            </div>
          ))}
        </div>
        <p style={{ fontSize: 11, color: 'var(--fg-subtle)', margin: 0, lineHeight: 1.6 }}>
          这些是 Python 侧真实存在的方法/数据，不是前端自造的字段。完整映射见
          <span className="mono"> docs/frontend-contract.md §2</span>。
        </p>
      </section>

      <hr className="divider" />

      <section style={{ display: 'grid', gap: 10 }}>
        <SectionLabel>语义图例 · 适用性四态</SectionLabel>
        <LegendRow tone="match" label="MATCH 明确适用" />
        <LegendRow tone="mismatch" label="MISMATCH 明确不适用" />
        <LegendRow tone="unknown" label="UNKNOWN 信息不足" />
        <LegendRow tone="ambiguous" label="AMBIGUOUS 存在歧义" />
        <p style={{ fontSize: 11, color: 'var(--fg-subtle)', margin: 0, lineHeight: 1.6 }}>
          注意：UNKNOWN / AMBIGUOUS 与 MISMATCH 是三种不同结论，界面上禁止合并。
        </p>
      </section>

      <hr className="divider" />

      <section style={{ display: 'grid', gap: 10 }}>
        <SectionLabel>语义图例 · 协作节点 7 态</SectionLabel>
        <LegendRow tone="pending" label="PENDING 待调度" />
        <LegendRow tone="running" label="RUNNING 执行中" />
        <LegendRow tone="produced" label="PRODUCED 有产物未核验" />
        <LegendRow tone="verified" label="VERIFIED 已核验" />
        <LegendRow tone="failed" label="FAILED 失败" />
        <LegendRow tone="blocked" label="BLOCKED 受阻" />
        <LegendRow tone="stale" label="STALE 已失效" />
      </section>

      <hr className="divider" />

      <section style={{ display: 'grid', gap: 10 }}>
        <SectionLabel>语义图例 · 结果有效性</SectionLabel>
        <LegendRow tone="valid" label="VALID 当前有效" />
        <LegendRow tone="stale" label="STALE 已失效" />
        <LegendRow tone="notchecked" label="NOT_CHECKED_WORKSPACE 未核验" />
      </section>
    </aside>
  )
}

function LegendRow({ tone, label }: { tone: Parameters<typeof StatusDot>[0]['tone']; label: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
      <StatusDot tone={tone} />
      <span style={{ color: 'var(--fg-muted)' }}>{label}</span>
    </div>
  )
}
