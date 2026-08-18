import { useCallback, useEffect, useState } from 'react'
import type { User } from '../types.ts'
import { fetchMe, logout } from '../lib/api.ts'

/**
 * The signed-in session: who the user is, and whether we know yet.
 *
 * Input: none. Output: { user, authChecked, setUser, signOut }.
 * `authChecked` stays false while the session check is in flight so the login
 * screen doesn't flash for someone who is already signed in.
 */
export function useAuth() {
  const [user, setUser] = useState<User | null>(null)
  const [authChecked, setAuthChecked] = useState(false)

  useEffect(() => {
    fetchMe()
      .then(setUser)
      .finally(() => setAuthChecked(true))
  }, [])

  const signOut = useCallback(async () => {
    await logout().catch(() => null)
    setUser(null)
  }, [])

  return { user, authChecked, setUser, signOut }
}
