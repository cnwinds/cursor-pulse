<template>
  <section class="block">
    <h4>answers</h4>
    <el-table :data="rows" size="small" stripe>
      <el-table-column label="问题" min-width="160">
        <template #default="{ row }">{{ row.label }}</template>
      </el-table-column>
      <el-table-column prop="kind" label="类型" width="88" />
      <el-table-column label="结果" min-width="200">
        <template #default="{ row }">
          <span v-if="row.summary">{{ row.summary }}</span>
          <code v-else class="raw-snippet">{{ row.rawSnippet }}</code>
        </template>
      </el-table-column>
    </el-table>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { JevTraceAccountLookup } from './jevTraceTypes'

const props = defineProps<{
  answers: Record<string, unknown>
  lookup: Map<string, JevTraceAccountLookup>
}>()

function accountLabel(id: string): string {
  return props.lookup.get(id)?.account_identifier || id
}

function summarize(name: string, raw: unknown): { kind: string; summary: string; rawSnippet: string } {
  const rawSnippet = JSON.stringify(raw)
  if (name === 'pick' && raw && typeof raw === 'object') {
    const o = raw as Record<string, unknown>
    const choice = o.choice != null ? String(o.choice) : '—'
    const conf = o.confidence != null ? ` · 置信 ${o.confidence}` : ''
    return { kind: 'choice', summary: `${accountLabel(choice)}${conf}`, rawSnippet }
  }
  if (name.startsWith('safe_for_owner_')) {
    let p: number | null = null
    if (typeof raw === 'number') p = raw
    else if (raw && typeof raw === 'object') {
      const o = raw as Record<string, unknown>
      if (typeof o.probability === 'number') p = o.probability
    }
    if (p != null) {
      const safe = p < 0.5
      return {
        kind: 'noul',
        summary: `P(侵占)=${(p * 100).toFixed(1)}% → ${safe ? '安全' : '风险'}`,
        rawSnippet,
      }
    }
  }
  return { kind: '—', summary: '', rawSnippet: rawSnippet.slice(0, 120) }
}

const rows = computed(() =>
  Object.entries(props.answers).map(([name, raw]) => {
    let label = name
    if (name === 'pick') label = 'pick'
    else if (name.startsWith('safe_for_owner_')) {
      label = `主负责人 · ${accountLabel(name.replace('safe_for_owner_', ''))}`
    }
    const { kind, summary, rawSnippet } = summarize(name, raw)
    return { name, label, kind, summary, rawSnippet }
  }),
)
</script>

<style scoped>
.block h4 {
  margin: 0 0 8px;
  font-size: 13px;
  font-weight: 600;
}
.raw-snippet {
  font-size: 11px;
  word-break: break-all;
}
</style>
