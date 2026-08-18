/**
 * Owns all chat state for the app: the signed-in user, the saved
 * conversation list, messages, draft text, pending attachment, selected
 * model. Decides between AuthScreen, WelcomeScreen, and ChatView. Every
 * component under src/components is a props-in view with no state of its
 * own beyond small local UI toggles (e.g. copy in MessageBubble).
 *
 * `messages` is still the live source of truth for the turn in progress —
 * the server persists in parallel (see server/main.py) rather than this
 * component reading back what it just sent.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import type {
  AttachmentKind,
  ChatMessage,
  ConversationSummary,
  DrawMode,
  ModelOption,
  User,
} from './types.ts'
import { WelcomeScreen } from './components/WelcomeScreen.tsx'
import { ChatView } from './components/ChatView.tsx'
import { AuthScreen } from './components/AuthScreen.tsx'
import { Sidebar } from './components/Sidebar.tsx'
import {
  deleteConversation,
  fetchConversation,
  fetchMe,
  fetchModels,
  listConversations,
  logout,
  streamChat,
} from './lib/api.ts'
import { DEMO_MESSAGES } from './lib/demoData.ts'

const MODEL_STORAGE_KEY = 'diagrammer:model'
const MODE_STORAGE_KEY = 'diagrammer:mode'
const SIDEBAR_STORAGE_KEY = 'diagrammer:sidebar'
const DRAW_MODES: DrawMode[] = ['auto', 'diagram', 'chart']

/** Input: none. Output: the persisted draw mode if it's one of the known values, else "auto". */
function restoreMode(): DrawMode {
  const stored = localStorage.getItem(MODE_STORAGE_KEY)
  return DRAW_MODES.includes(stored as DrawMode) ? (stored as DrawMode) : 'auto'
}

interface PendingAttachment {
  kind: AttachmentKind
  name: string
  previewUrl?: string
  base64: string
  mediaType: string
}

// Mirrors server/attachments.py's XLSX_MEDIA_TYPE — the one mediaType inline_file_attachment() parses with openpyxl; everything else (including .csv) decodes as plain text.
const XLSX_MEDIA_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

/** Input: the picked file + attach kind. Output: the mediaType to send — a "sheet" attachment is .xlsx (parsed server-side) or .csv (already plain text). */
function mediaTypeFor(file: File, kind: AttachmentKind): string {
  if (kind === 'image') return file.type || 'image/png'
  if (kind === 'text') return 'text/plain'
  return file.name.toLowerCase().endsWith('.csv') ? 'text/csv' : XLSX_MEDIA_TYPE
}

/**
 * Reads a picked file into a base64 string, for sending over JSON (no
 * multipart upload in this app).
 * Input: a File from the file picker. Output: Promise<base64 string>.
 */
function readFileAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = reader.result as string
      resolve(result.split(',')[1] ?? '')
    }
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}

/** Input: none. Output: a short client-only id, unique enough for React keys (no backend id exists). */
function makeId() {
  return Math.random().toString(36).slice(2)
}

/**
 * Chat header title. The server owns the real title (a placeholder at
 * first, then an LLM-generated one — see server/main.py), so this is only
 * the fallback for the window between sending the first message and the
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
 * Output: WelcomeScreen (no messages yet) or ChatView (once a turn has run),
 * wired to the state and handlers defined below.
 */
function App() {
  const [messages, setMessages] = useState<ChatMessage[]>(() => (IS_DEMO ? DEMO_MESSAGES : []))
  // Mirrored into a ref so handleRetry keeps a stable identity: `messages`
  // changes on every streamed token, and MessageBubble is memo'd on its props
  // — a fresh onRetry per token would defeat that and re-render the whole
  // list dozens of times a second.
  const messagesRef = useRef(messages)
  messagesRef.current = messages
  const [draft, setDraft] = useState('')
  const [pendingAttachment, setPendingAttachment] = useState<PendingAttachment | null>(null)
  const [isStreaming, setIsStreaming] = useState(false)
  const [models, setModels] = useState<ModelOption[]>([])
  const [modelId, setModelId] = useState<string>('')
  const [mode, setMode] = useState<DrawMode>(restoreMode)
  const [user, setUser] = useState<User | null>(null)
  // null while the session check is in flight, so the login screen doesn't
  // flash for someone who is already signed in.
  const [authChecked, setAuthChecked] = useState(false)
  const [conversations, setConversations] = useState<ConversationSummary[]>([])
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [title, setTitle] = useState<string | null>(null)
  const [sidebarOpen, setSidebarOpen] = useState(() => localStorage.getItem(SIDEBAR_STORAGE_KEY) !== 'closed')
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    fetchMe()
      .then(setUser)
      .finally(() => setAuthChecked(true))
  }, [])

  useEffect(() => {
    if (!user) return
    listConversations()
      .then(setConversations)
      .catch(() => {
        // Sidebar stays empty; chatting still works and will repopulate it.
      })
  }, [user])

  useEffect(() => {
    fetchModels()
      .then(({ defaultModelId, models }) => {
        setModels(models)
        const stored = localStorage.getItem(MODEL_STORAGE_KEY)
        const restored = stored && models.some((m) => m.id === stored && m.available) ? stored : undefined
        setModelId(restored ?? defaultModelId)
      })
      .catch(() => {
        // Model picker just won't populate; /api/chat still applies its own default server-side.
      })
  }, [])

  /** Updates the selected model and persists it to localStorage so it survives a reload. */
  const handleModelChange = useCallback((id: string) => {
    setModelId(id)
    localStorage.setItem(MODEL_STORAGE_KEY, id)
  }, [])

  /** Updates the draw-mode selector and persists it to localStorage so it survives a reload. */
  const handleModeChange = useCallback((next: DrawMode) => {
    setMode(next)
    localStorage.setItem(MODE_STORAGE_KEY, next)
  }, [])

  /** Input: a picked File + which attach option it came from. Output: none — stashes it as pendingAttachment, awaiting submit(). */
  const handleAttachFile = useCallback(async (file: File, kind: AttachmentKind) => {
    const base64 = await readFileAsBase64(file)
    setPendingAttachment({
      kind,
      name: file.name,
      previewUrl: kind === 'image' ? URL.createObjectURL(file) : undefined,
      base64,
      mediaType: mediaTypeFor(file, kind),
    })
  }, [])

  /** Clears the pending attachment and frees its object URL (image only). */
  const handleRemoveAttachment = useCallback(() => {
    setPendingAttachment((prev) => {
      if (prev?.previewUrl) URL.revokeObjectURL(prev.previewUrl)
      return null
    })
  }, [])

  /**
   * Sends one turn to the backend and streams the reply into a new
   * assistant message, event by event.
   * Input: the full message history, including the new user message.
   * Output: none — mutates `messages` via setMessages as SSE events arrive
   * (see lib/api.ts streamChat).
   */
  const runTurn = useCallback(
    async (history: ChatMessage[]) => {
      const assistantId = makeId()
      // Tracked locally rather than read back off state: a brand-new
      // conversation's id arrives mid-stream, and the `title` event that can
      // follow it in the same stream needs that id immediately.
      let activeId = conversationId
      setMessages([...history, { id: assistantId, role: 'assistant', text: '', isStreaming: true }])
      setIsStreaming(true)

      const controller = new AbortController()
      abortRef.current = controller

      await streamChat(
        history,
        (event) => {
          // conversation/title aren't per-message state — they're the
          // server telling us where this turn got filed (see server/main.py).
          if (event.type === 'conversation') {
            activeId = event.id
            setConversationId(event.id)
            setTitle(event.title)
            setConversations((prev) => [
              { id: event.id, title: event.title, updatedAt: new Date().toISOString() },
              ...prev,
            ])
            return
          }
          if (event.type === 'title') {
            setTitle(event.title)
            setConversations((prev) => prev.map((c) => (c.id === activeId ? { ...c, title: event.title } : c)))
            return
          }
          setMessages((prev) =>
            prev.map((m) => {
              if (m.id !== assistantId) return m
              switch (event.type) {
                case 'text':
                  return { ...m, text: m.text + event.text }
                case 'diagram':
                  return { ...m, diagrams: [...(m.diagrams ?? []), event.payload] }
                case 'chart':
                  return { ...m, charts: [...(m.charts ?? []), event.payload] }
                case 'trace':
                  return { ...m, trace: [...(m.trace ?? []), event.label] }
                case 'error':
                  return { ...m, error: event.message, isStreaming: false }
                case 'done':
                  return { ...m, isStreaming: false }
                default:
                  return m
              }
            }),
          )
        },
        controller.signal,
        modelId,
        mode,
        conversationId,
      ).catch(() => {
        setMessages((prev) =>
          prev.map((m) => (m.id === assistantId ? { ...m, isStreaming: false, error: 'Connection lost.' } : m)),
        )
      })

      setIsStreaming(false)
    },
    [modelId, mode, conversationId],
  )

  /**
   * Builds the user message from the current draft + pending attachment and
   * starts a turn. Input: the draft text. Output: none.
   */
  const submit = useCallback(
    (text: string) => {
      const trimmed = text.trim()
      if (!trimmed && !pendingAttachment) return

      const userMessage: ChatMessage = {
        id: makeId(),
        role: 'user',
        text: trimmed,
        image:
          pendingAttachment?.kind === 'image'
            ? {
                previewUrl: pendingAttachment.previewUrl ?? '',
                data: pendingAttachment.base64,
                mediaType: pendingAttachment.mediaType,
              }
            : undefined,
        file:
          pendingAttachment && pendingAttachment.kind !== 'image'
            ? {
                kind: pendingAttachment.kind,
                name: pendingAttachment.name,
                mediaType: pendingAttachment.mediaType,
                data: pendingAttachment.base64,
              }
            : undefined,
      }

      const history = [...messages, userMessage]
      setDraft('')
      setPendingAttachment(null)
      void runTurn(history)
    },
    [messages, pendingAttachment, runTurn],
  )

  /**
   * Re-runs a turn from just before the given message, discarding whatever
   * followed it. Input: the id of the assistant message to retry. Output: none.
   */
  const handleRetry = useCallback(
    (messageId: string) => {
      const current = messagesRef.current
      const idx = current.findIndex((m) => m.id === messageId)
      if (idx === -1) return
      void runTurn(current.slice(0, idx))
    },
    [runTurn],
  )

  /** Clears the view back to the welcome screen. The next turn creates a fresh conversation server-side, since conversationId goes null. */
  const handleNewChat = useCallback(() => {
    abortRef.current?.abort()
    setMessages([])
    setConversationId(null)
    setTitle(null)
    setDraft('')
  }, [])

  /** Loads a saved conversation into the chat view. Stored diagrams/charts/attachments come back with it, so nothing is re-generated or lost when switching away and back. */
  const handleSelectConversation = useCallback(async (id: string) => {
    abortRef.current?.abort()
    const conversation = await fetchConversation(id).catch(() => null)
    if (!conversation) return
    setConversationId(conversation.id)
    setTitle(conversation.title)
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
  }, [])

  /** Deletes a conversation, and clears the view too if it was the one open. */
  const handleDeleteConversation = useCallback(
    async (id: string) => {
      await deleteConversation(id).catch(() => null)
      setConversations((prev) => prev.filter((c) => c.id !== id))
      if (id === conversationId) handleNewChat()
    },
    [conversationId, handleNewChat],
  )

  /** Signs out and drops every trace of the session from the view. */
  const handleSignOut = useCallback(async () => {
    abortRef.current?.abort()
    await logout().catch(() => null)
    setUser(null)
    setConversations([])
    setMessages([])
    setConversationId(null)
    setTitle(null)
  }, [])

  /** Collapses/expands the sidebar and remembers the choice, like the model and mode pickers. */
  const handleToggleSidebar = useCallback(() => {
    setSidebarOpen((prev) => {
      localStorage.setItem(SIDEBAR_STORAGE_KEY, prev ? 'closed' : 'open')
      return !prev
    })
  }, [])

  if (!authChecked && !IS_DEMO) return <div className="min-h-svh bg-stone-200 dark:bg-black" />
  if (!user && !IS_DEMO) return <AuthScreen onSignedIn={setUser} />

  const sidebar = (
    <Sidebar
      user={user ?? DEMO_USER}
      conversations={conversations}
      activeId={conversationId}
      open={sidebarOpen}
      onToggle={handleToggleSidebar}
      onNewChat={handleNewChat}
      onSelect={(id) => void handleSelectConversation(id)}
      onDelete={(id) => void handleDeleteConversation(id)}
      onSignOut={() => void handleSignOut()}
    />
  )

  const screen =
    messages.length === 0 ? (
      <WelcomeScreen
        value={draft}
        onChange={setDraft}
        onSubmit={() => submit(draft)}
        attachment={pendingAttachment}
        onAttachFile={handleAttachFile}
        onRemoveAttachment={handleRemoveAttachment}
        onPickPrompt={(prompt) => setDraft(prompt)}
        models={models}
        modelId={modelId}
        onModelChange={handleModelChange}
        mode={mode}
        onModeChange={handleModeChange}
      />
    ) : (
      <ChatView
        title={title || deriveTitle(messages)}
        messages={messages}
        value={draft}
        onChange={setDraft}
        onSubmit={() => submit(draft)}
        onRetry={handleRetry}
        disabled={isStreaming}
        attachment={pendingAttachment}
        onAttachFile={handleAttachFile}
        onRemoveAttachment={handleRemoveAttachment}
        models={models}
        modelId={modelId}
        onModelChange={handleModelChange}
        mode={mode}
        onModeChange={handleModeChange}
      />
    )

  // Two surfaces, deliberately: a warm tinted ground, and the app itself as a
  // single card floating on it (inset only from `lg` up — a phone shouldn't
  // spend 12px on a border). Everything inside scrolls within that card, so the
  // sidebar rail, header and composer stay put.
  return (
    <div className="flex h-svh overflow-hidden bg-stone-200 dark:bg-black lg:p-3">
      <div className="flex min-w-0 flex-1 overflow-hidden bg-white shadow-[0_1px_3px_rgba(28,25,23,0.07),0_18px_44px_-14px_rgba(28,25,23,0.25)] lg:rounded-2xl dark:bg-stone-900 dark:shadow-none">
        {sidebar}
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">{screen}</div>
      </div>
    </div>
  )
}

export default App
