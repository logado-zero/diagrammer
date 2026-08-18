/**
 * The app's single source of truth for light vs dark.
 *
 * Two kinds of consumer read it and they can't share one mechanism: Tailwind's
 * `dark:` utilities need a `.dark` class on <html> (see the `@custom-variant`
 * line in index.css), while ECharts and Mermaid need a boolean in JS to pick a
 * palette. `apply()` sets both from the same value, so they can never disagree.
 *
 * Deliberately a module-level store rather than React state in App.tsx:
 * ChartCard/DiagramCard are memo'd leaves that already read the theme via a
 * hook, and prop-drilling it down would re-render them on every parent render.
 */
import { useSyncExternalStore } from 'react'

export type Theme = 'light' | 'dark' | 'system'

const STORAGE_KEY = 'diagrammer:theme'
export const THEMES: Theme[] = ['light', 'dark', 'system']

const mql = window.matchMedia('(prefers-color-scheme: dark)')
const listeners = new Set<() => void>()

const stored = localStorage.getItem(STORAGE_KEY)
let theme: Theme = THEMES.includes(stored as Theme) ? (stored as Theme) : 'system'

/** Input: none. Output: whether dark is active right now, resolving "system" against the OS. */
function resolved(): boolean {
  return theme === 'system' ? mql.matches : theme === 'dark'
}

/** Pushes the current theme onto <html> (class + color-scheme) and wakes every subscriber. */
function apply() {
  const dark = resolved()
  document.documentElement.classList.toggle('dark', dark)
  // Keeps native UI — scrollbars, the Composer's <select> popup, form controls
  // — from rendering in the opposite theme.
  document.documentElement.style.colorScheme = dark ? 'dark' : 'light'
  for (const listener of listeners) listener()
}

// Only relevant while "system" is selected; an explicit light/dark pick must
// survive the user changing their OS theme with the tab open.
mql.addEventListener('change', () => {
  if (theme === 'system') apply()
})

// index.html already did this pre-paint. Repeating it is idempotent and means
// the class is still correct if that script is ever dropped.
apply()

/** Input: the new theme. Output: none — persists it and repaints every consumer. */
export function setTheme(next: Theme) {
  theme = next
  localStorage.setItem(STORAGE_KEY, next)
  apply()
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => void listeners.delete(listener)
}

/** React hook: the selected theme, including "system" — for the picker UI. */
export function useTheme(): Theme {
  return useSyncExternalStore(subscribe, () => theme)
}

/** React hook: whether dark is active — for canvas libraries that need a palette, not a class. */
export function useIsDark(): boolean {
  return useSyncExternalStore(subscribe, resolved)
}
