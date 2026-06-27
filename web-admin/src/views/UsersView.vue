<template>
  <el-card shadow="never" v-loading="loading">
    <el-alert
      title="通过 CLI 创建首个 owner：pulse admin bootstrap --user-id <钉钉id> --password <密码>"
      type="info"
      :closable="false"
      class="mb"
    />
    <el-table :data="members" stripe>
      <el-table-column prop="display_name" label="姓名" width="120" />
      <el-table-column prop="dingtalk_user_id" label="钉钉 User ID" min-width="140" />
      <el-table-column prop="status" label="成员" width="80" />
      <el-table-column label="后台角色" width="160">
        <template #default="{ row }">
          <el-select v-model="row._role" placeholder="无" clearable @change="onRoleChange(row)">
            <el-option label="owner" value="owner" />
            <el-option label="operator" value="operator" />
            <el-option label="auditor" value="auditor" />
            <el-option label="custom" value="custom" />
          </el-select>
        </template>
      </el-table-column>
      <el-table-column label="自定义权限" min-width="220">
        <template #default="{ row }">
          <el-select
            v-if="row._role === 'custom'"
            v-model="row._perms"
            multiple
            collapse-tags
            placeholder="选择能力码"
            @change="save(row)"
          >
            <el-option v-for="p in allPermissions" :key="p" :label="p" :value="p" />
          </el-select>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="最近登录" width="170">
        <template #default="{ row }">{{ row.last_portal_login_at || '—' }}</template>
      </el-table-column>
    </el-table>
  </el-card>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import client from '@/api/client'

const allPermissions = [
  'settings:read', 'settings:write', 'members:read', 'members:write',
  'submissions:read', 'submissions:review', 'metrics:read', 'metrics:aggregate',
  'reports:publish', 'memory:read', 'memory:write', 'evolution:run',
  'tasks:nudge', 'tasks:group_message', 'audit:read', 'admin:users',
]

const loading = ref(false)
const members = ref<any[]>([])

async function load() {
  loading.value = true
  try {
    const { data } = await client.get('/api/members')
    members.value = data.map((m: any) => ({
      ...m,
      _role: m.portal_role,
      _perms: m.portal_permissions || [],
    }))
  } finally {
    loading.value = false
  }
}

function onRoleChange(row: any) {
  if (row._role !== 'custom') {
    row._perms = []
  }
  save(row)
}

async function save(row: any) {
  try {
    const body: Record<string, unknown> = { portal_role: row._role || null }
    if (row._role === 'custom') {
      body.portal_permissions = row._perms
    }
    await client.patch(`/api/members/${row.id}/portal`, body)
    row.portal_role = row._role
    ElMessage.success('已更新')
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '更新失败')
    row._role = row.portal_role
  }
}

onMounted(load)
</script>

<style scoped>
.mb { margin-bottom: 16px; }
.muted { color: #94a3b8; }
</style>
