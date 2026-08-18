/** Renders a render_chart tool payload as an Apache ECharts chart, theme-aware via useIsDark. */
import { memo, useCallback, useRef } from 'react'
import ReactECharts from 'echarts-for-react'
import type { RenderChartInput } from '../types.ts'
import {
  cardBackground,
  CATEGORICAL_DARK,
  CATEGORICAL_LIGHT,
  SEQUENTIAL_DARK,
  SEQUENTIAL_LIGHT,
  STATUS_CRITICAL,
  STATUS_GOOD,
} from '../lib/palette.ts'
import { useIsDark } from '../lib/theme.ts'
import { slugify } from '../lib/slugify.ts'
import { CardChrome, ExportActions } from './CardChrome.tsx'

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

  // The on-screen option stays transparent so it sits on the card surface;
  // an exported image needs that surface painted in flat.
  const getDataUrl = useCallback(
    (type: 'png' | 'jpeg') => {
      const instance = chartRef.current?.getEchartsInstance()
      if (!instance) return null
      return instance.getDataURL({ type, pixelRatio: 2, backgroundColor: cardBackground(isDark) })
    },
    [isDark],
  )

  return (
    <CardChrome
      title={chart.title}
      actions={
        <ExportActions getDataUrl={getDataUrl} filenameStem={slugify(chart.title, 'chart')} noun="chart" />
      }
    >
      <div className="min-h-0 flex-1 p-3 sm:p-4">
        <ReactECharts
          ref={chartRef}
          option={option}
          theme={isDark ? 'dark' : undefined}
          notMerge
          style={{ height: '100%', width: '100%' }}
        />
      </div>
    </CardChrome>
  )
})
