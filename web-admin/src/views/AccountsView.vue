<template>
  <div class="accounts-page">
    <header class="page-header">
      <div>
        <h2>AI 账号台账</h2>
        <p class="desc">按平台分 Tab 管理 Cursor、GLM、MiniMax 账号（Coding Plan 无历史用量，仅额度同步）。</p>
      </div>
    </header>
    <el-tabs v-model="activeTab" class="vendor-tabs">
      <el-tab-pane label="Cursor" name="cursor">
        <CursorAccountsPanel />
      </el-tab-pane>
      <el-tab-pane label="GLM" name="glm">
        <CodingPlanAccountsPanel vendor-slug="glm" />
      </el-tab-pane>
      <el-tab-pane label="MiniMax" name="minimax">
        <CodingPlanAccountsPanel vendor-slug="minimax" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import CursorAccountsPanel from '@/components/accounts/CursorAccountsPanel.vue'
import CodingPlanAccountsPanel from '@/components/accounts/CodingPlanAccountsPanel.vue'

const activeTab = ref('cursor')
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
