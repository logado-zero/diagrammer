/**
 * Every localStorage key this app writes, in one place.
 *
 * They used to be declared wherever they were first needed — three in
 * App.tsx, one inline in a useState initializer, one in lib/theme.ts — which
 * made "what do we persist?" a question you answered by grepping for
 * `localStorage`.
 */
export const STORAGE_KEYS = {
  model: 'diagrammer:model',
  mode: 'diagrammer:mode',
  sidebar: 'diagrammer:sidebar',
  /** Owned by lib/theme.ts, which is the only module allowed to read or write it. */
  theme: 'diagrammer:theme',
} as const

export type StorageKey = (typeof STORAGE_KEYS)[keyof typeof STORAGE_KEYS]
