<template>
  <div class="quota-board-page">
    <header class="page-header">
      <div>
        <h2>额度看板</h2>
        <p class="desc">各平台独立卡片：Cursor 对齐 Plan &amp; Usage；GLM / MiniMax / Kimi 对齐 Coding Plan 窗口额度。</p>
      </div>
    </header>
    <div class="vendor-tabs-shell">
      <el-tabs v-model="activeTab" class="vendor-tabs">
        <el-tab-pane name="cursor" :lazy="false">
          <template #label>Cursor ({{ tabCounts.cursor }})</template>
          <CursorQuotaBoardPanel ref="cursorPanelRef" @count-change="tabCounts.cursor = $event" />
        </el-tab-pane>
        <el-tab-pane name="glm" :lazy="false">
          <template #label>GLM ({{ tabCounts.glm }})</template>
          <CodingPlanQuotaBoardPanel
            ref="glmPanelRef"
            vendor="glm"
            @count-change="tabCounts.glm = $event"
          />
        </el-tab-pane>
        <el-tab-pane name="minimax" :lazy="false">
          <template #label>MiniMax ({{ tabCounts.minimax }})</template>
          <CodingPlanQuotaBoardPanel
            ref="minimaxPanelRef"
            vendor="minimax"
            @count-change="tabCounts.minimax = $event"
          />
        </el-tab-pane>
        <el-tab-pane name="kimi" :lazy="false">
          <template #label>Kimi ({{ tabCounts.kimi }})</template>
          <CodingPlanQuotaBoardPanel
            ref="kimiPanelRef"
            vendor="kimi"
            @count-change="tabCounts.kimi = $event"
          />
        </el-tab-pane>
      </el-tabs>
      <el-button class="tab-bar-refresh" size="small" :loading="refreshing" @click="refreshActive">
        刷新
      </el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import client from '@/api/client'
import CursorQuotaBoardPanel from '@/components/quota/CursorQuotaBoardPanel.vue'
import CodingPlanQuotaBoardPanel from '@/components/quota/CodingPlanQuotaBoardPanel.vue'

const QUOTA_VENDORS = ['cursor', 'glm', 'minimax', 'kimi'] as const

type QuotaPanelRef = { loadAll: () => void | Promise<void> }

const activeTab = ref('cursor')
const tabCounts = reactive({ cursor: 0, glm: 0, minimax: 0, kimi: 0 })

const cursorPanelRef = ref<QuotaPanelRef | null>(null)
const glmPanelRef = ref<QuotaPanelRef | null>(null)
const minimaxPanelRef = ref<QuotaPanelRef | null>(null)
const kimiPanelRef = ref<QuotaPanelRef | null>(null)
const refreshing = ref(false)

/** 与 Tab 标题计数对齐；不依赖子面板是否已挂载。 */
async function refreshTabCounts() {
  const results = await Promise.all(
    QUOTA_VENDORS.map((vendor) =>
      client
        .get('/api/v2/quota-board', { params: { vendor } })
        .then((res) => (Array.isArray(res.data) ? res.data.length : 0)),
    ),
  )
  tabCounts.cursor = results[0]
  tabCounts.glm = results[1]
  tabCounts.minimax = results[2]
  tabCounts.kimi = results[3]
}

async function refreshActive() {
  refreshing.value = true
  try {
    await Promise.all([
      refreshTabCounts(),
      cursorPanelRef.value?.loadAll(),
      glmPanelRef.value?.loadAll(),
      minimaxPanelRef.value?.loadAll(),
      kimiPanelRef.value?.loadAll(),
    ])
  } finally {
    refreshing.value = false
  }
}
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
.vendor-tabs-shell {
  position: relative;
}
.vendor-tabs-shell :deep(.el-tabs__header) {
  margin-bottom: 16px;
  padding-right: 72px;
}
.tab-bar-refresh {
  position: absolute;
  top: 0;
  right: 0;
  z-index: 1;
  height: 40px;
}
</style>
