<template>
  <el-card shadow="never" v-loading="loading">
    <el-table :data="members" stripe>
      <el-table-column prop="display_name" label="姓名" />
      <el-table-column prop="dingtalk_user_id" label="钉钉 User ID" />
      <el-table-column prop="status" label="状态" width="100" />
      <el-table-column prop="portal_role" label="后台角色" width="120">
        <template #default="{ row }">
          <el-tag v-if="row.portal_role" size="small">{{ row.portal_role }}</el-tag>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="加入时间" width="200" />
    </el-table>
  </el-card>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import client from '@/api/client'

const loading = ref(false)
const members = ref<any[]>([])

onMounted(async () => {
  loading.value = true
  try {
    const { data } = await client.get('/api/members')
    members.value = data
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.muted {
  color: #94a3b8;
}
</style>
