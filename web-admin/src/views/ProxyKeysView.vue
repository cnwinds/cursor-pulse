<template>
  <div class="proxy-page" v-loading="loading">
    <header class="page-header">
      <div>
        <h2>共享池代理</h2>
        <p class="desc">多账号入池、按额度与到期智能轮换。成员须先创建接入密钥（pk_…）并经 HTTPS 代理使用 Cursor Agent；可按 5 小时 / 7 天费用窗口限额，留空不限</p>
      </div>
      <div class="header-actions">
        <el-button v-if="canWrite" type="primary" @click="openCreate">新建接入密钥</el-button>
      </div>
    </header>

    <el-tabs v-model="tab">
      <el-tab-pane label="接入密钥" name="keys">
        <el-table :data="keys" style="width: 100%">
          <el-table-column prop="name" label="使用人" min-width="120" />
          <el-table-column label="用量 / 额度" min-width="220">
            <template #default="{ row }">
              <div v-if="row.window_5h_cost_usd != null || row.window_7d_cost_usd != null">
                <div v-if="row.window_5h_cost_usd != null">
                  5h: ${{ formatUsd(row.window_5h_cost_cents) }} / ${{ row.window_5h_cost_usd }}
                </div>
                <div v-if="row.window_7d_cost_usd != null">
                  7d: ${{ formatUsd(row.window_7d_cost_cents) }} / ${{ row.window_7d_cost_usd }}
                </div>
              </div>
              <span v-else>不限 · 累计 ${{ formatUsd(row.total_cost_cents) }}</span>
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

      <el-tab-pane label="账号池" name="pool">
        <p class="pool-hint">开启入池后，该账号主 Key 参与共享池轮换；额度与排序见「打分表」</p>
        <el-table :data="pool" style="width: 100%">
          <el-table-column label="账号" min-width="200">
            <template #default="{ row }">
              <div>{{ row.account_identifier }}</div>
              <div v-if="row.primary_member_name" class="account-sub">{{ row.primary_member_name }}</div>
            </template>
          </el-table-column>
          <el-table-column label="就绪" width="160">
            <template #default="{ row }">
              <el-tooltip
                v-if="poolReadyTooltip(row)"
                :content="poolReadyTooltip(row)!"
              >
                <el-tag :type="poolReadyType(row)" size="small">{{ poolReadyLabel(row) }}</el-tag>
              </el-tooltip>
              <el-tag v-else :type="poolReadyType(row)" size="small">{{ poolReadyLabel(row) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="入池" width="100">
            <template #default="{ row }">
              <el-switch
                :model-value="row.proxy_enabled"
                :disabled="!canWrite || (!row.proxy_enabled && !row.pool_ready)"
                @change="(val: boolean) => toggleAccount(row, val)"
              />
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="打分表" name="ranking">
        <div class="ranking-toolbar">
          <p class="pool-hint">与 Go 代理下发顺序同源：快到期优先消化（urgency），剩余额度多者优先（surplus + headroom），避免周期末浪费与主使用人额度被打光</p>
          <el-button size="small" @click="loadRanking">刷新</el-button>
        </div>
        <h4 class="usage-section-title">入选排序</h4>
        <el-table v-loading="rankingLoading" :data="ranking.ranked" style="width: 100%; margin-bottom: 20px">
          <el-table-column label="#" width="60">
            <template #default="{ $index }">{{ $index + 1 }}</template>
          </el-table-column>
          <el-table-column prop="account_identifier" label="账号" min-width="160" />
          <el-table-column prop="score" label="综合分" width="90" />
          <el-table-column prop="surplus_cents" label="预计余量" width="100" />
          <el-table-column prop="urgency_cents_per_day" label="消化压力/日" width="110" />
          <el-table-column prop="remaining_headroom_pct" label="剩余占比 %" width="110" />
          <el-table-column prop="deadline" label="作废日" width="120" />
          <el-table-column prop="hours_to_deadline" label="距作废(h)" width="100" />
          <el-table-column prop="active_loans" label="在借" width="70" />
          <el-table-column prop="snapshot_freshness" label="快照新鲜度" width="100" />
        </el-table>
        <h4 class="usage-section-title">已排除</h4>
        <el-table v-loading="rankingLoading" :data="ranking.excluded" style="width: 100%">
          <el-table-column prop="account_identifier" label="账号" min-width="160" />
          <el-table-column label="原因" min-width="160">
            <template #default="{ row }">{{ exclusionReasonLabel(row.reason) }}</template>
          </el-table-column>
          <el-table-column prop="active_loans" label="在借" width="70" />
          <el-table-column prop="status" label="额度状态" width="110" />
          <el-table-column prop="deadline" label="作废日" width="120" />
          <el-table-column prop="hours_to_deadline" label="距作废(h)" width="100" />
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="createVisible" title="新建接入密钥" width="480px">
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
        <el-form-item label="5小时上限 ($)">
          <el-input-number
            v-model="createForm.window_5h_cost_usd"
            :min="1"
            :step="1"
            :precision="0"
            :value-on-clear="null"
            controls-position="right"
            placeholder="留空不限"
          />
        </el-form-item>
        <el-form-item label="7天上限 ($)">
          <el-input-number
            v-model="createForm.window_7d_cost_usd"
            :min="1"
            :step="1"
            :precision="0"
            :value-on-clear="null"
            controls-position="right"
            placeholder="留空不限"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitCreate">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="createdVisible" title="接入密钥已创建" width="560px" :close-on-click-modal="false">
      <el-alert type="success" :closable="false" title="已加密保存，管理员与使用人可随时复制启动命令" />
      <el-input v-model="createdKey" readonly style="margin-top: 12px">
        <template #append>
          <el-button @click="copyCreated">复制密钥</el-button>
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
        <el-form-item label="5小时上限 ($)">
          <el-input-number
            v-model="editForm.window_5h_cost_usd"
            :min="1"
            :step="1"
            :precision="0"
            :value-on-clear="null"
            controls-position="right"
            placeholder="留空不限"
          />
        </el-form-item>
        <el-form-item label="7天上限 ($)">
          <el-input-number
            v-model="editForm.window_7d_cost_usd"
            :min="1"
            :step="1"
            :precision="0"
            :value-on-clear="null"
            controls-position="right"
            placeholder="留空不限"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitEdit">保存</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="usagesVisible" :title="`用量明细 - ${usagesKeyName}`" size="720px">
      <div v-loading="usagesLoading" class="usage-drawer-body">
      <p class="usage-estimate-note">以下费用为本地价表估算，非 Cursor 官方账单。</p>
      <div class="usage-summary" v-if="usageLoaded">
        <div>
          请求 {{ usageOverview.request_count }} 次 · tokens
          {{ formatTokensM(usageOverview.total_tokens) }}
        </div>
        <div>
          费用估算（非账单）：${{ (usageOverview.cost_cents / 100).toFixed(2) }}
          <span v-if="usageByAccount.length" class="usage-summary-sub">
            · 涉及 {{ usageByAccount.length }} 个台账账号
          </span>
        </div>
      </div>
      <el-collapse v-model="usageCollapseActive" class="usage-collapse">
        <el-collapse-item name="account">
          <template #title>
            <span class="collapse-title">按台账账号汇总</span>
            <span class="collapse-meta muted">{{ usageByAccount.length }} 个账号</span>
          </template>
          <el-table :data="usageByAccount" class="usage-fill-table">
            <el-table-column label="账号" min-width="200">
              <template #default="{ row }">{{ formatAccountWithPrimary(row) }}</template>
            </el-table-column>
            <el-table-column prop="plan_name" label="计划" width="100" />
            <el-table-column prop="request_count" label="请求数" width="90" align="right" />
            <el-table-column label="tokens" width="100" align="right">
              <template #default="{ row }">{{ formatTokensM(row.total_tokens) }}</template>
            </el-table-column>
            <el-table-column label="费用" width="100" align="right">
              <template #default="{ row }">${{ ((row.cost_cents ?? 0) / 100).toFixed(2) }}</template>
            </el-table-column>
          </el-table>
        </el-collapse-item>
        <el-collapse-item name="model">
          <template #title>
            <span class="collapse-title">按模型汇总</span>
            <span class="collapse-meta muted">{{ usageByModel.length }} 个模型</span>
          </template>
          <el-table :data="usageByModel" class="usage-fill-table">
            <el-table-column prop="model" label="模型" min-width="140" />
            <el-table-column prop="request_count" label="请求数" width="90" align="right" />
            <el-table-column label="tokens" width="100" align="right">
              <template #default="{ row }">{{ formatTokensM(row.total_tokens) }}</template>
            </el-table-column>
            <el-table-column label="费用" width="100" align="right">
              <template #default="{ row }">${{ ((row.cost_cents ?? 0) / 100).toFixed(2) }}</template>
            </el-table-column>
          </el-table>
        </el-collapse-item>
      </el-collapse>
      <h4 class="usage-section-title usage-section-title--daily">明细（按天 · 本地估算）</h4>
      <p class="usage-hint">点击整行展开 / 收起当天明细</p>
      <el-table
        :data="usageByDay"
        class="usage-fill-table day-usage-table"
        row-key="day"
        :expand-row-keys="expandedDayKeys"
        @expand-change="onDayExpandChange"
        @row-click="onDayRowClick"
      >
        <el-table-column type="expand" width="48">
          <template #default="{ row }">
            <div class="day-detail-wrap">
              <el-table :data="row.items" size="small" class="usage-fill-table day-detail-table">
                <el-table-column label="时间" width="150">
                  <template #default="{ row: item }">{{ formatChinaTime(item.ts) }}</template>
                </el-table-column>
                <el-table-column label="账号" min-width="120" show-overflow-tooltip>
                  <template #default="{ row: item }">{{ item.account_identifier || '—' }}</template>
                </el-table-column>
                <el-table-column prop="model" label="模型" min-width="100" show-overflow-tooltip />
                <el-table-column label="tokens" width="72" align="right">
                  <template #default="{ row: item }">{{ formatTokensM(item.total_tokens) }}</template>
                </el-table-column>
                <el-table-column label="费用" width="64" align="right">
                  <template #default="{ row: item }">
                    ${{ ((item.cost_cents ?? 0) / 100).toFixed(2) }}
                  </template>
                </el-table-column>
              </el-table>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="day" label="日期" min-width="160" />
        <el-table-column prop="request_count" label="请求数" width="88" align="right" />
        <el-table-column label="tokens" width="100" align="right">
          <template #default="{ row }">{{ formatTokensM(row.total_tokens) }}</template>
        </el-table-column>
        <el-table-column label="费用" width="100" align="right">
          <template #default="{ row }">${{ ((row.cost_cents ?? 0) / 100).toFixed(2) }}</template>
        </el-table-column>
      </el-table>
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import client from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { copyText } from '@/utils/clipboard'
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
  window_5h_cost_usd: number | null
  window_7d_cost_usd: number | null
  window_5h_cost_cents: number
  window_7d_cost_cents: number
  status: string
  suspended_reason: string | null
  total_tokens: number
  total_cost_cents: number
  recoverable?: boolean
}

interface PoolAccount {
  id: string
  account_identifier: string
  primary_member_name: string | null
  proxy_enabled: boolean
  pool_ready: boolean
  pool_ready_reason: string | null
  pool_effective: boolean
}

interface RankingRow {
  account_id: string
  account_identifier: string
  score?: number
  surplus_cents?: number
  urgency_cents_per_day?: number
  remaining_headroom_pct?: number
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

interface UsageByDayRow {
  day: string
  request_count: number
  total_tokens: number
  cost_cents: number
  items: UsageRow[]
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
const usageByDay = ref<UsageByDayRow[]>([])
const expandedDayKeys = ref<string[]>([])
const usageCollapseActive = ref<string[]>([])

const usageOverview = computed(() => {
  const fromDays = usageByDay.value
  if (fromDays.length > 0) {
    return {
      request_count: fromDays.reduce((s, d) => s + d.request_count, 0),
      total_tokens: fromDays.reduce((s, d) => s + d.total_tokens, 0),
      cost_cents: fromDays.reduce((s, d) => s + d.cost_cents, 0),
    }
  }
  return {
    request_count: usageByAccount.value.reduce((s, r) => s + r.request_count, 0),
    total_tokens: usageByAccount.value.reduce((s, r) => s + r.total_tokens, 0),
    cost_cents: usageByAccount.value.reduce((s, r) => s + r.cost_cents, 0),
  }
})

const createVisible = ref(false)
const createdVisible = ref(false)
const createdKey = ref('')
const createdProxyUrl = ref('http://127.0.0.1:8317')
const editVisible = ref(false)
const usagesVisible = ref(false)
const usagesLoading = ref(false)
const usageLoaded = ref(false)
const usagesKeyName = ref('')

const createForm = reactive({
  member_id: '',
  window_5h_cost_usd: null as number | null,
  window_7d_cost_usd: null as number | null,
})
const editForm = reactive({
  id: '',
  name: '',
  window_5h_cost_usd: null as number | null,
  window_7d_cost_usd: null as number | null,
})

function formatUsd(cents: number | null | undefined) {
  return ((cents ?? 0) / 100).toFixed(0)
}

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

function poolReadyLabel(row: PoolAccount) {
  if (row.proxy_enabled && !row.pool_ready) return '已入池·未就绪'
  if (!row.pool_ready) return '未就绪'
  return '就绪'
}

function poolReadyTooltip(row: PoolAccount) {
  if (row.proxy_enabled && !row.pool_ready) {
    return row.pool_ready_reason
      ? `开关仍开启，但不会进入轮换：${row.pool_ready_reason}`
      : '开关仍开启，但不会进入轮换'
  }
  return row.pool_ready_reason
}

function poolReadyType(row: PoolAccount) {
  if (row.proxy_enabled && !row.pool_ready) return 'warning'
  if (!row.pool_ready) return 'danger'
  return 'success'
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
  createForm.window_5h_cost_usd = null
  createForm.window_7d_cost_usd = null
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
      window_5h_cost_usd: createForm.window_5h_cost_usd,
      window_7d_cost_usd: createForm.window_7d_cost_usd,
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
  try {
    await copyText(createdKey.value)
    ElMessage.success('已复制 Key')
  } catch (err: any) {
    ElMessage.error(err?.message || '复制失败')
  }
}

async function copyCreatedCommand(shell: ShellKind) {
  try {
    const cmd = buildLocalCommand(shell, createdProxyUrl.value, createdKey.value)
    await copyText(cmd)
    ElMessage.success(shell === 'powershell' ? '已复制 PowerShell 命令' : '已复制 Linux 命令')
  } catch (err: any) {
    ElMessage.error(err?.message || '复制失败')
  }
}

async function copyCommand(row: ProxyKeyRow, shell: ShellKind) {
  try {
    const res = await client.get(`/api/v2/proxy-keys/${row.id}/client-setup`, {
      params: { shell },
    })
    await copyText(res.data.command)
    ElMessage.success(shell === 'powershell' ? '已复制 PowerShell 命令' : '已复制 Linux 命令')
  } catch (err: any) {
    const detail = err?.response?.data?.detail
    ElMessage.error(
      typeof detail === 'string' ? detail : err?.message || '复制失败'
    )
  }
}

function openEdit(row: ProxyKeyRow) {
  editForm.id = row.id
  editForm.name = row.name
  editForm.window_5h_cost_usd = row.window_5h_cost_usd
  editForm.window_7d_cost_usd = row.window_7d_cost_usd
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
      window_5h_cost_usd: editForm.window_5h_cost_usd,
      window_7d_cost_usd: editForm.window_7d_cost_usd,
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
    ElMessage.error('恢复失败')
  }
  await load()
}

async function openUsages(row: ProxyKeyRow) {
  usagesKeyName.value = row.name
  usages.value = []
  usageByAccount.value = []
  usageByModel.value = []
  usageByDay.value = []
  expandedDayKeys.value = []
  usageCollapseActive.value = []
  usageLoaded.value = false
  usagesVisible.value = true
  usagesLoading.value = true
  try {
    const res = await client.get(`/api/v2/proxy-keys/${row.id}/usages`)
    usageByAccount.value = res.data.by_account || []
    usageByModel.value = res.data.by_model || []
    usageByDay.value = res.data.by_day || []
    usages.value = res.data.items || []
    usageLoaded.value = true
  } catch {
    ElMessage.error('用量加载失败')
  } finally {
    usagesLoading.value = false
  }
}

function toggleDayExpand(row: UsageByDayRow) {
  const key = row.day
  if (expandedDayKeys.value.includes(key)) {
    expandedDayKeys.value = expandedDayKeys.value.filter((k) => k !== key)
  } else {
    expandedDayKeys.value = [...expandedDayKeys.value, key]
  }
}

function onDayExpandChange(row: UsageByDayRow, expandedRows: UsageByDayRow[]) {
  expandedDayKeys.value = expandedRows.map((r) => r.day)
}

function onDayRowClick(row: UsageByDayRow, _column: unknown, event: MouseEvent) {
  const target = event.target as HTMLElement | null
  if (target?.closest('.el-table__expand-icon')) return
  toggleDayExpand(row)
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
  } catch (err: any) {
    const detail = err?.response?.data?.detail
    ElMessage.error(typeof detail === 'string' ? detail : '操作失败')
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
.account-sub {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin-top: 2px;
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
.usage-section-title--daily {
  margin-top: 4px;
}
.usage-drawer-body {
  min-height: 120px;
}
.usage-estimate-note {
  margin: 0 0 12px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
.usage-summary {
  margin-bottom: 16px;
  line-height: 1.7;
  color: var(--el-text-color-regular);
  font-size: 14px;
}
.usage-summary-sub {
  font-size: 13px;
}
.usage-collapse {
  margin-bottom: 16px;
  border: none;
}
.usage-collapse :deep(.el-collapse-item__header) {
  font-size: 14px;
  font-weight: 600;
  border-bottom: 1px solid var(--el-border-color-lighter);
}
.usage-collapse :deep(.el-collapse-item__wrap) {
  border-bottom: none;
}
.collapse-title {
  margin-right: 8px;
}
.collapse-meta {
  font-size: 12px;
  font-weight: normal;
}
.muted {
  color: var(--el-text-color-secondary);
}
.usage-section-title + .usage-fill-table + .usage-section-title {
  margin-top: 20px;
}
.usage-hint {
  margin: -4px 0 10px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.usage-fill-table {
  width: 100%;
}
.day-usage-table :deep(.el-table__body tr) {
  cursor: pointer;
}
.day-usage-table :deep(.el-table__expanded-cell) {
  padding: 0;
}
.day-detail-wrap {
  padding: 8px;
  background: var(--el-fill-color-lighter);
  overflow-x: hidden;
}
.day-detail-table {
  width: 100%;
  --el-table-bg-color: transparent;
}
.day-detail-table :deep(.el-table__header-wrapper),
.day-detail-table :deep(.el-table__body-wrapper) {
  overflow-x: hidden !important;
}
.day-detail-table :deep(.el-table__body tr) {
  cursor: default;
}
</style>
