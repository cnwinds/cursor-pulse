<template>
  <div v-if="hasAny" class="quota-progress-bars">
    <div
      v-for="row in rows"
      :key="row.key"
      class="quota-row"
      :class="[row.tone, { sub: row.sub }]"
    >
      <span class="quota-label" :title="row.title">{{ row.label }}</span>
      <div class="quota-track" :class="{ empty: row.pct == null }">
        <div class="quota-fill" :style="{ width: row.width }" />
      </div>
      <span class="quota-pct">{{ row.text }}</span>
    </div>
  </div>
  <span v-else class="quota-progress-empty">—</span>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  total_pct?: number | null
  auto_pct?: number | null
  api_pct?: number | null
  status?: string | null
}>()

type Tone = 'ok' | 'warn' | 'danger' | 'muted'

interface ProgressRow {
  key: string
  label: string
  title: string
  pct: number | null
  width: string
  text: string
  tone: Tone
  sub: boolean
}

const hasAny = computed(
  () => props.total_pct != null || props.auto_pct != null || props.api_pct != null,
)

function pctNum(v: number | null | undefined): number | null {
  if (v == null) return null
  return Math.min(Math.round(v), 100)
}

function pctText(v: number | null | undefined): string {
  if (v == null) return '—'
  return `${Math.round(v)}%`
}

function pctWidth(v: number | null | undefined): string {
  const n = pctNum(v)
  if (n == null) return '0%'
  return `${n}%`
}

function toneFromPct(v: number | null | undefined, fallback?: string | null): Tone {
  if (v == null) return 'muted'
  if (v >= 100 || fallback === 'exhausted') return 'danger'
  if (v >= 80 || fallback === 'warning') return 'warn'
  return 'ok'
}

const rows = computed<ProgressRow[]>(() => [
  {
    key: 'total',
    label: 'Total',
    title: 'Total',
    pct: pctNum(props.total_pct),
    width: pctWidth(props.total_pct),
    text: pctText(props.total_pct),
    tone: toneFromPct(props.total_pct, props.status),
    sub: false,
  },
  {
    key: 'auto',
    label: 'Auto',
    title: 'Auto + Composer',
    pct: pctNum(props.auto_pct),
    width: pctWidth(props.auto_pct),
    text: pctText(props.auto_pct),
    tone: toneFromPct(props.auto_pct),
    sub: true,
  },
  {
    key: 'api',
    label: 'API',
    title: 'API',
    pct: pctNum(props.api_pct),
    width: pctWidth(props.api_pct),
    text: pctText(props.api_pct),
    tone: toneFromPct(props.api_pct),
    sub: true,
  },
])
</script>

<style scoped>
.quota-progress-bars {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 168px;
}

.quota-row {
  display: grid;
  grid-template-columns: 38px minmax(0, 1fr) 30px;
  align-items: center;
  gap: 6px;
  min-height: 22px;
}

.quota-row.sub {
  grid-template-columns: 38px minmax(0, 1fr) 30px;
  padding-left: 10px;
  min-height: 17px;
  position: relative;
}

.quota-row.sub::before {
  content: '';
  position: absolute;
  left: 3px;
  top: 0;
  bottom: 0;
  width: 2px;
  border-radius: 1px;
  background: color-mix(in srgb, var(--el-border-color) 55%, transparent);
}

.quota-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--el-text-color-primary);
  white-space: nowrap;
  line-height: 1;
}

.quota-row.sub .quota-label {
  font-size: 10px;
  font-weight: 400;
  color: var(--el-text-color-secondary);
}

.quota-track {
  height: 10px;
  border-radius: 999px;
  background: var(--el-fill-color);
  overflow: hidden;
}

.quota-row.sub .quota-track {
  height: 7px;
}

.quota-track.empty {
  opacity: 0.5;
}

.quota-fill {
  height: 100%;
  border-radius: inherit;
  transition: width 0.35s cubic-bezier(0.4, 0, 0.2, 1);
}

.quota-row.ok .quota-fill {
  background: linear-gradient(90deg, #3b9eff 0%, #5b6ef7 100%);
}

.quota-row.warn .quota-fill {
  background: linear-gradient(90deg, #f5b942 0%, #e6a23c 100%);
}

.quota-row.danger .quota-fill {
  background: linear-gradient(90deg, #f78989 0%, #f56c6c 100%);
}

.quota-row.muted .quota-fill {
  background: var(--el-border-color);
}

.quota-pct {
  font-size: 11px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  text-align: right;
  line-height: 1;
  color: var(--el-text-color-primary);
}

.quota-row.sub .quota-pct {
  font-size: 10px;
  font-weight: 500;
  color: var(--el-text-color-secondary);
}

.quota-row.warn .quota-pct {
  color: var(--el-color-warning);
}

.quota-row.danger .quota-pct {
  color: var(--el-color-danger);
}

.quota-progress-empty {
  color: var(--el-text-color-placeholder);
}
</style>
