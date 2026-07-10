<template>
  <div class="users-page" v-loading="loading">
    <header class="page-header">
      <div>
        <h2>用户与权限</h2>
        <p class="desc">审批首次扫码的钉钉用户，分配后台角色。超级管理员可授权其他超管。</p>
      </div>
    </header>

    <section class="panel pending-panel">
      <div class="panel-head">
        <div>
          <h3>待审批</h3>
          <p class="panel-desc">首次扫码登录后台的钉钉账号</p>
        </div>
        <el-badge :value="pendingUsers.length" :hidden="!pendingUsers.length" type="warning" />
      </div>

      <el-empty v-if="!pendingUsers.length" description="暂无待审批用户" />

      <div v-else class="pending-list">
        <div v-for="user in pendingUsers" :key="user.id" class="pending-card">
          <div class="user-info">
            <el-avatar :size="40">{{ user.display_name.slice(0, 1) }}</el-avatar>
            <div>
              <div class="name">{{ user.display_name }}</div>
              <div class="meta">钉钉 · {{ user.dingtalk_user_id }}</div>
            </div>
          </div>
          <div class="pending-actions">
            <el-button type="primary" @click="openApprove(user)">审批开通</el-button>
            <el-button @click="rejectUser(user)">拒绝</el-button>
          </div>
        </div>
      </div>
    </section>

    <section class="panel">
      <div class="panel-head">
        <div>
          <h3>已开通用户</h3>
          <p class="panel-desc">已分配后台角色的钉钉账号</p>
        </div>
        <el-badge :value="activeUsers.length" type="info" />
      </div>

      <el-table :data="activeUsers" stripe class="users-table">
        <el-table-column label="用户" min-width="180">
          <template #default="{ row }">
            <div class="user-cell">
              <el-avatar :size="32">{{ row.display_name.slice(0, 1) }}</el-avatar>
              <div>
                <div>{{ row.display_name }}</div>
                <div class="meta">钉钉 · {{ row.dingtalk_user_id }}</div>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="角色" width="140">
          <template #default="{ row }">
            <el-tag :type="roleTagType(row.portal_role)">{{ roleLabel(row.portal_role) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.portal_status === 'active' ? 'success' : 'info'">
              {{ row.portal_status === 'active' ? '已启用' : '已禁用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" min-width="260">
          <template #default="{ row }">
            <el-button link type="primary" @click="openApprove(row)">编辑角色</el-button>
            <el-button
              v-if="row.portal_status === 'active'"
              link
              type="warning"
              :disabled="row.id === currentUserId"
              @click="disableUser(row)"
            >
              禁用
            </el-button>
            <el-button
              link
              type="danger"
              :disabled="row.id === currentUserId"
              @click="deleteUser(row)"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <el-dialog
      v-model="approveVisible"
      :title="approveTarget ? `调整角色 · ${approveTarget.display_name}` : '分配角色'"
      width="720px"
      destroy-on-close
    >
      <p class="dialog-desc">选择该用户可访问的后台功能范围</p>
      <div class="role-grid">
        <button
          v-for="role in roles"
          :key="role.id"
          type="button"
          class="role-card"
          :class="{ selected: selectedRole === role.id, owner: role.id === 'owner' }"
          @click="selectedRole = role.id"
        >
          <div class="role-title">{{ role.label }}</div>
          <div class="role-desc">{{ role.description }}</div>
        </button>
      </div>

      <div v-if="selectedRole === 'custom'" class="custom-perms">
        <el-select v-model="customPerms" multiple collapse-tags placeholder="选择能力码" style="width: 100%">
          <el-option v-for="p in allPermissions" :key="p" :label="p" :value="p" />
        </el-select>
      </div>

      <template #footer>
        <el-button @click="approveVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveRole">保存角色</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import client from '@/api/client'
import { useAuthStore } from '@/stores/auth'

interface PortalUserRow {
  id: string
  display_name: string
  dingtalk_user_id: string
  portal_status: string
  portal_role: string | null
  portal_permissions?: string[]
}

interface RoleDef {
  id: string
  label: string
  description: string
}

const auth = useAuthStore()
const loading = ref(false)
const saving = ref(false)
const pendingUsers = ref<PortalUserRow[]>([])
const activeUsers = ref<PortalUserRow[]>([])
const roles = ref<RoleDef[]>([])
const approveVisible = ref(false)
const approveTarget = ref<PortalUserRow | null>(null)
const selectedRole = ref('operator')
const customPerms = ref<string[]>([])

const allPermissions = [
  'settings:read', 'settings:write',
  'submissions:read', 'submissions:review', 'metrics:read', 'metrics:aggregate',
  'reports:publish', 'memory:read', 'memory:write', 'evolution:run',
  'tasks:nudge', 'tasks:group_message', 'audit:read', 'admin:users',
]

const currentUserId = computed(() => auth.user?.id)

const roleLabels: Record<string, string> = {
  owner: '超级管理员',
  operator: '运营员',
  auditor: '审计员',
  ai_member: 'AI工具成员',
  custom: '自定义',
}

function roleLabel(role: string | null) {
  return role ? roleLabels[role] || role : '—'
}

function roleTagType(role: string | null) {
  if (role === 'owner') return 'danger'
  if (role === 'operator') return 'primary'
  if (role === 'ai_member') return 'success'
  return 'info'
}

async function load() {
  loading.value = true
  try {
    const [pendingRes, activeRes, rolesRes] = await Promise.all([
      client.get('/api/portal/users/pending'),
      client.get('/api/portal/users'),
      client.get('/api/portal/roles'),
    ])
    pendingUsers.value = pendingRes.data
    activeUsers.value = activeRes.data
    roles.value = rolesRes.data
  } finally {
    loading.value = false
  }
}

function openApprove(user: PortalUserRow) {
  approveTarget.value = user
  selectedRole.value = user.portal_role || 'operator'
  customPerms.value = user.portal_permissions || []
  approveVisible.value = true
}

async function saveRole() {
  if (!approveTarget.value) return
  saving.value = true
  try {
    const body: Record<string, unknown> = { portal_role: selectedRole.value }
    if (selectedRole.value === 'custom') {
      body.portal_permissions = customPerms.value
    }
    await client.post(`/api/portal/users/${approveTarget.value.id}/approve`, body)
    ElMessage.success('角色已保存')
    approveVisible.value = false
    await load()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

async function rejectUser(user: PortalUserRow) {
  await ElMessageBox.confirm(`确定拒绝 ${user.display_name} 的后台访问申请？`, '拒绝审批')
  try {
    await client.post(`/api/portal/users/${user.id}/reject`)
    ElMessage.success('已拒绝')
    await load()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '操作失败')
  }
}

async function disableUser(user: PortalUserRow) {
  await ElMessageBox.confirm(`确定禁用 ${user.display_name} 的后台访问？`, '禁用用户')
  try {
    await client.post(`/api/portal/users/${user.id}/disable`)
    ElMessage.success('已禁用')
    await load()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '操作失败')
  }
}

async function deleteUser(user: PortalUserRow) {
  await ElMessageBox.confirm(
    `确定删除 ${user.display_name}？仅无提交记录的用户可删除。`,
    '删除用户',
    { type: 'warning' },
  )
  try {
    await client.delete(`/api/portal/users/${user.id}`)
    ElMessage.success('已删除')
    await load()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '删除失败')
  }
}

onMounted(load)
</script>

<style scoped>
.users-page {
  max-width: 1100px;
}
.page-header h2 {
  margin: 0 0 4px;
  font-size: 20px;
}
.desc {
  margin: 0;
  color: #64748b;
  font-size: 14px;
}
.panel {
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 20px;
  margin-top: 20px;
}
.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 16px;
}
.panel-head h3 {
  margin: 0 0 4px;
  font-size: 16px;
}
.panel-desc {
  margin: 0;
  color: #94a3b8;
  font-size: 13px;
}
.pending-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.pending-card {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 14px 16px;
  border: 1px dashed #cbd5e1;
  border-radius: 10px;
  background: #f8fafc;
}
.user-info,
.user-cell {
  display: flex;
  align-items: center;
  gap: 12px;
}
.name {
  font-weight: 600;
}
.meta {
  font-size: 12px;
  color: #94a3b8;
}
.pending-actions {
  display: flex;
  gap: 8px;
}
.dialog-desc {
  margin: 0 0 16px;
  color: #64748b;
  font-size: 14px;
}
.role-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px;
}
.role-card {
  text-align: left;
  padding: 14px 16px;
  border: 2px solid #e2e8f0;
  border-radius: 10px;
  background: #fff;
  cursor: pointer;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.role-card:hover {
  border-color: #93c5fd;
}
.role-card.selected {
  border-color: #3b82f6;
  box-shadow: 0 0 0 1px #3b82f6;
}
.role-card.selected.owner {
  border-color: #ef4444;
  box-shadow: 0 0 0 1px #ef4444;
}
.role-title {
  font-weight: 600;
  margin-bottom: 6px;
}
.role-desc {
  font-size: 12px;
  color: #64748b;
  line-height: 1.4;
}
.custom-perms {
  margin-top: 16px;
}
</style>
