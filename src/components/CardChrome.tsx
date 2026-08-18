/**
 * The shell both canvas cards share: the rounded surface, the title bar, and
 * the copy/download actions on the right of it.
 *
 * DiagramCard and ChartCard had this markup twice — same class strings, same
 * aria labels, same PNG/JPG listbox, differing only in the word "diagram" or
 * "chart" and in where the raster came from (see useImageExport).
 */
import type { ReactNode } from 'react'
import { Check, Copy, Download } from 'lucide-react'
import { CLIPBOARD_IMAGE_SUPPORTED, useImageExport } from '../hooks/useImageExport.ts'

/** One 32px round icon button, the only button shape either card header uses. */
const ICON_BUTTON =
  'flex h-8 w-8 items-center justify-center rounded-full text-stone-500 transition hover:bg-stone-100 dark:text-stone-400 dark:hover:bg-stone-700'

/** The popover panel shape shared with the composer's menus. */
const MENU_PANEL =
  'absolute right-0 top-full z-20 mt-1 w-28 overflow-hidden rounded-xl border border-stone-200 bg-white py-1 shadow-lg shadow-black/10 dark:border-stone-700 dark:bg-stone-800 dark:shadow-black/40'

const MENU_ITEM =
  'block w-full px-3.5 py-2 text-left text-sm text-stone-700 hover:bg-stone-100 dark:text-stone-200 dark:hover:bg-stone-700'

interface ExportActionsProps {
  /** Sync or async; null when the visual isn't mounted yet. */
  getDataUrl: (type: 'png' | 'jpeg') => string | null | Promise<string | null>
  /** Filename stem, already slugified. */
  filenameStem: string
  /** The noun used in aria-labels and tooltips: "diagram" or "chart". */
  noun: string
}

/** Copy-image + download-as (PNG/JPG), for a card header. */
export function ExportActions({ getDataUrl, filenameStem, noun }: ExportActionsProps) {
  const { copyState, copyImage, downloadOpen, setDownloadOpen, downloadRef, downloadImage } = useImageExport(
    getDataUrl,
    filenameStem,
  )

  return (
    <div className="flex shrink-0 items-center gap-0.5">
      {CLIPBOARD_IMAGE_SUPPORTED && (
        <button
          type="button"
          onClick={() => void copyImage()}
          aria-label={`Copy ${noun} image`}
          title={copyState === 'error' ? 'Copy failed' : `Copy ${noun} image`}
          className={ICON_BUTTON}
        >
          {copyState === 'copied' ? <Check size={15} className="text-teal-500 dark:text-teal-400" /> : <Copy size={15} />}
        </button>
      )}
      <div className="relative" ref={downloadRef}>
        <button
          type="button"
          onClick={() => setDownloadOpen(!downloadOpen)}
          aria-haspopup="listbox"
          aria-expanded={downloadOpen}
          aria-label={`Download ${noun} image`}
          title={`Download ${noun} image`}
          className={ICON_BUTTON}
        >
          <Download size={15} />
        </button>
        {downloadOpen && (
          <ul role="listbox" aria-label="Download format" className={MENU_PANEL}>
            <li role="option">
              <button type="button" onClick={() => void downloadImage('png')} className={MENU_ITEM}>
                PNG
              </button>
            </li>
            <li role="option">
              <button type="button" onClick={() => void downloadImage('jpeg')} className={MENU_ITEM}>
                JPG
              </button>
            </li>
          </ul>
        )}
      </div>
    </div>
  )
}

/**
 * Input: the card title, whatever goes in the header's right-hand side, and
 * the card body. Output: the framed card.
 */
export function CardChrome({ title, actions, children }: { title: string; actions?: ReactNode; children: ReactNode }) {
  return (
    <div className="flex h-full w-full flex-col overflow-hidden rounded-2xl border border-stone-200/70 bg-white shadow-[0_1px_2px_rgba(28,25,23,0.04),0_10px_28px_-14px_rgba(28,25,23,0.18)] dark:border-stone-700 dark:bg-stone-900 dark:shadow-none">
      <div className="flex items-center justify-between gap-2 border-b border-stone-200 px-4 py-2.5 text-sm font-medium text-stone-700 dark:border-stone-700 dark:text-stone-200">
        <span className="truncate">{title}</span>
        {actions}
      </div>
      {children}
    </div>
  )
}
