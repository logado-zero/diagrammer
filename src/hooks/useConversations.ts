import { useCallback, useEffect, useState } from 'react'
import type { ConversationSummary, User } from '../types.ts'
import { deleteConversation as deleteConversationApi, fetchConversation, listConversations } from '../lib/api.ts'

/**
 * The saved-history side: the sidebar list, which conversation is open, and
 * its title.
 *
 * These three are one thing, not three — every place that touches one touches
 * all of them (opening a conversation, starting a new one, the server filing a
 * turn, signing out), which is why they moved out of App.tsx together.
 *
 * Input: the signed-in user (null before sign-in; the list reloads when it
 * changes). Output: the state plus the four transitions that write it.
 */
export function useConversations(user: User | null) {
  const [conversations, setConversations] = useState<ConversationSummary[]>([])
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [title, setTitle] = useState<string | null>(null)

  useEffect(() => {
    if (!user) return
    listConversations()
      .then(setConversations)
      .catch(() => {
        // Sidebar stays empty; chatting still works and will repopulate it.
      })
  }, [user])

  /** The server created a conversation for the turn in flight (its "conversation" SSE event). */
  const noteCreated = useCallback((id: string, newTitle: string) => {
    setConversationId(id)
    setTitle(newTitle)
    setConversations((prev) => [{ id, title: newTitle, updatedAt: new Date().toISOString() }, ...prev])
  }, [])

  /** The title subagent named a conversation (the "title" SSE event). */
  const noteTitled = useCallback((id: string | null, newTitle: string) => {
    setTitle(newTitle)
    setConversations((prev) => prev.map((c) => (c.id === id ? { ...c, title: newTitle } : c)))
  }, [])

  /** Back to no open conversation — the next turn creates a fresh one server-side. */
  const reset = useCallback(() => {
    setConversationId(null)
    setTitle(null)
  }, [])

  /**
   * Loads a saved conversation. Output: its stored messages, or null if the
   * fetch failed — the caller owns message state, so it does the setting.
   */
  const open = useCallback(async (id: string) => {
    const conversation = await fetchConversation(id).catch(() => null)
    if (!conversation) return null
    setConversationId(conversation.id)
    setTitle(conversation.title)
    return conversation
  }, [])

  /** Deletes one, and reports whether it was the one currently open. */
  const remove = useCallback(
    async (id: string) => {
      await deleteConversationApi(id).catch(() => null)
      setConversations((prev) => prev.filter((c) => c.id !== id))
      return id === conversationId
    },
    [conversationId],
  )

  /** Signing out drops the whole history view along with the session. */
  const clear = useCallback(() => {
    setConversations([])
    reset()
  }, [reset])

  return { conversations, conversationId, title, noteCreated, noteTitled, reset, open, remove, clear }
}
