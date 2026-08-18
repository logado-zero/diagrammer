import { useCallback, useState } from 'react'
import type { StorageKey } from '../lib/storage.ts'

/**
 * useState that survives a reload.
 *
 * The model picker, the draw-mode selector and the sidebar toggle each had
 * their own copy of "set the state, then write the same value to
 * localStorage", with the matching read half written a third way somewhere
 * else in the file.
 *
 * String values only, so what lands in storage is exactly what the state is —
 * no encode/decode pair to keep in sync. The sidebar is why: it persists
 * 'open'/'closed' rather than a boolean, and a hook that stringified booleans
 * would have silently changed that stored format under existing users.
 *
 * Input: the storage key, a fallback, and an optional `parse` that rejects a
 * stored value that is no longer valid (an unknown draw mode, say).
 * Output: [value, setValue, setWithoutPersisting]. The third is for values the
 * app derives rather than the user choosing — the model picker falls back to
 * the server's default when nothing valid is stored, and persisting that would
 * turn "never picked one" into "picked this one" and pin it against a later
 * change of default.
 */
export function usePersistedState<T extends string>(
  key: StorageKey,
  fallback: T,
  parse?: (stored: string) => T | undefined,
): [T, (next: T) => void, (next: T) => void] {
  const [value, setValue] = useState<T>(() => {
    const stored = localStorage.getItem(key)
    if (stored === null) return fallback
    return (parse ? parse(stored) : (stored as T)) ?? fallback
  })

  const set = useCallback(
    (next: T) => {
      setValue(next)
      localStorage.setItem(key, next)
    },
    [key],
  )

  return [value, set, setValue]
}
