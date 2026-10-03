<template>
  <div v-if="tiers.length" class="tier-bars">
    <div
      v-for="tier in tiers"
      :key="tier.name"
      class="tier-row"
      :class="toneForPct(tier.utilization_pct)"
    >
      <span class="tier-label" :title="tier.name">{{ tier.label || tier.name }}</span>
      <div class="tier-track">
        <div class="tier-fill" :style="{ width: pctWidth(tier.utilization_pct) }" />
      </div>
      <span class="tier-pct">{{ pctText(tier.utilization_pct) }}</span>
    </div>
  </div>
  <span v-else class="tier-empty">—</span>
</template>

<script setup lang="ts">
import { computed } from 'vue'

export interface CodingPlanTierRow {
  name: string
  label?: string
  utilization_pct?: number | null
}

const props = defineProps<{
  tiers?: CodingPlanTierRow[] | null
}>()

const tiers = computed(() => props.tiers ?? [])

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

function toneForPct(v: number | null | undefined): string {
  const n = pctNum(v)
  if (n == null) return 'muted'
  if (n >= 100) return 'danger'
  if (n >= 80) return 'warn'
  return 'ok'
}
</script>

<style scoped>
.tier-bars {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 148px;
}
.tier-row {
  display: grid;
  grid-template-columns: 52px minmax(0, 1fr) max-content;
  align-items: center;
  gap: 6px;
  min-height: 18px;
}
.tier-label {
  font-size: var(--pulse-text-xs);
  font-weight: 500;
  color: var(--el-text-color-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  line-height: 1;
}
.tier-track {
  height: 7px;
  border-radius: 999px;
  background: var(--el-fill-color);
  overflow: hidden;
}
.tier-fill {
  height: 100%;
  border-radius: inherit;
  transition: width 0.35s cubic-bezier(0.4, 0, 0.2, 1);
}
.tier-row.ok .tier-fill {
  background: linear-gradient(90deg, #3b9eff 0%, #5b6ef7 100%);
}
.tier-row.warn .tier-fill {
  background: linear-gradient(90deg, #f5b942 0%, #e6a23c 100%);
}
.tier-row.danger .tier-fill {
  background: linear-gradient(90deg, #f78989 0%, #f56c6c 100%);
}
.tier-row.muted .tier-fill {
  background: var(--el-border-color);
}
.tier-pct {
  font-size: var(--pulse-text-xs);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  text-align: right;
  line-height: 1;
  white-space: nowrap;
  color: var(--el-text-color-secondary);
}
.tier-row.warn .tier-pct {
  color: var(--el-color-warning);
}
.tier-row.danger .tier-pct {
  color: var(--el-color-danger);
}
.tier-empty {
  color: var(--el-text-color-placeholder);
}
</style>
