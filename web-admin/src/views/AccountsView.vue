<template>
  <div class="accounts-page">
    <header class="page-header">
      <div>
        <h2>AI 账号台账</h2>
        <p class="desc">按平台分 Tab 管理 Cursor、GLM、MiniMax 账号（Coding Plan 无历史用量，仅额度同步）。</p>
      </div>
    </header>
    <el-tabs v-model="activeTab" class="vendor-tabs">
      <el-tab-pane name="cursor">
        <template #label>Cursor ({{ tabCounts.cursor }})</template>
        <CursorAccountsPanel @count-change="tabCounts.cursor = $event" />
      </el-tab-pane>
      <el-tab-pane name="glm">
        <template #label>GLM ({{ tabCounts.glm }})</template>
        <CodingPlanAccountsPanel vendor-slug="glm" @count-change="tabCounts.glm = $event" />
      </el-tab-pane>
      <el-tab-pane name="minimax">
        <template #label>MiniMax ({{ tabCounts.minimax }})</template>
        <CodingPlanAccountsPanel vendor-slug="minimax" @count-change="tabCounts.minimax = $event" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import CursorAccountsPanel from '@/components/accounts/CursorAccountsPanel.vue'
import CodingPlanAccountsPanel from '@/components/accounts/CodingPlanAccountsPanel.vue'

const route = useRoute()
const activeTab = ref('cursor')
const tabCounts = reactive({ cursor: 0, glm: 0, minimax: 0 })

const TAB_NAMES = new Set(['cursor', 'glm', 'minimax'])

function syncTabFromRoute() {
  const tab = route.query.tab
  if (typeof tab === 'string' && TAB_NAMES.has(tab)) {
    activeTab.value = tab
  }
}

onMounted(syncTabFromRoute)
watch(() => route.query.tab, syncTabFromRoute)
</script>

<style scoped>
.page-header {
  margin-bottom: 8px;
}
.desc {
  color: var(--el-text-color-secondary);
  font-size: 14px;
  margin: 4px 0 0;
}
.vendor-tabs :deep(.el-tabs__header) {
  margin-bottom: 16px;
}
</style>
