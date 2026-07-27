<template>
  <div class="accounts-page" v-loading="loading">
    <header class="page-header">
      <div>
        <h2>Cursor 账号台账</h2>
        <p class="desc">管理 Cursor 账号、主使用人与套餐。用量与额度请查看「额度看板」。</p>
      </div>
      <div class="header-actions">
        <el-button type="primary" :disabled="!cursorVendor" @click="openCreate">新增账号</el-button>
      </div>
    </header>

    <el-table :data="accounts" stripe>
      <el-table-column label="账号" min-width="220">
        <template #default="{ row }">
          {{ row.account_identifier || '—' }}
        </template>
      </el-table-column>
      <el-table-column label="套餐" width="120" prop="plan_name" />
      <el-table-column label="类型" width="100">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)">{{ statusLabel(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="API Key" width="120">
        <template #default="{ row }">
          <template v-if="isCursorRow(row)">
            <el-tag :type="credentialTagType(credentialMap[row.id])" size="small">
              {{ credentialBadgeLabel(credentialMap[row.id]) }}
            </el-tag>
            <div v-if="credentialMap[row.id]?.key_hint" class="muted key-hint">
              {{ credentialMap[row.id]?.key_hint }}
            </div>
          </template>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="主使用人" width="140">
        <template #default="{ row }">
          {{ memberName(row.primary_member_id) || '—' }}
        </template>
      </el-table-column>
      <el-table-column label="用量重置" width="120">
        <template #default="{ row }">
          <span v-if="row.usage_resets_on">{{ row.usage_resets_on }}</span>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="升级建议" width="100">
        <template #default="{ row }">
          <el-tag v-if="row.suggest_dedicated" type="warning">建议独立号</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="90" fixed="right">
        <template #default="{ row }">
          <el-button
            v-if="canOpenEdit(row)"
            link
            type="primary"
            @click="openEdit(row)"
          >编辑</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" :title="editing ? '编辑账号' : '新增账号'" width="520px">
      <el-form label-width="100px">
        <el-form-item v-if="editing && canWrite" label="套餐">
          <el-select v-model="form.plan_id" style="width: 100%">
            <el-option
              v-for="p in editPlans"
              :key="p.id"
              :label="`${p.plan_name} (${p.price_amount} ${p.price_currency})`"
              :value="p.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item v-if="editing && canWrite" label="账号标识">
          <el-input v-model="form.account_identifier" placeholder="邮箱或登录名" />
        </el-form-item>
        <el-form-item v-if="!editing || canWrite" label="类型">
          <el-select v-model="form.status" style="width: 100%" :disabled="Boolean(editing) && !canWrite">
            <el-option label="试用" value="trial" />
            <el-option label="共享" value="shared" />
            <el-option label="独立" value="dedicated" />
            <el-option label="停用" value="suspended" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="!editing || canWrite" label="主使用人">
          <el-select
            v-model="form.primary_member_id"
            clearable
            filterable
            style="width: 100%"
            :disabled="Boolean(editing) && !canWrite"
          >
            <el-option
              v-for="m in members"
              :key="m.id"
              :label="m.display_name"
              :value="m.id"
            />
          </el-select>
          <p v-if="form.status === 'dedicated'" class="field-hint">独立账号建议指定主使用人</p>
        </el-form-item>
        <el-form-item v-if="!editing || canWrite" label="备注">
          <el-input
            v-model="form.shared_note"
            type="textarea"
            :rows="2"
            placeholder="例如共享范围、值班说明等"
            :disabled="Boolean(editing) && !canWrite"
          />
        </el-form-item>
        <el-form-item v-if="editing && canWrite && isCursorAccount" label="用量重置">
          <el-date-picker
            v-model="form.usage_resets_on"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="Cursor 额度重置日"
            style="width: 100%"
            clearable
          />
          <p class="field-hint">一般由同步自动更新；仅在需锁定时手动填写</p>
        </el-form-item>
        <el-form-item label="API Key" :required="!editing">
          <div v-if="editing && editCredential" class="credential-meta">
            <el-tag :type="credentialTagType(editCredential)" size="small">
              {{ credentialBadgeLabel(editCredential) }}
            </el-tag>
            <span v-if="editCredential.key_hint" class="muted">当前：{{ editCredential.key_hint }}</span>
            <span v-if="editCredential.last_sync_at" class="muted">
              上次同步：{{ formatChinaTime(editCredential.last_sync_at) }}
            </span>
            <span v-if="editCredential.last_sync_error" class="sync-error">
              {{ editCredential.last_sync_error }}
            </span>
          </div>
          <el-input
            v-model="form.api_key"
            type="password"
            show-password
            :placeholder="editing ? '留空表示不更换；填写 crsr_… 则重新绑定' : 'crsr_…（必填，将自动获取套餐、账号与重置日）'"
            autocomplete="off"
          />
          <p class="field-hint">
            {{ editing ? '填写新 Key 并保存即可换绑并同步' : '套餐、账号标识、用量重置日由 Key 同步后自动写入' }}
          </p>
        </el-form-item>
      </el-form>
      <template #footer>
        <div class="dialog-footer">
          <el-button
            v-if="editing && canWrite"
            type="danger"
            text
            :loading="deleting"
            @click="removeAccount"
          >
            删除
          </el-button>
          <el-button
            v-if="editing && editCredential?.bound && canManageCredential(editing)"
            type="danger"
            text
            :loading="credentialUnbinding"
            @click="unbindCredential"
          >
            解绑 Key
          </el-button>
          <el-button
            v-if="editing && editCredential?.bound && canManageCredential(editing)"
            text
            :loading="credentialSyncing"
            @click="syncCredential"
          >
            立即同步
          </el-button>
          <div class="dialog-footer-spacer" />
          <el-button @click="dialogVisible = false">取消</el-button>
          <el-button type="primary" :loading="saving" @click="save">保存</el-button>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import client from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { formatChinaTime } from '@/utils/time'

const auth = useAuthStore()
const canWrite = computed(() => auth.hasPermission('accounts:write'))

interface Vendor {
  id: string
  name: string
  slug?: string
}
interface Plan {
  id: string
  vendor_id: string
  plan_name: string
  price_amount: number
  price_currency: string
  usage_submit_methods?: string[]
}
interface Member {
  id: string
  display_name: string
}
interface CredentialStatus {
  bound: boolean
  key_hint: string | null
  last_sync_at: string | null
  last_sync_status: string
  status?: string
  last_sync_error?: string | null
}
interface Account {
  id: string
  vendor_id: string
  vendor_name: string
  plan_id: string
  plan_name: string
  account_identifier: string
  status: string
  primary_member_id: string | null
  shared_note: string | null
  usage_resets_on: string | null
  suggest_dedicated: boolean
  /** Embedded by GET /api/v2/accounts (avoids N+1 /credentials). */
  credential?: CredentialStatus | null
}

const loading = ref(false)
const saving = ref(false)
const deleting = ref(false)
const accounts = ref<Account[]>([])
const vendors = ref<Vendor[]>([])
const plans = ref<Plan[]>([])
const members = ref<Member[]>([])
const dialogVisible = ref(false)
const editing = ref<Account | null>(null)
const editCredential = ref<CredentialStatus | null>(null)
const credentialMap = ref<Record<string, CredentialStatus>>({})
const credentialUnbinding = ref(false)
const credentialSyncing = ref(false)

const form = reactive({
  vendor_id: '',
  plan_id: '',
  account_identifier: '',
  status: 'shared',
  primary_member_id: null as string | null,
  shared_note: '',
  api_key: '',
  usage_resets_on: null as string | null,
})

const cursorVendor = computed(
  () => vendors.value.find((v) => v.slug === 'cursor' || v.name === 'Cursor') || null,
)

const editPlans = computed(() => {
  if (editing.value) {
    return plans.value.filter((p) => p.vendor_id === editing.value!.vendor_id)
  }
  if (!cursorVendor.value) return []
  return plans.value.filter((p) => p.vendor_id === cursorVendor.value!.id)
})

const isCursorAccount = computed(() => {
  if (editing.value) {
    return editing.value.vendor_name === 'Cursor'
  }
  return Boolean(cursorVendor.value)
})

function memberName(id: string | null) {
  if (!id) return ''
  return members.value.find((m) => m.id === id)?.display_name || ''
}

function statusLabel(s: string) {
  const map: Record<string, string> = {
    trial: '试用',
    shared: '共享',
    dedicated: '独立',
    suspended: '停用',
  }
  return map[s] || s
}

function statusType(s: string) {
  if (s === 'trial') return 'warning'
  if (s === 'dedicated') return 'success'
  if (s === 'suspended') return 'info'
  return ''
}

function isCursorRow(row: Account) {
  return row.vendor_name === 'Cursor'
}

function credentialState(status?: CredentialStatus | null): 'no_credential' | 'synced' | 'sync_failed' {
  if (!status?.bound || status.status === 'revoked') return 'no_credential'
  if (status.last_sync_status === 'failed') return 'sync_failed'
  return 'synced'
}

function credentialBadgeLabel(status?: CredentialStatus | null) {
  const map = {
    no_credential: '未绑定',
    synced: '已同步',
    sync_failed: '同步失败',
  }
  return map[credentialState(status)]
}

function credentialTagType(status?: CredentialStatus | null) {
  const state = credentialState(status)
  if (state === 'synced') return 'success'
  if (state === 'sync_failed') return 'danger'
  return 'info'
}

function canManageCredential(row: Account) {
  if (canWrite.value) return true
  return row.primary_member_id === auth.user?.id
}

function canOpenEdit(row: Account) {
  return canWrite.value || (isCursorRow(row) && canManageCredential(row))
}

function applyCredentialMapFromAccounts(rows: Account[]) {
  const map: Record<string, CredentialStatus> = {}
  for (const account of rows) {
    if (!isCursorRow(account)) continue
    map[account.id] = account.credential ?? {
      bound: false,
      key_hint: null,
      last_sync_at: null,
      last_sync_status: 'never',
    }
  }
  credentialMap.value = map
}

async function loadEditCredential(accountId: string) {
  // Prefer embedded list payload; fall back to single GET only if missing.
  const embedded = accounts.value.find((a) => a.id === accountId)?.credential
  if (embedded) {
    editCredential.value = embedded
    credentialMap.value[accountId] = embedded
    return
  }
  try {
    const res = await client.get(`/api/v2/accounts/${accountId}/credentials`)
    editCredential.value = res.data
    credentialMap.value[accountId] = res.data
  } catch {
    editCredential.value = {
      bound: false,
      key_hint: null,
      last_sync_at: null,
      last_sync_status: 'never',
    }
  }
}

function credentialErrorDetail(e: unknown) {
  const err = e as { response?: { data?: { detail?: unknown } } }
  const detail = err.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object' && 'message' in detail) {
    return String((detail as { message?: string }).message || '绑定失败')
  }
  return '绑定失败'
}

async function unbindCredential() {
  if (!editing.value) return
  await ElMessageBox.confirm('解绑后将停止自动同步，确定？', '解绑 API Key', { type: 'warning' })
  credentialUnbinding.value = true
  try {
    await client.delete(`/api/v2/accounts/${editing.value.id}/credentials`)
    const cleared: CredentialStatus = {
      bound: false,
      key_hint: null,
      last_sync_at: null,
      last_sync_status: 'never',
    }
    editCredential.value = cleared
    credentialMap.value[editing.value.id] = cleared
    ElMessage.success('已解绑')
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } }
    ElMessage.error(err.response?.data?.detail || '解绑失败')
  } finally {
    credentialUnbinding.value = false
  }
}

async function syncCredential() {
  if (!editing.value) return
  credentialSyncing.value = true
  try {
    const res = await client.post(`/api/v2/accounts/${editing.value.id}/sync`)
    const updated: CredentialStatus = {
      bound: true,
      key_hint: editCredential.value?.key_hint ?? null,
      last_sync_at: res.data.last_sync_at,
      last_sync_status: res.data.last_sync_status,
    }
    editCredential.value = updated
    credentialMap.value[editing.value.id] = updated
    await loadAll()
    const refreshed = accounts.value.find((a) => a.id === editing.value?.id)
    if (refreshed) {
      editing.value = refreshed
      form.account_identifier = refreshed.account_identifier
      form.plan_id = refreshed.plan_id
      form.usage_resets_on = refreshed.usage_resets_on
    }
    ElMessage.success(`同步完成，${res.data.event_count} 条事件`)
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } }
    ElMessage.error(err.response?.data?.detail || '同步失败')
  } finally {
    credentialSyncing.value = false
  }
}

async function loadAll() {
  loading.value = true
  try {
    const [accRes, vendorRes, planRes, memberRes] = await Promise.all([
      client.get('/api/v2/accounts'),
      client.get('/api/v2/vendors'),
      client.get('/api/v2/plans'),
      client.get('/api/v2/members'),
    ])
    accounts.value = accRes.data
    vendors.value = vendorRes.data
    plans.value = planRes.data
    members.value = memberRes.data
    applyCredentialMapFromAccounts(accounts.value)
  } finally {
    loading.value = false
  }
}

function resetForm() {
  form.vendor_id = cursorVendor.value?.id || ''
  form.plan_id = editPlans.value[0]?.id || ''
  form.account_identifier = ''
  form.status = 'shared'
  form.primary_member_id = null
  form.shared_note = ''
  form.api_key = ''
  form.usage_resets_on = null
}

function openCreate() {
  if (!cursorVendor.value) {
    ElMessage.warning('未找到 Cursor 厂家，请先执行数据库初始化（seed）')
    return
  }
  editing.value = null
  editCredential.value = null
  resetForm()
  dialogVisible.value = true
}

async function openEdit(row: Account) {
  editing.value = row
  form.account_identifier = row.account_identifier
  form.plan_id = row.plan_id
  form.status = row.status === 'available' ? 'shared' : row.status
  form.primary_member_id = row.primary_member_id
  form.shared_note = row.shared_note || ''
  form.api_key = ''
  form.usage_resets_on = row.usage_resets_on
  editCredential.value = credentialMap.value[row.id] ?? null
  dialogVisible.value = true
  if (isCursorRow(row)) {
    await loadEditCredential(row.id)
  }
}

async function removeAccount() {
  if (!editing.value) return
  const label = editing.value.account_identifier || editing.value.id
  await ElMessageBox.confirm(
    `确定删除账号 ${label}？若无用量/Key 等关联数据将彻底删除，否则仅做删除标记并从台账隐藏。`,
    '删除账号',
    { type: 'warning', confirmButtonText: '删除', confirmButtonClass: 'el-button--danger' },
  )
  deleting.value = true
  try {
    const res = await client.delete(`/api/v2/accounts/${editing.value.id}`)
    const mode = res.data.mode as string
    ElMessage.success(mode === 'hard' ? '账号已删除' : '账号已标记删除（历史数据保留）')
    dialogVisible.value = false
    await loadAll()
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } }
    ElMessage.error(err.response?.data?.detail || '删除失败')
  } finally {
    deleting.value = false
  }
}

async function save() {
  saving.value = true
  try {
    if (editing.value) {
      const apiKey = form.api_key.trim()
      if (apiKey && !apiKey.startsWith('crsr_')) {
        ElMessage.warning('API Key 须以 crsr_ 开头')
        return
      }
      if (!canWrite.value && !apiKey) {
        ElMessage.warning('请填写新的 API Key')
        return
      }
      if (canWrite.value) {
        const patchBody: Record<string, unknown> = {
          account_identifier: form.account_identifier.trim(),
          status: form.status,
          primary_member_id: form.primary_member_id,
          shared_note: form.shared_note || null,
          usage_resets_on: form.usage_resets_on,
        }
        if (form.plan_id !== editing.value.plan_id) {
          patchBody.plan_id = form.plan_id
        }
        await client.patch(`/api/v2/accounts/${editing.value.id}`, patchBody)
      }
      if (apiKey) {
        if (!canManageCredential(editing.value)) {
          ElMessage.error('无权更换 API Key')
          return
        }
        await client.post(`/api/v2/accounts/${editing.value.id}/credentials`, {
          api_key: apiKey,
        })
        ElMessage.success(canWrite.value ? '已更新并更换 API Key' : 'API Key 已更换')
      } else {
        ElMessage.success('已更新')
      }
    } else {
      if (!cursorVendor.value) {
        ElMessage.warning('未找到 Cursor 厂家，请先执行数据库初始化（seed）')
        return
      }
      const apiKey = form.api_key.trim()
      if (!apiKey) {
        ElMessage.warning('请填写 API Key')
        return
      }
      if (!apiKey.startsWith('crsr_')) {
        ElMessage.warning('API Key 须以 crsr_ 开头')
        return
      }
      await client.post('/api/v2/accounts', {
        vendor_id: cursorVendor.value.id,
        status: form.status,
        primary_member_id: form.primary_member_id,
        shared_note: form.shared_note || null,
        api_key: apiKey,
      })
      ElMessage.success('已创建并同步账号信息')
    }
    dialogVisible.value = false
    await loadAll()
  } catch (e: unknown) {
    const detail = credentialErrorDetail(e)
    ElMessage.error(detail === '绑定失败' ? '保存失败' : detail)
  } finally {
    saving.value = false
  }
}

onMounted(loadAll)
</script>

<style scoped>
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 20px;
}
.desc {
  color: #64748b;
  font-size: 14px;
  margin-top: 4px;
}
.header-actions {
  display: flex;
  gap: 12px;
}
.muted {
  color: #94a3b8;
}
.field-hint {
  margin-top: 6px;
  color: #94a3b8;
  font-size: 12px;
  line-height: 1.4;
}
.key-hint {
  font-size: 11px;
  margin-top: 2px;
}
.credential-meta {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 12px;
  font-size: 13px;
}
.sync-error {
  color: #dc2626;
  font-size: 12px;
}
.dialog-footer {
  display: flex;
  align-items: center;
  width: 100%;
}
.dialog-footer-spacer {
  flex: 1;
}
</style>
