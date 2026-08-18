/**
 * The only place the frontend talks to the network: fetchModels() loads the
 * model catalog once, streamChat() POSTs a chat turn and hand-parses the
 * SSE response line by line.
 */
import type {
  ChatMessage,
  ConversationSummary,
  DrawMode,
  ModelOption,
  ServerEvent,
  StoredAttachment,
  User,
} from '../types.ts'

export interface OutgoingMessage {
  role: 'user' | 'assistant'
  text: string
  image?: { mediaType: string; data: string } | null
  file?: { name: string; mediaType: string; data: string } | null
}

export interface ModelsResponse {
  defaultModelId: string
  models: ModelOption[]
}

/** Input: none. Output: the model catalog + default id from GET /api/models, fetched once on app start (see App.tsx). */
export async function fetchModels(): Promise<ModelsResponse> {
  const response = await fetch('/api/models')
  if (!response.ok) throw new Error(`Failed to load models (${response.status}).`)
  return response.json() as Promise<ModelsResponse>
}

/**
 * One fetch wrapper for every authenticated JSON route.
 * `credentials: 'include'` sends the session cookie; FastAPI reports errors
 * as `{detail}` (HTTPException) but the chat route uses `{error}`, so both
 * are unwrapped here rather than at each call site.
 */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    credentials: 'include',
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
    ...init,
  })
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string; error?: string } | null
    throw new Error(body?.detail ?? body?.error ?? `Request failed (${response.status}).`)
  }
  return response.json() as Promise<T>
}

/** Input: none. Output: the signed-in user, or null if there's no valid session — what App.tsx calls on load to decide between AuthScreen and the app. */
export async function fetchMe(): Promise<User | null> {
  return request<User>('/api/auth/me').catch(() => null)
}

/** Input: credentials. Output: the new user (the session cookie is set by the response). */
export function register(userId: string, password: string): Promise<User> {
  return request<User>('/api/auth/register', { method: 'POST', body: JSON.stringify({ userId, password }) })
}

/** Input: credentials. Output: the signed-in user, or throws with the server's message. */
export function login(userId: string, password: string): Promise<User> {
  return request<User>('/api/auth/login', { method: 'POST', body: JSON.stringify({ userId, password }) })
}

/** Input: none. Output: a throwaway guest account — history still saves, it just isn't recoverable once the cookie is gone. */
export function loginAsGuest(): Promise<User> {
  return request<User>('/api/auth/guest', { method: 'POST' })
}

/** Input: none. Output: none — revokes the session server-side and clears the cookie. */
export function logout(): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>('/api/auth/logout', { method: 'POST' })
}

/** Input: none. Output: this user's conversations for the sidebar, newest first (titles only, no message bodies). */
export function listConversations(): Promise<ConversationSummary[]> {
  return request<ConversationSummary[]>('/api/conversations')
}

/** Input: a conversation id. Output: its full stored history, including every diagram/chart payload — enough to re-render with no LLM call. */
export function fetchConversation(id: string): Promise<{
  id: string
  title: string
  messages: {
    role: 'user' | 'assistant'
    text: string
    diagrams: ChatMessage['diagrams']
    charts: ChatMessage['charts']
    trace: string[]
    attachment: StoredAttachment | null
  }[]
}> {
  return request(`/api/conversations/${id}`)
}

/** Input: a conversation id. Output: none — deletes it server-side. */
export function deleteConversation(id: string): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>(`/api/conversations/${id}`, { method: 'DELETE' })
}

/**
 * A diagram/chart the model rendered isn't otherwise replayed in history
 * (only `text` is sent), so a follow-up like "make it horizontal" would
 * have no way to know what was drawn last turn. Appending a compact JSON
 * block to the assistant message's replayed text lets the model see and
 * revise its own prior output — the only change needed for edits to work,
 * since the canvas (CanvasPanel.tsx) always shows the latest diagram/chart
 * in the conversation regardless of which turn produced it.
 */
function withVisualContext(message: ChatMessage): string {
  const blocks = [
    ...(message.diagrams ?? []).map((d) => `[Previously rendered diagram: ${JSON.stringify(d)}]`),
    ...(message.charts ?? []).map((c) => `[Previously rendered chart: ${JSON.stringify(c)}]`),
  ]
  return blocks.length === 0 ? message.text : [message.text, ...blocks].join('\n\n')
}

/**
 * Strips client-only fields (diagrams/charts/isStreaming/id/etc) down to
 * what the server needs, and drops any message that errored.
 * Input: ChatMessage[]. Output: OutgoingMessage[].
 */
function toOutgoing(messages: ChatMessage[]): OutgoingMessage[] {
  return messages
    .filter((m) => !m.error)
    .map((m) => ({
      role: m.role,
      text: withVisualContext(m),
      image: m.image ? { mediaType: m.image.mediaType, data: m.image.data } : null,
      file: m.file ? { name: m.file.name, mediaType: m.file.mediaType, data: m.file.data } : null,
    }))
}

/**
 * Streams a chat turn from the backend. `history` should include the new
 * user message as its last entry. Calls `onEvent` for every SSE event as it
 * arrives (text deltas, diagram/chart payloads, error, done).
 */
export async function streamChat(
  history: ChatMessage[],
  onEvent: (event: ServerEvent) => void,
  signal?: AbortSignal,
  modelId?: string,
  mode?: DrawMode,
  conversationId?: string | null,
): Promise<void> {
  const response = await fetch('/api/chat', {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages: toOutgoing(history), model: modelId, mode, conversationId }),
    signal,
  })

  if (!response.ok || !response.body) {
    // `error` is this route's own shape; `detail` is what FastAPI's
    // HTTPException produces, e.g. the 401 from the current_user dependency.
    const body = await response.json().catch(() => null)
    onEvent({ type: 'error', message: body?.error ?? body?.detail ?? `Request failed (${response.status}).` })
    return
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    let boundary = buffer.indexOf('\n\n')
    while (boundary !== -1) {
      const rawEvent = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)
      const line = rawEvent.split('\n').find((l) => l.startsWith('data: '))
      if (line) {
        try {
          onEvent(JSON.parse(line.slice('data: '.length)) as ServerEvent)
        } catch {
          // ignore malformed chunk
        }
      }
      boundary = buffer.indexOf('\n\n')
    }
  }
}
