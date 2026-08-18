/** Closes an open popover on an outside click or Escape. Input: open flag + setter. Output: a ref to attach to the popover's outer container. */
import { useEffect, useRef } from 'react'

export function useDismissablePopover<T extends HTMLElement>(open: boolean, onDismiss: () => void) {
  const ref = useRef<T>(null)

  useEffect(() => {
    if (!open) return
    function handlePointerDown(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) onDismiss()
    }
    function handleEscape(e: KeyboardEvent) {
      if (e.key === 'Escape') onDismiss()
    }
    document.addEventListener('mousedown', handlePointerDown)
    document.addEventListener('keydown', handleEscape)
    return () => {
      document.removeEventListener('mousedown', handlePointerDown)
      document.removeEventListener('keydown', handleEscape)
    }
  }, [open, onDismiss])

  return ref
}
