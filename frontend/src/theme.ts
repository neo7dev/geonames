export type Theme = 'light' | 'dark'

// Same key and rules as public/theme-init.js, which applies the theme before
// the first paint.
const STORAGE_KEY = 'theme'

function stored(): Theme | null {
  try {
    const value = localStorage.getItem(STORAGE_KEY)
    return value === 'light' || value === 'dark' ? value : null
  } catch {
    return null
  }
}

/** The stored choice, or the system's until the user picks one. */
export function currentTheme(): Theme {
  return (
    stored() ??
    (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
  )
}

/** Switch instantly: CSS transitions are off for the frame the class flips in. */
export function applyTheme(theme: Theme) {
  const root = document.documentElement
  root.classList.add('no-transitions')
  root.classList.toggle('dark', theme === 'dark')
  try {
    localStorage.setItem(STORAGE_KEY, theme)
  } catch {
    // Storage blocked: the switch still works until the page reloads
  }
  // Force the new styles to apply before transitions come back.
  void window.getComputedStyle(root).color
  requestAnimationFrame(() => root.classList.remove('no-transitions'))
}
