<template>
  <div class="quota-board-page">
    <header class="page-header">
      <div>
        <h2>额度看板</h2>
        <p class="desc">各平台独立卡片：Cursor 对齐 Plan &amp; Usage；GLM / MiniMax / Kimi 对齐 Coding Plan 窗口额度。</p>
      </div>
    </header>
    <div class="tabs-row">
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
      <div class="tabs-actions">
        <el-button v-if="canWrite" type="primary" @click="goCreateAccount">{{ createLabel }}</el-button>
        <el-button :loading="refreshing" @click="refreshActive">刷新</el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import client from '@/api/client'
import CursorQuotaBoardPanel from '@/components/quota/CursorQuotaBoardPanel.vue'
import CodingPlanQuotaBoardPanel from '@/components/quota/CodingPlanQuotaBoardPanel.vue'
import { useAuthStore } from '@/stores/auth'

const QUOTA_VENDORS = ['cursor', 'glm', 'minimax', 'kimi'] as const

type QuotaPanelRef = { loadAll: () => void | Promise<void> }

const activeTab = ref('cursor')
const tabCounts = reactive({ cursor: 0, glm: 0, minimax: 0, kimi: 0 })
const router = useRouter()
const auth = useAuthStore()
const canWrite = computed(() => auth.hasPermission('accounts:write'))

const cursorPanelRef = ref<QuotaPanelRef | null>(null)
const glmPanelRef = ref<QuotaPanelRef | null>(null)
const minimaxPanelRef = ref<QuotaPanelRef | null>(null)
const kimiPanelRef = ref<QuotaPanelRef | null>(null)
const refreshing = ref(false)

/** 与 Tab 标题计数对齐；不依赖子面板是否已挂载（lazy / 未切 Tab）。 */
async function refreshTabCounts() {
  const results = await Promise.all(
    QUOTA_VENDORS.map((vendor) =>
      client.get('/api/v2/quota-board', { params: { vendor } })
        .then((res) => (Array.isArray(res.data) ? res.data.length : 0)),
    ),
  )
  tabCounts.cursor = results[0]
  tabCounts.glm = results[1]
  tabCounts.minimax = results[2]
  tabCounts.kimi = results[3]
}

const createLabel = computed(() => {
  if (activeTab.value === 'glm') return '新增 GLM 账号'
  if (activeTab.value === 'minimax') return '新增 MiniMax 账号'
  if (activeTab.value === 'kimi') return '新增 Kimi 账号'
  return '新增 Cursor 账号'
})

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

function goCreateAccount() {
  void router.push({ name: 'accounts', query: { tab: activeTab.value } })
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
.tabs-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
.vendor-tabs {
  flex: 1;
  min-width: 0;
}
.tabs-actions {
  display: flex;
  flex-shrink: 0;
  gap: 8px;
  padding-top: 4px;
}
.vendor-tabs :deep(.el-tabs__header) {
  margin-bottom: 16px;
}
</style>
