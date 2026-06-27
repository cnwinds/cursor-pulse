<template>
  <el-card shadow="never" v-loading="loading">
    <div class="toolbar">
      <el-input v-model="query" placeholder="搜索内容" clearable style="width: 240px" />
      <el-button @click="load">刷新</el-button>
    </div>
    <el-table :data="filtered" stripe max-height="640">
      <el-table-column prop="subject_name" label="主体" width="120" />
      <el-table-column prop="kind" label="类型" width="100" />
      <el-table-column prop="content" label="内容" min-width="280" show-overflow-tooltip />
      <el-table-column prop="sensitivity" label="敏感级" width="100" />
      <el-table-column prop="source_visibility" label="来源可见性" width="110" />
      <el-table-column prop="confidence" label="置信度" width="90" />
      <el-table-column prop="created_at" label="创建" width="180" />
    </el-table>
  </el-card>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import client from '@/api/client'

const loading = ref(false)
const rows = ref<any[]>([])
const query = ref('')

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return rows.value
  return rows.value.filter(
    (r) =>
      r.content?.toLowerCase().includes(q) ||
      r.subject_name?.toLowerCase().includes(q),
  )
})

async function load() {
  loading.value = true
  try {
    const { data } = await client.get('/api/memory/atoms')
    rows.value = data
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
