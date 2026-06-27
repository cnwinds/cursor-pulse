<template>
  <div v-loading="loading">
    <div class="toolbar">
      <el-button
        v-if="auth.hasPermission('evolution:run')"
        type="warning"
        @click="runEvolution"
      >
        手动运行自进化
      </el-button>
      <el-button @click="load">刷新</el-button>
    </div>
    <el-table :data="rows" stripe>
      <el-table-column prop="created_at" label="时间" width="180" />
      <el-table-column prop="action_type" label="动作" width="160" />
      <el-table-column prop="status" label="状态" width="100" />
      <el-table-column prop="detail" label="详情" min-width="240" show-overflow-tooltip />
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import client from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const loading = ref(false)
const rows = ref<any[]>([])

async function load() {
  loading.value = true
  try {
    const { data } = await client.get('/api/memory/evolution')
    rows.value = data
  } finally {
    loading.value = false
  }
}

async function runEvolution() {
  loading.value = true
  try {
    const { data } = await client.post('/api/memory/evolution/run')
    ElMessage.success(`完成：+${data.principles} 原则，${data.actions} 动作`)
    await load()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '运行失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
}
</style>
