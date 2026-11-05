import { useEffect, useMemo, useState } from 'react'

/**
 * 北京时间（UTC+8）时钟，精确到秒。
 *
 * 放在页面左下角（侧栏底部），模拟"系统状态栏"的位置感。
 * 用 `Intl.DateTimeFormat` 固定 `Asia/Shanghai`，不依赖用户机器所在时区，
 * 因此即使浏览器在别的时区，显示的也始终是北京时间。
 */

const DATE_FORMAT = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
})

const TIME_FORMAT = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
})

export function Clock() {
  const [now, setNow] = useState(() => new Date())

  useEffect(() => {
    // 对齐到整秒后再开始跳动，避免秒数"跳一格慢半拍"
    let timer = 0
    const tick = () => setNow(new Date())
    const delay = 1000 - (Date.now() % 1000)
    const start = window.setTimeout(() => {
      tick()
      timer = window.setInterval(tick, 1000)
    }, delay)
    return () => {
      window.clearTimeout(start)
      window.clearInterval(timer)
    }
  }, [])

  const date = useMemo(() => DATE_FORMAT.format(now).replace(/\//g, '-'), [now])
  const time = useMemo(() => TIME_FORMAT.format(now).replace(/\//g, '-'), [now])

  return (
    <div className="clock" title="北京时间（UTC+8），每秒刷新">
      <div className="clock__time">{time}</div>
      <div className="clock__meta">
        {date}
        <span className="clock__zone">UTC+8</span>
      </div>
    </div>
  )
}
