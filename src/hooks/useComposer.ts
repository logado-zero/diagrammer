import { useCallback, useState } from 'react'
import type { AttachmentKind, PendingAttachment } from '../types.ts'
import { mediaTypeFor, readFileAsBase64 } from '../lib/api.ts'

/**
 * What the user has typed and attached but not yet sent.
 *
 * Owning both together is what makes the clearing rules enforceable: the
 * draft and the attachment are armed together and must be disarmed together.
 * They previously weren't — starting a new chat cleared the draft but left the
 * attachment armed, and switching conversations cleared neither, so an image
 * picked in one chat would silently ride along into the next one.
 *
 * Input: none. Output: { draft, setDraft, attachment, attachFile, removeAttachment, clear }.
 */
export function useComposer() {
  const [draft, setDraft] = useState('')
  const [attachment, setAttachment] = useState<PendingAttachment | null>(null)

  /** Frees an image attachment's object URL. Doing this anywhere an attachment is dropped is the whole reason it lives in one place. */
  const revoke = (previous: PendingAttachment | null) => {
    if (previous?.previewUrl) URL.revokeObjectURL(previous.previewUrl)
  }

  /** Input: a picked File + which attach option it came from. Output: none — stashes it, awaiting submit. */
  const attachFile = useCallback(async (file: File, kind: AttachmentKind) => {
    const base64 = await readFileAsBase64(file)
    const next: PendingAttachment = {
      kind,
      name: file.name,
      previewUrl: kind === 'image' ? URL.createObjectURL(file) : undefined,
      base64,
      mediaType: mediaTypeFor(file, kind),
    }
    // Replacing an attachment has to free the one being replaced, or picking
    // two images before sending leaks the first blob for the life of the page.
    setAttachment((prev) => {
      revoke(prev)
      return next
    })
  }, [])

  const removeAttachment = useCallback(() => {
    setAttachment((prev) => {
      revoke(prev)
      return null
    })
  }, [])

  /** Drops both halves — after a send, and on any conversation switch. */
  const clear = useCallback(() => {
    setDraft('')
    removeAttachment()
  }, [removeAttachment])

  return { draft, setDraft, attachment, attachFile, removeAttachment, clear }
}
