/** Cursor 用量图：输入族同色相、按「相对单价」差饱和度；输出用高对比色相。 */

/** 输入 / cache 共用色相（青蓝，接近 pulse 主色族） */
export const USAGE_INPUT_HUE = 192

/** 输出：橙红，与输入族色差大 */
export const USAGE_OUTPUT_HUE = 24

/** 花费折线 */
export const USAGE_COST_COLOR = '#d97706'

function hsl(h: number, s: number, l: number) {
  return `hsl(${h} ${s}% ${l}%)`
}

/** 常规输入：单价高 → 饱和度高 */
export const USAGE_COLOR_INPUT = hsl(USAGE_INPUT_HUE, 72, 38)

/** Cache 读：同 hue，低饱和（相对低价） */
export const USAGE_COLOR_INPUT_CACHE = hsl(USAGE_INPUT_HUE, 32, 52)

/** 输出 */
export const USAGE_COLOR_OUTPUT = hsl(USAGE_OUTPUT_HUE, 88, 48)

export const USAGE_TREND_SERIES_COLORS = [
  USAGE_COLOR_INPUT,
  USAGE_COLOR_OUTPUT,
  USAGE_COLOR_INPUT_CACHE,
  USAGE_COST_COLOR,
] as const

export type UsageKpiTone = 'total' | 'input' | 'output' | 'cache' | 'neutral' | 'cost'

export function usageKpiToneForLabel(label: string): UsageKpiTone {
  if (label === '输入') return 'input'
  if (label === '输出') return 'output'
  if (label === 'Cache Read') return 'cache'
  if (label === '总 Token') return 'total'
  if (label === '估算花费') return 'cost'
  return 'neutral'
}
