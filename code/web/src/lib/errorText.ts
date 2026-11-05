/**
 * 错误文案的中文解释层。
 *
 * 背景：后端/桥接返回的错误既可能是结构化代码（`HTTP_ERROR`、`COLLABORATION_VERIFICATION_BLOCKED`），
 * 也可能是英文一句话（`verifier invented evidence`）。用户看不懂，但工程上又**不能吞掉原文**
 * （排查需要它）。所以这里的策略是：**原文保留 + 后面补一句中文解释**。
 *
 * 注意边界：这张表只做"显示层翻译"，不参与任何语义判断或重试策略。
 */

const ERROR_CODE_ZH: Record<string, string> = {
  BRIDGE_NOT_RUNNING: '连不上后端桥接进程（检查它是否启动、端口是否正确）',
  HTTP_ERROR: '后端返回了错误状态码（请求被拒绝或失败）',
  BAD_RESPONSE: '后端返回的数据格式不符合约定',
  NO_FIXTURE_RECORDED: '离线回放里没有这个接口的录制数据',
  OFFLINE_FIXTURE_READ_ONLY: '离线回放模式是只读的，不能执行写操作',
  NOT_SUPPORTED: '当前数据源不支持这个操作',
  AUTH_REQUIRED: '需要填写桥接访问口令（桥接启动时打印的那串随机码）',
  COLLABORATION_VERIFICATION_BLOCKED: '协作核验未通过：子任务缺少可核验证据，本轮未被标记为完成',
  EVIDENCE_ERROR: '证据不足：工具回执或来源文件无法支撑结论',
  VERIFICATION: '核验阶段失败',
  COORDINATION_CONFLICT: '协作执行冲突（审批/会话状态不一致）',
  RATE_LIMIT: '请求过于频繁，被限流',
  QUOTA_EXCEEDED: '额度已用尽（检查 API 余额或每轮预算）',
  TIMEOUT: '请求超时（后端处理时间过长或卡住）',
  NETWORK: '网络异常，无法连接后端',
  INTERNAL_ERROR: '后端内部错误',
}

/** 已知英文消息的精确翻译（都是我们在真实联调里见过的宿主/桥接文案） */
const MESSAGE_ZH: Record<string, string> = {
  '缺少或无效的桥接令牌（X-MAPAS-Token）':
    '缺少或无效的访问口令（本机桥接的校验码，不是模型 token）',
  'This turn has no Task 3 binding.':
    '本轮没有绑定任务（普通对话的正常状态，不需要处理；桥接已默认过滤这条）',
  'No bounded context and unintelligible user content; no proposals emitted.':
    '输入内容没有被识别为有效请求，因此没有产生任务提案（通常意味着消息是空的或乱码）',
  'verifier invented evidence': '核验器引用了不存在的证据编号（属实现缺陷）',
  'thread title must contain 1 to 256 characters': '标题长度需在 1–256 个字符之间',
  'thread not found': '会话不存在或已被删除',
  'unknown run for this thread': '该协作运行不属于当前会话',
  'resolve pending approvals or unfinished turns before deleting this thread':
    '删除前需先处理待审批或未完成的轮次',
  'thread changed before deletion': '会话刚刚有更新，请刷新后重试删除',
  'passing tool result has no successful supporting receipt':
    '核验通过但找不到对应的成功工具回执（属实现缺陷）',
  'tool receipt observations changed or are unavailable': '工具回执在核验时发生变化或已不可用',
  '当前子任务缺少可核验的记忆证据，请补充或确认：': '需要补充或确认记忆证据，子任务才能继续',
}

/** 前缀匹配（带动态内容的英文消息） */
const MESSAGE_PREFIX_ZH: Array<[string, string]> = [
  ['conversation session not found', '会话不存在或已被删除'],
  ['thread not found', '会话不存在或已被删除'],
  ['conversation session', '会话数据异常'],
  ['model call failed', '模型调用失败（检查 API key / 余额 / 网络）'],
  ['provider', '模型服务返回异常（检查 API key / 余额 / 网络）'],
  ['invalid arguments', '工具参数不合法'],
  ['timeout', '请求超时'],
]

const CJK = /[\u4e00-\u9fff]/

function isMostlyAscii(text: string): boolean {
  if (CJK.test(text)) return false
  const ascii = text.replace(/[^\x20-\x7e]/g, '').length
  return ascii / Math.max(text.length, 1) > 0.7
}

/**
 * 给一段错误文案补中文解释。
 * - 已经是中文 → 原样返回（不重复解释）
 * - 命中代码/消息/前缀字典 → `原文 —— 中文解释`
 * - 未知英文 → `原文 —— 后端返回的英文错误，可复制给后端排查`
 */
export function withErrorTranslation(text: string): string {
  const value = (text ?? '').trim()
  if (value === '') return value
  if (CJK.test(value) && !isMostlyAscii(value)) {
    // 中文里夹了错误代码时，仍然把代码翻译出来
    const code = value.match(/\b[A-Z][A-Z0-9_]{3,}\b/)?.[0]
    if (code !== undefined && ERROR_CODE_ZH[code] !== undefined) {
      return `${value} —— ${ERROR_CODE_ZH[code]}`
    }
    return value
  }

  if (MESSAGE_ZH[value] !== undefined) return `${value} —— ${MESSAGE_ZH[value]}`

  const upper = value.match(/\b[A-Z][A-Z0-9_]{3,}\b/)?.[0]
  if (upper !== undefined && ERROR_CODE_ZH[upper] !== undefined) {
    return `${value} —— ${ERROR_CODE_ZH[upper]}`
  }

  for (const [prefix, zh] of MESSAGE_PREFIX_ZH) {
    if (value.toLowerCase().startsWith(prefix)) return `${value} —— ${zh}`
  }

  return `${value} —— 后端返回的英文错误，可复制这段给后端排查`
}
