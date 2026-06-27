<template>
  <div v-loading="loading">
    <div class="toolbar">
      <el-input v-model="period" style="width: 160px" placeholder="账期 2026-06" />
      <el-button type="primary" @click="load">加载快照</el-button>
      <el-button
        v-if="auth.hasPermission('metrics:aggregate')"
        type="warning"
        @click="refresh"
      >
        重新聚合
      </el-button>
    </div>
    <el-input
      v-model="jsonText"
      type="textarea"
      :rows="24"
      readonly
      placeholder="选择账期后加载指标 JSON"
    />
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import client from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const loading = ref(false)
const period = ref('')
const jsonText = ref('')

async function ensurePeriod() {
  if (!period.value) {
    const cfg = await client.get('/api/config/summary')
    period.value = cfg.data.current_period
  }
}

async function load() {
  loading.value = true
  try {
    await ensurePeriod()
    const { data } = await client.get(`/api/periods/${period.value}/metrics`)
    jsonText.value = JSON.stringify(data, null, 2)
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '加载失败')
  } finally {
    loading.value = false
  }
}

async function refresh() {
  loading.value = true
  try {
    await ensurePeriod()
    const { data } = await client.get(`/api/periods/${period.value}/metrics`, {
      params: { refresh: true },
    })
    jsonText.value = JSON.stringify(data, null, 2)
    ElMessage.success('聚合完成')
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '聚合失败')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
}
</style>
