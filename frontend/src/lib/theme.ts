export type ColorTheme = 'pink' | 'blue'

export const THEME_STORAGE_KEY = 'warehouse-color-theme'
export const DEFAULT_THEME: ColorTheme = 'blue'

export function isColorTheme(value: unknown): value is ColorTheme {
  return value === 'pink' || value === 'blue'
}

export function getSavedTheme(): ColorTheme {
  try {
    const saved = window.localStorage.getItem(THEME_STORAGE_KEY)
    return isColorTheme(saved) ? saved : DEFAULT_THEME
  } catch {
    return DEFAULT_THEME
  }
}

export function initializeTheme(): ColorTheme {
  const theme = getSavedTheme()
  document.documentElement.dataset.theme = theme
  return theme
}

export function setColorTheme(theme: ColorTheme): void {
  document.documentElement.dataset.theme = theme
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme)
  } catch {
    // Theme switching still works when the browser disables local storage.
  }
}
