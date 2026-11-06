import { useEffect, useRef, useState } from 'react'
import { Icon } from '@/components/Icon'
import type { IconName } from '@/components/Icon'
import { useAppState } from '@/state/AppState'
import { THEME_HINT, THEME_LABEL, THEME_MODES, resolveAutoTheme } from '@/lib/theme'
import type { ThemeMode } from '@/lib/theme'

const MODE_ICON: Record<ThemeMode, IconName> = {
  light: 'sun',
  dark: 'moon',
  dawn: 'sunrise',
  sunset: 'sunset',
  auto: 'clock',
}

/**
 * 主题切换器（顶栏下拉）。
 *
 * 选项顺序固定：浅色 → 深色 → 晨光 → 夕阳 → 随北京时间变化。
 * 「随北京时间变化」的时段规则写在 lib/theme.ts，与左下角时钟同源（Asia/Shanghai）。
 */
export function ThemeSwitcher() {
  const { themeMode, theme, setTheme } = useAppState()
  const [open, setOpen] = useState(false)
  const boxRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!open) return
    const onDown = (event: MouseEvent) => {
      if (boxRef.current !== null && !boxRef.current.contains(event.target as Node)) setOpen(false)
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    window.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('mousedown', onDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [open])

  const autoNow = themeMode === 'auto' ? theme : resolveAutoTheme()
  const buttonLabel = themeMode === 'auto' ? `自动 · ${THEME_LABEL[autoNow]}` : THEME_LABEL[themeMode]

  return (
    <div style={{ position: 'relative' }} ref={boxRef}>
      <button
        type="button"
        className="chip chip--button"
        onClick={() => setOpen((value) => !value)}
        title={
          themeMode === 'auto'
            ? `主题：随北京时间变化（当前命中「${THEME_LABEL[autoNow]}」）`
            : `主题：${THEME_LABEL[themeMode]}（点开切换）`
        }
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <Icon name={MODE_ICON[themeMode]} size={13} />
        {buttonLabel}
      </button>

      {open && (
        <div className="theme-menu" role="listbox" aria-label="主题">
          {THEME_MODES.map((mode) => {
            const active = mode === themeMode
            return (
              <button
                key={mode}
                type="button"
                role="option"
                aria-selected={active}
                className="theme-menu__item"
                data-active={active ? '1' : undefined}
                onClick={() => {
                  setTheme(mode)
                  setOpen(false)
                }}
                title={mode === 'auto' ? '按北京时间自动切换主题' : THEME_HINT[mode]}
              >
                <Icon name={MODE_ICON[mode]} size={14} />
                <span className="theme-menu__label">
                  {THEME_LABEL[mode]}
                  {mode === 'auto' && (
                    <em className="theme-menu__now">现在 → {THEME_LABEL[resolveAutoTheme()]}</em>
                  )}
                </span>
                {active && <span className="theme-menu__check">✓</span>}
              </button>
            )
          })}
          <div className="theme-menu__hint">
            自动模式按<b>北京时间</b>切换：05–09 晨光 · 09–16 浅色 · 16–19 夕阳 · 19–05 深色
          </div>
        </div>
      )}
    </div>
  )
}
