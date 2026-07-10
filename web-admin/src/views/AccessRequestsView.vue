<template>
  <div class="requests-page" v-loading="loading">
    <header class="page-header">
      <div>
        <h2>AI 工具申请</h2>
        <p class="desc">员工申请试用 → 主管审批 → 管理员分配试用账号。也可在钉钉私聊机器人发送「申请 Cursor」。</p>
      </div>
      <div class="header-actions">
        <el-button v-if="canWrite" @click="syncDirectory" :loading="syncing">同步通讯录</el-button>
        <el-button v-if="canWrite" type="primary" @click="openCreate">发起申请</el-button>
      </div>
    </header>

    <el-table :data="requests" stripe>
      <el-table-column label="申请人" prop="applicant_name" width="120" />
      <el-table-column label="工具" prop="vendor_name" width="100" />
      <el-table-column label="理由" prop="reason" min-width="160" show-overflow-tooltip />
      <el-table-column label="状态" width="120">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)">{{ statusLabel(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="时间" width="170">
        <template #default="{ row }">{{ row.created_at?.slice(0, 19).replace('T', ' ') }}</template>
      </el-table-column>
      <el-table-column label="操作" width="280" fixed="right">
        <template #default="{ row }">
          <template v-if="row.status === 'pending_manager' && canApprove">
            <el-button link type="success" @click="approve(row)">通过</el-button>
            <el-button link type="danger" @click="reject(row)">拒绝</el-button>
          </template>
          <el-button
            v-if="row.status === 'approved' && canAssign"
            link
            type="primary"
            @click="assignTrial(row)"
          >
            分配试用号
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" title="申请 AI 工具" width="480px">
      <el-form label-width="80px">
        <el-form-item label="工具">
          <el-select v-model="form.vendor_id" style="width: 100%">
            <el-option v-for="v in vendors" :key="v.id" :label="v.name" :value="v.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="申请理由">
          <el-input v-model="form.reason" type="textarea" :rows="3" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitCreate">提交</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import client from '@/api/client'
import { useAuthStore } from '@/stores/auth'

interface AccessRequest {
  id: string
  applicant_name: string
  vendor_name: string
  reason: string | null
  status: string
  created_at: string
}

const auth = useAuthStore()
const loading = ref(false)
const saving = ref(false)
const syncing = ref(false)
const requests = ref<AccessRequest[]>([])
const vendors = ref<{ id: string; name: string }[]>([])
const dialogVisible = ref(false)
const form = reactive({ vendor_id: '', reason: '' })

const canWrite = computed(() => auth.hasPermission('requests:write'))
const canApprove = computed(() => auth.hasPermission('requests:approve'))
const canAssign = computed(() => auth.hasPermission('accounts:write'))

function statusLabel(s: string) {
  const map: Record<string, string> = {
    draft: '草稿',
    pending_manager: '待主管审批',
    approved: '已通过',
    rejected: '已拒绝',
    trial_assigned: '已分配试用',
    closed: '已关闭',
  }
  return map[s] || s
}

function statusType(s: string) {
  if (s === 'pending_manager') return 'warning'
  if (s === 'approved' || s === 'trial_assigned') return 'success'
  if (s === 'rejected') return 'danger'
  return 'info'
}

async function load() {
  loading.value = true
  try {
    const [reqRes, vendorRes] = await Promise.all([
      client.get('/api/v2/access-requests'),
      client.get('/api/v2/vendors'),
    ])
    requests.value = reqRes.data
    vendors.value = vendorRes.data
  } finally {
    loading.value = false
  }
}

function openCreate() {
  form.vendor_id = vendors.value.find((v) => v.name === 'Cursor')?.id || vendors.value[0]?.id || ''
  form.reason = ''
  dialogVisible.value = true
}

async function submitCreate() {
  saving.value = true
  try {
    const res = await client.post('/api/v2/access-requests', {
      vendor_id: form.vendor_id,
      reason: form.reason || null,
      submit: true,
    })
    ElMessage.success(res.data.message)
    dialogVisible.value = false
    await load()
  } catch {
    ElMessage.error('提交失败')
  } finally {
    saving.value = false
  }
}

async function approve(row: AccessRequest) {
  await client.post(`/api/v2/access-requests/${row.id}/approve`, {})
  ElMessage.success('已通过')
  await load()
}

async function reject(row: AccessRequest) {
  const { value } = await ElMessageBox.prompt('可选填拒绝原因', '拒绝申请', {
    confirmButtonText: '拒绝',
    cancelButtonText: '取消',
  }).catch(() => ({ value: null }))
  if (value === null) return
  await client.post(`/api/v2/access-requests/${row.id}/reject`, { note: value || null })
  ElMessage.success('已拒绝')
  await load()
}

async function assignTrial(row: AccessRequest) {
  const res = await client.post(`/api/v2/access-requests/${row.id}/assign-trial`, {})
  ElMessage.success(res.data.message)
  await load()
}

async function syncDirectory() {
  syncing.value = true
  try {
    const res = await client.post('/api/v2/sync/dingtalk-directory')
    ElMessage.success(`同步完成：${JSON.stringify(res.data)}`)
    await load()
  } catch {
    ElMessage.error('同步失败，请检查钉钉应用权限')
  } finally {
    syncing.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.page-header {
  display: flex;
  justify-content: space-between;
  margin-bottom: 20px;
}
.desc {
  color: #64748b;
  font-size: 14px;
  margin-top: 4px;
}
.header-actions {
  display: flex;
  gap: 8px;
}
</style>
