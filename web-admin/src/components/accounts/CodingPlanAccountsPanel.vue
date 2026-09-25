<template>
  <div class="coding-plan-accounts" v-loading="loading">
    <el-table :data="accounts" stripe>
      <el-table-column label="账号标识" min-width="200">
        <template #default="{ row }">{{ accountLabel(row) }}</template>
      </el-table-column>
      <el-table-column label="套餐" width="120" prop="plan_name" />
      <el-table-column v-if="vendorSlug !== 'kimi'" label="站点/区域" width="140">
        <template #default="{ row }">{{ regionLabel(row) }}</template>
      </el-table-column>
      <el-table-column v-else label="端点" width="140">
        <template #default>api.kimi.com</template>
      </el-table-column>
      <el-table-column v-if="vendorSlug === 'glm'" label="版本" width="88">
        <template #default="{ row }">
          <el-tag v-if="isTeamRow(row)" size="small" type="warning">团队</el-tag>
          <el-tag v-else size="small" type="info">个人</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="类型" width="100">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)">{{ statusLabel(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="API Key" width="140">
        <template #default="{ row }">
          <el-tag :type="credentialTagType(credentialMap[row.id])" size="small">
            {{ credentialBadgeLabel(credentialMap[row.id]) }}
          </el-tag>
          <div v-if="credentialMap[row.id]?.key_hint" class="muted key-hint">
            {{ credentialMap[row.id]?.key_hint }}
          </div>
        </template>
      </el-table-column>
      <el-table-column label="主使用人" width="120">
        <template #default="{ row }">{{ memberName(row.primary_member_id) || '—' }}</template>
      </el-table-column>
      <el-table-column label="操作" width="90" fixed="right">
        <template #default="{ row }">
          <el-button v-if="canOpenEdit(row)" link type="primary" @click="openEdit(row)">编辑</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" :title="editing ? '编辑账号' : '新增账号'" width="520px">
      <el-form label-width="100px">
        <el-form-item v-if="vendorSlug === 'glm' && !editing" label="账号类型">
          <el-radio-group v-model="glmAccountKind">
            <el-radio-button value="personal">个人 Coding Plan</el-radio-button>
            <el-radio-button value="team">智谱团队版</el-radio-button>
          </el-radio-group>
          <p v-if="glmAccountKind === 'team'" class="field-hint">
            团队版固定国内 open.bigmodel.cn，需 API Key + 组织 ID + 项目 ID（与 cc-switch 一致）。
          </p>
        </el-form-item>
        <el-form-item v-if="vendorSlug === 'glm' && glmAccountKind === 'team' && !editing" label="组织 ID" required>
          <el-input v-model="form.glm_organization_id" placeholder="bigmodel-organization" />
        </el-form-item>
        <el-form-item v-if="vendorSlug === 'glm' && glmAccountKind === 'team' && !editing" label="项目 ID" required>
          <el-input v-model="form.glm_project_id" placeholder="bigmodel-project" />
        </el-form-item>
        <el-form-item v-if="showRegionSelect" label="站点/区域" required>
          <el-select v-model="form.api_region" style="width: 100%" :disabled="Boolean(editing)">
            <el-option v-for="o in regionOptions" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
        </el-form-item>
        <template v-if="vendorSlug === 'glm' && editing && isTeamRow(editing)">
          <el-form-item label="组织 ID">
            <el-input :model-value="editing.glm_organization_id || '—'" disabled />
          </el-form-item>
          <el-form-item label="项目 ID">
            <el-input :model-value="editing.glm_project_id || '—'" disabled />
          </el-form-item>
        </template>
        <el-form-item label="账号标识">
          <el-input
            v-model="form.account_identifier"
            placeholder="选填；留空保存后用 API Key 脱敏标识（不参与鉴权）"
          />
        </el-form-item>
        <el-form-item v-if="vendorSlug === 'glm' && editing && canWrite" label="套餐">
          <el-select v-model="form.plan_id" style="width: 100%">
            <el-option v-for="p in vendorPlans" :key="p.id" :label="p.plan_name" :value="p.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="类型">
          <el-select v-model="form.status" style="width: 100%">
            <el-option label="试用" value="trial" />
            <el-option label="共享" value="shared" />
            <el-option label="独立" value="dedicated" />
            <el-option label="停用" value="suspended" />
          </el-select>
        </el-form-item>
        <el-form-item label="主使用人">
          <el-select v-model="form.primary_member_id" clearable filterable style="width: 100%">
            <el-option v-for="m in members" :key="m.id" :label="m.display_name" :value="m.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.shared_note" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="API Key" :required="!editing">
          <el-input
            v-model="form.api_key"
            type="password"
            show-password
            :placeholder="editing ? '留空不更换' : '必填，保存后自动同步额度'"
            autocomplete="off"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button v-if="editing && canWrite" type="danger" text :loading="deleting" @click="removeAccount">删除</el-button>
        <el-button v-if="editing && editCredential?.bound" text :loading="credentialSyncing" @click="syncCredential">
          立即同步
        </el-button>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import client from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const props = defineProps<{
  vendorSlug: 'glm' | 'minimax' | 'kimi'
}>()

const emit = defineEmits<{ countChange: [count: number] }>()

const auth = useAuthStore()
const canWrite = computed(() => auth.hasPermission('accounts:write'))

const vendorLabel = computed(() => {
  if (props.vendorSlug === 'glm') return 'GLM'
  if (props.vendorSlug === 'kimi') return 'Kimi'
  return 'MiniMax'
})

const regionOptions = computed(() => {
  if (props.vendorSlug === 'glm') {
    return [
      { value: 'zai', label: '国际 (api.z.ai)' },
      { value: 'bigmodel', label: '国内 (bigmodel.cn)' },
    ]
  }
  if (props.vendorSlug === 'kimi') {
    return []
  }
  return [
    { value: 'cn', label: '国内 (minimaxi.com)' },
    { value: 'global', label: '国际 (minimax.io)' },
  ]
})

interface Account {
  id: string
  vendor_id: string
  vendor_slug?: string
  api_region?: string | null
  glm_organization_id?: string | null
  glm_project_id?: string | null
  plan_id: string
  plan_name: string
  account_identifier: string
  status: string
  primary_member_id: string | null
  shared_note: string | null
  credential?: CredentialStatus | null
}

interface CredentialStatus {
  bound: boolean
  key_hint: string | null
  last_sync_at: string | null
  last_sync_status: string
  last_sync_error?: string | null
}

const loading = ref(false)
const saving = ref(false)
const deleting = ref(false)
const accounts = ref<Account[]>([])
const vendor = ref<{ id: string; name: string } | null>(null)
const plans = ref<{ id: string; vendor_id: string; plan_name: string }[]>([])
const members = ref<{ id: string; display_name: string }[]>([])
const dialogVisible = ref(false)
const editing = ref<Account | null>(null)
const editCredential = ref<CredentialStatus | null>(null)
const credentialMap = ref<Record<string, CredentialStatus>>({})
const credentialSyncing = ref(false)
const glmAccountKind = ref<'personal' | 'team'>('personal')

const form = reactive({
  api_region: '',
  glm_organization_id: '',
  glm_project_id: '',
  plan_id: '',
  account_identifier: '',
  status: 'shared',
  primary_member_id: null as string | null,
  shared_note: '',
  api_key: '',
})

const showRegionSelect = computed(() => {
  if (props.vendorSlug === 'kimi') return false
  if (props.vendorSlug !== 'glm') return true
  if (editing.value) return !isTeamRow(editing.value)
  return glmAccountKind.value === 'personal'
})

const vendorPlans = computed(() => plans.value.filter((p) => p.vendor_id === vendor.value?.id))

function isTeamRow(row: Account) {
  return Boolean(row.glm_organization_id?.trim() && row.glm_project_id?.trim())
}

function regionLabel(row: Account | string | null | undefined) {
  if (row && typeof row === 'object') {
    if (isTeamRow(row)) return '国内团队版'
    return regionLabel(row.api_region)
  }
  const code = typeof row === 'string' ? row : null
  const o = regionOptions.value.find((x) => x.value === code)
  return o?.label || code || '—'
}

function memberName(id: string | null) {
  if (!id) return ''
  return members.value.find((m) => m.id === id)?.display_name || ''
}

function accountLabel(row: Account) {
  const id = (row.account_identifier || '').trim()
  if (id) return id
  return credentialMap.value[row.id]?.key_hint || row.id.slice(0, 8)
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

function credentialBadgeLabel(c?: CredentialStatus | null) {
  if (!c?.bound) return '未绑定'
  if (c.last_sync_status === 'success') return '已同步'
  if (c.last_sync_status === 'failed') return '同步失败'
  return '已绑定'
}

function credentialTagType(c?: CredentialStatus | null) {
  if (!c?.bound) return 'info'
  if (c.last_sync_status === 'success') return 'success'
  if (c.last_sync_status === 'failed') return 'danger'
  return 'warning'
}

function canManageCredential(row: Account) {
  if (canWrite.value) return true
  return row.primary_member_id === auth.user?.id
}

function canOpenEdit(row: Account) {
  return canWrite.value || canManageCredential(row)
}

function applyCredentialMap(list: Account[]) {
  const map: Record<string, CredentialStatus> = {}
  for (const a of list) {
    if (a.credential) map[a.id] = a.credential
  }
  credentialMap.value = map
}

async function loadAll() {
  loading.value = true
  try {
    const [accRes, vendorRes, planRes, memberRes] = await Promise.all([
      client.get('/api/v2/accounts', { params: { vendor_slug: props.vendorSlug } }),
      client.get('/api/v2/vendors'),
      client.get('/api/v2/plans'),
      client.get('/api/v2/members'),
    ])
    accounts.value = accRes.data
    plans.value = planRes.data
    members.value = memberRes.data
    vendor.value =
      vendorRes.data.find((v: { slug?: string }) => v.slug === props.vendorSlug) || null
    applyCredentialMap(accounts.value)
    emit('countChange', accounts.value.length)
  } finally {
    loading.value = false
  }
}

defineExpose({ loadAll, openCreate })

function resetForm() {
  glmAccountKind.value = 'personal'
  form.api_region = regionOptions.value[0]?.value || ''
  form.glm_organization_id = ''
  form.glm_project_id = ''
  form.plan_id = vendorPlans.value[0]?.id || ''
  form.account_identifier = ''
  form.status = 'shared'
  form.primary_member_id = null
  form.shared_note = ''
  form.api_key = ''
}

async function openCreate() {
  if (!vendor.value) {
    await loadAll()
  }
  if (!vendor.value) {
    ElMessage.warning(
      `未找到 ${vendorLabel.value} 厂家记录。请重启服务或执行：pulse init-v2 --seed`,
    )
    return
  }
  editing.value = null
  editCredential.value = null
  resetForm()
  dialogVisible.value = true
}

function openEdit(row: Account) {
  editing.value = row
  glmAccountKind.value = isTeamRow(row) ? 'team' : 'personal'
  form.api_region = row.api_region || regionOptions.value[0]?.value || ''
  form.glm_organization_id = row.glm_organization_id || ''
  form.glm_project_id = row.glm_project_id || ''
  form.plan_id = row.plan_id
  form.account_identifier = row.account_identifier
  form.status = row.status
  form.primary_member_id = row.primary_member_id
  form.shared_note = row.shared_note || ''
  form.api_key = ''
  editCredential.value = credentialMap.value[row.id] ?? null
  dialogVisible.value = true
}

async function syncCredential() {
  if (!editing.value) return
  credentialSyncing.value = true
  try {
    await client.post(`/api/v2/accounts/${editing.value.id}/sync`)
    ElMessage.success('同步完成')
    await loadAll()
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } }
    ElMessage.error(err.response?.data?.detail || '同步失败')
  } finally {
    credentialSyncing.value = false
  }
}

async function removeAccount() {
  if (!editing.value) return
  await ElMessageBox.confirm(`确定删除账号 ${accountLabel(editing.value)}？`, '删除账号', {
    type: 'warning',
  })
  deleting.value = true
  try {
    await client.delete(`/api/v2/accounts/${editing.value.id}`)
    dialogVisible.value = false
    await loadAll()
  } finally {
    deleting.value = false
  }
}

async function save() {
  if (!vendor.value) return
  saving.value = true
  try {
    if (editing.value) {
      const apiKey = form.api_key.trim()
      if (!canWrite.value && !apiKey) {
        ElMessage.warning('请填写新的 API Key')
        return
      }
      if (canWrite.value) {
        const patch: Record<string, unknown> = {
          status: form.status,
          primary_member_id: form.primary_member_id,
          shared_note: form.shared_note || null,
        }
        const idLabel = form.account_identifier.trim()
        if (idLabel) patch.account_identifier = idLabel
        if (props.vendorSlug === 'glm' && form.plan_id) patch.plan_id = form.plan_id
        await client.patch(`/api/v2/accounts/${editing.value.id}`, patch)
      }
      if (apiKey) {
        if (!canManageCredential(editing.value)) {
          ElMessage.error('无权更换 API Key')
          return
        }
        await client.post(`/api/v2/accounts/${editing.value.id}/credentials`, { api_key: apiKey })
        ElMessage.success(canWrite.value ? '已更新并更换 API Key' : 'API Key 已更换')
      } else if (canWrite.value) {
        ElMessage.success('已更新')
      }
    } else {
      if (!form.api_key.trim()) {
        ElMessage.warning('请填写 API Key')
        return
      }
      const payload: Record<string, unknown> = {
        vendor_id: vendor.value.id,
        api_region: form.api_region,
        status: form.status,
        primary_member_id: form.primary_member_id,
        shared_note: form.shared_note || null,
        api_key: form.api_key.trim(),
        plan_id: props.vendorSlug === 'glm' ? form.plan_id || undefined : undefined,
      }
      if (props.vendorSlug === 'glm' && glmAccountKind.value === 'team') {
        payload.api_region = 'bigmodel'
        payload.glm_organization_id = form.glm_organization_id.trim()
        payload.glm_project_id = form.glm_project_id.trim()
      }
      const idLabel = form.account_identifier.trim()
      if (idLabel) payload.account_identifier = idLabel
      await client.post('/api/v2/accounts', payload)
    }
    dialogVisible.value = false
    await loadAll()
    if (!editing.value) {
      ElMessage.success('已创建并同步账号')
    }
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } }
    ElMessage.error(err.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

watch(glmAccountKind, (kind) => {
  if (props.vendorSlug === 'glm' && kind === 'team') {
    form.api_region = 'bigmodel'
  }
})

watch(
  () => props.vendorSlug,
  () => {
    void loadAll()
  },
)

onMounted(loadAll)
</script>

<style scoped>
.key-hint {
  font-size: 12px;
}
.field-hint {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.4;
}
</style>
