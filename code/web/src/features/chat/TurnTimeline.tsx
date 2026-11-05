import { useState } from 'react'
import type { Message } from '@/types'
import { Badge, StatusDot } from '@/components/ui/primitives'
import type { Tone } from '@/components/ui/primitives'
import { GeneratingIndicator } from '@/components/FlameLoader'
import { withErrorTranslation } from '@/lib/errorText'
import { formatDuration, formatTime } from './eventModel'
import type { ProcessItem, TurnBlock } from './eventModel'

/**
 * 对话时间线（P2，按工作型 Agent 的读法重做）。
 *
 * 设计原则：
 * - **工作中**：过程以小字日志实时滚动（一行一项，等宽字体，低对比度），不打扰阅读；
 * - **完成后**：过程自动折叠成一行摘要（`过程 12 项 · 24.8s`），只留结果与产出；
 * - **可回溯**：任何一轮都能点开/收起看完整过程；
 * - **不能藏的东西不藏**：待审批动作、错误、警告永远在折叠外。
 */
export function TurnTimeline({
  timeline,
  busy,
  pendingMessage,
  onDecide,
}: {
  timeline: TurnBlock[]
  busy: boolean
  /** 刚发出、后端尚未回显的用户消息：立即显示在末尾 */
  pendingMessage?: Message | null
  onDecide(pendingActionId: string, decision: 'CONFIRM' | 'REJECT'): void
}) {
  if (timeline.length === 0) {
    return (
      <div style={{ padding: 24, color: 'var(--fg-subtle)', fontSize: 13, lineHeight: 1.8 }}>
        这个会话还没有内容。发一条消息，就能看到这一轮里 Agent 的阶段、工具调用和最终回答。
      </div>
    )
  }

  return (
    <div style={{ display: 'grid', gap: 16, padding: '16px 20px 28px' }}>
      {numberTimeline(timeline).map(({ block, exchange }) => (
        <TurnBlockView
          key={block.turnId}
          block={block}
          index={exchange}
          busy={busy}
          suppressGenerating={pendingMessage !== undefined && pendingMessage !== null}
          onDecide={onDecide}
        />
      ))}

      {/* 刚发出的用户消息 + 回答将要出现的位置（左对齐，松鼠翻筐子） */}
      {pendingMessage !== undefined && pendingMessage !== null && (
        <section style={{ display: 'grid', gap: 8 }}>
          <MessageBubble message={pendingMessage} />
          {busy && <GeneratingIndicator size={17} text="token 燃烧中" />}
        </section>
      )}
    </div>
  )
}

function TurnBlockView({
  block,
  index,
  busy,
  suppressGenerating = false,
  onDecide,
}: {
  block: TurnBlock
  index: number
  busy: boolean
  /** 末尾已有"待回显消息 + 生成中"时不重复显示松鼠 */
  suppressGenerating?: boolean
  onDecide(pendingActionId: string, decision: 'CONFIRM' | 'REJECT'): void
}) {
  // 待审批项必须始终可见，不随过程折叠
  const pendingApprovals = block.items.filter(
    (item): item is Extract<ProcessItem, { kind: 'approval' }> =>
      item.kind === 'approval' && item.status === 'requested',
  )
  const logItems = block.items.filter(
    (item) => !(item.kind === 'approval' && item.status === 'requested'),
  )

  const [userExpanded, setUserExpanded] = useState<boolean | null>(null)
  const expanded = userExpanded ?? block.status === 'RUNNING'

  const failCount = logItems.filter(
    (item) =>
      (item.kind === 'tool' && item.status === 'failed') ||
      (item.kind === 'stage' && item.status === 'FAILED'),
  ).length
  const warnCount =
    logItems.filter((item) => item.kind === 'stage' && item.status === 'WARNING').length +
    block.warnings.length

  const statusTone: Tone =
    block.status === 'SUCCESS' ? 'verified' : block.status === 'FAILED' ? 'failed' : 'running'

  return (
    <section style={{ display: 'grid', gap: 8 }}>
      {/* ---- 轮次头：一行读完 ---- */}
      <header style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span className="mono" style={{ color: 'var(--fg-subtle)', fontSize: 11 }}>
          {index > 0 ? `#${index}` : '会话开始'}
        </span>
        <Badge tone={statusTone} filled>
          {block.status}
          {block.statusDetail !== undefined && block.statusDetail !== block.status
            ? ` · ${block.statusDetail}`
            : ''}
        </Badge>
        {block.collaborationRunId !== undefined && (
          <Badge
            tone="ambiguous"
            plain
            title={`协作运行 ${block.collaborationRunId}：本轮由 Planner/Memory/Executor/Verifier 协作产生`}
          >
            协作
          </Badge>
        )}
        {block.durationSeconds !== undefined && (
          <span className="mono" style={{ color: 'var(--fg-subtle)', fontSize: 11 }}>
            {formatDuration(block.durationSeconds)}
          </span>
        )}
        {block.stepCount !== undefined && (
          <span className="mono" style={{ color: 'var(--fg-subtle)', fontSize: 11 }}>
            {block.stepCount} 步
          </span>
        )}
        <span className="mono" style={{ color: 'var(--fg-subtle)', fontSize: 11, marginLeft: 'auto' }}>
          {formatTime(block.startedAt)}
        </span>
      </header>

      {/* ---- 用户提问 ---- */}
      {block.userMessages.map((message) => (
        <MessageBubble key={message.messageId} message={message} />
      ))}

      {/* ---- 待审批（永不折叠）---- */}
      {pendingApprovals.length > 0 && (
        <div style={{ display: 'grid', gap: 6 }}>
          {pendingApprovals.map((item) => (
            <ApprovalCard key={item.id} item={item} busy={busy} onDecide={onDecide} />
          ))}
        </div>
      )}

      {/* ---- 过程：折叠条 / 展开列表 ---- */}
      {logItems.length > 0 && (
        <ProcessBlock
          items={logItems}
          expanded={expanded}
          onToggle={() => setUserExpanded(!expanded)}
          failCount={failCount}
          warnCount={warnCount}
        />
      )}

      {/* ---- 生成中：松鼠翻筐子（占据回答将要出现的位置，左对齐）---- */}
      {!suppressGenerating &&
        block.status === 'RUNNING' &&
        block.assistantMessages.length === 0 && (
          <GeneratingIndicator size={17} text="token 燃烧中" />
        )}

      {/* ---- 结果 ---- */}
      {block.assistantMessages.map((message) => (
        <MessageBubble key={message.messageId} message={message} emphasized />
      ))}

      {/* ---- 产出文件（从真实工具参数提取）---- */}
      {block.outputs.length > 0 && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', paddingLeft: 2 }}>
          <span style={{ fontSize: 11, color: 'var(--fg-subtle)' }}>产出</span>
          {block.outputs.map((output) => (
            <span
              key={output.path}
              className="chip"
              title={`由 ${output.tool} 写入`}
              style={{ height: 22, fontSize: 11, fontFamily: 'var(--font-mono)' }}
            >
              {output.path}
            </span>
          ))}
        </div>
      )}

      {/* ---- 错误与警告（永不折叠）---- */}
      {block.status === 'FAILED' && (
        <div
          style={{
            fontSize: 12,
            color: 'var(--sem-failed)',
            borderLeft: '2px solid var(--sem-failed)',
            paddingLeft: 10,
            lineHeight: 1.7,
          }}
        >
          本轮失败{block.errorType !== undefined ? `：${withErrorTranslation(block.errorType)}` : ''}
        </div>
      )}

      {block.warnings.length > 0 && (
        <ul style={{ margin: 0, paddingLeft: 18, fontSize: 11.5, color: 'var(--sem-warning, var(--sem-unknown))', lineHeight: 1.7 }}>
          {block.warnings.map((warning) => (
            <li key={warning}>{withErrorTranslation(warning)}</li>
          ))}
        </ul>
      )}
    </section>
  )
}

/* ============================ 过程块 ============================ */

function ProcessBlock({
  items,
  expanded,
  onToggle,
  failCount,
  warnCount,
}: {
  items: ProcessItem[]
  expanded: boolean
  onToggle(): void
  failCount: number
  warnCount: number
}) {
  const toolCount = items.filter((item) => item.kind === 'tool').length
  const stageCount = items.filter((item) => item.kind === 'stage').length
  const totalSeconds = items.reduce((sum, item) => {
    if (item.kind === 'tool' || item.kind === 'stage') return sum + (item.durationSeconds ?? 0)
    return sum
  }, 0)

  return (
    <div style={{ display: 'grid', gap: 4 }}>
      <button type="button" className="processbar" aria-expanded={expanded} onClick={onToggle}>
        <span className="processbar__chev" aria-hidden="true">
          ▸
        </span>
        <span>过程 {items.length} 项</span>
        <span style={{ opacity: 0.6 }}>
          {[toolCount > 0 ? `${toolCount} 工具` : null, stageCount > 0 ? `${stageCount} 阶段` : null]
            .filter(Boolean)
            .join(' · ')}
        </span>
        {totalSeconds > 0 && <span style={{ opacity: 0.6 }}>· {formatDuration(totalSeconds)}</span>}
        <span className="processbar__spacer" />
        {failCount > 0 && (
          <span style={{ color: 'var(--sem-failed)' }}>{failCount} 失败</span>
        )}
        {warnCount > 0 && (
          <span style={{ color: 'var(--sem-warning, var(--sem-unknown))' }}>{warnCount} 警告</span>
        )}
        <span style={{ opacity: 0.7 }}>{expanded ? '收起' : '展开'}</span>
      </button>

      {expanded && (
        <div style={{ display: 'grid', gap: 0, paddingLeft: 6, paddingTop: 2 }}>
          {items.map((item) => (
            <LogLine key={item.id} item={item} />
          ))}
        </div>
      )}
    </div>
  )
}

/** 一行一条的过程日志：等宽、小字、低对比 */
function LogLine({ item }: { item: ProcessItem }) {
  if (item.kind === 'tool') {
    const failed = item.status === 'failed'
    return (
      <div className={failed ? 'logline logline--fail' : 'logline'}>
        <span className="logline__glyph">{failed ? '✕' : '▸'}</span>
        <span className="logline__name">{item.toolName}</span>
        <span className="logline__meta">
          {item.status}
          {item.durationSeconds !== undefined ? ` · ${formatDuration(item.durationSeconds)}` : ''}
          {item.resultSize !== undefined ? ` · ${item.resultSize} B` : ''}
          {item.arguments !== undefined ? ` · ${compactArgs(item.arguments)}` : ''}
        </span>
      </div>
    )
  }

  if (item.kind === 'stage') {
    return (
      <div className={item.status === 'WARNING' ? 'logline logline--warn logline--dim' : 'logline logline--dim'}>
        <span className="logline__glyph">·</span>
        <span className="logline__name">{item.stage}</span>
        <span className="logline__meta">
          {item.status}
          {item.durationSeconds !== undefined ? ` · ${formatDuration(item.durationSeconds)}` : ''}
          {item.warnings.length > 0 ? ` · ${item.warnings.join('；')}` : ''}
        </span>
      </div>
    )
  }

  if (item.kind === 'approval') {
    const rejected = item.decision === 'REJECT'
    return (
      <div className={rejected ? 'logline logline--fail' : 'logline'}>
        <span className="logline__glyph">{rejected ? '✕' : '✓'}</span>
        <span className="logline__name">审批 {item.toolName}</span>
        <span className="logline__meta">
          {item.decision ?? item.status}
          {item.executed === true ? ' · 已执行' : ''}
        </span>
      </div>
    )
  }

  if (item.kind === 'compaction') {
    return (
      <div className="logline logline--dim">
        <span className="logline__glyph">⇣</span>
        <span className="logline__name">上下文压缩</span>
        <span className="logline__meta">
          {item.status} · {item.trigger}
          {item.degraded === true ? ' · 降级' : ''}
        </span>
      </div>
    )
  }

  return (
    <div className="logline logline--dim">
      <span className="logline__glyph">?</span>
      <span className="logline__name">{item.eventType}</span>
      <span className="logline__meta">未识别事件</span>
    </div>
  )
}

/** 参数压成一行短摘要，避免占版面 */
function compactArgs(argumentsValue: unknown): string {
  let text: string
  try {
    text = JSON.stringify(argumentsValue) ?? ''
  } catch {
    text = String(argumentsValue)
  }
  if (text === '{}') return ''
  return text.length > 72 ? text.slice(0, 72) + '…' : text
}

/* ============================ 审批卡 ============================ */

function ApprovalCard({
  item,
  busy,
  onDecide,
}: {
  item: Extract<ProcessItem, { kind: 'approval' }>
  busy: boolean
  onDecide(pendingActionId: string, decision: 'CONFIRM' | 'REJECT'): void
}) {
  return (
    <div
      style={{
        border: '1px solid color-mix(in oklab, var(--sem-waiting) 45%, transparent)',
        background: 'color-mix(in oklab, var(--sem-waiting) 8%, var(--bg-elev))',
        borderRadius: 'var(--radius)',
        padding: '10px 12px',
        display: 'grid',
        gap: 6,
      }}
    >
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <StatusDot tone="waiting" />
        <span style={{ fontSize: 13 }}>需要确认 · {item.toolName}</span>
        <Badge tone="waiting" plain>
          SIDE_EFFECT
        </Badge>
      </div>
      <p style={{ margin: 0, fontSize: 12, color: 'var(--fg-muted)', lineHeight: 1.6 }}>
        这是有副作用的操作，按系统安全契约必须由你确认后才会执行。
      </p>
      {item.arguments !== undefined && (
        <pre
          className="mono"
          style={{
            margin: 0,
            padding: '6px 8px',
            background: 'var(--bg-sunken)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius-sm)',
            color: 'var(--fg-muted)',
            fontSize: 11,
            lineHeight: 1.6,
            maxHeight: 160,
            overflow: 'auto',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-all',
          }}
        >
          {JSON.stringify(item.arguments, null, 2)}
        </pre>
      )}
      <div style={{ display: 'flex', gap: 8 }}>
        <button
          type="button"
          className="chip chip--button"
          disabled={busy}
          onClick={() => onDecide(item.pendingActionId, 'CONFIRM')}
          style={{ color: 'var(--sem-verified)' }}
        >
          确认执行
        </button>
        <button
          type="button"
          className="chip chip--button"
          disabled={busy}
          onClick={() => onDecide(item.pendingActionId, 'REJECT')}
          style={{ color: 'var(--sem-failed)' }}
        >
          拒绝
        </button>
      </div>
    </div>
  )
}

/* ============================ 消息气泡 ============================ */

function MessageBubble({ message, emphasized = false }: { message: Message; emphasized?: boolean }) {
  const isUser = message.role === 'USER'
  return (
    <div style={{ display: 'flex', justifyContent: isUser ? 'flex-end' : 'flex-start' }}>
      <div
        style={{
          maxWidth: '86%',
          padding: emphasized ? '12px 16px' : '9px 13px',
          borderRadius: 'var(--radius)',
          border: '1px solid ' + (isUser ? 'color-mix(in oklab, var(--accent) 40%, transparent)' : 'var(--border)'),
          background: isUser ? 'color-mix(in oklab, var(--accent) 12%, transparent)' : 'var(--bg-elev)',
          fontSize: 13,
          lineHeight: 1.8,
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}
      >
        {!isUser && (
          <div
            style={{
              fontSize: 10,
              letterSpacing: '0.08em',
              textTransform: 'uppercase',
              color: 'var(--fg-subtle)',
              marginBottom: 4,
            }}
          >
            {message.role}
          </div>
        )}
        {message.content}
      </div>
    </div>
  )
}

/**
 * 轮次编号按「用户第几次发言」计算，符合"第几句来回就是第几轮"的直觉：
 * - 含用户消息的块 = 新一轮（#1、#2…）；
 * - 不含用户消息的块（续跑 / 确认触发的补充轮）沿用上一个编号，不占新号。
 */
function numberTimeline(timeline: TurnBlock[]): { block: TurnBlock; exchange: number }[] {
  let exchange = 0
  return timeline.map((block) => {
    if (block.userMessages.length > 0) exchange += 1
    return { block, exchange }
  })
}
