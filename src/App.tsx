/**
 * Wires the app together and decides which screen is on: AuthScreen,
 * WelcomeScreen, or ChatView.
 *
 * State lives in four hooks under src/hooks — the session (useAuth), saved
 * history (useConversations), the live turn (useChatTurn), and the unsent
 * draft plus attachment (useComposer) — because each of those clusters is
 * written as a unit. What stays here is only what crosses between them.
 * Every component under src/components is still a props-in view with no state
 * of its own beyond small local UI toggles (e.g. copy in MessageBubble).
 *
 * `messages` is still the live source of truth for the turn in progress — the
 * server persists in parallel (see server/routes/chat.py) rather than this
 * component reading back what it just sent.
 */
import { useCallback, useEffect, useState } from 'react'
import type { ChatMessage, DrawMode, ModelOption, User } from './types.ts'
import { WelcomeScreen } from './components/WelcomeScreen.tsx'
import { ChatView } from './components/ChatView.tsx'
import { AuthScreen } from './components/AuthScreen.tsx'
import { Sidebar } from './components/Sidebar.tsx'
import { fetchModels } from './lib/api.ts'
import { STORAGE_KEYS } from './lib/storage.ts'
import { DEMO_MESSAGES } from './lib/demoData.ts'
import { useAuth } from './hooks/useAuth.ts'
import { useChatTurn, makeId } from './hooks/useChatTurn.ts'
import { useComposer } from './hooks/useComposer.ts'
import { useConversations } from './hooks/useConversations.ts'
import { usePersistedState } from './hooks/usePersistedState.ts'

const DRAW_MODES: DrawMode[] = ['auto', 'diagram', 'chart']

/**
 * Chat header title. The server owns the real title (a placeholder at
 * first, then an LLM-generated one — see server/routes/chat.py), so this is
 * only the fallback for the window between sending the first message and the
 * server's "conversation" event coming back.
 * Input: the current messages. Output: the first user message, truncated
 * to 48 chars, or "New diagram" if there isn't one yet.
 */
function deriveTitle(messages: ChatMessage[]): string {
  const firstUser = messages.find((m) => m.role === 'user')
  if (!firstUser?.text) return 'New diagram'
  return firstUser.text.length > 48 ? `${firstUser.text.slice(0, 48)}…` : firstUser.text
}

/**
 * `?demo=1` seeds the chat from demoData.ts and skips the sign-in screen, so
 * UI/visual work needs no backend, no database and no account — which is the
 * only way to look at the chat view when MongoDB isn't reachable. Read once
 * at module load: it never changes without a page load.
 */
const IS_DEMO = new URLSearchParams(window.location.search).has('demo')

/** Stand-in so the sidebar has someone to name in demo mode; never sent anywhere. */
const DEMO_USER: User = { id: 'demo', userId: 'demo', guest: true }

/**
 * Root component. Input: none (reads ?demo=1 from the URL once on mount).
 * Output: WelcomeScreen (no messages yet) or ChatView (once a turn has run).
 */
function App() {
  const { user, authChecked, setUser, signOut } = useAuth()
  const history = useConversations(user)
  const composer = useComposer()

  const [models, setModels] = useState<ModelOption[]>([])
  const [modelId, setModelId, setModelIdLocal] = usePersistedState<string>(STORAGE_KEYS.model, '')
  const [mode, setMode] = usePersistedState<DrawMode>(STORAGE_KEYS.mode, 'auto', (stored) =>
    DRAW_MODES.includes(stored as DrawMode) ? (stored as DrawMode) : undefined,
  )
  const [sidebar, setSidebar] = usePersistedState<'open' | 'closed'>(STORAGE_KEYS.sidebar, 'open', (stored) =>
    stored === 'closed' ? 'closed' : 'open',
  )
  const sidebarOpen = sidebar === 'open'

  const turn = useChatTurn({
    modelId,
    mode,
    conversationId: history.conversationId,
    onCreated: history.noteCreated,
    onTitled: history.noteTitled,
    initialMessages: IS_DEMO ? DEMO_MESSAGES : [],
  })
  const { messages, setMessages, runTurn } = turn

  useEffect(() => {
    fetchModels()
      .then(({ defaultModelId, models }) => {
        setModels(models)
        // A stored id that has since been removed from the catalog, or gated
        // off, falls back to the server's default rather than sending an id
        // POST /api/chat would now refuse.
        const stored = localStorage.getItem(STORAGE_KEYS.model)
        const restored = stored && models.some((m) => m.id === stored && m.available) ? stored : undefined
        setModelIdLocal(restored ?? defaultModelId)
      })
      .catch(() => {
        // Model picker just won't populate; /api/chat still applies its own default server-side.
      })
  }, [setModelIdLocal])

  /** Builds the user message from the current draft + pending attachment and starts a turn. */
  const submit = useCallback(
    (text: string) => {
      const trimmed = text.trim()
      const attachment = composer.attachment
      if (!trimmed && !attachment) return

      const userMessage: ChatMessage = {
        id: makeId(),
        role: 'user',
        text: trimmed,
        image:
          attachment?.kind === 'image'
            ? {
                previewUrl: attachment.previewUrl ?? '',
                data: attachment.base64,
                mediaType: attachment.mediaType,
              }
            : undefined,
        file:
          attachment && attachment.kind !== 'image'
            ? {
                kind: attachment.kind,
                name: attachment.name,
                mediaType: attachment.mediaType,
                data: attachment.base64,
              }
            : undefined,
      }

      const next = [...messages, userMessage]
      // Cleared before the turn runs, not after: the message now owns the
      // attachment's bytes, and leaving it armed would attach it again.
      composer.clear()
      void runTurn(next)
    },
    [messages, composer, runTurn],
  )

  /** Clears the view back to the welcome screen. The next turn creates a fresh conversation server-side. */
  const handleNewChat = useCallback(() => {
    turn.abort()
    setMessages([])
    history.reset()
    composer.clear()
  }, [turn, setMessages, history, composer])

  /** Loads a saved conversation. Stored diagrams/charts/attachments come back with it, so nothing is re-generated or lost when switching away and back. */
  const handleSelectConversation = useCallback(
    async (id: string) => {
      turn.abort()
      const conversation = await history.open(id)
      if (!conversation) return
      // An attachment armed in the previous conversation must not follow the
      // user into this one.
      composer.clear()
      setMessages(
        conversation.messages.map((m) => ({
          id: makeId(),
          role: m.role,
          text: m.text,
          diagrams: m.diagrams,
          charts: m.charts,
          trace: m.trace,
          attachment: m.attachment,
        })),
      )
    },
    [turn, history, composer, setMessages],
  )

  /** Deletes a conversation, and clears the view too if it was the one open. */
  const handleDeleteConversation = useCallback(
    async (id: string) => {
      if (await history.remove(id)) handleNewChat()
    },
    [history, handleNewChat],
  )

  /** Signs out and drops every trace of the session from the view. */
  const handleSignOut = useCallback(async () => {
    turn.abort()
    await signOut()
    history.clear()
    setMessages([])
    composer.clear()
  }, [turn, signOut, history, setMessages, composer])

  const handleToggleSidebar = useCallback(() => {
    setSidebar(sidebarOpen ? 'closed' : 'open')
  }, [sidebarOpen, setSidebar])

  if (!authChecked && !IS_DEMO) return <div className="h-svh bg-stone-200 dark:bg-black" />
  if (!user && !IS_DEMO) return <AuthScreen onSignedIn={setUser} />

  const sidebarEl = (
    <Sidebar
      user={user ?? DEMO_USER}
      conversations={history.conversations}
      activeId={history.conversationId}
      open={sidebarOpen}
      onToggle={handleToggleSidebar}
      onNewChat={handleNewChat}
      onSelect={(id) => void handleSelectConversation(id)}
      onDelete={(id) => void handleDeleteConversation(id)}
      onSignOut={() => void handleSignOut()}
    />
  )

  // The one block of props both screens take, declared once — it used to be
  // spelled out twice here and re-typed in three component files.
  const composerProps = {
    value: composer.draft,
    onChange: composer.setDraft,
    onSubmit: () => submit(composer.draft),
    attachment: composer.attachment,
    onAttachFile: composer.attachFile,
    onRemoveAttachment: composer.removeAttachment,
    models,
    modelId,
    onModelChange: setModelId,
    mode,
    onModeChange: setMode,
  }

  const screen =
    messages.length === 0 ? (
      <WelcomeScreen {...composerProps} onPickPrompt={(prompt) => composer.setDraft(prompt)} />
    ) : (
      <ChatView
        {...composerProps}
        title={history.title || deriveTitle(messages)}
        messages={messages}
        onRetry={turn.retry}
        disabled={turn.isStreaming}
      />
    )

  // Two surfaces, deliberately: a warm tinted ground, and the app itself as a
  // single card floating on it (inset only from `lg` up — a phone shouldn't
  // spend 12px on a border). Everything inside scrolls within that card, so the
  // sidebar rail, header and composer stay put.
  return (
    <div className="flex h-svh overflow-hidden bg-stone-200 dark:bg-black lg:p-3">
      <div className="flex min-w-0 flex-1 overflow-hidden bg-white shadow-[0_1px_3px_rgba(28,25,23,0.07),0_18px_44px_-14px_rgba(28,25,23,0.25)] lg:rounded-2xl dark:bg-stone-900 dark:shadow-none">
        {sidebarEl}
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">{screen}</div>
      </div>
    </div>
  )
}

export default App
