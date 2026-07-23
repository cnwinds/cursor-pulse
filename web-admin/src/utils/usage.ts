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

export function apiSpend(summary?: UsageSummary) {
  const premium = premiumApiSpend(summary)
  const thirdParty = thirdPartySpend(summary)
  if (premium > 0 || thirdParty > 0) {
    return premium + thirdParty
  }
  return summary?.primary_metric_value ?? 0
}

export function apiQuotaUsd(summary?: UsageSummary) {
  return summary?.cursor_pools?.api?.quota_usd ?? summary?.quota_denominator_snapshot ?? null
}

export function poolModelBreakdown(
  summary: UsageSummary | undefined,
  pool: 'auto_composer' | 'api' | 'third_party',
) {
  const bucket = summary?.cursor_pools?.[pool]
  const breakdown = bucket?.breakdown_by_model
  if (!breakdown) return []
  const tokens = bucket?.tokens_by_model || {}
  return Object.entries(breakdown)
    .filter(([, amount]) => Number(amount) > 0)
    .sort((a, b) => Number(b[1]) - Number(a[1]))
    .map(([name, value]) => ({
      name,
      value: Number(value),
      tokens: Number(tokens[name] || 0) || null,
    }))
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
  const end = new Date(cycleEnd)
  end.setDate(end.getDate() - 1)
  const endStr = end.toISOString().slice(0, 10)
  return [cycleStart, endStr]
}
