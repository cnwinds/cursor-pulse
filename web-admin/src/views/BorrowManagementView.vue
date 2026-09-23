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
const VALID_TABS = new Set(['loans', 'pool', 'ranking', 'rules', 'jev'])

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
    if ((q === 'pool' || q === 'ranking') && !canProxy.value) {
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
</style>
