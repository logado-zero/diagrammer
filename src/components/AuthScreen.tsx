/**
 * Sign-in screen, shown by App.tsx whenever there's no valid session.
 * One form that flips between signing in and signing up, plus a guest
 * option. Styled off WelcomeScreen.tsx — same centered column, serif
 * headline, DonutMark, and the neutral/stone palette; deliberately no
 * accent color, so it reads as the same product rather than a bolted-on
 * login page.
 */
import { useState } from 'react'
import { DonutMark } from './icons/DonutMark.tsx'
import type { User } from '../types.ts'
import { login, loginAsGuest, register } from '../lib/api.ts'

interface AuthScreenProps {
  onSignedIn: (user: User) => void
}

/** Input: onSignedIn, called with the account once the server accepts. Output: the centered auth layout. */
export function AuthScreen({ onSignedIn }: AuthScreenProps) {
  const [isSignUp, setIsSignUp] = useState(false)
  const [userId, setUserId] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  /** Runs one auth call, surfacing its message on failure instead of throwing. */
  async function attempt(call: () => Promise<User>) {
    setBusy(true)
    setError(null)
    try {
      onSignedIn(await call())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong.')
      setBusy(false)
    }
  }

  const inputClass =
    'w-full rounded-xl border border-stone-200 bg-white px-3.5 py-2.5 text-sm text-stone-800 outline-none transition placeholder:text-stone-400 focus:border-stone-400 dark:border-stone-700 dark:bg-stone-800 dark:text-stone-100 dark:placeholder:text-stone-500 dark:focus:border-stone-500'

  return (
    <div className="flex min-h-svh flex-col items-center justify-center bg-white px-4 py-10 dark:bg-stone-900">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex items-center justify-center gap-3">
          <DonutMark size={34} />
          <h1 className="text-center font-serif text-3xl text-stone-800 dark:text-stone-100">Diagrammer</h1>
        </div>

        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault()
            if (busy) return
            void attempt(() => (isSignUp ? register : login)(userId.trim(), password))
          }}
        >
          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-medium text-stone-500 dark:text-stone-400">User ID</span>
            <input
              className={inputClass}
              value={userId}
              onChange={(e) => setUserId(e.target.value)}
              autoComplete="username"
              placeholder="your-id"
              autoFocus
            />
          </label>

          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-medium text-stone-500 dark:text-stone-400">Password</span>
            <input
              className={inputClass}
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={isSignUp ? 'new-password' : 'current-password'}
              placeholder={isSignUp ? 'At least 8 characters' : '••••••••'}
            />
          </label>

          {error && (
            <p role="alert" className="text-sm text-stone-600 dark:text-stone-400">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={busy || userId.trim().length < 3 || password.length < 8}
            className="mt-1 rounded-xl bg-stone-900 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-stone-800 disabled:opacity-40 dark:bg-stone-100 dark:text-stone-900 dark:hover:bg-white"
          >
            {isSignUp ? 'Create account' : 'Sign in'}
          </button>
        </form>

        <div className="mt-5 flex flex-col items-center gap-3 text-sm">
          <button
            type="button"
            onClick={() => {
              setIsSignUp(!isSignUp)
              setError(null)
            }}
            className="text-stone-500 underline-offset-4 transition hover:text-stone-800 hover:underline dark:text-stone-400 dark:hover:text-stone-200"
          >
            {isSignUp ? 'Already have an account? Sign in' : "Don't have an account? Sign up"}
          </button>

          <div className="flex w-full items-center gap-3">
            <span className="h-px flex-1 bg-stone-200 dark:bg-stone-700" />
            <span className="text-xs text-stone-400 dark:text-stone-500">or</span>
            <span className="h-px flex-1 bg-stone-200 dark:bg-stone-700" />
          </div>

          <button
            type="button"
            disabled={busy}
            onClick={() => void attempt(loginAsGuest)}
            className="w-full rounded-xl border border-stone-200 bg-white px-4 py-2.5 text-sm font-medium text-stone-600 transition hover:border-stone-300 hover:bg-stone-50 disabled:opacity-40 dark:border-stone-700 dark:bg-stone-800 dark:text-stone-300 dark:hover:bg-stone-700"
          >
            Continue as guest
          </button>
          <p className="text-center text-xs text-stone-400 dark:text-stone-500">
            Guest chats are saved, but the account can't be signed back into later.
          </p>
        </div>
      </div>
    </div>
  )
}
