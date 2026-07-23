<template>
  <div class="accounts-page" v-loading="loading">
    <header class="page-header">
      <div>
        <h2>AI 工具账号台账</h2>
        <p class="desc">管理各厂家账号、主使用人与套餐。用量与额度请查看「额度看板」。</p>
      </div>
      <div class="header-actions">
        <el-button type="primary" @click="openCreate">新增账号</el-button>
      </div>
    </header>

    <el-table :data="accounts" stripe>
      <el-table-column label="账号" min-width="220">
        <template #default="{ row }">
          {{ row.account_identifier || '—' }}
        </template>
      </el-table-column>
      <el-table-column label="厂家" width="100" prop="vendor_name" />
      <el-table-column label="套餐" width="120" prop="plan_name" />
      <el-table-column label="状态" width="100">
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
      <el-table-column label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <el-button
            v-if="canWrite && supportsManual(row)"
            link
            @click="openManual(row)"
          >上报</el-button>
          <el-button
            v-if="isCursorRow(row) && canManageCredential(row)"
            link
            @click="openCredential(row)"
          >Key</el-button>
          <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" :title="editing ? '编辑账号' : '新增账号'" width="520px">
      <el-form label-width="100px">
        <el-form-item v-if="!editing" label="厂家">
          <el-select v-model="form.vendor_id" style="width: 100%" @change="onVendorChange">
            <el-option v-for="v in vendors" :key="v.id" :label="v.name" :value="v.id" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="!editing" label="套餐">
          <el-select v-model="form.plan_id" style="width: 100%">
            <el-option
              v-for="p in filteredPlans"
              :key="p.id"
              :label="`${p.plan_name} (${p.price_amount} ${p.price_currency})`"
              :value="p.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item v-if="editing" label="套餐">
          <el-select v-model="form.plan_id" style="width: 100%">
            <el-option
              v-for="p in editPlans"
              :key="p.id"
              :label="`${p.plan_name} (${p.price_amount} ${p.price_currency})`"
              :value="p.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item v-if="editing && isCursorAccount" label="升级生效日">
          <el-date-picker
            v-model="form.plan_effective_from"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="套餐变更日期，如 2026-06-24"
            style="width: 100%"
            clearable
          />
        </el-form-item>
        <el-form-item v-if="editing && isCursorAccount" label="原套餐">
          <el-select v-model="form.previous_plan_id" clearable style="width: 100%" placeholder="续费升级前档位">
            <el-option
              v-for="p in editPlans"
              :key="p.id"
              :label="`${p.plan_name} (${p.price_amount} ${p.price_currency})`"
              :value="p.id"
            />
          </el-select>
          <p class="field-hint">6/24 从 Pro 升到 Pro+ 时：原套餐选 Pro，生效日填 2026-06-24</p>
        </el-form-item>
        <el-form-item label="账号标识">
          <el-input v-model="form.account_identifier" placeholder="邮箱或登录名（可留空，绑定 Key 后自动填入）" />
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="form.status" style="width: 100%">
            <el-option label="试用" value="trial" />
            <el-option label="共享" value="shared" />
            <el-option label="独立" value="dedicated" />
            <el-option label="可用" value="available" />
            <el-option label="停用" value="suspended" />
          </el-select>
        </el-form-item>
        <el-form-item label="主使用人">
          <el-select v-model="form.primary_member_id" clearable filterable style="width: 100%">
            <el-option
              v-for="m in members"
              :key="m.id"
              :label="m.display_name"
              :value="m.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="共享说明">
          <el-input v-model="form.shared_note" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item v-if="isCursorAccount" label="用量重置">
          <el-date-picker
            v-model="form.usage_resets_on"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="Cursor 额度重置日"
            style="width: 100%"
            clearable
          />
          <p class="field-hint">在 Cursor Dashboard → Usage 查看 Resets on 日期</p>
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
          <div class="dialog-footer-spacer" />
          <el-button @click="dialogVisible = false">取消</el-button>
          <el-button type="primary" :loading="saving" @click="save">保存</el-button>
        </div>
      </template>
    </el-dialog>

    <el-dialog v-model="credentialVisible" title="Cursor API Key" width="480px">
      <p class="manual-hint">
        账号：{{ credentialAccount?.account_identifier || '（未填写，绑定后自动填入）' }}
      </p>
      <div v-if="credentialStatus" class="credential-meta">
        <el-tag :type="credentialTagType(credentialStatus)" size="small">
          {{ credentialBadgeLabel(credentialStatus) }}
        </el-tag>
        <span v-if="credentialStatus.key_hint" class="muted">密钥：{{ credentialStatus.key_hint }}</span>
        <span v-if="credentialStatus.last_sync_at" class="muted">
          上次同步：{{ formatChinaTime(credentialStatus.last_sync_at) }}
        </span>
        <span v-if="credentialStatus.last_sync_error" class="sync-error">
          {{ credentialStatus.last_sync_error }}
        </span>
      </div>
      <el-form label-width="90px">
        <el-form-item label="API Key">
          <el-input
            v-model="apiKeyInput"
            type="password"
            show-password
            placeholder="crsr_..."
            autocomplete="off"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button
          v-if="credentialStatus?.bound"
          type="danger"
          plain
          :loading="credentialUnbinding"
          @click="unbindCredential"
        >解绑</el-button>
        <el-button
          v-if="canWrite && credentialStatus?.bound"
          :loading="credentialSyncing"
          @click="syncCredential"
        >立即同步</el-button>
        <el-button @click="credentialVisible = false">取消</el-button>
        <el-button type="primary" :loading="credentialBinding" @click="bindCredential">绑定</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="manualVisible" title="手工上报用量" width="420px">
      <p class="manual-hint">账号：{{ manualAccount?.account_identifier }}（{{ manualAccount?.vendor_name }}）</p>
      <el-form label-width="80px">
        <el-form-item label="账期">
          <el-select v-model="manualPeriod" style="width: 100%">
            <el-option v-for="p in periodOptions" :key="p" :label="p" :value="p" />
          </el-select>
        </el-form-item>
        <el-form-item label="主指标">
          <el-input-number v-model="manualValue" :min="0" :precision="2" style="width: 100%" />
        </el-form-item>
        <el-form-item label="单位">
          <el-select v-model="manualUnit" clearable style="width: 100%">
            <el-option label="calls（调用次数）" value="calls" />
            <el-option label="messages（消息）" value="messages" />
            <el-option label="prompts" value="prompts" />
            <el-option label="CNY" value="cny" />
            <el-option label="USD" value="usd" />
          </el-select>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="manualNote" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="manualVisible = false">取消</el-button>
        <el-button type="primary" :loading="manualSaving" @click="submitManual">提交</el-button>
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
import type { UsageSummary } from '@/utils/usage'

const auth = useAuthStore()
const canWrite = computed(() => auth.hasPermission('accounts:write'))

interface Vendor {
  id: string
  name: string
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
}
interface CredentialStatus {
  bound: boolean
  key_hint: string | null
  last_sync_at: string | null
  last_sync_status: string
  status?: string
  last_sync_error?: string | null
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
const manualVisible = ref(false)
const manualSaving = ref(false)
const manualAccount = ref<Account | null>(null)
const manualValue = ref<number>(0)
const manualUnit = ref<string>('')
const manualNote = ref('')
const manualPeriod = ref('')
const credentialVisible = ref(false)
const credentialAccount = ref<Account | null>(null)
const credentialStatus = ref<CredentialStatus | null>(null)
const credentialMap = ref<Record<string, CredentialStatus>>({})
const apiKeyInput = ref('')
const credentialBinding = ref(false)
const credentialUnbinding = ref(false)
const credentialSyncing = ref(false)

const now = new Date()
const periodOptions = computed(() => {
  const list: string[] = []
  for (let i = 0; i < 6; i++) {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1)
    list.push(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`)
  }
  return list
})

const form = reactive({
  vendor_id: '',
  plan_id: '',
  account_identifier: '',
  status: 'shared',
  primary_member_id: null as string | null,
  shared_note: '',
  usage_resets_on: null as string | null,
  plan_effective_from: null as string | null,
  previous_plan_id: null as string | null,
  plan_change_note: '',
})

const editPlans = computed(() => {
  if (editing.value) {
    return plans.value.filter((p) => p.vendor_id === editing.value!.vendor_id)
  }
  return filteredPlans.value
})

const isCursorAccount = computed(() => {
  if (editing.value) {
    return editing.value.vendor_name === 'Cursor'
  }
  const vendor = vendors.value.find((v) => v.id === form.vendor_id)
  return vendor?.name === 'Cursor'
})

const filteredPlans = computed(() => plans.value.filter((p) => p.vendor_id === form.vendor_id))

function memberName(id: string | null) {
  if (!id) return ''
  return members.value.find((m) => m.id === id)?.display_name || ''
}

function statusLabel(s: string) {
  const map: Record<string, string> = {
    trial: '试用',
    shared: '共享',
    dedicated: '独立',
    available: '可用',
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

async function loadCredentialStatuses() {
  const cursorAccounts = accounts.value.filter(isCursorRow)
  const entries = await Promise.all(
    cursorAccounts.map(async (account) => {
      try {
        const res = await client.get(`/api/v2/accounts/${account.id}/credentials`)
        return [account.id, res.data as CredentialStatus] as const
      } catch {
        return [account.id, { bound: false, key_hint: null, last_sync_at: null, last_sync_status: 'never' }] as const
      }
    }),
  )
  credentialMap.value = Object.fromEntries(entries)
}

async function openCredential(row: Account) {
  credentialAccount.value = row
  apiKeyInput.value = ''
  credentialVisible.value = true
  try {
    const res = await client.get(`/api/v2/accounts/${row.id}/credentials`)
    credentialStatus.value = res.data
    credentialMap.value[row.id] = res.data
  } catch {
    credentialStatus.value = { bound: false, key_hint: null, last_sync_at: null, last_sync_status: 'never' }
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

async function bindCredential() {
  if (!credentialAccount.value) return
  const apiKey = apiKeyInput.value.trim()
  if (!apiKey.startsWith('crsr_')) {
    ElMessage.warning('API Key 须以 crsr_ 开头')
    return
  }
  credentialBinding.value = true
  try {
    const res = await client.post(`/api/v2/accounts/${credentialAccount.value.id}/credentials`, {
      api_key: apiKey,
    })
    credentialStatus.value = res.data
    credentialMap.value[credentialAccount.value.id] = res.data
    apiKeyInput.value = ''
    await loadAll()
    const updated = accounts.value.find((a) => a.id === credentialAccount.value?.id)
    if (updated) credentialAccount.value = updated
    ElMessage.success('API Key 已绑定')
  } catch (e: unknown) {
    ElMessage.error(credentialErrorDetail(e))
  } finally {
    credentialBinding.value = false
  }
}

async function unbindCredential() {
  if (!credentialAccount.value) return
  await ElMessageBox.confirm('解绑后将停止自动同步，确定？', '解绑 API Key', { type: 'warning' })
  credentialUnbinding.value = true
  try {
    await client.delete(`/api/v2/accounts/${credentialAccount.value.id}/credentials`)
    const cleared: CredentialStatus = {
      bound: false,
      key_hint: null,
      last_sync_at: null,
      last_sync_status: 'never',
    }
    credentialStatus.value = cleared
    credentialMap.value[credentialAccount.value.id] = cleared
    ElMessage.success('已解绑')
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } }
    ElMessage.error(err.response?.data?.detail || '解绑失败')
  } finally {
    credentialUnbinding.value = false
  }
}

async function syncCredential() {
  if (!credentialAccount.value) return
  credentialSyncing.value = true
  try {
    const res = await client.post(`/api/v2/accounts/${credentialAccount.value.id}/sync`)
    const updated: CredentialStatus = {
      bound: true,
      key_hint: credentialStatus.value?.key_hint ?? null,
      last_sync_at: res.data.last_sync_at,
      last_sync_status: res.data.last_sync_status,
    }
    credentialStatus.value = updated
    credentialMap.value[credentialAccount.value.id] = updated
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
    if (!manualPeriod.value) manualPeriod.value = periodOptions.value[0]
    await loadCredentialStatuses()
  } finally {
    loading.value = false
  }
}

function resetForm() {
  form.vendor_id = vendors.value[0]?.id || ''
  form.plan_id = ''
  form.account_identifier = ''
  form.status = 'shared'
  form.primary_member_id = null
  form.shared_note = ''
  form.usage_resets_on = null
  form.plan_effective_from = null
  form.previous_plan_id = null
  form.plan_change_note = ''
}

function openCreate() {
  editing.value = null
  resetForm()
  dialogVisible.value = true
}

function openEdit(row: Account) {
  editing.value = row
  form.account_identifier = row.account_identifier
  form.plan_id = row.plan_id
  form.status = row.status
  form.primary_member_id = row.primary_member_id
  form.shared_note = row.shared_note || ''
  form.usage_resets_on = row.usage_resets_on
  form.plan_effective_from = null
  form.previous_plan_id = null
  form.plan_change_note = ''
  dialogVisible.value = true
}

function onVendorChange() {
  form.plan_id = filteredPlans.value[0]?.id || ''
}

function supportsManual(row: Account) {
  const plan = plans.value.find((p) => p.id === row.plan_id)
  const methods = plan?.usage_submit_methods || []
  return methods.includes('manual') || methods.includes('screenshot')
}

async function openManual(row: Account) {
  manualAccount.value = row
  manualPeriod.value = periodOptions.value[0]
  manualValue.value = 0
  manualUnit.value = ''
  manualNote.value = ''
  try {
    const res = await client.get('/api/v2/usage-summaries', {
      params: { period: manualPeriod.value },
    })
    const summary = (res.data as UsageSummary[]).find((s) => s.account_id === row.id)
    if (summary) {
      manualValue.value = summary.primary_metric_value || 0
      manualUnit.value = summary.primary_metric_unit || ''
    }
  } catch {
    /* ignore */
  }
  manualVisible.value = true
}

async function submitManual() {
  if (!manualAccount.value || manualValue.value <= 0) {
    ElMessage.warning('请填写有效用量')
    return
  }
  manualSaving.value = true
  try {
    await client.post(`/api/v2/accounts/${manualAccount.value.id}/usage/manual`, {
      period: manualPeriod.value,
      metric_value: manualValue.value,
      metric_unit: manualUnit.value || undefined,
      note: manualNote.value || undefined,
    })
    ElMessage.success('用量已入库')
    manualVisible.value = false
  } catch {
    ElMessage.error('提交失败')
  } finally {
    manualSaving.value = false
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
      const patchBody: Record<string, unknown> = {
        account_identifier: form.account_identifier.trim(),
        status: form.status,
        primary_member_id: form.primary_member_id,
        shared_note: form.shared_note || null,
        usage_resets_on: form.usage_resets_on,
      }
      if (form.plan_id !== editing.value.plan_id) {
        patchBody.plan_id = form.plan_id
        if (form.plan_effective_from) patchBody.plan_effective_from = form.plan_effective_from
        if (form.plan_change_note) patchBody.plan_change_note = form.plan_change_note
      }
      if (form.previous_plan_id && form.plan_effective_from) {
        patchBody.previous_plan_id = form.previous_plan_id
        patchBody.plan_effective_from = form.plan_effective_from
        if (form.plan_change_note) patchBody.plan_change_note = form.plan_change_note
      }
      await client.patch(`/api/v2/accounts/${editing.value.id}`, patchBody)
      ElMessage.success('已更新')
    } else {
      if (!form.plan_id) {
        ElMessage.warning('请选择套餐')
        return
      }
      await client.post('/api/v2/accounts', {
        ...form,
        account_identifier: form.account_identifier.trim(),
        usage_resets_on: isCursorAccount.value ? form.usage_resets_on : null,
      })
      ElMessage.success('已创建')
    }
    dialogVisible.value = false
    await loadAll()
  } catch (e: unknown) {
    ElMessage.error('保存失败')
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
.manual-hint {
  margin-bottom: 12px;
  color: #64748b;
  font-size: 14px;
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
