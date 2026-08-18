/**
 * Renders one chat message: a right-aligned user bubble, or an assistant
 * reply with an action-icon row (copy and retry are wired up; read-aloud
 * is a decorative placeholder). Diagrams/charts no longer render inline
 * here — once a conversation has one, it lives full-size on ChatView's
 * canvas panel (CanvasPanel.tsx); this just notes that canvas changed.
 */
import { memo, useState } from 'react'
import { Copy, FileSpreadsheet, FileText, RotateCcw } from 'lucide-react'
import type { ChatMessage } from '../types.ts'
import { DonutMark } from './icons/DonutMark.tsx'
import { ImageLightbox } from './ImageLightbox.tsx'
import { Markdown } from './Markdown.tsx'
import { ProcessTrace } from './ProcessTrace.tsx'

/** Small round icon button used in the action row under an assistant reply. */
function IconButton({
  icon: Icon,
  label,
  active,
  onClick,
}: {
  icon: typeof Copy
  label: string
  active?: boolean
  onClick?: () => void
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      className={`flex h-7 w-7 items-center justify-center rounded-md transition hover:bg-stone-100 dark:hover:bg-stone-800 ${
        active ? 'text-stone-700 dark:text-stone-200' : 'text-stone-400 dark:text-stone-500'
      }`}
    >
      <Icon size={15} />
    </button>
  )
}

/**
 * Input: message + onRetry. Output: the user or assistant bubble, depending
 * on message.role.
 *
 * memo'd because the whole list re-renders on every streamed token (the
 * `messages` array identity changes), and only the one message actually
 * growing has new props — without this, a long conversation re-renders every
 * finished bubble, markdown and all, dozens of times a second.
 */
export const MessageBubble = memo(function MessageBubble({
  message,
  onRetry,
}: {
  message: ChatMessage
  // Takes the id rather than being pre-bound per message, so ChatView can pass
  // one stable callback down instead of a fresh arrow per row (see the memo above).
  onRetry?: (messageId: string) => void
}) {
  const [copied, setCopied] = useState(false)
  const [lightboxOpen, setLightboxOpen] = useState(false)

  if (message.role === 'user') {
    // A live composed-and-sent attachment (message.image/.file, with local
    // blob:/base64 data) takes priority; a message loaded from saved history
    // only ever has `attachment` (a GridFS reference, see types.ts's
    // StoredAttachment) — never both, but image wins if somehow present.
    const imageSrc = message.image?.previewUrl ?? (message.attachment?.kind === 'image' ? message.attachment.url : undefined)
    const fileAttachment =
      message.file ??
      (message.attachment?.kind === 'file'
        ? { name: message.attachment.name ?? 'Attached file', mediaType: message.attachment.mediaType }
        : undefined)
    const isSheet = fileAttachment
      ? 'kind' in fileAttachment
        ? fileAttachment.kind === 'sheet'
        : fileAttachment.mediaType.includes('spreadsheet') || fileAttachment.mediaType === 'text/csv'
      : false

    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] sm:max-w-[70%]">
          {imageSrc && (
            <button
              type="button"
              onClick={() => setLightboxOpen(true)}
              aria-label="View attached image"
              className="mb-2 ml-auto block"
            >
              <img src={imageSrc} alt="Attached" className="h-32 w-32 rounded-2xl object-cover" />
            </button>
          )}
          {imageSrc && lightboxOpen && (
            <ImageLightbox src={imageSrc} alt="Attached" onClose={() => setLightboxOpen(false)} />
          )}
          {fileAttachment && (
            <div className="mb-2 ml-auto flex w-fit items-center gap-2 rounded-2xl bg-stone-100 px-3 py-2 text-sm text-stone-700 dark:bg-stone-800 dark:text-stone-200">
              {isSheet ? <FileSpreadsheet size={16} /> : <FileText size={16} />}
              <span className="max-w-[12rem] truncate">{fileAttachment.name}</span>
            </div>
          )}
          {message.text && (
            <div className="rounded-3xl bg-stone-100 px-4 py-2.5 text-[15px] text-stone-800 dark:bg-stone-800 dark:text-stone-100">
              {message.text}
            </div>
          )}
        </div>
      </div>
    )
  }

  const hasVisual = (message.diagrams?.length ?? 0) > 0 || (message.charts?.length ?? 0) > 0
  const hasChart = (message.charts?.length ?? 0) > 0
  // Kept after the turn ends, not only while it's mid-flight — one collapsed
  // row that answers "did this turn search anything?" after the fact.
  // ProcessTrace collapses itself when done. Status labels only: what a memory
  // search returned is a `retrieval` event the server logs and never sends here.
  const showTrace = (message.trace?.length ?? 0) > 0

  return (
    <div className="max-w-full">
      {message.error ? (
        <p className="text-[15px] text-rose-600 dark:text-rose-400">{message.error}</p>
      ) : (
        <>
          {showTrace && <ProcessTrace steps={message.trace ?? []} done={!message.isStreaming} />}

          {message.text && (
            <div className="relative">
              <Markdown text={message.text} />
              {message.isStreaming && (
                <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-stone-400 align-middle" />
              )}
            </div>
          )}

          {hasVisual && (
            <p className="mt-2 text-sm italic text-stone-500 dark:text-stone-400">
              Updated the {hasChart ? 'chart' : 'diagram'} — see canvas
            </p>
          )}

          {!message.isStreaming && (
            <>
              <div className="mt-2 flex items-center gap-0.5">
                <IconButton
                  icon={Copy}
                  label={copied ? 'Copied' : 'Copy'}
                  active={copied}
                  onClick={() => {
                    navigator.clipboard.writeText(message.text)
                    setCopied(true)
                    setTimeout(() => setCopied(false), 1500)
                  }}
                />
                <IconButton icon={RotateCcw} label="Retry" onClick={() => onRetry?.(message.id)} />
              </div>
              <DonutMark size={20} className="mt-3" />
            </>
          )}
        </>
      )}
    </div>
  )
})
