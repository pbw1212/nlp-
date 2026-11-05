import type { ProvenanceRef } from '@/types'

/**
 * 记忆库的视图模型 + 适配器。
 *
 * 为什么需要这一层：`PersonalAgentApp` 的记忆接口返回的是**扁平/分组的**字典，
 * 而不是 P0 契约里假设的 `MemoryItem + current_revision` 嵌套结构（实测差异见 README §2.7）。
 * 这里把真实返回形状原样描述出来，再由页面组合展示 —— 不做任何字段臆造。
 *
 * 真实来源（`code/src/memory/app/console.py`）：
 * - `list_memories()`            → { memory_id, type, content, granularity }
 * - `get_memory_detail(id)`      → { memory_id, type, content, granularity, revision_id,
 *                                    retention, confidence, semantic_validity, provenance_count }
 * - `get_memory_history(id)`     → [{ revision_id, content, created_at }]
 * - `get_memory_provenance(id)`  → [{ revision_id, revision_no, change_kind, refs: [...] }]
 */

export interface MemoryListRow {
  memory_id: string
  type: string
  content: string
  granularity: string
}

export interface MemoryDetailRow {
  memory_id: string
  type: string
  content: string
  granularity: string
  revision_id: string
  retention: string
  confidence: number
  semantic_validity: string
  provenance_count: number
}

export interface MemoryHistoryRow {
  revision_id: string
  content: string
  created_at: number
}

/** 某个版本的全部来源引用（后端按版本分组返回） */
export interface MemoryProvenanceGroup {
  revision_id: string
  revision_no: number
  change_kind: string
  refs: ProvenanceRef[]
}

/** 页面用的一条版本（history × provenance 关联后的结果） */
export interface RevisionView {
  revision_id: string
  /** 版本号；只有 provenance 里才有，缺失时为 null（界面显示"版本号未提供"，不编号） */
  revision_no: number | null
  change_kind: string | null
  content: string
  created_at: number | null
  refs: ProvenanceRef[]
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : {}
}

function asString(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

export function toMemoryListRows(value: unknown): MemoryListRow[] {
  if (!Array.isArray(value)) return []
  return value.map((raw) => {
    const row = asRecord(raw)
    return {
      memory_id: asString(row['memory_id']),
      type: asString(row['type'], 'UNKNOWN'),
      content: asString(row['content']),
      granularity: asString(row['granularity'], 'UNKNOWN'),
    }
  })
}

export function toMemoryDetail(value: unknown): MemoryDetailRow | null {
  if (typeof value !== 'object' || value === null) return null
  const row = asRecord(value)
  const memoryId = asString(row['memory_id'])
  if (memoryId === '') return null
  return {
    memory_id: memoryId,
    type: asString(row['type'], 'UNKNOWN'),
    content: asString(row['content']),
    granularity: asString(row['granularity'], 'UNKNOWN'),
    revision_id: asString(row['revision_id']),
    retention: asString(row['retention'], 'UNKNOWN'),
    confidence: asNumber(row['confidence']),
    semantic_validity: asString(row['semantic_validity'], 'UNKNOWN'),
    provenance_count: asNumber(row['provenance_count']),
  }
}

export function toHistoryRows(value: unknown): MemoryHistoryRow[] {
  if (!Array.isArray(value)) return []
  return value.map((raw) => {
    const row = asRecord(raw)
    return {
      revision_id: asString(row['revision_id']),
      content: asString(row['content']),
      created_at: asNumber(row['created_at']),
    }
  })
}

export function toProvenanceGroups(value: unknown): MemoryProvenanceGroup[] {
  if (!Array.isArray(value)) return []
  return value.map((raw) => {
    const row = asRecord(raw)
    const refs = Array.isArray(row['refs']) ? (row['refs'] as unknown[]).map((item) => asRecord(item) as unknown as ProvenanceRef) : []
    return {
      revision_id: asString(row['revision_id']),
      revision_no: asNumber(row['revision_no']),
      change_kind: asString(row['change_kind'], 'UNKNOWN'),
      refs,
    }
  })
}

/**
 * 把「history（内容+时间）」与「provenance（版本号+变更类型+来源）」按 revision_id 合并。
 *
 * 为什么要合并：单看 history 不知道第几版、是新增还是更正；单看 provenance 没有正文与时间。
 * 两边都以 revision_id 为键，属于同一份真实数据，合并只是展示层的 join。
 */
export function buildRevisions(
  history: MemoryHistoryRow[],
  groups: MemoryProvenanceGroup[],
): RevisionView[] {
  const byId = new Map<string, RevisionView>()

  for (const item of history) {
    byId.set(item.revision_id, {
      revision_id: item.revision_id,
      revision_no: null,
      change_kind: null,
      content: item.content,
      created_at: item.created_at,
      refs: [],
    })
  }

  for (const group of groups) {
    const existing = byId.get(group.revision_id)
    if (existing === undefined) {
      byId.set(group.revision_id, {
        revision_id: group.revision_id,
        revision_no: group.revision_no,
        change_kind: group.change_kind,
        content: '',
        created_at: null,
        refs: group.refs,
      })
    } else {
      existing.revision_no = group.revision_no
      existing.change_kind = group.change_kind
      existing.refs = group.refs
    }
  }

  return [...byId.values()].sort((a, b) => {
    if (a.revision_no !== null && b.revision_no !== null) return b.revision_no - a.revision_no
    return (b.created_at ?? 0) - (a.created_at ?? 0)
  })
}
