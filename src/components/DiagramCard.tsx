/** Renders a render_diagram tool payload as a Mermaid flowchart, theme-aware via useIsDark. */
import { memo, useCallback, useEffect, useRef, useState } from 'react'
import mermaid from 'mermaid'
import type { RenderDiagramInput } from '../types.ts'
import { cardBackground, CARD_SURFACE, CATEGORICAL_DARK, CATEGORICAL_LIGHT } from '../lib/palette.ts'
import { useIsDark } from '../lib/theme.ts'
import { slugify } from '../lib/slugify.ts'
import { CardChrome, ExportActions } from './CardChrome.tsx'

/**
 * Excalidraw-style palette (Open Color values, the palette Excalidraw
 * itself is built on), used only in hand-drawn mode: a very light tinted
 * fill, a mid-saturation stroke of the same hue, and high-contrast ink.
 *
 * The fill/stroke split matters more than it looks, because of how the two
 * libraries interact — both facts below were read out of the rendered DOM,
 * not assumed:
 *
 * 1. Mermaid renders handDrawn through rough.js, which paints the fill as
 *    *hachure* (diagonal pen strokes) rather than a flat color, and
 *    `fillStyle: "hachure"` is hardcoded in mermaid's `userNodeOverrides`
 *    with no config option to change it. Hachure is also Excalidraw's own
 *    default fill style, so this is the right look to lean into, not fight.
 * 2. A `classDef` compiles to CSS with `!important` on *every* `<path>` in
 *    the node — including the hachure path rough.js drew. So the hachure
 *    lines end up painted in the classDef's **stroke** color, not its fill,
 *    and the fill shows through underneath as a flat wash.
 *
 * Net effect per node: flat light `fill` + pen strokes and outline in
 * `stroke`. That means `stroke` must be mid-saturation — a near-black
 * stroke (the obvious choice for a "process" box) turns the whole node
 * into a dense black scribble, which is exactly how the first attempt at
 * this failed.
 */
const SKETCH = {
  light: {
    green: { fill: '#2f9e44', ink: '#ffffff' },
    red: { fill: '#e8564f', ink: '#ffffff' },
    yellow: { fill: '#e8a33d', ink: '#1a1a1a' },
    blue: { fill: '#4098d7', ink: '#ffffff' },
    neutral: { fill: '#dee2e6', ink: '#1e1e1e' },
  },
  dark: {
    green: { fill: '#2f9e44', ink: '#ffffff' },
    red: { fill: '#e8564f', ink: '#ffffff' },
    yellow: { fill: '#e8a33d', ink: '#1a1a1a' },
    blue: { fill: '#4098d7', ink: '#ffffff' },
    neutral: { fill: '#57534e', ink: '#f5f5f4' },
  },
}

/**
 * Wider than rough.js's 5.2px `hachureGap`, which is what turns the hand-drawn fill into a solid
 * block of color instead of visible diagonal stripes.
 *
 * rough.js has no solid-fill option here — mermaid hardcodes `fillStyle: "hachure"` — so the fill
 * is always a set of parallel pen strokes. But a classDef's `stroke-width` lands on the hachure
 * path with `!important` (the blanket `path` rule described above), overriding rough's own 4px
 * `fillWeight`. Push it past the gap between strokes and adjacent lines overlap into continuous
 * color. The same width also thickens the outline path, but since hand-drawn nodes use one color
 * for both, that just reads as a slightly soft, hand-inked edge.
 *
 * Keep this comfortably above 5.2 and never exactly `4`, which is the value rough writes as the
 * hachure path's own attribute.
 */
const SKETCH_STROKE_WIDTH = '7px'

/**
 * Builds a fixed classDef header for the five node kinds the flowchart
 * subagent tags nodes with (:::start/:::endNode/:::process/:::decision/
 * :::io — "endNode" not "end", since bare `end` is a reserved Mermaid
 * keyword that closes subgraph blocks and breaks parsing if used as a
 * class name, confirmed by an actual failed render, not a guess) —
 * appended after the subagent's own Mermaid body so it always wins,
 * without having to care where a YAML frontmatter block (handDrawn look)
 * ends. Recomputed per theme so a toggle swaps colors instantly — same
 * principle as ChartCard's color injection, just string concatenation
 * instead of object merging since Mermaid has no equivalent of ECharts'
 * `option.color` hook.
 *
 * Two color sets, because the two looks need opposite things: classic
 * uses the app's validated categorical palette as solid fills with white
 * text (mirroring the old FlowNode.tsx shape-per-kind intent —
 * start=green, end=red, decision=amber, io=blue), while hand-drawn uses
 * SKETCH above, for the reason documented there.
 */
function buildClassDefHeader(isDark: boolean, handDrawn: boolean): string {
  if (handDrawn) {
    const s = isDark ? SKETCH.dark : SKETCH.light
    const w = SKETCH_STROKE_WIDTH
    // fill and stroke are deliberately the same color per kind: the hachure strokes *are* the
    // fill here (see SKETCH_STROKE_WIDTH), so a separate border color would repaint the whole
    // interior in it rather than outlining the node.
    const kind = (name: string, c: { fill: string; ink: string }) =>
      `classDef ${name} fill:${c.fill},stroke:${c.fill},stroke-width:${w},color:${c.ink}`
    return [
      kind('start', s.green),
      kind('endNode', s.red),
      kind('process', s.neutral),
      kind('decision', s.yellow),
      kind('io', s.blue),
    ].join('\n')
  }
  const c = isDark ? CATEGORICAL_DARK : CATEGORICAL_LIGHT
  const surface = isDark ? CARD_SURFACE.dark : CARD_SURFACE.light
  const [blue, , , yellow, , green, , red] = c
  return [
    `classDef start fill:${green},stroke:${green},color:#ffffff`,
    `classDef endNode fill:${red},stroke:${red},color:#ffffff`,
    `classDef process fill:${surface.fill},stroke:${surface.border},color:${surface.ink}`,
    `classDef decision fill:${yellow},stroke:${yellow},color:#1a1a1a`,
    `classDef io fill:${blue},stroke:${blue},color:#ffffff`,
  ].join('\n')
}

/**
 * Picks readable label ink for an arbitrary background, using the same two
 * values the palettes above already use. Relative luminance (WCAG's
 * coefficients, skipping the sRGB linearization — the extra precision doesn't
 * change which side of the threshold anything lands on at this contrast gap).
 * Input: a #rgb or #rrggbb string. Output: near-black or white.
 */
function inkFor(hex: string): string {
  const h = hex.slice(1)
  const full = h.length === 3 ? [...h].map((c) => c + c).join('') : h
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16) / 255)
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.55 ? '#1a1a1a' : '#ffffff'
}

/**
 * Per-node color overrides, appended after buildClassDefHeader's five kind
 * classes so CSS source order lets them win — a node keeps its `:::kind` class
 * and gains a second one. Verified in a real render, both looks: the node ends
 * up `class="node default process ncolor0"` and every path takes the override's
 * fill.
 *
 * Built here rather than server-side because the two looks need different
 * declarations from the same color: hand-drawn has to repeat SKETCH_STROKE_WIDTH
 * so rough.js's hachure strokes overlap into solid color (see that constant),
 * while classic must keep a hairline border. The hand-drawn toggle is local
 * state, so only the client can know which to emit.
 *
 * Input: {nodeId: hex} from the server (see server/agent.py's _node_colors) and
 * the current look. Output: Mermaid lines, or '' when there are no overrides.
 */
function buildColorOverrides(colors: Record<string, string> | undefined, handDrawn: boolean): string {
  const entries = Object.entries(colors ?? {})
  if (entries.length === 0) return ''
  const width = handDrawn ? `,stroke-width:${SKETCH_STROKE_WIDTH}` : ''
  return entries
    .flatMap(([id, hex], i) => [
      `classDef ncolor${i} fill:${hex},stroke:${hex}${width},color:${inkFor(hex)}`,
      `class ${id} ncolor${i}`,
    ])
    .join('\n')
}

/**
 * Strips a leading YAML frontmatter block (`---\n...\n---\n`) from a
 * Mermaid source string. Safe to do unconditionally — the flowchart
 * subagent only ever adds this frontmatter to set `look: handDrawn`
 * (server/subagents.py), never any other config, so removing it just
 * clears whatever look decision the server made, letting the client-side
 * toggle below be the sole source of truth.
 */
function stripLookFrontmatter(source: string): string {
  return source.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/, '')
}

/**
 * Rasterizes a rendered SVG element onto a canvas with a solid background
 * (SVGs have no flat background of their own, and JPEG has no alpha) and
 * returns a data URL. Input: the mounted <svg>, an image type, the fill
 * color. Output: a Promise<data URL>.
 */
function svgToDataUrl(svg: SVGSVGElement, type: 'png' | 'jpeg', backgroundColor: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const svgString = new XMLSerializer().serializeToString(svg)
    const svgUrl = URL.createObjectURL(new Blob([svgString], { type: 'image/svg+xml;charset=utf-8' }))
    const img = new Image()
    img.onload = () => {
      const scale = 2
      const canvas = document.createElement('canvas')
      canvas.width = img.naturalWidth * scale
      canvas.height = img.naturalHeight * scale
      const ctx = canvas.getContext('2d')
      URL.revokeObjectURL(svgUrl)
      if (!ctx) {
        reject(new Error('Canvas not supported'))
        return
      }
      ctx.fillStyle = backgroundColor
      ctx.fillRect(0, 0, canvas.width, canvas.height)
      ctx.scale(scale, scale)
      ctx.drawImage(img, 0, 0)
      resolve(canvas.toDataURL(type === 'jpeg' ? 'image/jpeg' : 'image/png'))
    }
    img.onerror = () => {
      URL.revokeObjectURL(svgUrl)
      reject(new Error('Failed to load rendered SVG'))
    }
    img.src = svgUrl
  })
}

/**
 * Input: diagram (RenderDiagramInput — {title, mermaid}, mermaid source
 * already built server-side by the flowchart subagent). Output: a themed
 * Mermaid flowchart card with copy-image and download (PNG/JPG) actions.
 */
// memo'd for the same reason as ChartCard: the canvas renders next to the
// streaming message list, and `diagram` is a stable object, so the default
// shallow compare keeps Mermaid out of the per-token render path.
export const DiagramCard = memo(function DiagramCard({ diagram }: { diagram: RenderDiagramInput }) {
  const isDark = useIsDark()
  const idRef = useRef(`mermaid-diagram-${Math.random().toString(36).slice(2)}`)
  const containerRef = useRef<HTMLDivElement>(null)
  const [svg, setSvg] = useState<string | null>(null)
  const [renderError, setRenderError] = useState<string | null>(null)

  // Hand-drawn look starts as whatever the flowchart subagent decided for this diagram, but is
  // then fully client-controlled — a new diagram (later turn) resets it to that turn's own
  // decision, since manual toggling never touches diagram.mermaid itself.
  const [handDrawn, setHandDrawn] = useState(() => /look:\s*handDrawn/.test(diagram.mermaid))
  useEffect(() => {
    setHandDrawn(/look:\s*handDrawn/.test(diagram.mermaid))
  }, [diagram.mermaid])

  useEffect(() => {
    let cancelled = false
    mermaid.initialize({
      startOnLoad: false,
      theme: isDark ? 'dark' : 'default',
      themeVariables: {
        // A handwriting face is as much of the Excalidraw look as the sketchy strokes are.
        // ponytail: system fonts only, no bundled webfont — Segoe Print/Bradley Hand/Comic Sans
        // cover Windows and macOS, and the generic `cursive` keyword catches the rest. Ship an
        // actual Excalifont/Virgil woff2 in public/ only if the fallback proves ugly somewhere.
        fontFamily: handDrawn
          ? "'Segoe Print', 'Bradley Hand', 'Comic Sans MS', 'Comic Neue', cursive"
          : 'ui-sans-serif, system-ui, sans-serif',
      },
      // Without a fixed seed mermaid re-randomizes every sketch stroke on each render, so a theme
      // toggle or a resize visibly reshuffles the whole diagram. Any constant does; 42 is arbitrary.
      handDrawnSeed: 42,
      // Mermaid defaults to <foreignObject>-based HTML labels, which taints any canvas the
      // rendered SVG is later drawn onto (confirmed live: toDataURL() throws "Tainted canvases
      // may not be exported" for the Copy/Download actions below) — plain SVG <text> labels don't.
      htmlLabels: false,
    })
    // The handDrawn toggle owns the frontmatter outright: strip whatever the subagent decided,
    // then re-add it only if the switch is on. Mermaid's per-diagram frontmatter config overrides
    // the site default (`look` is never set in the initialize() call above, so it defaults to
    // 'classic'), so this always wins regardless of what the server originally sent.
    const body = stripLookFrontmatter(diagram.mermaid)
    const frontmatter = handDrawn ? '---\nconfig:\n  look: handDrawn\n---\n' : ''
    const source = [
      `${frontmatter}${body}`,
      buildClassDefHeader(isDark, handDrawn),
      buildColorOverrides(diagram.colors, handDrawn),
    ]
      .filter(Boolean)
      .join('\n')
    mermaid
      .render(idRef.current, source)
      .then(({ svg: rendered }) => {
        if (!cancelled) {
          setSvg(rendered)
          setRenderError(null)
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setSvg(null)
          setRenderError(err instanceof Error ? err.message : String(err))
        }
      })
    return () => {
      cancelled = true
    }
  }, [diagram.mermaid, diagram.colors, isDark, handDrawn])

  // Unlike ECharts, Mermaid gives us a live <svg> rather than a raster, so
  // this is the half that genuinely differs from ChartCard.
  const getDataUrl = useCallback(
    (type: 'png' | 'jpeg') => {
      const svgEl = containerRef.current?.querySelector('svg')
      if (!svgEl) return null
      return svgToDataUrl(svgEl, type, cardBackground(isDark))
    },
    [isDark],
  )

  return (
    <CardChrome
      title={diagram.title}
      actions={
        svg && (
          <div className="flex shrink-0 items-center gap-2">
            <button
              type="button"
              role="switch"
              aria-checked={handDrawn}
              aria-label="Hand-drawn style"
              title="Hand-drawn style"
              onClick={() => setHandDrawn((v) => !v)}
              className="flex items-center gap-2 rounded-full py-1 pl-2 pr-1 text-xs font-normal text-stone-500 transition hover:bg-stone-100 dark:text-stone-400 dark:hover:bg-stone-800"
            >
              Hand-drawn
              <span
                className={`relative inline-block h-4 w-7 shrink-0 rounded-full transition-colors ${
                  handDrawn ? 'bg-teal-500' : 'bg-stone-300 dark:bg-stone-600'
                }`}
              >
                {/* left-0.5 pins the thumb to the track's own left edge — without an explicit
                    `left` an absolutely-positioned child falls back to its static position, which
                    inside a button is horizontally centered, so the translate pushed it out. */}
                <span
                  className={`absolute left-0.5 top-0.5 h-3 w-3 rounded-full bg-white shadow-sm transition-transform ${
                    handDrawn ? 'translate-x-3' : 'translate-x-0'
                  }`}
                />
              </span>
            </button>
            <ExportActions
              getDataUrl={getDataUrl}
              filenameStem={slugify(diagram.title, 'diagram')}
              noun="diagram"
            />
          </div>
        )
      }
    >
      <div className="min-h-0 w-full flex-1 overflow-auto p-4">
        {renderError ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-center text-sm text-stone-500 dark:text-stone-400">
            <p>Couldn't render this diagram.</p>
            <details className="max-w-full text-left text-xs">
              <summary className="cursor-pointer select-none text-stone-400 dark:text-stone-500">
                Show details
              </summary>
              <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded-lg bg-stone-100 p-2 text-stone-600 dark:bg-stone-800 dark:text-stone-300">
                {renderError}
                {'\n\n'}
                {diagram.mermaid}
              </pre>
            </details>
          </div>
        ) : (
          <div
            ref={containerRef}
            className="flex h-full w-full items-center justify-center [&_svg]:max-h-full [&_svg]:max-w-full"
            dangerouslySetInnerHTML={{ __html: svg ?? '' }}
          />
        )}
      </div>
    </CardChrome>
  )
})
