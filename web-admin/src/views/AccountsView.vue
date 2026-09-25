<template>
  <div class="accounts-page">
    <header class="page-header">
      <div>
        <h2>AI 账号台账</h2>
        <p class="desc">按平台分 Tab 管理 Cursor、GLM、MiniMax、Kimi 账号（Coding Plan 无历史用量，仅额度同步）。</p>
      </div>
    </header>
    <div class="tabs-row">
      <el-tabs v-model="activeTab" class="vendor-tabs">
        <el-tab-pane name="cursor">
          <template #label>Cursor ({{ tabCounts.cursor }})</template>
          <CursorAccountsPanel ref="cursorPanelRef" @count-change="tabCounts.cursor = $event" />
        </el-tab-pane>
        <el-tab-pane name="glm">
          <template #label>GLM ({{ tabCounts.glm }})</template>
          <CodingPlanAccountsPanel
            ref="glmPanelRef"
            vendor-slug="glm"
            @count-change="tabCounts.glm = $event"
          />
        </el-tab-pane>
        <el-tab-pane name="minimax">
          <template #label>MiniMax ({{ tabCounts.minimax }})</template>
          <CodingPlanAccountsPanel
            ref="minimaxPanelRef"
            vendor-slug="minimax"
            @count-change="tabCounts.minimax = $event"
          />
        </el-tab-pane>
        <el-tab-pane name="kimi">
          <template #label>Kimi ({{ tabCounts.kimi }})</template>
          <CodingPlanAccountsPanel
            ref="kimiPanelRef"
            vendor-slug="kimi"
            @count-change="tabCounts.kimi = $event"
          />
        </el-tab-pane>
      </el-tabs>
      <div class="tabs-actions">
        <el-button v-if="canWrite" type="primary" @click="openCreateActive">{{ createLabel }}</el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import CursorAccountsPanel from '@/components/accounts/CursorAccountsPanel.vue'
import CodingPlanAccountsPanel from '@/components/accounts/CodingPlanAccountsPanel.vue'
import { useAuthStore } from '@/stores/auth'

type AccountsPanelRef = { openCreate: () => void }

const route = useRoute()
const auth = useAuthStore()
const activeTab = ref('cursor')
const tabCounts = reactive({ cursor: 0, glm: 0, minimax: 0, kimi: 0 })
const canWrite = computed(() => auth.hasPermission('accounts:write'))

const cursorPanelRef = ref<AccountsPanelRef | null>(null)
const glmPanelRef = ref<AccountsPanelRef | null>(null)
const minimaxPanelRef = ref<AccountsPanelRef | null>(null)
const kimiPanelRef = ref<AccountsPanelRef | null>(null)

const createLabel = computed(() => {
  if (activeTab.value === 'glm') return '新增 GLM 账号'
  if (activeTab.value === 'minimax') return '新增 MiniMax 账号'
  if (activeTab.value === 'kimi') return '新增 Kimi 账号'
  return '新增 Cursor 账号'
})

function openCreateActive() {
  if (activeTab.value === 'glm') {
    glmPanelRef.value?.openCreate()
    return
  }
  if (activeTab.value === 'minimax') {
    minimaxPanelRef.value?.openCreate()
    return
  }
  if (activeTab.value === 'kimi') {
    kimiPanelRef.value?.openCreate()
    return
  }
  cursorPanelRef.value?.openCreate()
}

const TAB_NAMES = new Set(['cursor', 'glm', 'minimax', 'kimi'])

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
