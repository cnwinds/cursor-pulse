<template>
  <div class="proxy-page" v-loading="loading">
    <header class="page-header">
      <div>
        <h2>代理 Key</h2>
        <p class="desc">入池账号供代理轮换；使用代理须在「脉冲 Key」单独创建 pk_…。畅享不限量记账，限额支持 token/费用/5h 窗口</p>
      </div>
      <div class="header-actions">
        <el-button v-if="canWrite" type="primary" @click="openCreate">新建 Key</el-button>
      </div>
    </header>

    <el-tabs v-model="tab">
      <el-tab-pane label="脉冲 Key" name="keys">
        <el-table :data="keys" style="width: 100%">
          <el-table-column prop="name" label="使用人" min-width="120" />
          <el-table-column label="模式" width="90">
            <template #default="{ row }">
              <el-tag :type="row.mode === 'unlimited' ? 'success' : 'warning'">
                {{ row.mode === 'unlimited' ? '畅享' : '限额' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="用量 / 额度" min-width="200">
            <template #default="{ row }">
              <div v-if="row.mode === 'quota'">
                <div v-if="row.token_limit != null">token: {{ formatTokensM(row.total_tokens) }} / {{ formatTokensM(row.token_limit) }}</div>
                <div v-if="row.cost_limit_cents != null">费用: ${{ (row.total_cost_cents / 100).toFixed(2) }} / ${{ (row.cost_limit_cents / 100).toFixed(2) }}</div>
                <div v-if="row.window_5h_token_limit != null">5h窗口: {{ formatTokensM(row.window_5h_tokens) }} / {{ formatTokensM(row.window_5h_token_limit) }}</div>
                <div v-if="row.token_limit == null && row.cost_limit_cents == null && row.window_5h_token_limit == null">未配置额度</div>
              </div>
              <span v-else>{{ formatTokensM(row.total_tokens) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="110">
            <template #default="{ row }">
              <el-tag :type="statusType(row.status)">{{ statusLabel(row.status) }}</el-tag>
              <el-tooltip v-if="row.suspended_reason" :content="row.suspended_reason">
                <el-icon><WarningFilled /></el-icon>
              </el-tooltip>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="300" fixed="right">
            <template #default="{ row }">
              <el-button size="small" @click="openUsages(row)">用量</el-button>
              <el-dropdown
                v-if="canCopyCommand(row)"
                trigger="click"
                @command="(shell: ShellKind) => copyCommand(row, shell)"
              >
                <el-button size="small" type="primary" plain>复制命令</el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="powershell">Windows PowerShell</el-dropdown-item>
                    <el-dropdown-item command="bash">Linux / macOS</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
              <el-button v-if="canWrite" size="small" @click="openEdit(row)">编辑</el-button>
              <el-button v-if="canWrite && row.status === 'suspended'" size="small" type="warning" @click="resume(row)">恢复</el-button>
              <el-button v-if="canWrite && row.status !== 'revoked'" size="small" type="danger" @click="revoke(row)">吊销</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="代理池" name="pool">
        <p class="pool-hint">一行一 Cursor 台账账号；开启后仅其主 Key（primary）进入代理轮换池，借用 Key 不入池</p>
        <el-table :data="pool" style="width: 100%">
          <el-table-column prop="account_identifier" label="账号" min-width="160" />
          <el-table-column prop="plan_name" label="计划" width="120" />
          <el-table-column prop="primary_member_name" label="主责" width="120" />
          <el-table-column prop="active_credential_count" label="主 Key" width="90" />
          <el-table-column prop="status" label="状态" width="100" />
          <el-table-column label="入池" width="100">
            <template #default="{ row }">
              <el-switch
                :model-value="row.proxy_enabled"
                :disabled="!canWrite"
                @change="(val: boolean) => toggleAccount(row, val)"
              />
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="打分表" name="ranking">
        <div class="ranking-toolbar">
          <p class="pool-hint">与代理池下发给 Go 的选号顺序同源；在借人数仅展示、不参与过滤与打分。下方为已入池但被硬过滤排除的账号</p>
          <el-button size="small" @click="loadRanking">刷新</el-button>
        </div>
        <h4 class="usage-section-title">入选排序</h4>
        <el-table v-loading="rankingLoading" :data="ranking.ranked" style="width: 100%; margin-bottom: 20px">
          <el-table-column label="#" width="60">
            <template #default="{ $index }">{{ $index + 1 }}</template>
          </el-table-column>
          <el-table-column prop="account_identifier" label="账号" min-width="160" />
          <el-table-column prop="score" label="score" width="90" />
          <el-table-column prop="surplus_cents" label="surplus" width="100" />
          <el-table-column prop="urgency_cents_per_day" label="urgency/日" width="110" />
          <el-table-column prop="deadline" label="deadline" width="120" />
          <el-table-column prop="hours_to_deadline" label="距作废(h)" width="100" />
          <el-table-column prop="active_loans" label="在借" width="70" />
          <el-table-column prop="snapshot_freshness" label="新鲜度" width="90" />
        </el-table>
        <h4 class="usage-section-title">已排除</h4>
        <el-table v-loading="rankingLoading" :data="ranking.excluded" style="width: 100%">
          <el-table-column prop="account_identifier" label="账号" min-width="160" />
          <el-table-column label="原因" min-width="160">
            <template #default="{ row }">{{ exclusionReasonLabel(row.reason) }}</template>
          </el-table-column>
          <el-table-column prop="active_loans" label="在借" width="70" />
          <el-table-column prop="status" label="额度状态" width="110" />
          <el-table-column prop="deadline" label="deadline" width="120" />
          <el-table-column prop="hours_to_deadline" label="距作废(h)" width="100" />
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="createVisible" title="新建脉冲 Key" width="480px">
      <el-form label-width="120px">
        <el-form-item label="选择使用人" required>
          <el-select
            v-model="createForm.member_id"
            filterable
            placeholder="选择成员"
            style="width: 100%"
          >
            <el-option
              v-for="m in members"
              :key="m.id"
              :label="m.display_name"
              :value="m.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="模式" required>
          <el-radio-group v-model="createForm.mode">
            <el-radio value="unlimited">畅享（不限量）</el-radio>
            <el-radio value="quota">限额</el-radio>
          </el-radio-group>
        </el-form-item>
        <template v-if="createForm.mode === 'quota'">
          <el-form-item label="token 总额度">
            <el-input-number v-model="createForm.token_limit" :min="0" :step="1000000" placeholder="留空不限" />
          </el-form-item>
          <el-form-item label="费用额度(cents)">
            <el-input-number v-model="createForm.cost_limit_cents" :min="0" :step="100" />
          </el-form-item>
          <el-form-item label="5h 窗口 token">
            <el-input-number v-model="createForm.window_5h_token_limit" :min="0" :step="100000" />
          </el-form-item>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitCreate">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="createdVisible" title="Key 创建成功" width="560px" :close-on-click-modal="false">
      <el-alert type="success" :closable="false" title="已加密保存，管理员与使用人可随时复制启动命令" />
      <el-input v-model="createdKey" readonly style="margin-top: 12px">
        <template #append>
          <el-button @click="copyCreated">复制 Key</el-button>
        </template>
      </el-input>
      <div class="created-actions">
        <el-button type="primary" plain @click="copyCreatedCommand('powershell')">复制 PowerShell 命令</el-button>
        <el-button type="primary" plain @click="copyCreatedCommand('bash')">复制 Linux 命令</el-button>
      </div>
      <template #footer>
        <el-button type="primary" @click="createdVisible = false">完成</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="editVisible" title="编辑额度" width="480px">
      <el-form label-width="120px">
        <el-form-item label="使用人">
          <el-input v-model="editForm.name" />
        </el-form-item>
        <el-form-item label="token 总额度">
          <el-input-number v-model="editForm.token_limit" :min="0" :step="1000000" />
        </el-form-item>
        <el-form-item label="费用额度(cents)">
          <el-input-number v-model="editForm.cost_limit_cents" :min="0" :step="100" />
        </el-form-item>
        <el-form-item label="5h 窗口 token">
          <el-input-number v-model="editForm.window_5h_token_limit" :min="0" :step="100000" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitEdit">保存</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="usagesVisible" :title="`用量明细 - ${usagesKeyName}`" size="720px">
      <p class="usage-estimate-note">以下费用为本地价表估算，非 Cursor 官方账单。</p>
      <h4 class="usage-section-title">按台账账号汇总</h4>
      <el-table :data="usageByAccount" style="width: 100%; margin-bottom: 20px">
        <el-table-column label="账号" min-width="200">
          <template #default="{ row }">{{ formatAccountWithPrimary(row) }}</template>
        </el-table-column>
        <el-table-column prop="plan_name" label="计划" width="100" />
        <el-table-column prop="request_count" label="请求数" width="90" />
        <el-table-column label="tokens" width="100">
          <template #default="{ row }">{{ formatTokensM(row.total_tokens) }}</template>
        </el-table-column>
        <el-table-column label="费用" width="100">
          <template #default="{ row }">${{ ((row.cost_cents ?? 0) / 100).toFixed(2) }}</template>
        </el-table-column>
      </el-table>
      <h4 class="usage-section-title">按模型汇总</h4>
      <el-table :data="usageByModel" style="width: 100%; margin-bottom: 20px">
        <el-table-column prop="model" label="模型" min-width="140" />
        <el-table-column prop="request_count" label="请求数" width="90" />
        <el-table-column label="tokens" width="100">
          <template #default="{ row }">{{ formatTokensM(row.total_tokens) }}</template>
        </el-table-column>
        <el-table-column label="费用" width="100">
          <template #default="{ row }">${{ ((row.cost_cents ?? 0) / 100).toFixed(2) }}</template>
        </el-table-column>
      </el-table>
      <h4 class="usage-section-title">明细（最近）</h4>
      <el-table :data="usages" style="width: 100%">
        <el-table-column label="时间" width="170">
          <template #default="{ row }">{{ formatChinaTime(row.ts) }}</template>
        </el-table-column>
        <el-table-column label="账号" min-width="180">
          <template #default="{ row }">{{ formatAccountWithPrimary(row) }}</template>
        </el-table-column>
        <el-table-column prop="model" label="模型" min-width="120" />
        <el-table-column label="tokens" width="100">
          <template #default="{ row }">{{ formatTokensM(row.total_tokens) }}</template>
        </el-table-column>
        <el-table-column label="费用" width="100">
          <template #default="{ row }">${{ ((row.cost_cents ?? 0) / 100).toFixed(2) }}</template>
        </el-table-column>
      </el-table>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import client from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { formatChinaTime } from '@/utils/time'
import { formatTokensM } from '@/utils/usage'

type ShellKind = 'bash' | 'powershell'

interface MemberOption {
  id: string
  display_name: string
}

interface ProxyKeyRow {
  id: string
  key_hint: string
  name: string
  member_id: string
  member_name: string | null
  mode: string
  token_limit: number | null
  cost_limit_cents: number | null
  window_5h_token_limit: number | null
  status: string
  suspended_reason: string | null
  total_tokens: number
  total_cost_cents: number
  window_5h_tokens: number
  recoverable?: boolean
}

interface PoolAccount {
  id: string
  account_identifier: string
  plan_name: string | null
  status: string
  primary_member_name: string | null
  active_credential_count: number
  proxy_enabled: boolean
}

interface RankingRow {
  account_id: string
  account_identifier: string
  score?: number
  surplus_cents?: number
  urgency_cents_per_day?: number
  deadline?: string | null
  hours_to_deadline?: number | null
  active_loans?: number
  snapshot_freshness?: number
  reason?: string
  status?: string | null
}

interface RankingBoard {
  ranked: RankingRow[]
  excluded: RankingRow[]
}

interface UsageRow {
  id: string
  model: string | null
  account_identifier: string | null
  primary_member_name: string | null
  total_tokens: number
  cost_cents: number
  ts: string
}

interface UsageByAccountRow {
  account_id: string | null
  account_identifier: string
  primary_member_name: string | null
  plan_name: string | null
  request_count: number
  total_tokens: number
  cost_cents: number
}

interface UsageByModelRow {
  model: string
  request_count: number
  total_tokens: number
  cost_cents: number
}

const auth = useAuthStore()
const canWrite = computed(() => auth.hasPermission('proxy:write'))
const loading = ref(false)
const saving = ref(false)
const tab = ref('keys')
const keys = ref<ProxyKeyRow[]>([])
const pool = ref<PoolAccount[]>([])
const ranking = ref<RankingBoard>({ ranked: [], excluded: [] })
const rankingLoading = ref(false)
const rankingLoaded = ref(false)
const members = ref<MemberOption[]>([])
const usages = ref<UsageRow[]>([])
const usageByAccount = ref<UsageByAccountRow[]>([])
const usageByModel = ref<UsageByModelRow[]>([])

const createVisible = ref(false)
const createdVisible = ref(false)
const createdKey = ref('')
const createdProxyUrl = ref('http://127.0.0.1:8317')
const editVisible = ref(false)
const usagesVisible = ref(false)
const usagesKeyName = ref('')

const createForm = reactive({
  member_id: '',
  mode: 'unlimited',
  token_limit: null as number | null,
  cost_limit_cents: null as number | null,
  window_5h_token_limit: null as number | null,
})
const editForm = reactive({
  id: '',
  name: '',
  token_limit: null as number | null,
  cost_limit_cents: null as number | null,
  window_5h_token_limit: null as number | null,
})

function statusType(status: string) {
  if (status === 'active') return 'success'
  if (status === 'suspended') return 'warning'
  return 'danger'
}

function formatAccountWithPrimary(row: {
  account_identifier?: string | null
  primary_member_name?: string | null
}) {
  const account = row.account_identifier || '—'
  if (!row.primary_member_name) return account
  return `${account}（${row.primary_member_name}）`
}

function statusLabel(status: string) {
  return { active: '正常', suspended: '已停用', revoked: '已吊销' }[status] ?? status
}

function exclusionReasonLabel(reason: string | undefined) {
  return (
    {
      no_snapshot: '无额度快照',
      exhausted: '额度已耗尽',
      exhausts_before_reset: '号主将在重置前耗尽',
      loan_cap: '在借达上限',
      coverage_too_short: '距作废过短',
    }[reason || ''] ?? (reason || '—')
  )
}

function canCopyCommand(row: ProxyKeyRow) {
  if (!row.recoverable) return false
  if (canWrite.value) return true
  return auth.hasPermission('proxy:read') && row.member_id === auth.user?.id
}

function buildLocalCommand(shell: ShellKind, proxyUrl: string, plaintext: string) {
  if (shell === 'powershell') {
    return `$env:HTTPS_PROXY = "${proxyUrl}"\n$env:CURSOR_API_KEY = "${plaintext}"\nagent -k`
  }
  return `export HTTPS_PROXY="${proxyUrl}"\nexport CURSOR_API_KEY="${plaintext}"\nagent -k`
}

async function load() {
  loading.value = true
  try {
    const [keysRes, poolRes] = await Promise.all([
      client.get('/api/v2/proxy-keys'),
      client.get('/api/v2/proxy-pool/accounts'),
    ])
    keys.value = keysRes.data
    pool.value = poolRes.data
  } finally {
    loading.value = false
  }
}

async function loadMembers() {
  try {
    const res = await client.get('/api/v2/members')
    members.value = (res.data as MemberOption[]).map((m) => ({
      id: m.id,
      display_name: m.display_name,
    }))
  } catch {
    members.value = []
  }
}

async function openCreate() {
  createForm.member_id = ''
  createForm.mode = 'unlimited'
  createForm.token_limit = null
  createForm.cost_limit_cents = null
  createForm.window_5h_token_limit = null
  if (!members.value.length) await loadMembers()
  createVisible.value = true
}

async function submitCreate() {
  if (!createForm.member_id) {
    ElMessage.error('请选择使用人')
    return
  }
  saving.value = true
  try {
    const res = await client.post('/api/v2/proxy-keys', {
      member_id: createForm.member_id,
      mode: createForm.mode,
      token_limit: createForm.mode === 'quota' ? createForm.token_limit : null,
      cost_limit_cents: createForm.mode === 'quota' ? createForm.cost_limit_cents : null,
      window_5h_token_limit: createForm.mode === 'quota' ? createForm.window_5h_token_limit : null,
    })
    createdKey.value = res.data.plaintext_key
    createdProxyUrl.value = res.data.proxy_url || 'http://127.0.0.1:8317'
    createVisible.value = false
    createdVisible.value = true
    await load()
  } catch {
    ElMessage.error('创建失败')
  } finally {
    saving.value = false
  }
}

async function copyCreated() {
  await navigator.clipboard.writeText(createdKey.value)
  ElMessage.success('已复制 Key')
}

async function copyCreatedCommand(shell: ShellKind) {
  const cmd = buildLocalCommand(shell, createdProxyUrl.value, createdKey.value)
  await navigator.clipboard.writeText(cmd)
  ElMessage.success(shell === 'powershell' ? '已复制 PowerShell 命令' : '已复制 Linux 命令')
}

async function copyCommand(row: ProxyKeyRow, shell: ShellKind) {
  try {
    const res = await client.get(`/api/v2/proxy-keys/${row.id}/client-setup`, {
      params: { shell },
    })
    await navigator.clipboard.writeText(res.data.command)
    ElMessage.success(shell === 'powershell' ? '已复制 PowerShell 命令' : '已复制 Linux 命令')
  } catch (err: any) {
    const detail = err?.response?.data?.detail
    ElMessage.error(typeof detail === 'string' ? detail : '复制失败')
  }
}

function openEdit(row: ProxyKeyRow) {
  editForm.id = row.id
  editForm.name = row.name
  editForm.token_limit = row.token_limit
  editForm.cost_limit_cents = row.cost_limit_cents
  editForm.window_5h_token_limit = row.window_5h_token_limit
  editVisible.value = true
}

async function submitEdit() {
  if (!editForm.name.trim()) {
    ElMessage.error('请填写使用人')
    return
  }
  saving.value = true
  try {
    await client.patch(`/api/v2/proxy-keys/${editForm.id}`, {
      name: editForm.name.trim(),
      token_limit: editForm.token_limit,
      cost_limit_cents: editForm.cost_limit_cents,
      window_5h_token_limit: editForm.window_5h_token_limit,
    })
    editVisible.value = false
    ElMessage.success('已保存')
    await load()
  } catch {
    ElMessage.error('保存失败')
  } finally {
    saving.value = false
  }
}

async function revoke(row: ProxyKeyRow) {
  try {
    await ElMessageBox.confirm(`确定吊销「${row.name}」？吊销后不可恢复。`, '吊销确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await client.post(`/api/v2/proxy-keys/${row.id}/revoke`)
    ElMessage.success('已吊销')
    await load()
  } catch {
    ElMessage.error('吊销失败')
  }
}

async function resume(row: ProxyKeyRow) {
  try {
    await client.post(`/api/v2/proxy-keys/${row.id}/resume`)
    ElMessage.success('已恢复')
  } catch {
    ElMessage.error('恢复失败：额度仍超限，请先调高额度')
  }
  await load()
}

async function openUsages(row: ProxyKeyRow) {
  usagesKeyName.value = row.name
  usages.value = []
  usageByAccount.value = []
  usageByModel.value = []
  usagesVisible.value = true
  try {
    const res = await client.get(`/api/v2/proxy-keys/${row.id}/usages`)
    usageByAccount.value = res.data.by_account || []
    usageByModel.value = res.data.by_model || []
    usages.value = res.data.items || []
  } catch {
    ElMessage.error('用量加载失败')
  }
}

async function loadRanking() {
  rankingLoading.value = true
  try {
    const res = await client.get('/api/v2/proxy-pool/ranking')
    ranking.value = {
      ranked: res.data.ranked || [],
      excluded: res.data.excluded || [],
    }
    rankingLoaded.value = true
  } catch {
    ElMessage.error('打分表加载失败')
  } finally {
    rankingLoading.value = false
  }
}

watch(tab, (name) => {
  if (name === 'ranking' && !rankingLoaded.value) {
    void loadRanking()
  }
})

async function toggleAccount(row: PoolAccount, val: boolean) {
  try {
    await client.post(`/api/v2/proxy-pool/accounts/${row.id}`, { proxy_enabled: val })
    row.proxy_enabled = val
  } catch {
    ElMessage.error('操作失败')
  }
}

onMounted(load)
</script>

<style scoped>
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 16px;
}
.page-header h2 {
  margin: 0 0 4px;
}
.desc {
  color: var(--el-text-color-secondary);
  font-size: 13px;
  margin: 0;
}
.pool-hint {
  color: var(--el-text-color-secondary);
  font-size: 13px;
  margin: 0 0 12px;
}
.ranking-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 4px;
}
.ranking-toolbar .pool-hint {
  margin-bottom: 0;
  flex: 1;
}
.created-actions {
  display: flex;
  gap: 8px;
  margin-top: 12px;
  flex-wrap: wrap;
}
.usage-section-title {
  margin: 0 0 8px;
  font-size: 14px;
  font-weight: 600;
}
.usage-estimate-note {
  margin: 0 0 12px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
</style>
