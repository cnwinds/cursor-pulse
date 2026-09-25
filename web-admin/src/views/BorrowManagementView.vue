<template>
  <div class="borrow-page">
    <header class="page-header">
      <div>
        <h2>借用管理</h2>
      </div>
    </header>

    <el-tabs v-model="tab" class="main-tabs" @tab-change="onTabChange">
      <el-tab-pane v-if="canLoans" label="借用记录" name="loans" lazy>
        <LoansView embedded />
      </el-tab-pane>
      <el-tab-pane v-if="canProxy" label="入池账号" name="pool" lazy>
        <PoolAccountsPanel />
      </el-tab-pane>
      <el-tab-pane v-if="canProxy" label="打分表" name="ranking" lazy>
        <PoolRankingPanel />
      </el-tab-pane>
      <el-tab-pane v-if="canRules" label="选号规则" name="rules" lazy>
        <PoolSelectionRulesPanel />
      </el-tab-pane>
      <el-tab-pane v-if="canRules" label="Jev 决策" name="jev" lazy>
        <PoolJevSettingsPanel />
      </el-tab-pane>
      <el-tab-pane v-if="canProxy" name="openai-divider" disabled class="tab-divider-pane">
        <template #label>
          <span class="tab-group-divider" aria-hidden="true" />
        </template>
        <template #default><span /></template>
      </el-tab-pane>
      <el-tab-pane v-if="canProxy" name="openai-gateway" lazy class="openai-gateway-pane">
        <template #label>
          <span class="openai-tab-label">OpenAI 网关</span>
        </template>
        <CpOpenAiGatewayPanel />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import LoansView from '@/views/LoansView.vue'
import PoolAccountsPanel from '@/components/borrow/PoolAccountsPanel.vue'
import PoolRankingPanel from '@/components/borrow/PoolRankingPanel.vue'
import CpOpenAiGatewayPanel from '@/components/borrow/CpOpenAiGatewayPanel.vue'
import PoolSelectionRulesPanel from '@/components/borrow/PoolSelectionRulesPanel.vue'
import PoolJevSettingsPanel from '@/components/borrow/PoolJevSettingsPanel.vue'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const canLoans = computed(() => auth.hasPermission('accounts:read'))
const canProxy = computed(() => auth.hasPermission('proxy:read'))
const canRules = computed(
  () => auth.hasPermission('settings:read') || auth.hasPermission('proxy:read'),
)

const tab = ref('loans')
const VALID_TABS = new Set(['loans', 'pool', 'openai-gateway', 'ranking', 'rules', 'jev'])

function defaultTab(): string {
  if (canLoans.value) return 'loans'
  if (canProxy.value) return 'pool'
  return 'loans'
}

function syncTabFromRoute() {
  const q = route.query.tab
  if (typeof q === 'string' && VALID_TABS.has(q)) {
    if (q === 'loans' && !canLoans.value) {
      tab.value = canProxy.value ? 'pool' : defaultTab()
      return
    }
    if ((q === 'pool' || q === 'openai-gateway' || q === 'ranking') && !canProxy.value) {
      tab.value = defaultTab()
      return
    }
    if ((q === 'rules' || q === 'jev') && !canRules.value) {
      tab.value = defaultTab()
      return
    }
    tab.value = q
    return
  }
  tab.value = defaultTab()
}

watch(() => route.query.tab, syncTabFromRoute, { immediate: true })

function onTabChange(name: string | number) {
  const value = String(name)
  if (route.query.tab !== value) {
    router.replace({ query: { ...route.query, tab: value } })
  }
}
</script>

<style scoped>
.borrow-page {
  min-height: 200px;
}
.page-header {
  margin-bottom: 16px;
}
.page-header h2 {
  margin: 0 0 6px;
}
.desc {
  margin: 0;
  color: var(--el-text-color-secondary);
  font-size: 14px;
  line-height: 1.55;
  max-width: 720px;
}
.main-tabs :deep(.el-tabs__header) {
  margin-bottom: 16px;
}
.main-tabs :deep(.el-tabs__item.tab-divider-pane),
.main-tabs :deep(#tab-openai-divider) {
  cursor: default;
  padding: 0 4px !important;
  pointer-events: none;
}
.main-tabs :deep(#tab-openai-divider.is-disabled) {
  color: inherit;
}
.tab-group-divider {
  display: inline-block;
  width: 2px;
  height: 18px;
  margin: 0 12px;
  vertical-align: middle;
  border-radius: 1px;
  background: var(--el-border-color-darker, #c0c4cc);
  box-shadow: 1px 0 0 var(--el-border-color-lighter, #e4e7ed);
}
.openai-tab-label {
  font-weight: 600;
}
/* 未选中时与其它 Tab 同色；选中态由 Element Plus .is-active 控制 */
.main-tabs :deep(#tab-openai-gateway:not(.is-active) .openai-tab-label) {
  color: var(--el-text-color-regular);
  font-weight: 400;
}
.main-tabs :deep(#tab-openai-gateway) {
  padding-left: 4px;
}
</style>
