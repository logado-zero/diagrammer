/**
 * History sidebar: the signed-in user's saved conversations, a "New chat"
 * button, per-row delete (behind a confirm dialog), the light/dark/system
 * theme picker, and sign-out. Rendered by App.tsx to the left of
 * WelcomeScreen/ChatView.
 *
 * The rail is dark in *both* themes — it's the one high-contrast surface in
 * the app, and in light mode it's what gives the white content area an edge to
 * sit against. That's why it styles itself with white/alpha overlays instead of
 * the `dark:` pairs every other component uses: the background it sits on is
 * the same either way, so a second set of variants would say nothing.
 *
 * Collapses to a slide-over on small screens, since the chat view already
 * splits into canvas + conversation columns there and a third permanent
 * column wouldn't fit.
 */
import { useEffect, useRef, useState } from 'react'
import { LogOut, MessageSquare, Monitor, Moon, PanelLeft, Plus, Sun, Trash2 } from 'lucide-react'
import type { ConversationSummary, User } from '../types.ts'
import { setTheme, useTheme, type Theme } from '../lib/theme.ts'

interface SidebarProps {
  user: User
  conversations: ConversationSummary[]
  activeId: string | null
  open: boolean
  onToggle: () => void
  onNewChat: () => void
  onSelect: (id: string) => void
  onDelete: (id: string) => void
  onSignOut: () => void
}

const THEME_ICON = { light: Sun, dark: Moon, system: Monitor }
const NEXT_THEME: Record<Theme, Theme> = { light: 'dark', dark: 'system', system: 'light' }

// One shared class list so the theme and sign-out buttons stay identical.
const FOOTER_BUTTON =
  'shrink-0 rounded-lg p-1.5 text-stone-400 transition hover:bg-white/10 hover:text-stone-100'

/** Input: the conversation list + handlers. Output: the sidebar, plus the floating button that reopens it when collapsed. */
export function Sidebar({
  user,
  conversations,
  activeId,
  open,
  onToggle,
  onNewChat,
  onSelect,
  onDelete,
  onSignOut,
}: SidebarProps) {
  const theme = useTheme()
  const ThemeIcon = THEME_ICON[theme]
  // The conversation awaiting confirmation, or null when the dialog is closed.
  const [pending, setPending] = useState<ConversationSummary | null>(null)
  const dialogRef = useRef<HTMLDialogElement>(null)

  // showModal() is what buys the backdrop, Escape, focus trapping and
  // top-layer stacking (above this sidebar's own z-30) for free.
  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    if (pending) dialog.showModal()
    else if (dialog.open) dialog.close()
  }, [pending])

  if (!open) {
    return (
      <button
        type="button"
        onClick={onToggle}
        aria-label="Show conversation history"
        className="fixed left-4 top-4 z-30 rounded-lg bg-stone-800/90 p-2 text-stone-300 backdrop-blur transition hover:bg-stone-800 hover:text-white lg:left-6 lg:top-6 dark:bg-stone-950/90"
      >
        <PanelLeft size={16} />
      </button>
    )
  }

  return (
    <aside className="fixed inset-y-0 left-0 z-30 flex w-64 shrink-0 flex-col bg-stone-800 lg:static lg:z-auto dark:bg-stone-950">
      <div className="flex items-center justify-between px-3 py-3">
        <button
          type="button"
          onClick={onToggle}
          aria-label="Hide conversation history"
          className="rounded-lg p-1.5 text-stone-400 transition hover:bg-white/10 hover:text-stone-100"
        >
          <PanelLeft size={16} />
        </button>
        <button
          type="button"
          onClick={onNewChat}
          className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-sm font-medium text-stone-200 transition hover:bg-white/10 hover:text-white"
        >
          <Plus size={15} />
          New chat
        </button>
      </div>

      <nav className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        {conversations.length === 0 ? (
          <p className="px-2 py-6 text-center text-xs text-stone-500">Your saved chats will show up here.</p>
        ) : (
          <>
            <p className="px-3 pb-1.5 pt-3 text-[11px] font-medium uppercase tracking-wider text-stone-500">
              Recent
            </p>
            <ul className="flex flex-col gap-0.5">
              {conversations.map((c) => (
                <li key={c.id} className="group relative">
                  <button
                    type="button"
                    onClick={() => onSelect(c.id)}
                    aria-current={c.id === activeId ? 'true' : undefined}
                    className={`flex w-full items-center gap-2 rounded-lg py-2 pl-2.5 pr-8 text-left text-sm transition ${
                      c.id === activeId
                        ? 'bg-white/10 text-white'
                        : 'text-stone-400 hover:bg-white/5 hover:text-stone-200'
                    }`}
                  >
                    <MessageSquare size={14} className="shrink-0 opacity-60" />
                    <span className="truncate">{c.title || 'Untitled'}</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => setPending(c)}
                    aria-label={`Delete ${c.title || 'Untitled'}`}
                    // Kept mounted rather than conditionally rendered so it stays
                    // reachable by keyboard when the row isn't hovered.
                    className="absolute right-1 top-1/2 -translate-y-1/2 rounded-md p-1.5 text-stone-400 opacity-0 transition hover:bg-white/15 hover:text-white focus:opacity-100 group-hover:opacity-100"
                  >
                    <Trash2 size={13} />
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}
      </nav>

      <div className="flex items-center justify-between gap-2 border-t border-white/10 px-3 py-3">
        <span className="min-w-0 truncate text-sm text-stone-400" title={user.userId}>
          {user.guest ? 'Guest' : user.userId}
        </span>
        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            onClick={() => setTheme(NEXT_THEME[theme])}
            // Names the current state, not the action — the icon is the only
            // affordance, so "what is it now" is the part a reader can't see.
            aria-label={`Theme: ${theme}`}
            title={`Theme: ${theme}`}
            className={FOOTER_BUTTON}
          >
            <ThemeIcon size={15} />
          </button>
          <button type="button" onClick={onSignOut} aria-label="Sign out" className={FOOTER_BUTTON}>
            <LogOut size={15} />
          </button>
        </div>
      </div>

      {/* Zero padding on the dialog itself so only true backdrop clicks land on
          it; m-auto because Tailwind's preflight zeroes the UA's centering margin. */}
      <dialog
        ref={dialogRef}
        onClose={() => setPending(null)}
        onClick={(e) => {
          if (e.target === dialogRef.current) dialogRef.current?.close()
        }}
        className="m-auto max-w-sm rounded-2xl border border-stone-200/70 bg-white p-0 text-stone-900 shadow-[0_8px_40px_-8px_rgba(28,25,23,0.35)] backdrop:bg-stone-900/40 backdrop:backdrop-blur-[2px] dark:border-stone-700 dark:bg-stone-900 dark:text-stone-100"
      >
        <div className="p-5">
          <h2 className="text-sm font-semibold">Delete this conversation?</h2>
          <p className="mt-2 text-sm text-stone-600 dark:text-stone-400">
            &ldquo;{pending?.title || 'Untitled'}&rdquo; and everything in it — messages, charts and
            attachments — will be permanently deleted.
          </p>
          <div className="mt-5 flex justify-end gap-2">
            <button
              type="button"
              onClick={() => dialogRef.current?.close()}
              className="rounded-lg px-3 py-1.5 text-sm font-medium text-stone-600 transition hover:bg-stone-100 dark:text-stone-300 dark:hover:bg-stone-800"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => {
                if (pending) onDelete(pending.id)
                dialogRef.current?.close()
              }}
              // red, not rose: rose is a cool pink against this warm stone
              // palette, and red-600 is the neighbour of palette.ts's own
              // STATUS_CRITICAL — the app already has a critical color.
              className="rounded-lg bg-red-600 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-red-700 dark:bg-red-700 dark:hover:bg-red-600"
            >
              Delete
            </button>
          </div>
        </div>
      </dialog>
    </aside>
  )
}
