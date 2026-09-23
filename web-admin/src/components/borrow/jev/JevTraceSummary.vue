<template>
  <div class="jev-summary">
    <section v-if="trace.guards" class="card">
      <h4>护栏结论</h4>
      <dl class="kv">
        <div v-if="trace.guards.pick_choice">
          <dt>Jev 选择</dt>
          <dd>{{ labelAccount(trace.guards.pick_choice) }}</dd>
        </div>
        <div v-if="trace.guards.confidence != null">
          <dt>置信度</dt>
          <dd>{{ trace.guards.confidence }}</dd>
        </div>
        <div v-if="trace.guards.fallback_reason">
          <dt>回落原因</dt>
          <dd>{{ fallbackLabel(trace.guards.fallback_reason) }}</dd>
        </div>
      </dl>
      <div v-if="pickProbabilities.length" class="prob-bars">
        <div v-for="row in pickProbabilities" :key="row.id" class="prob-row">
          <span class="prob-label">{{ row.label }}</span>
          <div class="prob-track">
            <div class="prob-fill" :style="{ width: `${row.pct}%` }" />
          </div>
          <span class="prob-pct">{{ row.pctText }}</span>
        </div>
      </div>
    </section>

    <section v-if="ownerRows.length" class="card">
      <h4>主负责人安全（noul）</h4>
      <el-table :data="ownerRows" size="small" stripe>
        <el-table-column label="账号" min-width="140">
          <template #default="{ row }">{{ row.label }}</template>
        </el-table-column>
        <el-table-column label="判定" width="88" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.safe === true" type="success" size="small" effect="plain">安全</el-tag>
            <el-tag v-else-if="row.safe === false" type="danger" size="small" effect="plain">风险</el-tag>
            <span v-else>—</span>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <section v-if="trace.guards?.thresholds" class="card card--muted">
      <h4>阈值（选号规则）</h4>
      <dl class="kv kv--compact">
        <div>
          <dt>最低置信度</dt>
          <dd>{{ trace.guards.thresholds.auto_min_confidence }}</dd>
        </div>
        <div>
          <dt>首选概率间隔</dt>
          <dd>{{ trace.guards.thresholds.auto_min_margin }}</dd>
        </div>
        <div>
          <dt>侵占判定线</dt>
          <dd>P(是) ≥ {{ trace.guards.thresholds.owner_unsafe_probability }}</dd>
        </div>
      </dl>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { JevTrace, JevTraceAccountLookup } from './jevTraceTypes'

const FALLBACK_LABELS: Record<string, string> = {
  jev_unavailable: '未启用 Jev',
  insufficient_candidates: '候选不足',
  jev_error: '调用失败',
  circuit_open: '熔断',
  no_pick_answer: '未返回答案',
  no_choice: '选择为空',
  unknown_account: '返回账号不在候选内',
  low_confidence: '置信度不足',
  narrow_margin: '概率间隔过小',
  owner_unsafe: '主负责人预留风险',
}

const props = defineProps<{
  trace: JevTrace
  lookup: Map<string, JevTraceAccountLookup>
}>()

function labelAccount(accountId: string): string {
  const row = props.lookup.get(accountId)
  if (row?.account_identifier) return row.account_identifier
  return accountId
}

function fallbackLabel(reason: string): string {
  return FALLBACK_LABELS[reason] || reason
}

const pickProbabilities = computed(() => {
  const probs = props.trace.guards?.probabilities || {}
  return Object.entries(probs)
    .sort((a, b) => b[1] - a[1])
    .map(([id, p]) => ({
      id,
      label: labelAccount(id),
      pct: Math.min(100, Math.round(p * 100)),
      pctText: `${(p * 100).toFixed(1)}%`,
    }))
})

const ownerRows = computed(() => {
  const safe = props.trace.guards?.owner_safe || {}
  return Object.entries(safe).map(([id, ok]) => ({
    id,
    label: labelAccount(id),
    safe: ok,
  }))
})
</script>

<style scoped>
.jev-summary {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.card {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px;
  padding: 12px 14px;
  background: var(--el-fill-color-blank);
}
.card--muted {
  background: var(--el-fill-color-light);
}
.card h4 {
  margin: 0 0 10px;
  font-size: 13px;
  font-weight: 600;
}
.kv {
  display: grid;
  gap: 8px;
  margin: 0;
}
.kv div {
  display: grid;
  grid-template-columns: 100px 1fr;
  gap: 8px;
  font-size: 13px;
}
.kv dt {
  margin: 0;
  color: var(--el-text-color-secondary);
}
.kv dd {
  margin: 0;
}
.kv--compact div {
  grid-template-columns: 120px 1fr;
}
.prob-bars {
  margin-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.prob-row {
  display: grid;
  grid-template-columns: minmax(80px, 1fr) 1fr 52px;
  gap: 8px;
  align-items: center;
  font-size: 12px;
}
.prob-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.prob-track {
  height: 8px;
  background: var(--el-fill-color);
  border-radius: 4px;
  overflow: hidden;
}
.prob-fill {
  height: 100%;
  background: linear-gradient(90deg, #6366f1, #8b5cf6);
  border-radius: 4px;
}
.prob-pct {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
</style>
