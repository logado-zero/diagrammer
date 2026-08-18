import { useCallback, useEffect, useRef, useState } from 'react'
import type { ChatMessage, DrawMode, ServerEvent } from '../types.ts'
import { streamChat } from '../lib/api.ts'

/** Input: none. Output: a short client-only id, unique enough for React keys (no backend id exists). */
export function makeId() {
  return Math.random().toString(36).slice(2)
}

/**
 * Folds one streamed event into the assistant message it belongs to.
 * Input: the message being streamed into, and the event. Output: the next
 * version of that message.
 *
 * Split out of the stream callback because it is the client's half of the
 * server contract in types.ts's ServerEvent union — the one place that has to
 * change when an event type is added, and worth being able to see whole.
 * `conversation` and `title` never reach here: they are not per-message state.
 */
function applyEvent(message: ChatMessage, event: ServerEvent): ChatMessage {
  switch (event.type) {
    case 'text':
      return { ...message, text: message.text + event.text }
    case 'diagram':
      return { ...message, diagrams: [...(message.diagrams ?? []), event.payload] }
    case 'chart':
      return { ...message, charts: [...(message.charts ?? []), event.payload] }
    case 'trace':
      return { ...message, trace: [...(message.trace ?? []), event.label] }
    case 'error':
      return { ...message, error: event.message, isStreaming: false }
    case 'done':
      return { ...message, isStreaming: false }
    default:
      return message
  }
}

interface Options {
  modelId: string
  mode: DrawMode
  conversationId: string | null
  /** The server filed this turn under a conversation it had to create. */
  onCreated: (id: string, title: string) => void
  /** The title subagent named the conversation. */
  onTitled: (id: string | null, title: string) => void
  initialMessages?: ChatMessage[]
}

/**
 * The live turn: the message list, whether one is streaming, and the two ways
 * to start one (send, retry).
 *
 * Input: the picked model/mode, the open conversation, and callbacks for the
 * two events that aren't per-message state. Output: the state plus
 * send/retry/setMessages/abort.
 */
export function useChatTurn({ modelId, mode, conversationId, onCreated, onTitled, initialMessages = [] }: Options) {
  const [messages, setMessages] = useState<ChatMessage[]>(initialMessages)
  const [isStreaming, setIsStreaming] = useState(false)
  // Mirrored into a ref so retry keeps a stable identity: `messages` changes
  // on every streamed token, and MessageBubble is memo'd on its props — a
  // fresh onRetry per token would defeat that and re-render the whole list
  // dozens of times a second.
  const messagesRef = useRef(messages)
  messagesRef.current = messages
  const abortRef = useRef<AbortController | null>(null)

  /** Aborts the turn in flight, if any. Safe to call when there isn't one. */
  const abort = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
  }, [])

  // A turn still streaming when the app unmounts would otherwise keep its
  // fetch and its controller alive with nothing left to render into.
  useEffect(() => () => abortRef.current?.abort(), [])

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
          // conversation/title aren't per-message state — they're the server
          // telling us where this turn got filed (see server/routes/chat.py).
          if (event.type === 'conversation') {
            activeId = event.id
            onCreated(event.id, event.title)
            return
          }
          if (event.type === 'title') {
            onTitled(activeId, event.title)
            return
          }
          setMessages((prev) => prev.map((m) => (m.id === assistantId ? applyEvent(m, event) : m)))
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

      // Only if this turn is still the current one. Retrying mid-stream aborts
      // this turn and starts another immediately; without the guard, this line
      // would run late and switch off the *new* turn's streaming flag, leaving
      // the composer enabled and the spinner gone while text was still arriving.
      if (abortRef.current === controller) {
        abortRef.current = null
        setIsStreaming(false)
      }
    },
    [modelId, mode, conversationId, onCreated, onTitled],
  )

  /** Re-runs a turn from just before the given message, discarding whatever followed it. */
  const retry = useCallback(
    (messageId: string) => {
      const current = messagesRef.current
      const idx = current.findIndex((m) => m.id === messageId)
      if (idx === -1) return
      void runTurn(current.slice(0, idx))
    },
    [runTurn],
  )

  return { messages, setMessages, isStreaming, runTurn, retry, abort }
}
