import { useMemo } from 'react'
import type { CollaborationRunDetail, CollaborationStage, CollaborationSubtask } from '@/types'
import { toneOf } from './tone'

/**
 * 协作拓扑 DAG 视图（纯展示 + 布局计算，不含数据获取）。
 *
 * 布局是固定网格：列 = 角色流水线，行 = 子任务。
 * 之所以不用通用图库：这里的图形结构是确定的（计划 → 记忆 → 执行 → 证据 → 核验 → 整体核验），
 * 固定网格能让节点对齐、可读性最好，也不增加依赖。
 */

const NODE_W = 152
const NODE_H = 56
const GAP_X = 44
const GAP_Y = 18
const PAD = 14
const COLUMNS = ['计划', '记忆选择', '执行', '证据交接', '子任务核验', '整体核验']

interface DagNode {
  id: string
  label: string
  sub: string
  status: string | null
  stageKey: string | null
  col: number
  row: number
}

function buildDag(detail: CollaborationRunDetail): { nodes: DagNode[]; edges: [string, string][] } {
  const subtasks: CollaborationSubtask[] = detail.plan.subtasks ?? []
  const rows = Math.max(subtasks.length, 1)
  const middle = (rows - 1) / 2
  const nodes: DagNode[] = []
  const edges: [string, string][] = []

  const stageOf = (stageKey: string | null): CollaborationStage | null =>
    stageKey === null ? null : detail.roles.find((stage) => stage.stage_key === stageKey) ?? null

  const memoryStageKey = (key: string): string | null => {
    const found = detail.roles.find(
      (stage) => stage.stage_key.startsWith('memory:') && stage.stage_key.endsWith(`:${key}`),
    )
    return found?.stage_key ?? null
  }

  const planStage = stageOf('plan')
  nodes.push({
    id: 'plan',
    label: 'Planner',
    sub: '拆解子任务',
    status: planStage?.status ?? null,
    stageKey: 'plan',
    col: 0,
    row: middle,
  })

  const overallStage = stageOf('verify:overall')
  nodes.push({
    id: 'overall',
    label: 'Verifier',
    sub: '整体核验',
    status: overallStage?.status ?? null,
    stageKey: 'verify:overall',
    col: 5,
    row: middle,
  })

  subtasks.forEach((subtask, row) => {
    const key = subtask.key
    const memoryKey = memoryStageKey(key)
    const chain: Array<{ id: string; label: string; sub: string; stage: string | null; col: number }> = [
      { id: `mem-${key}`, label: 'Memory', sub: '选证据', stage: memoryKey, col: 1 },
      { id: `exec-${key}`, label: 'Executor', sub: key, stage: `execute:${key}`, col: 2 },
      { id: `evid-${key}`, label: 'Evidence', sub: '交接回执', stage: `support:${key}`, col: 3 },
      { id: `verify-${key}`, label: 'Verifier', sub: '子任务核验', stage: `verify:${key}`, col: 4 },
    ]
    let previous = 'plan'
    for (const item of chain) {
      const stageValue = stageOf(item.stage)
      const nodeStatus =
        stageValue?.status ??
        detail.nodes.find((node) => node.node_key === key)?.status ??
        null
      nodes.push({
        id: item.id,
        label: item.label,
        sub: item.sub,
        status: nodeStatus,
        stageKey: item.stage,
        col: item.col,
        row,
      })
      edges.push([previous, item.id])
      previous = item.id
    }
    edges.push([previous, 'overall'])
  })

  // 子任务依赖：从依赖方的核验节点指向被依赖方的记忆选择节点（样式上画成虚线）
  for (const subtask of subtasks) {
    for (const dependency of subtask.dependencies ?? []) {
      if (subtasks.some((item) => item.key === dependency)) {
        edges.push([`verify-${dependency}`, `mem-${subtask.key}`])
      }
    }
  }

  return { nodes, edges }
}

function nodeCenter(node: DagNode): { x: number; y: number } {
  return {
    x: PAD + node.col * (NODE_W + GAP_X) + NODE_W / 2,
    y: PAD + node.row * (NODE_H + GAP_Y) + NODE_H / 2,
  }
}

export function DagView({
  detail,
  selectedStage,
  onSelectStage,
}: {
  detail: CollaborationRunDetail
  selectedStage: string | null
  onSelectStage(stageKey: string): void
}) {
  const { nodes, edges } = useMemo(() => buildDag(detail), [detail])
  const rows = Math.max(detail.plan.subtasks.length, 1)
  const width = PAD * 2 + COLUMNS.length * NODE_W + (COLUMNS.length - 1) * GAP_X
  const height = PAD * 2 + rows * NODE_H + Math.max(0, rows - 1) * GAP_Y
  const byId = new Map(nodes.map((node) => [node.id, node]))

  return (
    <div className="dag" style={{ width, height }}>
      <div className="dag__colheads">
        {COLUMNS.map((label, index) => (
          <span
            key={label}
            className="dag__colhead"
            style={{ left: PAD + index * (NODE_W + GAP_X), width: NODE_W }}
          >
            {label}
          </span>
        ))}
      </div>

      <svg width={width} height={height} className="dag__edges" aria-hidden="true">
        {edges.map(([fromId, toId], index) => {
          const from = byId.get(fromId)
          const to = byId.get(toId)
          if (from === undefined || to === undefined) return null
          const a = nodeCenter(from)
          const b = nodeCenter(to)
          const isDependency = from.col > to.col
          if (isDependency) {
            const midY = (a.y + b.y) / 2 + (b.y >= a.y ? 22 : -22)
            return (
              <path
                key={`${fromId}-${toId}-${index}`}
                d={`M${a.x},${a.y} C${a.x - 40},${midY} ${b.x - 40},${midY} ${b.x},${b.y}`}
                className="dag__edge dag__edge--dependency"
              />
            )
          }
          const startX = a.x + NODE_W / 2 - 4
          const endX = b.x - NODE_W / 2 + 4
          return (
            <path
              key={`${fromId}-${toId}-${index}`}
              d={`M${startX},${a.y} C${(startX + endX) / 2},${a.y} ${(startX + endX) / 2},${b.y} ${endX},${b.y}`}
              className="dag__edge"
            />
          )
        })}
      </svg>

      {nodes.map((node) => {
        const left = PAD + node.col * (NODE_W + GAP_X)
        const top = PAD + node.row * (NODE_H + GAP_Y)
        const active = node.stageKey !== null && node.stageKey === selectedStage
        const tone = toneOf(node.status)
        return (
          <button
            key={node.id}
            type="button"
            className="dag__node"
            data-tone={tone}
            data-active={active ? '1' : undefined}
            style={{ left, top, width: NODE_W, height: NODE_H }}
            onClick={() => {
              if (node.stageKey !== null) onSelectStage(node.stageKey)
            }}
            title={node.stageKey ?? node.label}
          >
            <span className="dag__node-label">{node.label}</span>
            <span className="dag__node-sub">{node.sub}</span>
            <span className="dag__node-status" data-tone={tone}>
              {node.status ?? '未执行'}
            </span>
          </button>
        )
      })}
    </div>
  )
}
