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
 * Picks the i-th categorical color for the current theme.
 * Input: a series index + whether dark mode is active.
 * Output: a hex color string; wraps around if there are more series than colors.
 */
export function categoricalColor(index: number, isDark: boolean): string {
  const palette = isDark ? CATEGORICAL_DARK : CATEGORICAL_LIGHT
  return palette[index % palette.length]
}
