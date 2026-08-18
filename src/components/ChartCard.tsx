/** Renders a render_chart tool payload as an Apache ECharts chart, theme-aware via useIsDark. */
import { memo, useRef, useState } from 'react'
import ReactECharts from 'echarts-for-react'
import { Check, Copy, Download } from 'lucide-react'
import type { RenderChartInput } from '../types.ts'
import {
  CATEGORICAL_DARK,
  CATEGORICAL_LIGHT,
  SEQUENTIAL_DARK,
  SEQUENTIAL_LIGHT,
  STATUS_CRITICAL,
  STATUS_GOOD,
} from '../lib/palette.ts'
import { useIsDark } from '../lib/theme.ts'
import { useDismissablePopover } from '../lib/useDismissablePopover.ts'
import { slugify } from '../lib/slugify.ts'

// Matches the card's own Tailwind surface classes (bg-white / dark:bg-stone-900),
// used as the flat background for exported images since the on-screen option
// itself stays transparent to sit on that surface.
const CARD_SURFACE = { light: '#ffffff', dark: '#1c1917' }

const CLIPBOARD_IMAGE_SUPPORTED =
  typeof navigator !== 'undefined' && !!navigator.clipboard?.write && typeof ClipboardItem !== 'undefined'

/**
 * Layers the app's validated palette (src/lib/palette.ts) onto the
 * subagent's ECharts option, chosen by the option's own structure rather
 * than a field the model has to self-report:
 *  - default: the 8-hue categorical array (bar/line/pie/radar/funnel/... —
 *    anything that consumes ECharts' global `color` list).
 *  - a `visualMap` present (heatmap/calendar): the sequential ramp.
 *  - a gauge series with no zone colors set: sequential ramp as axis zones.
 *  - a candlestick series with no item colors set: the fixed good/critical
 *    status pair (up/down is a direction encoding, not identity).
 */
function buildThemedOption(rawOption: Record<string, unknown>, isDark: boolean) {
  const categorical = isDark ? CATEGORICAL_DARK : CATEGORICAL_LIGHT
  const sequential = isDark ? SEQUENTIAL_DARK : SEQUENTIAL_LIGHT
  const option: any = { backgroundColor: 'transparent', ...rawOption, color: categorical }

  if (option.visualMap && !option.visualMap.inRange?.color) {
    option.visualMap = { ...option.visualMap, inRange: { ...option.visualMap.inRange, color: sequential } }
  }

  const seriesList = Array.isArray(option.series) ? option.series : option.series ? [option.series] : []
  if (seriesList.length > 0) {
    const styled = seriesList.map((s: any) => {
      if (s.type === 'gauge' && !s.axisLine?.lineStyle?.color) {
        return {
          ...s,
          axisLine: {
            ...s.axisLine,
            lineStyle: {
              ...s.axisLine?.lineStyle,
              color: sequential.map((c, i) => [(i + 1) / sequential.length, c]),
            },
          },
        }
      }
      if (s.type === 'candlestick' && !s.itemStyle?.color) {
        return {
          ...s,
          itemStyle: {
            ...s.itemStyle,
            color: STATUS_GOOD,
            color0: STATUS_CRITICAL,
            borderColor: STATUS_GOOD,
            borderColor0: STATUS_CRITICAL,
          },
        }
      }
      return s
    })
    option.series = Array.isArray(option.series) ? styled : styled[0]
  }

  return option
}

/**
 * Input: chart (RenderChartInput — {title, option}, option already built
 * server-side by the chart subagent). Output: a themed ECharts card with
 * copy-image and download (PNG/JPG) actions.
 */
// memo'd because the canvas sits alongside the streaming message list: without
// it, every text token re-runs buildThemedOption() and pushes a fresh option
// into ECharts. `chart` is a stable object (App.tsx appends the payload once
// and never mutates it), so the default shallow compare is enough.
export const ChartCard = memo(function ChartCard({ chart }: { chart: RenderChartInput }) {
  const isDark = useIsDark()
  const option = buildThemedOption(chart.option, isDark)
  const chartRef = useRef<ReactECharts>(null)
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'error'>('idle')
  const [downloadOpen, setDownloadOpen] = useState(false)
  const downloadRef = useDismissablePopover<HTMLDivElement>(downloadOpen, () => setDownloadOpen(false))

  function getDataUrl(type: 'png' | 'jpeg') {
    const instance = chartRef.current?.getEchartsInstance()
    if (!instance) return null
    return instance.getDataURL({
      type,
      pixelRatio: 2,
      backgroundColor: isDark ? CARD_SURFACE.dark : CARD_SURFACE.light,
    })
  }

  async function copyImage() {
    const url = getDataUrl('png')
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
  }

  function downloadImage(type: 'png' | 'jpeg') {
    const url = getDataUrl(type)
    if (!url) return
    const a = document.createElement('a')
    a.href = url
    a.download = `${slugify(chart.title, 'chart')}.${type === 'jpeg' ? 'jpg' : 'png'}`
    a.click()
    setDownloadOpen(false)
  }

  return (
    <div className="flex h-full w-full flex-col overflow-hidden rounded-2xl border border-stone-200/70 bg-white shadow-[0_1px_2px_rgba(28,25,23,0.04),0_10px_28px_-14px_rgba(28,25,23,0.18)] dark:border-stone-700 dark:bg-stone-900 dark:shadow-none">
      <div className="flex items-center justify-between gap-2 border-b border-stone-200 px-4 py-2 text-sm font-medium text-stone-700 dark:border-stone-700 dark:text-stone-200">
        <span className="truncate">{chart.title}</span>
        <div className="flex shrink-0 items-center gap-0.5">
          {CLIPBOARD_IMAGE_SUPPORTED && (
            <button
              type="button"
              onClick={copyImage}
              aria-label="Copy chart image"
              title={copyState === 'error' ? 'Copy failed' : 'Copy chart image'}
              className="flex h-8 w-8 items-center justify-center rounded-full text-stone-500 transition hover:bg-stone-100 dark:text-stone-400 dark:hover:bg-stone-700"
            >
              {copyState === 'copied' ? (
                <Check size={15} className="text-teal-500 dark:text-teal-400" />
              ) : (
                <Copy size={15} />
              )}
            </button>
          )}
          <div className="relative" ref={downloadRef}>
            <button
              type="button"
              onClick={() => setDownloadOpen((open) => !open)}
              aria-haspopup="listbox"
              aria-expanded={downloadOpen}
              aria-label="Download chart image"
              title="Download chart image"
              className="flex h-8 w-8 items-center justify-center rounded-full text-stone-500 transition hover:bg-stone-100 dark:text-stone-400 dark:hover:bg-stone-700"
            >
              <Download size={15} />
            </button>
            {downloadOpen && (
              <ul
                role="listbox"
                aria-label="Download format"
                className="absolute right-0 top-full z-20 mt-1 w-28 overflow-hidden rounded-xl border border-stone-200 bg-white py-1 shadow-lg shadow-black/10 dark:border-stone-700 dark:bg-stone-800 dark:shadow-black/40"
              >
                <li role="option">
                  <button
                    type="button"
                    onClick={() => downloadImage('png')}
                    className="block w-full px-3.5 py-2 text-left text-sm text-stone-700 hover:bg-stone-100 dark:text-stone-200 dark:hover:bg-stone-700"
                  >
                    PNG
                  </button>
                </li>
                <li role="option">
                  <button
                    type="button"
                    onClick={() => downloadImage('jpeg')}
                    className="block w-full px-3.5 py-2 text-left text-sm text-stone-700 hover:bg-stone-100 dark:text-stone-200 dark:hover:bg-stone-700"
                  >
                    JPG
                  </button>
                </li>
              </ul>
            )}
          </div>
        </div>
      </div>
      <div className="min-h-0 flex-1 p-3 sm:p-4">
        <ReactECharts
          ref={chartRef}
          option={option}
          theme={isDark ? 'dark' : undefined}
          notMerge
          style={{ height: '100%', width: '100%' }}
        />
      </div>
    </div>
  )
})
