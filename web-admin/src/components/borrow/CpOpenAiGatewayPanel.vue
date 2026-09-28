<template>
  <div class="cp-openai-panel" v-loading="loading">
    <div class="endpoint-card">
      <div class="endpoint-head">
        <h3>OpenAI Base URL</h3>
        <el-button v-if="!openaiEndpoints.length" link type="primary" @click="goProxySettings">
          前往配置代理地址
        </el-button>
      </div>
      <p class="endpoint-desc">
        Coding Plan 网关与 Cursor 借用同 Go 代理、路径为 <code>/openai/v1</code>（来自系统设置 → 代理地址）。
        客户端填下表 <code>base_url</code> 与签发的 <code>pkcp_</code>，按网络选「公司 / 外网」等即可。
        同一密钥在 dwell 内（默认 30 分钟，见选号规则）固定后端账号，超时按额度与并发均衡，429 自动换号。
      </p>
      <el-alert
        v-if="!openaiEndpoints.length"
        type="warning"
        :closable="false"
        show-icon
        title="尚未配置代理地址"
        description="请先在「系统设置 → 代理地址」添加 Go 代理（如 :8317），否则客户端无法接入。"
      />
      <el-table v-else :data="openaiEndpoints" size="small" stripe class="endpoint-table">
        <el-table-column label="代理" prop="display_name" width="120" show-overflow-tooltip />
        <el-table-column label="Base URL（base_url）" min-width="300">
          <template #default="{ row }">
            <code class="endpoint-code">{{ row.openai_base_url }}</code>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="72" align="center">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="copyOpenAiBaseUrl(row)">复制</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
    <el-tabs v-model="vendorTab" class="vendor-tabs" @tab-change="onVendorChange">
      <el-tab-pane label="GLM" name="glm" />
      <el-tab-pane label="MiniMax" name="minimax" />
      <el-tab-pane label="Kimi" name="kimi" />
    </el-tabs>

    <el-collapse v-model="poolSectionOpen" class="section pool-collapse">
      <el-collapse-item name="pool">
        <template #title>
          <div class="pool-collapse-head">
            <h3 class="pool-collapse-title">{{ poolAccountsTitle }}</h3>
            <el-button size="small" @click.stop="loadAccounts">刷新</el-button>
          </div>
        </template>
        <el-table :data="accounts" stripe size="small">
          <el-table-column label="账号" prop="account_identifier" min-width="160" />
          <el-table-column label="区域" prop="api_region" width="100" />
          <el-table-column label="额度压力" width="100">
            <template #default="{ row }">{{ row.tier_pressure_pct?.toFixed?.(1) ?? row.tier_pressure_pct }}%</template>
          </el-table-column>
          <el-table-column label="就绪" width="100">
            <template #default="{ row }">
              <el-tag :type="row.pool_ready ? 'success' : 'danger'" size="small">
                {{ row.pool_ready ? '就绪' : '未就绪' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="入池" width="88" align="center">
            <template #default="{ row }">
              <el-switch
                :model-value="row.cp_proxy_enabled"
                :disabled="!canWrite || (!row.cp_proxy_enabled && !row.pool_ready)"
                @change="(v: boolean) => togglePool(row, v)"
              />
            </template>
          </el-table-column>
        </el-table>
      </el-collapse-item>
    </el-collapse>

    <div class="section">
      <div class="section-head">
        <h3>pkcp_ 接入密钥</h3>
        <div class="section-head-actions">
          <div class="filter-switch">
            <span class="filter-label">仅显示在用</span>
            <el-switch v-model="activeKeysOnly" />
          </div>
          <el-button size="small" :loading="keysLoading" @click="loadKeys">刷新</el-button>
          <el-button v-if="canWrite" type="primary" size="small" @click="openCreateKey">签发密钥</el-button>
        </div>
      </div>
      <el-table
        :data="displayKeys"
        stripe
        size="small"
        row-class-name="cp-key-row"
        v-loading="keysLoading"
        @row-click="onKeyRowClick"
      >
        <el-table-column label="归属" prop="member_name" min-width="140" />
        <el-table-column label="备注" min-width="120">
          <template #default="{ row }">
            <span :class="{ muted: !row.name?.trim() }">{{ row.name?.trim() || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="厂家" prop="coding_plan_vendor" width="88" />
        <el-table-column label="状态" width="96">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="累计用量/5h/7d" min-width="152" align="left">
          <template #default="{ row }">
            <span class="usage-combined" :title="formatKeyUsageTooltip(row)">
              {{ formatKeyUsageCombined(row) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="168" align="center" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="row.recoverable"
              link
              type="primary"
              size="small"
              @click.stop="copyKey(row)"
            >
              复制 Key
            </el-button>
            <el-button link type="primary" size="small" @click.stop="openUsages(row)">用量</el-button>
            <el-button
              v-if="canWrite && row.status === 'active'"
              link
              type="danger"
              size="small"
              @click.stop="confirmRevoke(row)"
            >
              吊销
            </el-button>
            <el-button
              v-if="canWrite && row.status === 'suspended'"
              link
              type="warning"
              size="small"
              @click.stop="resumeKey(row)"
            >
              恢复
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="createVisible" title="签发 Coding Plan OpenAI 密钥" width="480px">
      <el-form label-width="100px">
        <el-form-item label="厂家">
          <el-select v-model="createForm.coding_plan_vendor" style="width: 100%">
            <el-option label="GLM" value="glm" />
            <el-option label="MiniMax" value="minimax" />
            <el-option label="Kimi" value="kimi" />
          </el-select>
        </el-form-item>
        <el-form-item label="归属成员">
          <el-select v-model="createForm.member_id" filterable style="width: 100%">
            <el-option v-for="m in members" :key="m.id" :label="m.display_name" :value="m.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="备注">
          <el-input
            v-model="createForm.name"
            placeholder="可选；留空则备注列为 —"
          />
        </el-form-item>
      </el-form>
      <template v-if="createdKey">
        <el-alert type="success" :closable="false" show-icon title="请立即保存密钥（仅显示一次）" />
        <el-input class="key-block" :model-value="createdKey.plaintext_key" readonly />
        <div v-if="createdKeyEndpoints.length" class="created-endpoints">
          <p class="created-endpoints-title">OpenAI Base URL（按网络选择其一）</p>
          <el-table :data="createdKeyEndpoints" size="small" stripe>
            <el-table-column label="代理" prop="display_name" width="100" />
            <el-table-column label="Base URL" min-width="240">
              <template #default="{ row }">
                <code class="endpoint-code">{{ row.openai_base_url }}</code>
              </template>
            </el-table-column>
            <el-table-column width="64" align="center">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="copyOpenAiBaseUrl(row)">复制</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </template>
      <template #footer>
        <el-button @click="createVisible = false">关闭</el-button>
        <el-button v-if="!createdKey" type="primary" :loading="creating" @click="submitCreate">签发</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="revealVisible" title="pkcp_ 密钥" width="580px">
      <p class="muted reveal-hint">完整密钥（由服务端加密保存，可再次复制）</p>
      <el-input :model-value="revealedPlaintext" readonly type="textarea" :rows="2" />
      <div v-if="revealedEndpoints.length" class="created-endpoints">
        <p class="created-endpoints-title">OpenAI Base URL</p>
        <el-table :data="revealedEndpoints" size="small" stripe>
          <el-table-column label="代理" prop="display_name" width="100" />
          <el-table-column label="Base URL" min-width="240">
            <template #default="{ row }">
              <code class="endpoint-code">{{ row.openai_base_url }}</code>
            </template>
          </el-table-column>
          <el-table-column width="64" align="center">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="copyOpenAiBaseUrl(row)">复制</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
      <template #footer>
        <el-button @click="revealVisible = false">关闭</el-button>
        <el-button type="primary" @click="copyRevealed">复制 Key</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="usagesVisible" :title="usageDrawerTitle" size="720px">
      <el-collapse v-model="usageAccountSectionOpen" class="usage-section-collapse">
        <el-collapse-item name="account">
          <template #title>
            <div class="usage-collapse-head">
              <span class="usage-collapse-title">按后端账号汇总（本地估算）</span>
              <span class="usage-collapse-totals">
                账号数 {{ usageAccountTotals.accountCount }} · 请求数
                {{ usageAccountTotals.requestCount }} · tokens
                {{ formatTokensM(usageAccountTotals.totalTokens) }} · 费用 ${{
                  (usageAccountTotals.costCents / 100).toFixed(2)
                }}
              </span>
            </div>
          </template>
          <el-table :data="usageByAccount" class="usage-fill-table" v-loading="usagesLoading">
            <el-table-column label="账号" min-width="168">
              <template #default="{ row }">
                <div class="account-stack">
                  <span class="account-id">{{ row.account_identifier || '—' }}</span>
                  <span v-if="row.primary_member_name" class="account-owner">
                    {{ row.primary_member_name }}
                  </span>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="额度" min-width="176">
              <template #default="{ row }">
                <CodingPlanTierBars
                  v-if="row.quota_display === 'coding_plan_tiers'"
                  :tiers="row.quota_tiers"
                />
                <QuotaProgressBars
                  v-else
                  :total_pct="row.total_pct"
                  :auto_pct="row.auto_pct"
                  :api_pct="row.api_pct"
                  :status="row.status"
                />
              </template>
            </el-table-column>
            <el-table-column prop="request_count" label="请求数" width="88" align="right" />
            <el-table-column label="tokens" width="110" align="right">
              <template #default="{ row }">{{ formatTokensM(row.total_tokens) }}</template>
            </el-table-column>
            <el-table-column label="费用" width="110" align="right">
              <template #default="{ row }">${{ ((row.cost_cents ?? 0) / 100).toFixed(2) }}</template>
            </el-table-column>
          </el-table>
        </el-collapse-item>
      </el-collapse>
      <el-collapse v-model="usageModelSectionOpen" class="usage-section-collapse">
        <el-collapse-item name="model">
          <template #title>
            <div class="usage-collapse-head">
              <span class="usage-collapse-title">按模型汇总（本地估算）</span>
              <span class="usage-collapse-totals">
                模型数 {{ usageModelTotals.modelCount }} · 请求数
                {{ usageModelTotals.requestCount }} · tokens
                {{ formatTokensM(usageModelTotals.totalTokens) }} · 费用 ${{
                  (usageModelTotals.costCents / 100).toFixed(2)
                }}
              </span>
            </div>
          </template>
          <el-table :data="usageByModel" class="usage-fill-table" v-loading="usagesLoading">
            <el-table-column prop="model" label="模型" min-width="180" />
            <el-table-column prop="request_count" label="请求数" width="88" align="right" />
            <el-table-column label="tokens" width="110" align="right">
              <template #default="{ row }">{{ formatTokensM(row.total_tokens) }}</template>
            </el-table-column>
            <el-table-column label="费用" width="110" align="right">
              <template #default="{ row }">${{ ((row.cost_cents ?? 0) / 100).toFixed(2) }}</template>
            </el-table-column>
          </el-table>
        </el-collapse-item>
      </el-collapse>
      <h4 class="usage-section-title">proxy 明细（按天 · 本地估算）</h4>
      <el-table
        :data="usageByDay"
        class="usage-fill-table day-usage-table"
        v-loading="usagesLoading"
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
        <el-table-column label="tokens" width="110" align="right">
          <template #default="{ row }">{{ formatTokensM(row.total_tokens) }}</template>
        </el-table-column>
        <el-table-column label="费用" width="110" align="right">
          <template #default="{ row }">${{ ((row.cost_cents ?? 0) / 100).toFixed(2) }}</template>
        </el-table-column>
      </el-table>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import client from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { copyText } from '@/utils/clipboard'
import CodingPlanTierBars from '@/components/CodingPlanTierBars.vue'
import QuotaProgressBars from '@/components/QuotaProgressBars.vue'
import { formatTokens, formatTokensM } from '@/utils/usage'
import { formatChinaTime } from '@/utils/time'

interface CpAccount {
  id: string
  account_identifier: string
  api_region: string | null
  cp_proxy_enabled: boolean
  pool_ready: boolean
  pool_ready_reason: string | null
  tier_pressure_pct: number
}

interface OpenAiEndpoint {
  display_name: string
  proxy_url: string
  openai_base_url: string
}

interface CpKey {
  id: string
  name: string
  coding_plan_vendor: string | null
  member_name: string | null
  key_hint: string
  status: string
  recoverable?: boolean
  total_tokens: number
  request_count?: number
  window_5h_tokens?: number
  window_7d_tokens?: number
}

interface UsageSummary {
  name: string
  key_hint: string
  coding_plan_vendor: string | null
  request_count: number
  total_tokens: number
  window_5h_tokens: number
  window_7d_tokens: number
}

interface UsageByAccountRow {
  account_id: string | null
  account_identifier: string
  primary_member_name: string | null
  request_count: number
  total_tokens: number
  cost_cents: number
  quota_display?: 'cursor' | 'coding_plan_tiers'
  quota_tiers?: Array<{ name: string; label?: string; utilization_pct?: number | null }>
  total_pct?: number | null
  auto_pct?: number | null
  api_pct?: number | null
  status?: string | null
}

interface UsageByModelRow {
  model: string
  request_count: number
  total_tokens: number
  cost_cents: number
}

interface UsageByDayRow {
  day: string
  request_count: number
  total_tokens: number
  cost_cents: number
  items: Array<{
    ts: string
    account_identifier: string | null
    model: string | null
    total_tokens: number
    cost_cents: number
  }>
}

interface MemberOption {
  id: string
  display_name: string
}

const auth = useAuthStore()
const router = useRouter()
const canWrite = computed(() => auth.hasPermission('proxy:write'))
const loading = ref(false)
const vendorTab = ref('glm')
/** 默认折叠；展开时包含 "pool" */
const poolSectionOpen = ref<string[]>([])
const accounts = ref<CpAccount[]>([])
const poolAccountsTitle = computed(() => {
  const total = accounts.value.length
  const enabled = accounts.value.filter((a) => a.cp_proxy_enabled).length
  return `入池账号（${enabled}/${total}）`
})
const keys = ref<CpKey[]>([])
const keysLoading = ref(false)
/** 默认只展示 status=active 的密钥 */
const activeKeysOnly = ref(true)
const displayKeys = computed(() => {
  if (!activeKeysOnly.value) return keys.value
  return keys.value.filter((k) => k.status === 'active')
})
const openaiEndpoints = ref<OpenAiEndpoint[]>([])
const members = ref<MemberOption[]>([])

const createVisible = ref(false)
const creating = ref(false)
const createdKey = ref<{ plaintext_key: string; openai_endpoints?: OpenAiEndpoint[] } | null>(null)
const createdKeyEndpoints = computed(() => {
  const fromKey = createdKey.value?.openai_endpoints
  if (fromKey?.length) return fromKey
  return openaiEndpoints.value
})
const createForm = reactive({
  coding_plan_vendor: 'glm',
  member_id: '',
  name: '',
})

const revealVisible = ref(false)
const revealedPlaintext = ref('')
const revealedEndpoints = ref<OpenAiEndpoint[]>([])

const usagesVisible = ref(false)
const usagesLoading = ref(false)
const usagesTitle = ref('')
const usageSummary = ref<UsageSummary | null>(null)
const usageByAccount = ref<UsageByAccountRow[]>([])
const usageByModel = ref<UsageByModelRow[]>([])
const usageByDay = ref<UsageByDayRow[]>([])
const usageAccountSectionOpen = ref<string[]>([])
const usageModelSectionOpen = ref<string[]>([])
const expandedDayKeys = ref<string[]>([])

const usageAccountTotals = computed(() => {
  const rows = usageByAccount.value
  return {
    accountCount: rows.length,
    requestCount: rows.reduce((sum, row) => sum + (row.request_count ?? 0), 0),
    totalTokens: rows.reduce((sum, row) => sum + (row.total_tokens ?? 0), 0),
    costCents: rows.reduce((sum, row) => sum + (row.cost_cents ?? 0), 0),
  }
})

const usageModelTotals = computed(() => {
  const rows = usageByModel.value
  return {
    modelCount: rows.length,
    requestCount: rows.reduce((sum, row) => sum + (row.request_count ?? 0), 0),
    totalTokens: rows.reduce((sum, row) => sum + (row.total_tokens ?? 0), 0),
    costCents: rows.reduce((sum, row) => sum + (row.cost_cents ?? 0), 0),
  }
})

const usageDrawerTitle = computed(() => {
  const s = usageSummary.value
  if (!s) return usagesTitle.value ? `用量详情 - ${usagesTitle.value}` : '用量详情'
  return `用量详情 - ${s.name || s.key_hint}`
})

watch(vendorTab, (v) => {
  createForm.coding_plan_vendor = v
})

async function loadMembers() {
  const res = await client.get('/api/v2/members')
  members.value = res.data
}

async function loadAccounts() {
  loading.value = true
  try {
    const res = await client.get('/api/v2/openai-proxy/accounts', { params: { vendor: vendorTab.value } })
    accounts.value = res.data
  } catch {
    ElMessage.error('入池账号加载失败')
  } finally {
    loading.value = false
  }
}

async function loadOpenaiEndpoints() {
  try {
    const res = await client.get('/api/v2/openai-proxy/endpoints')
    openaiEndpoints.value = Array.isArray(res.data?.endpoints) ? res.data.endpoints : []
  } catch {
    openaiEndpoints.value = []
  }
}

async function loadKeys() {
  keysLoading.value = true
  try {
    const res = await client.get('/api/v2/openai-proxy/keys')
    keys.value = res.data.filter(
      (k: CpKey & { coding_plan_vendor?: string }) => k.coding_plan_vendor === vendorTab.value,
    )
  } catch {
    ElMessage.error('密钥列表加载失败')
  } finally {
    keysLoading.value = false
  }
}

function goProxySettings() {
  void router.push({ path: '/settings', query: { tab: 'proxy_addresses' } })
}

async function copyOpenAiBaseUrl(row: OpenAiEndpoint) {
  try {
    await copyText(row.openai_base_url)
    ElMessage.success(`已复制 ${row.display_name} Base URL`)
  } catch {
    ElMessage.error('复制失败')
  }
}

async function loadAll() {
  await Promise.all([loadAccounts(), loadKeys()])
}

function onVendorChange() {
  void loadAll()
}

async function togglePool(row: CpAccount, val: boolean) {
  try {
    await client.post(`/api/v2/openai-proxy/accounts/${row.id}`, { cp_proxy_enabled: val })
    row.cp_proxy_enabled = val
  } catch (err: unknown) {
    const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    ElMessage.error(typeof detail === 'string' ? detail : '操作失败')
  }
}

function openCreateKey() {
  createdKey.value = null
  createForm.coding_plan_vendor = vendorTab.value
  createForm.member_id = members.value[0]?.id || ''
  createForm.name = ''
  createVisible.value = true
}

async function submitCreate() {
  if (!createForm.member_id) {
    ElMessage.warning('请选择归属成员')
    return
  }
  creating.value = true
  try {
    const res = await client.post('/api/v2/openai-proxy/keys', {
      member_id: createForm.member_id,
      coding_plan_vendor: createForm.coding_plan_vendor,
      name: createForm.name || undefined,
    })
    createdKey.value = {
      plaintext_key: res.data.plaintext_key,
      openai_endpoints: res.data.openai_endpoints,
    }
    if (Array.isArray(res.data.openai_endpoints) && res.data.openai_endpoints.length) {
      openaiEndpoints.value = res.data.openai_endpoints
    }
    await loadKeys()
  } catch (err: unknown) {
    const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    ElMessage.error(typeof detail === 'string' ? detail : '签发失败')
  } finally {
    creating.value = false
  }
}

function statusLabel(status: string) {
  const map: Record<string, string> = {
    active: '有效',
    revoked: '已吊销',
    suspended: '已停用',
  }
  return map[status] || status
}

function formatKeyUsageCombined(row: CpKey) {
  const total = formatTokensM(row.total_tokens ?? 0)
  const count = row.request_count ?? 0
  const h5 = formatTokens(row.window_5h_tokens ?? 0)
  const d7 = formatTokens(row.window_7d_tokens ?? 0)
  return `${total}·${count}次/${h5}/${d7}`
}

function formatKeyUsageTooltip(row: CpKey) {
  const count = row.request_count ?? 0
  return `累计 ${formatTokensM(row.total_tokens ?? 0)}（${count} 次请求）· 近 5h ${formatTokens(row.window_5h_tokens ?? 0)} · 近 7d ${formatTokens(row.window_7d_tokens ?? 0)}`
}

function statusTagType(status: string) {
  if (status === 'active') return 'success'
  if (status === 'suspended') return 'warning'
  if (status === 'revoked') return 'info'
  return 'info'
}

function onKeyRowClick(row: CpKey) {
  openUsages(row)
}

async function copyKey(row: CpKey) {
  try {
    const res = await client.get(`/api/v2/openai-proxy/keys/${row.id}/reveal`)
    revealedPlaintext.value = res.data.plaintext_key
    revealedEndpoints.value = Array.isArray(res.data.openai_endpoints)
      ? res.data.openai_endpoints
      : openaiEndpoints.value
    revealVisible.value = true
    try {
      await copyText(res.data.plaintext_key)
      ElMessage.success('已复制到剪贴板')
    } catch {
      ElMessage.info('请在下方的对话框中手动复制')
    }
  } catch (err: unknown) {
    const status = (err as { response?: { status?: number; data?: { detail?: string } } })?.response?.status
    const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    if (status === 410) {
      ElMessage.warning(typeof detail === 'string' ? detail : '该 Key 不可还原，请重新签发')
      return
    }
    ElMessage.error(typeof detail === 'string' ? detail : '无法获取 Key')
  }
}

async function copyRevealed() {
  if (!revealedPlaintext.value) return
  try {
    await copyText(revealedPlaintext.value)
    ElMessage.success('已复制')
  } catch {
    ElMessage.error('复制失败，请手动选择文本')
  }
}

async function confirmRevoke(row: CpKey) {
  try {
    await ElMessageBox.confirm(
      `吊销后客户端将无法再使用该密钥（${row.key_hint}）。此操作不可撤销。`,
      '吊销 pkcp_ 密钥',
      { type: 'warning', confirmButtonText: '吊销', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await client.post(`/api/v2/openai-proxy/keys/${row.id}/revoke`)
    ElMessage.success('已吊销')
    await loadKeys()
  } catch (err: unknown) {
    const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    ElMessage.error(typeof detail === 'string' ? detail : '吊销失败')
  }
}

async function resumeKey(row: CpKey) {
  try {
    await client.post(`/api/v2/openai-proxy/keys/${row.id}/resume`)
    ElMessage.success('已恢复')
    await loadKeys()
  } catch (err: unknown) {
    const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    ElMessage.error(typeof detail === 'string' ? detail : '恢复失败')
  }
}

async function openUsages(row: CpKey) {
  usagesTitle.value = row.name || row.key_hint
  usageSummary.value = null
  usageByAccount.value = []
  usageByModel.value = []
  usageByDay.value = []
  usageAccountSectionOpen.value = []
  usageModelSectionOpen.value = []
  expandedDayKeys.value = []
  usagesVisible.value = true
  usagesLoading.value = true
  try {
    const res = await client.get(`/api/v2/openai-proxy/keys/${row.id}/usages`)
    usageSummary.value = res.data.summary ?? null
    usageByAccount.value = res.data.by_account || []
    usageByModel.value = res.data.by_model || []
    usageByDay.value = res.data.by_day || []
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

onMounted(async () => {
  await loadMembers()
  await loadOpenaiEndpoints()
  await loadAll()
})

defineExpose({
  load: async () => {
    await loadOpenaiEndpoints()
    await loadAll()
  },
})
</script>

<style scoped>
.endpoint-card {
  margin-bottom: 16px;
  padding: 12px 14px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-fill-color-blank);
}
.endpoint-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 4px;
}
.endpoint-head h3 {
  margin: 0;
  font-size: 15px;
}
.endpoint-desc {
  margin: 0 0 10px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.5;
}
.endpoint-table {
  width: 100%;
}
.endpoint-code {
  font-size: 12px;
  word-break: break-all;
}
.created-endpoints {
  margin-top: 12px;
}
.created-endpoints-title {
  margin: 0 0 8px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
.section {
  margin-top: 16px;
}
.pool-collapse {
  border: none;
}
.pool-collapse :deep(.el-collapse-item__header) {
  height: auto;
  line-height: 1.4;
  border: none;
  background: transparent;
}
.pool-collapse :deep(.el-collapse-item__wrap) {
  border: none;
}
.pool-collapse :deep(.el-collapse-item__content) {
  padding-bottom: 0;
}
.pool-collapse-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex: 1;
  gap: 12px;
  padding-right: 8px;
}
.pool-collapse-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
}
.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.section-head h3 {
  margin: 0;
  font-size: 15px;
}
.section-head-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.filter-switch {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.filter-label {
  font-size: 13px;
  color: var(--el-text-color-regular);
  white-space: nowrap;
}
.base-url {
  margin: 8px 0 0;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
.key-block {
  margin-top: 12px;
}
.usage-combined {
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  line-height: 1.4;
  white-space: nowrap;
}
:deep(.cp-key-row) {
  cursor: pointer;
}
.usage-section-collapse {
  margin-bottom: 12px;
  border: none;
}
.usage-section-collapse :deep(.el-collapse-item__header) {
  height: auto;
  min-height: 40px;
  line-height: 1.5;
  border: none;
}
.usage-section-collapse :deep(.el-collapse-item__wrap) {
  border: none;
}
.usage-section-collapse :deep(.el-collapse-item__content) {
  padding-bottom: 4px;
}
.usage-collapse-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px 12px;
  padding-right: 8px;
  width: 100%;
}
.usage-collapse-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}
.usage-collapse-totals {
  font-size: 13px;
  font-weight: 400;
  color: var(--el-text-color-secondary);
}
.usage-section-title {
  margin: 16px 0 12px;
  font-size: 14px;
  font-weight: 600;
}
.account-stack {
  display: inline-flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  line-height: 1.25;
  max-width: 100%;
}
.account-id {
  font-size: 13px;
  word-break: break-all;
}
.account-owner {
  font-size: 11px;
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
.reveal-hint {
  margin: 0 0 8px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
</style>
