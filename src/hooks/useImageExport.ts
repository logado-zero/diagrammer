import { useCallback, useState } from 'react'
import { useDismissablePopover } from '../lib/useDismissablePopover.ts'

/** Whether this browser can put an image on the clipboard at all (Safari/Firefox lag here). */
export const CLIPBOARD_IMAGE_SUPPORTED =
  typeof navigator !== 'undefined' && !!navigator.clipboard?.write && typeof ClipboardItem !== 'undefined'

/**
 * Copy-to-clipboard and download-as-PNG/JPG for a rendered visual.
 *
 * DiagramCard and ChartCard had a copy each — same state, same 1500ms
 * "copied" reset, same anchor-click download, same slugified filename. The
 * only thing that genuinely differed is where the raster comes from: the
 * diagram serializes its mounted <svg> through a canvas, the chart asks
 * ECharts for a data URL. That is the callback.
 *
 * Input: `getDataUrl(type)` (sync or async, null when the visual isn't
 * mounted yet) and the filename stem, already slugified.
 * Output: the state and handlers the card's buttons bind to.
 */
export function useImageExport(
  getDataUrl: (type: 'png' | 'jpeg') => string | null | Promise<string | null>,
  filenameStem: string,
) {
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'error'>('idle')
  const [downloadOpen, setDownloadOpen] = useState(false)
  const downloadRef = useDismissablePopover<HTMLDivElement>(downloadOpen, () => setDownloadOpen(false))

  const copyImage = useCallback(async () => {
    const url = await getDataUrl('png')
    if (!url) return
    try {
      const blob = await (await fetch(url)).blob()
      await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })])
      setCopyState('copied')
    } catch {
      setCopyState('error')
    } finally {
      setTimeout(() => setCopyState('idle'), 1500)
    }
  }, [getDataUrl])

  const downloadImage = useCallback(
    async (type: 'png' | 'jpeg') => {
      const url = await getDataUrl(type)
      if (!url) return
      const a = document.createElement('a')
      a.href = url
      a.download = `${filenameStem}.${type === 'jpeg' ? 'jpg' : 'png'}`
      a.click()
      setDownloadOpen(false)
    },
    [getDataUrl, filenameStem],
  )

  return { copyState, copyImage, downloadOpen, setDownloadOpen, downloadRef, downloadImage }
}
