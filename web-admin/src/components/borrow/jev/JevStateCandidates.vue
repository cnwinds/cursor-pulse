<template>
  <section class="block">
    <h4>state.candidates</h4>
    <el-table :data="rows" size="small" stripe max-height="280">
      <el-table-column label="账号" min-width="120" fixed>
        <template #default="{ row }">
          {{ displayId(String(row.account_id || '')) }}
        </template>
      </el-table-column>
      <el-table-column prop="pool_headroom_pct" label="池余量%" width="88" align="right" />
      <el-table-column prop="pool_idle_surplus_usd" label="闲置 USD" width="96" align="right" />
      <el-table-column prop="algorithm_score" label="算法分" width="88" align="right" />
      <el-table-column prop="hours_to_reset" label="距重置 h" width="96" align="right" />
      <el-table-column prop="proxy_active_seats" label="在用" width="64" align="center" />
    </el-table>
  </section>
</template>

<script setup lang="ts">
import type { JevTraceAccountLookup } from './jevTraceTypes'

const props = defineProps<{
  rows: Record<string, unknown>[]
  lookup: Map<string, JevTraceAccountLookup>
}>()

function displayId(accountId: string): string {
  const row = props.lookup.get(accountId)
  return row?.account_identifier || accountId
}
</script>

<style scoped>
.block {
  margin-bottom: 16px;
}
.block h4 {
  margin: 0 0 8px;
  font-size: 13px;
  font-weight: 600;
}
</style>
