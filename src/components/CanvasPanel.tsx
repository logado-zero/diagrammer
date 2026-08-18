/**
 * Left-panel canvas shown once a conversation has produced a diagram or
 * chart: renders whichever one is latest across the whole conversation,
 * full height. ChatView.tsx computes `visual`; a follow-up message that
 * revises the diagram/chart (see lib/api.ts's withVisualContext) simply
 * replaces what's shown here, since it's always "the latest one."
 */
import type { RenderChartInput, RenderDiagramInput } from '../types.ts'
import { DiagramCard } from './DiagramCard.tsx'
import { ChartCard } from './ChartCard.tsx'

export type CanvasVisual =
  | { type: 'diagram'; value: RenderDiagramInput }
  | { type: 'chart'; value: RenderChartInput }

/** Input: the latest diagram/chart (CanvasVisual). Output: that card, filling the panel. */
export function CanvasPanel({ visual }: { visual: CanvasVisual }) {
  return (
    <div className="flex h-full min-h-0 flex-col bg-stone-200/60 p-4 lg:p-6 dark:bg-stone-950">
      {visual.type === 'diagram' ? <DiagramCard diagram={visual.value} /> : <ChartCard chart={visual.value} />}
    </div>
  )
}
