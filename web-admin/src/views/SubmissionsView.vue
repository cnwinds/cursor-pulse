<template>
  <div v-loading="loading">
    <div class="toolbar">
      <el-input v-model="period" style="width: 160px" placeholder="账期 2026-06" />
      <el-button type="primary" @click="load">刷新</el-button>
    </div>

    <el-alert
      v-if="data"
      :title="`已提交 ${data.submitted_count} / ${data.active_count}`"
      type="info"
      show-icon
      class="mb"
    />

    <el-table v-if="data" :data="data.members" stripe>
      <el-table-column prop="display_name" label="姓名" />
      <el-table-column prop="status" label="状态" width="100" />
      <el-table-column label="已提交" width="100">
        <template #default="{ row }">
          <el-tag :type="row.submitted ? 'success' : 'danger'" size="small">
            {{ row.submitted ? '是' : '否' }}
          </el-tag>
        </template>
      </el-table-column>
    </el-table>

    <el-card v-if="data?.unsubmitted?.length" class="mt" shadow="never" header="未提交名单">
      <el-tag v-for="name in data.unsubmitted" :key="name" class="tag">{{ name }}</el-tag>
    </el-card>

    <el-card v-if="pending.length" class="mt" shadow="never" header="待审提交">
      <el-table :data="pending" size="small">
        <el-table-column prop="id_prefix" label="ID" width="100" />
        <el-table-column prop="period" label="账期" width="100" />
        <el-table-column prop="input_type" label="类型" width="100" />
        <el-table-column prop="submitted_at" label="提交时间" />
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import client from '@/api/client'

const loading = ref(false)
const period = ref('')
const data = ref<any>(null)
const pending = ref<any[]>([])

async function load() {
  loading.value = true
  try {
    if (!period.value) {
      const cfg = await client.get('/api/config/summary')
      period.value = cfg.data.current_period
    }
    const res = await client.get(`/api/periods/${period.value}/status`)
    data.value = res.data
    const pendingRes = await client.get('/api/pending-reviews', { params: { period: period.value } })
    pending.value = pendingRes.data
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
.mb {
  margin-bottom: 16px;
}
.mt {
  margin-top: 16px;
}
.tag {
  margin: 4px;
}
</style>
