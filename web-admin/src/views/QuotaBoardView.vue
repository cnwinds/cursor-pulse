<template>
  <div class="quota-board-page">
    <header class="page-header">
      <div>
        <h2>额度看板</h2>
        <p class="desc">各平台独立卡片：Cursor 对齐 Plan &amp; Usage；GLM / MiniMax 对齐 Coding Plan 窗口额度。</p>
      </div>
    </header>
    <el-tabs v-model="activeTab" class="vendor-tabs">
      <el-tab-pane label="Cursor" name="cursor">
        <CursorQuotaBoardPanel />
      </el-tab-pane>
      <el-tab-pane label="GLM" name="glm">
        <CodingPlanQuotaBoardPanel vendor="glm" />
      </el-tab-pane>
      <el-tab-pane label="MiniMax" name="minimax">
        <CodingPlanQuotaBoardPanel vendor="minimax" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import CursorQuotaBoardPanel from '@/components/quota/CursorQuotaBoardPanel.vue'
import CodingPlanQuotaBoardPanel from '@/components/quota/CodingPlanQuotaBoardPanel.vue'

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
