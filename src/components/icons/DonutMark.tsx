interface Segment {
  /** Fraction of the full circle, 0-1. All segments should sum to 1. */
  fraction: number
  fill: string
}

const SEGMENTS: Segment[] = [
  { fraction: 0.54, fill: 'url(#donut-teal)' },
  { fraction: 0.28, fill: 'url(#donut-amber)' },
  { fraction: 0.18, fill: 'url(#donut-white)' },
]

/** Converts a polar (center, radius, angle) coordinate to the cartesian (x, y) point on this SVG's 0-100 viewBox. */
function polarToCartesian(cx: number, cy: number, r: number, angleDeg: number) {
  const a = ((angleDeg - 90) * Math.PI) / 180
  return { x: cx + r * Math.cos(a), y: cy + r * Math.sin(a) }
}

/** Input: center, outer/inner radius, start/end angle. Output: an SVG path `d` string for one ring segment. */
function donutSegmentPath(
  cx: number,
  cy: number,
  rOuter: number,
  rInner: number,
  startAngle: number,
  endAngle: number,
) {
  const startOuter = polarToCartesian(cx, cy, rOuter, endAngle)
  const endOuter = polarToCartesian(cx, cy, rOuter, startAngle)
  const startInner = polarToCartesian(cx, cy, rInner, endAngle)
  const endInner = polarToCartesian(cx, cy, rInner, startAngle)
  const largeArc = endAngle - startAngle <= 180 ? 0 : 1

  return [
    'M', startOuter.x, startOuter.y,
    'A', rOuter, rOuter, 0, largeArc, 0, endOuter.x, endOuter.y,
    'L', endInner.x, endInner.y,
    'A', rInner, rInner, 0, largeArc, 1, startInner.x, startInner.y,
    'Z',
  ].join(' ')
}

/**
 * This app's brand mark: a flat-style donut/pie chart, standing in for the
 * usual sunburst — fitting since the app's job is drawing charts and diagrams.
 */
export function DonutMark({ size = 24, className }: { size?: number; className?: string }) {
  const cx = 50
  const cy = 50
  const rOuter = 44
  const rInner = 22
  const strokeWidth = 5

  let angle = 0
  const paths = SEGMENTS.map((seg, i) => {
    const start = angle
    const end = angle + seg.fraction * 360
    angle = end
    return (
      <path
        key={i}
        d={donutSegmentPath(cx, cy, rOuter, rInner, start, end)}
        fill={seg.fill}
        stroke="#232839"
        strokeWidth={strokeWidth}
        strokeLinejoin="round"
      />
    )
  })

  return (
    <svg
      viewBox="0 0 100 100"
      width={size}
      height={size}
      className={className}
      role="img"
      aria-label="Diagrammer"
    >
      <defs>
        <linearGradient id="donut-teal" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#7CF2D6" />
          <stop offset="100%" stopColor="#2FCBA8" />
        </linearGradient>
        <linearGradient id="donut-amber" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#FFD166" />
          <stop offset="100%" stopColor="#F5A623" />
        </linearGradient>
        <linearGradient id="donut-white" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#FFFFFF" />
          <stop offset="100%" stopColor="#D7DEE8" />
        </linearGradient>
      </defs>
      {paths}
    </svg>
  )
}
