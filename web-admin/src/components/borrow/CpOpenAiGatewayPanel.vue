<template>
  <div class="cp-openai-panel" v-loading="loading">
    <p class="hint">
      Coding Plan 专用 OpenAI 网关（与 Cursor MITM 同进程、不同路径）。客户端
      <code>base_url</code> 指向 Go 代理地址下的 <code>/openai/v1</code>（默认
      <code>http://127.0.0.1:8317/openai/v1</code>），API Key 使用签发的
      <code>pkcp_</code>。同一密钥在 Switch dwell（默认 30 分钟，见系统选号规则）内固定后端账号；超时后按额度与并发负载均衡；429 时自动换号。
    </p>
    <el-tabs v-model="vendorTab" class="vendor-tabs" @tab-change="onVendorChange">
      <el-tab-pane label="GLM" name="glm" />
      <el-tab-pane label="MiniMax" name="minimax" />
      <el-tab-pane label="Kimi" name="kimi" />
    </el-tabs>

    <div class="section">
      <div class="section-head">
        <h3>入池账号</h3>
        <el-button size="small" @click="loadAccounts">刷新</el-button>
      </div>
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
    </div>

    <div class="section">
      <div class="section-head">
        <h3>pkcp_ 接入密钥</h3>
        <el-button v-if="canWrite" type="primary" size="small" @click="openCreateKey">签发密钥</el-button>
      </div>
<<<<<<< HEAD
      <el-table :data="keys" stripe size="small">
        <el-table-column label="归属" prop="member_name" min-width="140" />
        <el-table-column label="备注" prop="name" min-width="120" />
=======
      <el-table :data="keys" stripe size="small" row-class-name="cp-key-row" @row-click="onKeyRowClick">
        <el-table-column label="名称" prop="name" min-width="120" />
>>>>>>> cursor/cp-key-usage-ui-1c40
        <el-table-column label="厂家" prop="coding_plan_vendor" width="88" />
        <el-table-column label="Hint" prop="key_hint" width="120" />
        <el-table-column label="状态" width="96">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="累计用量" min-width="140">
          <template #default="{ row }">
            <div class="usage-cell">
              <span class="usage-tokens">{{ formatTokensM(row.total_tokens ?? 0) }}</span>
              <span class="usage-sub">{{ row.request_count ?? 0 }} 次请求</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="近 5h" width="88" align="right">
          <template #default="{ row }">{{ formatTokens(row.window_5h_tokens ?? 0) }}</template>
        </el-table-column>
        <el-table-column label="近 7d" width="88" align="right">
          <template #default="{ row }">{{ formatTokens(row.window_7d_tokens ?? 0) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="120" align="center" fixed="right">
          <template #default="{ row }">
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
      <p v-if="openaiBase" class="base-url">OpenAI Base URL：<code>{{ openaiBase }}</code></p>
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
          <el-input v-model="createForm.name" placeholder="可选，便于区分密钥用途" />
        </el-form-item>
      </el-form>
      <template v-if="createdKey">
        <el-alert type="success" :closable="false" show-icon title="请立即保存密钥（仅显示一次）" />
        <el-input class="key-block" :model-value="createdKey.plaintext_key" readonly />
        <p class="base-url"><code>{{ createdKey.openai_base_url }}</code></p>
      </template>
      <template #footer>
        <el-button @click="createVisible = false">关闭</el-button>
        <el-button v-if="!createdKey" type="primary" :loading="creating" @click="submitCreate">签发</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="usagesVisible" :title="usageDrawerTitle" size="720px">
      <div class="usage-summary" v-if="usageSummary">
        <div>
          网关计量（非厂家账单）：{{ formatTokensM(usageSummary.total_tokens) }} tokens ·
          {{ usageSummary.request_count }} 次请求
        </div>
        <div class="usage-summary-sub">
          近 5h {{ formatTokensM(usageSummary.window_5h_tokens) }} · 近 7d
          {{ formatTokensM(usageSummary.window_7d_tokens) }}
        </div>
      </div>
      <h4 class="usage-section-title">按后端账号汇总</h4>
      <el-table :data="usageByAccount" class="usage-fill-table" v-loading="usagesLoading" size="small">
        <el-table-column label="账号" prop="account_identifier" min-width="180" />
        <el-table-column prop="request_count" label="请求数" width="88" align="right" />
        <el-table-column label="tokens" width="110" align="right">
          <template #default="{ row }">{{ formatTokensM(row.total_tokens) }}</template>
        </el-table-column>
      </el-table>
      <h4 class="usage-section-title">按模型汇总</h4>
      <el-table :data="usageByModel" class="usage-fill-table" v-loading="usagesLoading" size="small">
        <el-table-column prop="model" label="模型" min-width="180" />
        <el-table-column prop="request_count" label="请求数" width="88" align="right" />
        <el-table-column label="tokens" width="110" align="right">
          <template #default="{ row }">{{ formatTokensM(row.total_tokens) }}</template>
        </el-table-column>
      </el-table>
      <h4 class="usage-section-title">明细（按天）</h4>
      <p class="usage-hint">点击行展开当天请求</p>
      <el-table
        :data="usageByDay"
        class="usage-fill-table day-usage-table"
        v-loading="usagesLoading"
        row-key="day"
        size="small"
        @row-click="onDayRowClick"
      >
        <el-table-column type="expand" width="48">
          <template #default="{ row }">
            <div class="day-detail-wrap">
              <el-table :data="row.items" size="small" class="usage-fill-table">
                <el-table-column label="时间" width="150">
                  <template #default="{ row: item }">{{ formatChinaTime(item.ts) }}</template>
                </el-table-column>
                <el-table-column label="账号" min-width="120" show-overflow-tooltip>
                  <template #default="{ row: item }">{{ item.account_identifier || '—' }}</template>
                </el-table-column>
                <el-table-column prop="model" label="模型" min-width="100" show-overflow-tooltip />
                <el-table-column label="in / out" width="100" align="right">
                  <template #default="{ row: item }">
                    {{ item.tokens_input ?? 0 }} / {{ item.tokens_output ?? 0 }}
                  </template>
                </el-table-column>
                <el-table-column label="tokens" width="72" align="right">
                  <template #default="{ row: item }">{{ formatTokensM(item.total_tokens) }}</template>
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
      </el-table>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import client from '@/api/client'
import { useAuthStore } from '@/stores/auth'
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

interface CpKey {
  id: string
  name: string
  coding_plan_vendor: string | null
  member_name: string | null
  key_hint: string
  status: string
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
  account_identifier: string
  request_count: number
  total_tokens: number
}

interface UsageByModelRow {
  model: string
  request_count: number
  total_tokens: number
}

interface UsageByDayRow {
  day: string
  request_count: number
  total_tokens: number
  items: Array<{
    ts: string
    account_identifier: string | null
    model: string | null
    tokens_input: number
    tokens_output: number
    total_tokens: number
  }>
}

interface MemberOption {
  id: string
  display_name: string
}

const auth = useAuthStore()
const canWrite = computed(() => auth.hasPermission('proxy:write'))
const loading = ref(false)
const vendorTab = ref('glm')
const accounts = ref<CpAccount[]>([])
const keys = ref<CpKey[]>([])
const openaiBase = ref('')
const members = ref<MemberOption[]>([])

const createVisible = ref(false)
const creating = ref(false)
const createdKey = ref<{ plaintext_key: string; openai_base_url: string } | null>(null)
const createForm = reactive({
  coding_plan_vendor: 'glm',
  member_id: '',
  name: '',
})

const usagesVisible = ref(false)
const usagesLoading = ref(false)
const usagesTitle = ref('')
const usageSummary = ref<UsageSummary | null>(null)
const usageByAccount = ref<UsageByAccountRow[]>([])
const usageByModel = ref<UsageByModelRow[]>([])
const usageByDay = ref<UsageByDayRow[]>([])

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

async function loadKeys() {
  try {
    const res = await client.get('/api/v2/openai-proxy/keys')
    keys.value = res.data.filter(
      (k: CpKey & { coding_plan_vendor?: string }) => k.coding_plan_vendor === vendorTab.value,
    )
    if (res.data.length && res.data[0].openai_base_url) {
      openaiBase.value = res.data[0].openai_base_url
    }
  } catch {
    ElMessage.error('密钥列表加载失败')
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
      openai_base_url: res.data.openai_base_url,
    }
    openaiBase.value = res.data.openai_base_url
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

function statusTagType(status: string) {
  if (status === 'active') return 'success'
  if (status === 'suspended') return 'warning'
  if (status === 'revoked') return 'info'
  return 'info'
}

function onKeyRowClick(row: CpKey) {
  openUsages(row)
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

function onDayRowClick(row: UsageByDayRow, _column: unknown, event: Event) {
  const target = event.target as HTMLElement | null
  if (target?.closest('.el-table__expand-icon')) return
  const table = target?.closest('.day-usage-table')
  if (!table) return
  const expandIcon = (event.currentTarget as HTMLElement)?.querySelector('.el-table__expand-icon') as HTMLElement | null
  expandIcon?.click()
}

onMounted(async () => {
  await loadMembers()
  await loadAll()
})

defineExpose({ load: loadAll })
</script>

<style scoped>
.hint {
  margin: 0 0 12px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  line-height: 1.55;
}
.section {
  margin-top: 16px;
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
.base-url {
  margin: 8px 0 0;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
.key-block {
  margin-top: 12px;
}
.usage-cell {
  display: flex;
  flex-direction: column;
  line-height: 1.35;
}
.usage-tokens {
  font-variant-numeric: tabular-nums;
  font-weight: 500;
}
.usage-sub {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
:deep(.cp-key-row) {
  cursor: pointer;
}
.usage-summary {
  margin-bottom: 16px;
  font-size: 14px;
  line-height: 1.6;
}
.usage-summary-sub {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.usage-section-title {
  margin: 20px 0 8px;
  font-size: 14px;
  font-weight: 600;
}
.usage-hint {
  margin: 0 0 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.usage-fill-table {
  width: 100%;
}
.day-usage-table :deep(.el-table__body tr) {
  cursor: pointer;
}
.day-detail-wrap {
  padding: 4px 8px 12px 40px;
}
</style>
