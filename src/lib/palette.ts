// Validated categorical palette (fixed order — never cycle/reassign by rank).
// See the dataviz skill's references/palette.md for the source and validation.
export const CATEGORICAL_LIGHT = [
  '#2a78d6', // blue
  '#eb6834', // orange
  '#1baf7a', // aqua
  '#eda100', // yellow
  '#e87ba4', // magenta
  '#008300', // green
  '#4a3aa7', // violet
  '#e34948', // red
]

export const CATEGORICAL_DARK = [
  '#3987e5',
  '#d95926',
  '#199e70',
  '#c98500',
  '#d55181',
  '#008300',
  '#9085e9',
  '#e66767',
]

// Sequential (magnitude) ramp — one hue, light->dark, for heatmaps/visualMap/
// gauge zones. Stops from the dataviz skill's validated blue 100->700 ramp,
// clipped to each mode's usable band (light: no lighter than step 250; dark:
// no darker than step 600, so darker still reads as "less" against the dark
// surface, brighter as "more").
export const SEQUENTIAL_LIGHT = ['#86b6ef', '#5598e7', '#2a78d6', '#1c5cab', '#104281']
export const SEQUENTIAL_DARK = ['#184f95', '#256abf', '#3987e5', '#6da7ec', '#9ec5f4']

// Fixed status pair (mode-invariant) — reserved for direction/state encodings
// (e.g. candlestick up/down), never used as general categorical series colors.
export const STATUS_GOOD = '#0ca30c'
export const STATUS_CRITICAL = '#d03b3b'


/**
 * The card's own Tailwind surface classes (bg-white / dark:bg-stone-900) as
 * hex, for the places CSS can't reach: the flat background baked into an
 * exported PNG/JPG (an SVG has no background of its own, and JPEG has no
 * alpha), and Mermaid's "process" node fill, so process nodes blend into the
 * card and only their border stands out.
 *
 * Both cards had their own copy of this, in two different shapes, mirroring
 * one set of Tailwind classes.
 */
export const CARD_SURFACE = {
  light: { fill: '#ffffff', border: '#d4d4d4', ink: '#292524' },
  dark: { fill: '#1c1917', border: '#57534e', ink: '#f5f5f4' },
} as const

/** Input: whether dark mode is on. Output: that theme's card background hex. */
export function cardBackground(isDark: boolean): string {
  return isDark ? CARD_SURFACE.dark.fill : CARD_SURFACE.light.fill
}
