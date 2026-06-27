<template>
  <el-card shadow="never" v-loading="loading">
    <div class="toolbar">
      <el-button @click="load">刷新</el-button>
    </div>
    <el-table :data="rows" stripe>
      <el-table-column prop="created_at" label="时间" width="180" />
      <el-table-column prop="visibility" label="场景" width="100" />
      <el-table-column prop="audience_name" label="受众" width="120" />
      <el-table-column prop="query_excerpt" label="查询摘要" min-width="200" show-overflow-tooltip />
      <el-table-column prop="deflection_reason" label="拦截原因" width="120" />
      <el-table-column label="释放/拦截" width="140">
        <template #default="{ row }">
          {{ row.released_atom_ids?.length || 0 }} / {{ row.blocked_atom_ids?.length || 0 }}
        </template>
      </el-table-column>
    </el-table>
  </el-card>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import client from '@/api/client'

const loading = ref(false)
const rows = ref<any[]>([])

async function load() {
  loading.value = true
  try {
    const { data } = await client.get('/api/memory/disclosure')
    rows.value = data
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.toolbar {
  margin-bottom: 16px;
}
</style>
