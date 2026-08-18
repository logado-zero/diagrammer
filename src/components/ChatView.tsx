/**
 * Full chat screen: header + message list + composer. Shown by App.tsx
 * once the conversation has at least one message. Once the conversation
 * has produced a diagram or chart, splits into a canvas panel (the latest
 * one, full size — CanvasPanel.tsx) alongside a narrower conversation
 * column; before that, a single centered column like the welcome screen.
 */
import { useEffect, useRef } from 'react'
import type { AttachmentKind, ChatMessage, DrawMode, ModelOption } from '../types.ts'
import { ChatHeader } from './ChatHeader.tsx'
import { MessageBubble } from './MessageBubble.tsx'
import { Composer, type ComposerAttachment } from './Composer.tsx'
import { CanvasPanel, type CanvasVisual } from './CanvasPanel.tsx'

interface ChatViewProps {
  title: string
  messages: ChatMessage[]
  value: string
  onChange: (value: string) => void
  onSubmit: () => void
  onRetry: (messageId: string) => void
  disabled: boolean
  attachment?: ComposerAttachment | null
  onAttachFile: (file: File, kind: AttachmentKind) => void
  onRemoveAttachment: () => void
  models: ModelOption[]
  modelId: string
  onModelChange: (id: string) => void
  mode: DrawMode
  onModeChange: (mode: DrawMode) => void
}

/**
 * The single latest diagram/chart across the whole conversation, newest
 * message first. If a message somehow produced both in one turn, the
 * chart is treated as "more recent" (arrival order between the two arrays
 * isn't tracked) — a rare edge case, not worth threading timestamps for.
 */
function findLatestVisual(messages: ChatMessage[]): CanvasVisual | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i]
    if (m.charts && m.charts.length > 0) return { type: 'chart', value: m.charts[m.charts.length - 1] }
    if (m.diagrams && m.diagrams.length > 0) return { type: 'diagram', value: m.diagrams[m.diagrams.length - 1] }
  }
  return null
}

/** Input: messages + composer state/handlers (ChatViewProps). Output: the assembled chat screen, auto-scrolled to the newest message. */
export function ChatView({
  title,
  messages,
  value,
  onChange,
  onSubmit,
  onRetry,
  disabled,
  attachment,
  onAttachFile,
  onRemoveAttachment,
  models,
  modelId,
  onModelChange,
  mode,
  onModeChange,
}: ChatViewProps) {
  const bottomRef = useRef<HTMLDivElement>(null)

  // `messages` changes on every streamed token, and a smooth scroll is a
  // ~500ms animation — asking for a new one 30+ times a second leaves them
  // fighting each other, which is what reads as janky streaming. Instant
  // while text is arriving (the text itself is the motion); smooth only for
  // the one-off jump when a turn ends or a message is added. `disabled` is
  // App.tsx's isStreaming — same flag, already threaded through.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: disabled ? 'auto' : 'smooth', block: 'end' })
  }, [messages, disabled])

  const messageList = (
    <div className="flex flex-col gap-8">
      {messages.map((m) => (
        <MessageBubble key={m.id} message={m} onRetry={onRetry} />
      ))}
      <div ref={bottomRef} />
    </div>
  )

  const composer = (
    <Composer
      value={value}
      onChange={onChange}
      onSubmit={onSubmit}
      placeholder="Write a message..."
      disabled={disabled}
      attachment={attachment}
      onAttachFile={onAttachFile}
      onRemoveAttachment={onRemoveAttachment}
      models={models}
      modelId={modelId}
      onModelChange={onModelChange}
      mode={mode}
      onModeChange={onModeChange}
    />
  )

  const disclaimer = (
    <p className="mt-2 text-center text-xs text-stone-400 dark:text-stone-500">
      Diagrammer is AI and can make mistakes. Please double-check generated diagrams.
    </p>
  )

  const visual = findLatestVisual(messages)

  // Both layouts fill the app card App.tsx puts them in rather than the
  // viewport, so the header and composer stay pinned and only the message list
  // scrolls. That's also why the composer no longer needs a fade-out gradient
  // to hide content sliding under it — nothing slides under it.
  if (!visual) {
    return (
      <div className="flex h-full min-h-0 flex-col">
        <ChatHeader title={title} />
        <main className="min-h-0 flex-1 overflow-y-auto px-4 pb-6 pt-8 sm:px-6">
          <div className="mx-auto w-full max-w-3xl">{messageList}</div>
        </main>
        <div className="shrink-0 px-4 pb-5 pt-2 sm:px-6">
          <div className="mx-auto w-full max-w-3xl">
            {composer}
            {disclaimer}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ChatHeader title={title} />
      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <div className="min-h-[320px] flex-1 lg:min-h-0">
          <CanvasPanel visual={visual} />
        </div>
        <div className="flex min-h-0 w-full flex-col border-stone-200/80 lg:w-8/21 lg:shrink-0 lg:border-l dark:border-stone-800">
          <main className="min-h-0 flex-1 overflow-y-auto px-5 pb-4 pt-6">{messageList}</main>
          <div className="shrink-0 px-4 pb-4 pt-2">
            {composer}
            {disclaimer}
          </div>
        </div>
      </div>
    </div>
  )
}
