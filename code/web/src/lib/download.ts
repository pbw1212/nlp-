/**
 * 浏览器端文件下载工具。
 *
 * 用 Blob + 临时 <a> 触发下载，不依赖任何库；URL 用完即回收，避免内存泄漏。
 */
export function downloadJson(filename: string, value: unknown): void {
  const blob = new Blob([JSON.stringify(value, null, 2)], {
    type: 'application/json;charset=utf-8',
  })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  // 稍后回收，确保浏览器已经开始下载
  window.setTimeout(() => URL.revokeObjectURL(url), 2000)
}

/** 把会话标题变成安全的文件名（去掉 Windows/Unix 不允许的字符） */
export function sanitizeFileName(name: string, fallback = 'session'): string {
  const cleaned = (name ?? '')
    .replace(/[\\/:*?"<>|]/g, '_')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 80)
  return cleaned === '' ? fallback : cleaned
}
