export interface CursorPoolBucket {
  spend_usd?: number
  reported_spend_usd?: number
  estimated_spend_usd?: number
  usage_ratio?: number | null
  quota_usd?: number | null
  breakdown_by_model?: Record<string, number>
  tokens_by_model?: Record<string, number>
}

export interface ExternalModelStats {
  total_tokens: number
  event_count: number
}

export interface UsageSummary {
  account_id: string
  period?: string
  primary_metric_value: number
  primary_metric_unit: string
  reported_spend_usd?: number | null
  estimated_included_spend_usd?: number | null
  quota_usage_ratio: number | null
  billing_cycle_start?: string | null
  billing_cycle_end?: string | null
  quota_denominator_snapshot?: number | null
  cycle_metric_value?: number | null
  cycle_quota_usage_ratio?: number | null
  breakdown_by_model?: Record<string, number> | null
  cursor_pools?: {
    auto_composer?: CursorPoolBucket
    api?: CursorPoolBucket
    third_party?: CursorPoolBucket
  } | null
  external_models?: Record<string, ExternalModelStats> | null
}

export function formatSpend(value?: number | null) {
  if (value == null) return '—'
  return `$${Number(value).toFixed(2)}`
}

export function formatAmount(value: number, unit?: string | null) {
  const normalized = (unit || 'usd').toUpperCase()
  if (normalized === 'USD') return `$${Number(value).toFixed(2)}`
  if (normalized === 'CNY') return `¥${Number(value).toFixed(2)}`
  return `${Number(value).toFixed(2)} ${normalized}`
}

export const KIND_FAMILY_LABELS: Record<string, string> = {
  included: '套餐',
  user_api_key: 'BYOK',
  excluded: '未计费',
  unknown: '未知',
}

export function kindFamilyLabel(family?: string | null): string {
  const key = (family || '').trim()
  if (!key) return KIND_FAMILY_LABELS.unknown
  return KIND_FAMILY_LABELS[key] || KIND_FAMILY_LABELS.unknown
}

export function formatTokens(value: number) {
  const n = Number(value)
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return `${n}`
}

/** 始终以百万 tokens（M）展示，两位小数，便于对照按 M 计价 */
export function formatTokensM(value?: number | null) {
  const n = Number(value) || 0
  return `${(n / 1_000_000).toFixed(2)}M`
}

/** 表格内紧凑展示，如 1.2M / 34.5K */
export function formatCompactTokens(value?: number | null) {
  if (value == null || Number(value) <= 0) return ''
  const n = Number(value)
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return `${Math.round(n)}`
}

export function autoComposerSpend(summary?: UsageSummary) {
  return summary?.cursor_pools?.auto_composer?.spend_usd ?? 0
}

export function premiumApiSpend(summary?: UsageSummary) {
  return summary?.cursor_pools?.api?.spend_usd ?? 0
}

export function thirdPartySpend(summary?: UsageSummary) {
  return summary?.cursor_pools?.third_party?.spend_usd ?? 0
}

export function poolModelBreakdown(
  summary: UsageSummary | undefined,
  pool: 'auto_composer' | 'api' | 'third_party',
) {
  const bucket = summary?.cursor_pools?.[pool]
  const breakdown = bucket?.breakdown_by_model || {}
  const tokens = bucket?.tokens_by_model || {}
  const names = new Set([...Object.keys(breakdown), ...Object.keys(tokens)])
  if (!names.size) return []
  return [...names]
    .map((name) => ({
      name,
      value: Number(breakdown[name] || 0),
      tokens: Number(tokens[name] || 0) || null,
    }))
    // Keep rows with spend or tokens — included API models can be $0 after estimate miss.
    .filter((row) => row.value > 0 || (row.tokens || 0) > 0)
    .sort((a, b) => b.value - a.value || (b.tokens || 0) - (a.tokens || 0) || a.name.localeCompare(b.name))
}

export function poolHasTokens(
  summary: UsageSummary | undefined,
  pool: 'auto_composer' | 'api' | 'third_party',
) {
  return poolModelBreakdown(summary, pool).some((m) => (m.tokens || 0) > 0)
}

export function poolTotalTokens(
  summary: UsageSummary | undefined,
  pool: 'auto_composer' | 'api' | 'third_party',
) {
  return poolModelBreakdown(summary, pool).reduce((sum, m) => sum + (m.tokens || 0), 0)
}

export function hasExternalModels(summary?: UsageSummary) {
  return Object.keys(summary?.external_models || {}).length > 0
}

export function externalModelBreakdown(summary?: UsageSummary) {
  const models = summary?.external_models || {}
  return Object.entries(models)
    .filter(([, stats]) => Number(stats.total_tokens) > 0)
    .sort((a, b) => Number(b[1].total_tokens) - Number(a[1].total_tokens))
    .map(([name, stats]) => ({ name, tokens: Number(stats.total_tokens) }))
}

export type ByokModelRow = {
  name: string
  tokens: number | null
  value: number | null
}

/** Merge leftover third_party spend rows with BYOK `external_models` (tokens only). */
export function byokModelRows(summary?: UsageSummary): ByokModelRow[] {
  const byName = new Map<string, ByokModelRow>()
  for (const m of poolModelBreakdown(summary, 'third_party')) {
    byName.set(m.name, {
      name: m.name,
      tokens: m.tokens,
      value: m.value > 0 ? m.value : null,
    })
  }
  for (const m of externalModelBreakdown(summary)) {
    const existing = byName.get(m.name)
    if (existing) {
      existing.tokens = (existing.tokens || 0) + m.tokens
    } else {
      byName.set(m.name, { name: m.name, tokens: m.tokens, value: null })
    }
  }
  return [...byName.values()].sort(
    (a, b) =>
      (b.value || 0) - (a.value || 0) ||
      (b.tokens || 0) - (a.tokens || 0) ||
      a.name.localeCompare(b.name),
  )
}

export function hasByokUsage(summary?: UsageSummary) {
  return (
    thirdPartySpend(summary) > 0 ||
    hasExternalModels(summary) ||
    poolHasTokens(summary, 'third_party')
  )
}

export function byokHasTokens(summary?: UsageSummary) {
  return byokModelRows(summary).some((m) => (m.tokens || 0) > 0)
}

export function byokTotalTokens(summary?: UsageSummary) {
  return byokModelRows(summary).reduce((sum, m) => sum + (m.tokens || 0), 0)
}

export function modelBreakdown(summary?: UsageSummary) {
  if (!summary?.breakdown_by_model) return []
  return Object.entries(summary.breakdown_by_model)
    .filter(([, amount]) => Number(amount) > 0)
    .sort((a, b) => Number(b[1]) - Number(a[1]))
    .map(([name, value]) => ({ name, value: Number(value) }))
}

export function periodDateRange(periodStr: string): [string, string] {
  const [year, month] = periodStr.split('-').map(Number)
  const lastDay = new Date(year, month, 0).getDate()
  return [`${periodStr}-01`, `${periodStr}-${String(lastDay).padStart(2, '0')}`]
}

export function billingCycleDateRange(cycleStart?: string | null, cycleEnd?: string | null): [string, string] | null {
  if (!cycleStart || !cycleEnd) return null
  // Parse as UTC calendar dates to avoid local-TZ shifting the exclusive cycle_end.
  const end = new Date(`${cycleEnd}T00:00:00Z`)
  if (Number.isNaN(end.getTime())) return null
  end.setUTCDate(end.getUTCDate() - 1)
  const endStr = end.toISOString().slice(0, 10)
  return [cycleStart, endStr]
}

/** Calendar months that may hold UsageSummary rows for a board snapshot cycle. */
export function periodsForBoardCycle(
  cycleStart?: string | null,
  cycleEnd?: string | null,
): string[] {
  const range = billingCycleDateRange(cycleStart, cycleEnd)
  if (!range) return []
  const [start, end] = range
  const periods: string[] = []
  const cursor = new Date(`${start}T00:00:00Z`)
  const last = new Date(`${end}T00:00:00Z`)
  if (Number.isNaN(cursor.getTime()) || Number.isNaN(last.getTime())) return []
  while (cursor <= last) {
    periods.push(
      `${cursor.getUTCFullYear()}-${String(cursor.getUTCMonth() + 1).padStart(2, '0')}`,
    )
    cursor.setUTCMonth(cursor.getUTCMonth() + 1)
  }
  return periods
}

/**
 * Pick which UsageSummary to show on a quota-board card.
 *
 * `loadSummaries` fetches the union of calendar months for *all* cards, so a
 * previous-month row can arrive for an account that has no row in the current
 * cycle. Reject those — otherwise 「本周期用量明细」 shows e.g. July while the
 * 「明细」 dialog opens on the snapshot cycle (August) and looks empty/wrong.
 */
export function preferSummaryForBoardCycle(
  current: UsageSummary | undefined,
  next: UsageSummary,
  cycleStart: string | null | undefined,
): UsageSummary | undefined {
  if (cycleStart && next.billing_cycle_start !== cycleStart) {
    return current
  }
  if (!current) return next
  // Prefer denser API breakdown, then newer period key.
  const curApi = Object.keys(current.cursor_pools?.api?.breakdown_by_model || {}).length
  const nextApi = Object.keys(next.cursor_pools?.api?.breakdown_by_model || {}).length
  if (nextApi !== curApi) return nextApi > curApi ? next : current
  return (next.period || '') > (current.period || '') ? next : current
}
