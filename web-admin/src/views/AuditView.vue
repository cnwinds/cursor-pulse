<template>
  <div v-loading="loading">
    <el-tabs v-model="tab">
      <el-tab-pane label="管理操作" name="admin">
        <el-table :data="data.admin_actions" stripe>
          <el-table-column label="时间" width="170">
            <template #default="{ row }">{{ formatChinaTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column prop="operator_name" label="操作人" width="100" />
          <el-table-column prop="action_label" label="动作" width="130" />
          <el-table-column label="权限" width="110">
            <template #default="{ row }">{{ row.capability_label || row.capability || '—' }}</template>
          </el-table-column>
          <el-table-column label="通道" width="100">
            <template #default="{ row }">{{ row.channel_label || row.channel }}</template>
          </el-table-column>
          <el-table-column prop="detail" label="详情" min-width="280" show-overflow-tooltip />
        </el-table>
      </el-tab-pane>
      <el-tab-pane label="异常告警" name="alerts">
        <el-table :data="data.alerts" stripe>
          <el-table-column label="时间" width="180">
            <template #default="{ row }">{{ formatChinaTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column prop="period" label="账期" width="100" />
          <el-table-column prop="severity" label="级别" width="90" />
          <el-table-column prop="message" label="消息" min-width="280" show-overflow-tooltip />
        </el-table>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import client from '@/api/client'
import { formatChinaTime } from '@/utils/time'

const loading = ref(false)
const tab = ref('admin')
const data = reactive({
  admin_actions: [] as any[],
  alerts: [] as any[],
})

async function load() {
  loading.value = true
  try {
    const res = await client.get('/api/audit-logs')
    Object.assign(data, res.data)
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>
