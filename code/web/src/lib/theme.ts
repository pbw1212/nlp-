/**
 * 主题模型。
 *
 * 四种视觉主题 + 一个"跟随北京时间"的模式：
 * - light  浅色
 * - dark   深色
 * - dawn   晨光（蓝白色调 + 渐变晨雾氛围）
 * - sunset 夕阳（橙黄色调 + 渐变晚霞氛围）
 *
 * 自动模式的时段规则（按北京时间 UTC+8，与左下角时钟同源）：
 *   05:00–08:59 晨光 · 09:00–15:59 浅色 · 16:00–18:59 夕阳 · 19:00–04:59 深色
 * （用户给的是"4点到8点/9点到16点/16点到18点/18点到次日6点"，这里取左闭右开区间覆盖全天）
 */

export type Theme = 'light' | 'dark' | 'dawn' | 'sunset'
export type ThemeMode = Theme | 'auto'

export const THEME_STORAGE_KEY = 'mapas.theme'

export const THEME_MODES: ThemeMode[] = ['light', 'dark', 'dawn', 'sunset', 'auto']

export const THEME_LABEL: Record<ThemeMode, string> = {
  light: '浅色',
  dark: '深色',
  dawn: '晨光',
  sunset: '夕阳',
  auto: '随北京时间变化厉娜',
}

export const THEME_HINT: Record<Theme, string> = {
  light: '清爽的白底，白天最易读',
  dark: '深蓝底色，夜间不刺眼',
  dawn: '蓝白渐变晨雾，冷调通透',
  sunset: '橙黄渐变晚霞，暖调柔和',
}

export function isThemeMode(value: string): value is ThemeMode {
  return (THEME_MODES as string[]).includes(value)
}

const SHANGHAI_HOUR = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'Asia/Shanghai',
  hour: '2-digit',
  hour12: false,
})

/** 取北京时间的小时（0–23），不依赖机器时区 */
export function shanghaiHour(date: Date = new Date()): number {
  return Number(SHANGHAI_HOUR.format(date))
}

/** 按北京时间算出当前应使用的主题 */
export function resolveAutoTheme(date: Date = new Date()): Theme {
  const hour = shanghaiHour(date)
  if (hour >= 5 && hour < 9) return 'dawn'
  if (hour >= 9 && hour < 16) return 'light'
  if (hour >= 16 && hour < 19) return 'sunset'
  return 'dark'
}

export function resolveTheme(mode: ThemeMode, date: Date = new Date()): Theme {
  return mode === 'auto' ? resolveAutoTheme(date) : mode
}

/** 读取用户保存的主题选择；**新用户默认 auto**（跟随北京时间） */
export function readStoredThemeMode(): ThemeMode {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY)
    if (stored !== null && isThemeMode(stored)) return stored
  } catch {
    /* 隐私模式/禁用 localStorage：仍返回默认值 */
  }
  return 'auto'
}

/**
 * 在 React 挂载前先把主题写到 `<html>`。
 * 否则首屏会先渲染 :root 的深色，再被 React 切到目标主题，出现一下闪白/闪黑。
 */
export function applyInitialTheme(): void {
  const mode = readStoredThemeMode()
  const theme = resolveTheme(mode)
  const root = document.documentElement
  root.dataset['theme'] = theme
  root.dataset['themeMode'] = mode
}
