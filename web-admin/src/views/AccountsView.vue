<template>
  <div class="accounts-page" v-loading="loading">
    <header class="page-header">
      <div>
        <h2>AI 工具账号台账</h2>
        <p class="desc">管理各厂家账号、主使用人与套餐。Cursor 绑定 API Key 后自动同步；其他工具由主使用人手工上报。</p>
      </div>
      <div class="header-actions">
        <el-select v-model="period" style="width: 140px" @change="loadSummaries">
          <el-option v-for="p in periodOptions" :key="p" :label="p" :value="p" />
        </el-select>
        <el-button type="primary" @click="openCreate">新增账号</el-button>
      </div>
    </header>

    <el-table :data="accounts" stripe>
      <el-table-column label="账号" min-width="220" prop="account_identifier" />
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
      <el-table-column label="本月用量" min-width="280">
        <template #default="{ row }">
          <div v-if="summaryMap[row.id]" class="usage-cell">
            <template v-if="summaryMap[row.id].cursor_pools">
              <div class="usage-section">
                <div class="usage-section-title">Cursor 套内</div>
                <div class="usage-pool">
                  <span class="pool-label">Auto+Composer</span>
                  <span class="pool-value">
                    {{ formatSpend(autoComposerSpend(summaryMap[row.id])) }}
                  </span>
                </div>
                <div
                  v-for="item in poolModelBreakdown(summaryMap[row.id], 'auto_composer')"
                  :key="'ac-' + item.name"
                  class="usage-model muted"
                >
                  {{ item.name }} {{ formatSpend(item.value) }}
                </div>
                <div class="usage-pool">
                  <span class="pool-label">高级模型 API</span>
                  <span class="pool-value">
                    {{ formatSpend(apiSpend(summaryMap[row.id])) }}
                    <template v-if="apiQuotaUsd(summaryMap[row.id])">
                      / {{ formatSpend(apiQuotaUsd(summaryMap[row.id])) }}
                    </template>
                  </span>
                </div>
                <div
                  v-for="item in poolModelBreakdown(summaryMap[row.id], 'api')"
                  :key="'api-' + item.name"
                  class="usage-model muted"
                >
                  {{ item.name }} {{ formatSpend(item.value) }}
                </div>
              </div>
              <div v-if="hasExternalModels(summaryMap[row.id])" class="usage-section external">
                <div class="usage-section-title">外部模型（不计 Cursor 额度）</div>
                <div
                  v-for="item in externalModelBreakdown(summaryMap[row.id])"
                  :key="'ext-' + item.name"
                  class="usage-model muted"
                >
                  {{ item.name }} {{ formatTokens(item.tokens) }}
                </div>
              </div>
            </template>
            <template v-else>
              <div class="usage-total">
                {{ summaryMap[row.id].primary_metric_value }}
                {{ summaryMap[row.id].primary_metric_unit?.toUpperCase() }}
              </div>
              <div
                v-if="summaryMap[row.id].estimated_included_spend_usd"
                class="usage-sub muted"
              >
                超套 {{ formatSpend(summaryMap[row.id].reported_spend_usd) }}
                · 套内估 {{ formatSpend(summaryMap[row.id].estimated_included_spend_usd) }}
              </div>
              <div
                v-for="item in modelBreakdown(summaryMap[row.id])"
                :key="item.name"
                class="usage-model muted"
              >
                {{ item.name }} {{ formatAmount(item.value, summaryMap[row.id].primary_metric_unit) }}
              </div>
            </template>
          </div>
          <span v-else class="muted">未提交</span>
        </template>
      </el-table-column>
      <el-table-column label="额度使用率" width="150">
        <template #default="{ row }">
          <div v-if="summaryMap[row.id]?.cursor_pools" class="quota-cell">
            <div>
              <span class="quota-pool-label">高级模型</span>
              <span
                v-if="apiQuotaRatio(summaryMap[row.id]) != null"
                :class="{ 'quota-over': apiQuotaRatio(summaryMap[row.id])! > 100 }"
              >
                {{ apiQuotaRatio(summaryMap[row.id]) }}%
              </span>
              <span v-else class="muted">—</span>
            </div>
            <div class="muted quota-pool-hint">Auto+Composer —</div>
            <div v-if="cycleLabel(summaryMap[row.id])" class="muted cycle-hint">
              {{ cycleLabel(summaryMap[row.id]) }}
            </div>
          </div>
          <div v-else-if="effectiveQuotaRatio(summaryMap[row.id]) != null" class="quota-cell">
            <span :class="{ 'quota-over': effectiveQuotaRatio(summaryMap[row.id])! > 100 }">
              {{ effectiveQuotaRatio(summaryMap[row.id]) }}%
            </span>
            <div v-if="cycleLabel(summaryMap[row.id])" class="muted cycle-hint">
              {{ cycleLabel(summaryMap[row.id]) }}
            </div>
          </div>
          <span v-else>—</span>
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
      <el-table-column label="操作" width="260" fixed="right">
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
          <el-button
            v-if="summaryMap[row.id]"
            link
            @click="openDailyUsage(row)"
          >明细</el-button>
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
          <el-input v-model="form.account_identifier" placeholder="邮箱或登录名" />
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
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="credentialVisible" title="Cursor API Key" width="480px">
      <p class="manual-hint">账号：{{ credentialAccount?.account_identifier }}</p>
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

    <el-dialog v-model="dailyVisible" title="按日用量明细" width="720px">
      <p class="manual-hint">账号：{{ dailyAccount?.account_identifier }}</p>
      <div class="daily-toolbar">
        <el-date-picker
          v-model="dailyRange"
          type="daterange"
          value-format="YYYY-MM-DD"
          range-separator="至"
          start-placeholder="起始"
          end-placeholder="结束"
          style="width: 280px"
          @change="loadDailyUsage"
        />
        <el-button :loading="dailyLoading" @click="loadDailyUsage">刷新</el-button>
      </div>
      <div v-loading="dailyLoading" class="daily-body">
        <p v-if="!dailyLoading && dailyGrouped.length === 0" class="muted">该区间暂无用量数据</p>
        <section v-for="group in dailyGrouped" :key="group.date" class="daily-day">
          <header class="daily-day-header">
            <span>{{ group.date }}</span>
            <span class="muted">{{ group.event_count }} 次 · {{ formatSpend(group.total_cost_usd) }}</span>
          </header>
          <div v-for="row in group.models" :key="row.model" class="daily-model-row">
            <span class="daily-model-name">{{ row.model }}</span>
            <div class="daily-bar-track">
              <div
                class="daily-bar-fill"
                :style="{ width: dailyBarWidth(row.total_cost_usd, group.max_cost) }"
              />
            </div>
            <span class="daily-model-cost">{{ formatSpend(row.total_cost_usd) }}</span>
            <span class="daily-model-tokens muted">{{ formatTokens(row.tokens_total) }}</span>
          </div>
        </section>
      </div>
      <template #footer>
        <el-button @click="dailyVisible = false">关闭</el-button>
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
interface CursorPoolBucket {
  spend_usd?: number
  reported_spend_usd?: number
  estimated_spend_usd?: number
  usage_ratio?: number | null
  quota_usd?: number | null
  breakdown_by_model?: Record<string, number>
}

interface ExternalModelStats {
  total_tokens: number
  event_count: number
}

interface UsageSummary {
  account_id: string
  primary_metric_value: number
  primary_metric_unit: string
  reported_spend_usd?: number | null
  estimated_included_spend_usd?: number | null
  quota_usage_ratio: number | null
  billing_cycle_start?: string | null
  billing_cycle_end?: string | null
  quota_denominator_snapshot?: number | null
  cycle_metric_value?: number | null
  cycle_quota_usage_ratio?: number | null
  estimation_coverage_pct?: number | null
  breakdown_by_model?: Record<string, number> | null
  cursor_pools?: {
    auto_composer?: CursorPoolBucket
    api?: CursorPoolBucket
  } | null
  external_models?: Record<string, ExternalModelStats> | null
}

const loading = ref(false)
const saving = ref(false)
const accounts = ref<Account[]>([])
const vendors = ref<Vendor[]>([])
const plans = ref<Plan[]>([])
const members = ref<Member[]>([])
const summaries = ref<UsageSummary[]>([])
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

interface DailyUsageRow {
  account_id: string
  event_date: string
  model: string
  event_count: number
  total_cost_usd: number
  tokens_input: number
  tokens_output: number
  tokens_cache_read: number
}

const dailyVisible = ref(false)
const dailyLoading = ref(false)
const dailyAccount = ref<Account | null>(null)
const dailyRows = ref<DailyUsageRow[]>([])
const dailyRange = ref<[string, string] | null>(null)

const dailyGrouped = computed(() => {
  const byDate = new Map<string, DailyUsageRow[]>()
  for (const row of dailyRows.value) {
    const list = byDate.get(row.event_date) || []
    list.push(row)
    byDate.set(row.event_date, list)
  }
  return [...byDate.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, models]) => {
      const sorted = [...models].sort((a, b) => b.total_cost_usd - a.total_cost_usd)
      const total_cost_usd = sorted.reduce((sum, m) => sum + m.total_cost_usd, 0)
      const event_count = sorted.reduce((sum, m) => sum + m.event_count, 0)
      const max_cost = sorted[0]?.total_cost_usd || 0
      return {
        date,
        models: sorted.map((m) => ({
          ...m,
          tokens_total: m.tokens_input + m.tokens_output + m.tokens_cache_read,
        })),
        total_cost_usd,
        event_count,
        max_cost,
      }
    })
})

const now = new Date()
const period = ref(`${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`)
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
const summaryMap = computed(() =>
  Object.fromEntries(summaries.value.map((s) => [s.account_id, s])),
)

function memberName(id: string | null) {
  if (!id) return ''
  return members.value.find((m) => m.id === id)?.display_name || ''
}

function formatSpend(value?: number | null) {
  if (value == null) return '—'
  return `$${Number(value).toFixed(2)}`
}

function formatAmount(value: number, unit?: string | null) {
  const normalized = (unit || 'usd').toUpperCase()
  if (normalized === 'USD') return `$${Number(value).toFixed(2)}`
  if (normalized === 'CNY') return `¥${Number(value).toFixed(2)}`
  return `${Number(value).toFixed(2)} ${normalized}`
}

function formatTokens(value: number) {
  const n = Number(value)
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M tokens`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K tokens`
  return `${n} tokens`
}

function autoComposerSpend(summary?: UsageSummary) {
  return summary?.cursor_pools?.auto_composer?.spend_usd ?? 0
}

function apiSpend(summary?: UsageSummary) {
  return summary?.cursor_pools?.api?.spend_usd ?? summary?.primary_metric_value ?? 0
}

function apiQuotaUsd(summary?: UsageSummary) {
  return summary?.cursor_pools?.api?.quota_usd ?? summary?.quota_denominator_snapshot ?? null
}

function apiQuotaRatio(summary?: UsageSummary): number | null {
  if (!summary) return null
  const fromPool = summary.cursor_pools?.api?.usage_ratio
  if (fromPool != null) return fromPool
  if (summary.cycle_quota_usage_ratio != null) return summary.cycle_quota_usage_ratio
  return summary.quota_usage_ratio
}

function poolModelBreakdown(summary: UsageSummary | undefined, pool: 'auto_composer' | 'api') {
  const breakdown = summary?.cursor_pools?.[pool]?.breakdown_by_model
  if (!breakdown) return []
  return Object.entries(breakdown)
    .filter(([, amount]) => Number(amount) > 0)
    .sort((a, b) => Number(b[1]) - Number(a[1]))
    .map(([name, value]) => ({ name, value: Number(value) }))
}

function hasExternalModels(summary?: UsageSummary) {
  return Object.keys(summary?.external_models || {}).length > 0
}

function externalModelBreakdown(summary?: UsageSummary) {
  const models = summary?.external_models || {}
  return Object.entries(models)
    .filter(([, stats]) => Number(stats.total_tokens) > 0)
    .sort((a, b) => Number(b[1].total_tokens) - Number(a[1].total_tokens))
    .map(([name, stats]) => ({ name, tokens: Number(stats.total_tokens) }))
}

function modelBreakdown(summary?: UsageSummary) {
  if (!summary?.breakdown_by_model) return []
  return Object.entries(summary.breakdown_by_model)
    .filter(([, amount]) => Number(amount) > 0)
    .sort((a, b) => Number(b[1]) - Number(a[1]))
    .map(([name, value]) => ({ name, value: Number(value) }))
}

function effectiveQuotaRatio(summary?: UsageSummary): number | null {
  if (!summary) return null
  if (summary.cycle_quota_usage_ratio != null) return summary.cycle_quota_usage_ratio
  return summary.quota_usage_ratio
}

function cycleLabel(summary?: UsageSummary): string | null {
  if (!summary?.billing_cycle_start || !summary.billing_cycle_end) return null
  const end = summary.billing_cycle_end
  const denom = summary.quota_denominator_snapshot
  const cycleVal = summary.cycle_metric_value
  const denomText = denom != null ? ` / $${denom}` : ''
  const valText = cycleVal != null ? `$${Number(cycleVal).toFixed(2)}` : ''
  return `周期 ${summary.billing_cycle_start}~${end}${valText ? `：${valText}${denomText}` : ''}`
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
    ElMessage.success('API Key 已绑定')
    await loadSummaries()
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } }
    ElMessage.error(err.response?.data?.detail || '绑定失败')
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

function periodDateRange(periodStr: string): [string, string] {
  const [year, month] = periodStr.split('-').map(Number)
  const lastDay = new Date(year, month, 0).getDate()
  return [
    `${periodStr}-01`,
    `${periodStr}-${String(lastDay).padStart(2, '0')}`,
  ]
}

function dailyBarWidth(cost: number, maxCost: number) {
  if (!maxCost || maxCost <= 0) return '0%'
  return `${Math.max(4, Math.round((cost / maxCost) * 100))}%`
}

function openDailyUsage(row: Account) {
  dailyAccount.value = row
  dailyRange.value = periodDateRange(period.value)
  dailyVisible.value = true
  loadDailyUsage()
}

async function loadDailyUsage() {
  if (!dailyAccount.value || !dailyRange.value) return
  const [start, end] = dailyRange.value
  dailyLoading.value = true
  try {
    const res = await client.get(`/api/v2/accounts/${dailyAccount.value.id}/usage/daily`, {
      params: { start, end },
    })
    dailyRows.value = res.data
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } }
    ElMessage.error(err.response?.data?.detail || '加载用量明细失败')
    dailyRows.value = []
  } finally {
    dailyLoading.value = false
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
    await loadSummaries()
    if (dailyVisible.value && dailyAccount.value?.id === credentialAccount.value?.id) {
      await loadDailyUsage()
    }
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
    if (!manualPeriod.value) manualPeriod.value = period.value
    await Promise.all([loadSummaries(), loadCredentialStatuses()])
  } finally {
    loading.value = false
  }
}

async function loadSummaries() {
  const res = await client.get('/api/v2/usage-summaries', { params: { period: period.value } })
  summaries.value = res.data
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

function openManual(row: Account) {
  manualAccount.value = row
  manualValue.value = summaryMap.value[row.id]?.primary_metric_value || 0
  manualUnit.value = summaryMap.value[row.id]?.primary_metric_unit || ''
  manualPeriod.value = period.value
  manualNote.value = ''
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
    ElMessage.success('用量已记录')
    manualVisible.value = false
    await loadSummaries()
  } catch {
    ElMessage.error('提交失败')
  } finally {
    manualSaving.value = false
  }
}

async function save() {
  saving.value = true
  try {
    if (editing.value) {
      if (!form.account_identifier.trim()) {
        ElMessage.warning('请填写账号标识')
        return
      }
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
      if (!form.account_identifier || !form.plan_id) {
        ElMessage.warning('请填写完整信息')
        return
      }
      await client.post('/api/v2/accounts', {
        ...form,
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
.usage-cell {
  line-height: 1.35;
}
.usage-section {
  margin-bottom: 6px;
}
.usage-section.external {
  margin-top: 4px;
  padding-top: 4px;
  border-top: 1px dashed #e2e8f0;
}
.usage-section-title {
  font-size: 12px;
  font-weight: 600;
  color: #475569;
  margin-bottom: 2px;
}
.usage-pool {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-size: 13px;
}
.pool-label {
  color: #334155;
}
.pool-value {
  font-weight: 600;
  white-space: nowrap;
}
.quota-pool-label {
  font-size: 12px;
  color: #64748b;
  margin-right: 4px;
}
.quota-pool-hint {
  font-size: 12px;
  margin-top: 2px;
}
.usage-total {
  font-weight: 600;
}
.usage-sub {
  font-size: 12px;
  margin-top: 2px;
}
.usage-model {
  font-size: 12px;
  margin-top: 1px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 280px;
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
.quota-over {
  color: #dc2626;
  font-weight: 600;
}
.cycle-hint {
  margin-top: 2px;
  font-size: 12px;
  line-height: 1.3;
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
.daily-toolbar {
  display: flex;
  gap: 12px;
  align-items: center;
  margin-bottom: 16px;
}
.daily-body {
  max-height: 480px;
  overflow-y: auto;
}
.daily-day {
  margin-bottom: 16px;
}
.daily-day-header {
  display: flex;
  justify-content: space-between;
  font-weight: 600;
  font-size: 14px;
  margin-bottom: 8px;
  padding-bottom: 4px;
  border-bottom: 1px solid #e2e8f0;
}
.daily-model-row {
  display: grid;
  grid-template-columns: 140px 1fr 72px 80px;
  gap: 8px;
  align-items: center;
  font-size: 13px;
  margin-bottom: 6px;
}
.daily-model-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.daily-bar-track {
  height: 8px;
  background: #f1f5f9;
  border-radius: 4px;
  overflow: hidden;
}
.daily-bar-fill {
  height: 100%;
  background: linear-gradient(90deg, #3b82f6, #6366f1);
  border-radius: 4px;
}
.daily-model-cost {
  text-align: right;
  font-weight: 600;
}
.daily-model-tokens {
  text-align: right;
  font-size: 12px;
}
</style>
