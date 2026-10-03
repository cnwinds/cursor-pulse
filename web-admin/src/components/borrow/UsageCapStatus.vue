<template>
  <span v-if="!groups.length" class="cap-empty">不限</span>
  <div v-else class="cap-list">
    <div v-for="group in groups" :key="group.pool" class="cap-group">
      <el-tooltip
        v-for="(row, index) in group.rows"
        :key="row.key"
        :content="row.tip"
        placement="top"
        :show-after="280"
      >
        <div class="cap-row" :class="row.tone">
          <span class="cap-label">
            <span v-if="index" class="cap-or">或</span>
            <span v-else class="cap-pool">{{ group.label }}</span>
            <span class="cap-period">{{ row.period }}</span>
          </span>
          <span class="cap-money">{{ row.money }}</span>
          <span class="cap-track" aria-hidden="true">
            <span
              class="cap-fill"
              :class="{ started: row.usedCents > 0 }"
              :style="{ width: row.width }"
            />
          </span>
        </div>
      </el-tooltip>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { formatDurationMs, parseApiDateTime } from '@/utils/time'

export interface UsageCapSnapshot {
  period: string
  pool: string
  limit_cents?: number | null
  cost_usd?: number | null
  used_cents?: number | null
  exceeded?: boolean
  resets_at?: string | null
}

const props = defineProps<{
  rules?: UsageCapSnapshot[] | null
}>()

const PERIOD_SHORT: Record<string, string> = {
  '5h': '5小时',
  week: '7天',
  month: '30天',
}

const PERIOD_LONG: Record<string, string> = {
  '5h': '滚动 5 小时',
  week: '滚动 7 天',
  month: '滚动 30 天',
}

const POOL_LABEL: Record<string, string> = {
  auto: 'Auto',
  api: 'API',
}

const POOL_ORDER = ['auto', 'api']

type Tone = 'ok' | 'warn' | 'danger'

interface CapRow {
  key: string
  period: string
  money: string
  width: string
  tone: Tone
  tip: string
  usedCents: number
}

interface CapGroup {
  pool: string
  label: string
  rows: CapRow[]
}

function limitCentsOf(rule: UsageCapSnapshot): number {
  if (typeof rule.limit_cents === 'number' && rule.limit_cents > 0) return rule.limit_cents
  if (typeof rule.cost_usd === 'number' && rule.cost_usd > 0) return Math.round(rule.cost_usd * 100)
  return 0
}

function usdText(cents: number): string {
  const usd = cents / 100
  if (Number.isInteger(usd)) return `$${usd}`
  return `$${usd.toFixed(2)}`
}

function remainingResetSuffix(iso: string | null | undefined): string {
  if (!iso) return ''
  const at = parseApiDateTime(iso)
  if (!at) return ''
  const delta = at.getTime() - Date.now()
  if (delta <= 60_000) return '，即将重置'
  return `，还剩 ${formatDurationMs(delta)}重置`
}

function toRow(rule: UsageCapSnapshot, index: number): CapRow | null {
  const limitCents = limitCentsOf(rule)
  if (!limitCents) return null
  const usedCents = Math.max(0, rule.used_cents ?? 0)
  const pct = (usedCents / limitCents) * 100
  const exceeded = Boolean(rule.exceeded) || pct >= 100
  const tone: Tone = exceeded ? 'danger' : pct >= 80 ? 'warn' : 'ok'
  const periodShort = PERIOD_SHORT[rule.period] || rule.period
  const periodLong = PERIOD_LONG[rule.period] || rule.period
  const pool = POOL_LABEL[rule.pool] || rule.pool
  const used = usdText(usedCents)
  const limit = usdText(limitCents)
  let status = `已用 ${used} / ${limit}`
  if (exceeded) status = '已达到上限'
  else if (pct >= 80) status = `已用 ${Math.round(pct)}%，接近上限`
  const resetSuffix = remainingResetSuffix(rule.resets_at)
  return {
    key: `${rule.pool}-${rule.period}-${index}`,
    period: periodShort,
    money: `${used}/${limit}`,
    width: `${Math.min(100, Math.max(0, pct))}%`,
    tone,
    tip: `${periodLong} · ${pool}，${status}${resetSuffix}`,
    usedCents,
  }
}

const groups = computed<CapGroup[]>(() => {
  const buckets = new Map<string, CapRow[]>()
  for (const [index, rule] of (props.rules ?? []).entries()) {
    const row = toRow(rule, index)
    if (!row) continue
    const pool = rule.pool || 'auto'
    const list = buckets.get(pool) ?? []
    list.push(row)
    buckets.set(pool, list)
  }
  const pools = [...buckets.keys()].sort((a, b) => {
    const ai = POOL_ORDER.indexOf(a)
    const bi = POOL_ORDER.indexOf(b)
    return (ai === -1 ? POOL_ORDER.length : ai) - (bi === -1 ? POOL_ORDER.length : bi)
  })
  return pools.map((pool) => ({
    pool,
    label: POOL_LABEL[pool] || pool,
    rows: buckets.get(pool) ?? [],
  }))
})
</script>

<style scoped>
.cap-empty {
  color: var(--el-text-color-placeholder);
  font-size: var(--pulse-text-sm);
}

.cap-list {
  display: inline-flex;
  flex-direction: column;
  gap: 6px;
  vertical-align: middle;
}

.cap-group {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.cap-row {
  display: inline-flex;
  flex-direction: column;
  gap: 1px;
  width: max-content;
  cursor: default;
}

.cap-label {
  display: inline-flex;
  align-items: baseline;
  gap: 4px;
  font-size: var(--pulse-text-sm);
  line-height: 1.25;
  white-space: nowrap;
}

.cap-pool {
  font-weight: var(--pulse-font-semibold);
  color: var(--el-text-color-regular);
}

.cap-period {
  color: var(--el-text-color-secondary);
}

.cap-or {
  color: var(--el-text-color-placeholder);
}

.cap-track {
  display: block;
  width: 100%;
  height: 3px;
  margin-top: 2px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--el-text-color-primary) 14%, transparent);
  overflow: hidden;
}

.cap-fill {
  display: block;
  height: 100%;
  border-radius: inherit;
}

.cap-fill.started {
  min-width: 2px;
}

.cap-row.ok .cap-fill {
  background: linear-gradient(90deg, #3b9eff 0%, #5b6ef7 100%);
}

.cap-row.warn .cap-fill {
  background: linear-gradient(90deg, #f5b942 0%, #e6a23c 100%);
}

.cap-row.danger .cap-fill {
  background: linear-gradient(90deg, #f78989 0%, #f56c6c 100%);
}

.cap-money {
  font-family: var(--pulse-font-mono);
  font-size: var(--pulse-text-xs);
  font-variant-numeric: tabular-nums;
  font-weight: var(--pulse-font-medium);
  line-height: 1.25;
  white-space: nowrap;
  color: var(--el-text-color-primary);
}

.cap-row.warn .cap-money {
  color: var(--el-color-warning);
}

.cap-row.danger .cap-money {
  color: var(--el-color-danger);
}
</style>
