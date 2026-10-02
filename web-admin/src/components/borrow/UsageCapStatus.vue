<template>
  <span v-if="!groups.length" class="cap-empty">不限</span>
  <div v-else class="cap-list">
    <div v-for="group in groups" :key="group.pool" class="cap-group">
      <div v-for="(row, index) in group.rows" :key="row.key" class="cap-row-wrap">
        <el-tooltip :content="row.tip" placement="top" :show-after="280">
          <div class="cap-row" :class="row.tone">
            <span class="cap-pool">{{ index === 0 ? group.label : '' }}</span>
            <span class="cap-period">
              <span v-if="index" class="cap-or">或</span>{{ row.period }}
            </span>
            <span class="cap-track" aria-hidden="true">
              <span
                class="cap-fill"
                :class="{ started: row.usedCents > 0 }"
                :style="{ width: row.width }"
              />
            </span>
            <span class="cap-money">
              {{ row.money }}
              <span v-if="row.reset" class="cap-reset">{{ row.reset }}</span>
            </span>
          </div>
        </el-tooltip>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { formatDurationMs } from '@/utils/time'

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
  reset: string
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

function shortReset(deltaMs: number): string {
  const totalMinutes = Math.max(1, Math.floor(deltaMs / 60_000))
  const days = Math.floor(totalMinutes / (60 * 24))
  const hours = Math.floor((totalMinutes % (60 * 24)) / 60)
  const minutes = totalMinutes % 60
  if (days >= 1) return hours > 0 ? `${days}天${hours}小时后恢复` : `${days}天后恢复`
  if (hours >= 1) return `${hours}小时后恢复`
  return `${minutes}分后恢复`
}

function resetText(iso: string | null | undefined): { short: string; tip: string } {
  if (!iso) return { short: '已满', tip: '已达到上限' }
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return { short: '已满', tip: '已达到上限' }
  const delta = at.getTime() - Date.now()
  if (delta <= 60_000) return { short: '即将恢复', tip: '已达到上限，即将恢复' }
  return {
    short: shortReset(delta),
    tip: `已达到上限，约 ${formatDurationMs(delta)}后恢复`,
  }
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
  const reset = exceeded ? resetText(rule.resets_at) : null
  let status = `已用 ${used} / ${limit}`
  if (reset) status = reset.tip
  else if (pct >= 80) status = `已用 ${Math.round(pct)}%，接近上限`
  return {
    key: `${rule.pool}-${rule.period}-${index}`,
    period: periodShort,
    money: `${used} / ${limit}`,
    reset: reset?.short ?? '',
    width: `${Math.min(100, Math.max(0, pct))}%`,
    tone,
    tip: `${periodLong} · ${pool}，${status}`,
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
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 188px;
  padding: 2px 0;
}

.cap-group {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.cap-row-wrap {
  display: block;
}

.cap-row {
  display: grid;
  grid-template-columns: 34px 62px minmax(36px, 1fr) max-content;
  align-items: center;
  column-gap: 6px;
  min-height: 18px;
  width: 100%;
  cursor: default;
}

.cap-pool,
.cap-period,
.cap-money {
  font-size: var(--pulse-text-sm);
  line-height: 1;
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
  margin-right: 4px;
  color: var(--el-text-color-placeholder);
}

.cap-track {
  height: 6px;
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
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 2px;
  font-family: var(--pulse-font-mono);
  font-variant-numeric: tabular-nums;
  font-weight: var(--pulse-font-medium);
  color: var(--el-text-color-primary);
}

.cap-reset {
  font-family: var(--pulse-font-sans);
  font-size: var(--pulse-text-xs);
  font-weight: var(--pulse-font-regular);
  line-height: 1;
}

.cap-row.warn .cap-money {
  color: var(--el-color-warning);
}

.cap-row.danger .cap-money {
  color: var(--el-color-danger);
}
</style>
