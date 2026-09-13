import { useEffect, useState } from 'react'
import {
  getSavedTheme,
  initializeTheme,
  setColorTheme,
  THEME_STORAGE_KEY,
} from '../lib/theme'
import type { ColorTheme } from '../lib/theme'

const themes: { value: ColorTheme; label: string }[] = [
  { value: 'pink', label: '渐变粉红' },
  { value: 'blue', label: '渐变淡蓝' },
]

export default function ThemeSwitcher() {
  const [theme, setTheme] = useState<ColorTheme>(getSavedTheme)

  useEffect(() => {
    const handleStorage = (event: StorageEvent) => {
      if (event.key === THEME_STORAGE_KEY || event.key === null) {
        setTheme(initializeTheme())
      }
    }
    window.addEventListener('storage', handleStorage)
    return () => window.removeEventListener('storage', handleStorage)
  }, [])

  const selectTheme = (nextTheme: ColorTheme) => {
    setColorTheme(nextTheme)
    setTheme(nextTheme)
  }

  return (
    <div className="theme-switcher" role="group" aria-label="背景主题">
      {themes.map(({ value, label }) => (
        <button
          key={value}
          type="button"
          className="theme-switcher__button"
          aria-pressed={theme === value}
          onClick={() => selectTheme(value)}
        >
          <span
            className={`theme-switcher__swatch theme-switcher__swatch--${value}`}
            aria-hidden="true"
          />
          <span>{label}</span>
        </button>
      ))}
    </div>
  )
}
