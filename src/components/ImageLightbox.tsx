/**
 * Full-screen click-to-view for an attached chat image. Mounted via a
 * portal to document.body — ChatView's layout has overflow-hidden/
 * overflow-y-auto ancestors that would clip a plain position:fixed
 * element rendered in place.
 */
import { useEffect } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'

export function ImageLightbox({ src, alt, onClose }: { src: string; alt: string; onClose: () => void }) {
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4"
      onClick={onClose}
    >
      <button
        type="button"
        onClick={onClose}
        aria-label="Close"
        className="absolute right-4 top-4 flex h-9 w-9 items-center justify-center rounded-full bg-white/10 text-white transition hover:bg-white/20"
      >
        <X size={18} />
      </button>
      <img
        src={src}
        alt={alt}
        onClick={(e) => e.stopPropagation()}
        className="max-h-[90vh] max-w-[90vw] rounded-lg object-contain"
      />
    </div>,
    document.body,
  )
}
