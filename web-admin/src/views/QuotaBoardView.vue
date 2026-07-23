<template>
  <div class="quota-board" v-loading="loading">
    <header class="page-header">
      <div>
        <h2>额度看板</h2>
        <p class="desc">
          额度与 Cursor 后台 Plan &amp; Usage 对齐（Total / Auto+Composer / API 百分比）。
        </p>
      </div>
      <div class="header-actions">
        <el-button @click="loadAll">刷新</el-button>
      </div>
    </header>

    <el-row :gutter="16">
      <el-col
        v-for="item in board"
        :key="item.account_id"
        :xs="24"
        :sm="12"
        :lg="8"
        class="card-col"
      >
        <el-card shadow="never" class="quota-card" :class="item.status">
          <div class="card-top">
            <div>
              <div class="account-id">
                <span>{{ item.account_identifier }}</span>
                <span v-if="item.primary_member_name" class="primary-member">
                  {{ item.primary_member_name }}
                </span>
              </div>
              <div class="muted">{{ item.plan_name }} · {{ item.vendor_name }}</div>
            </div>
            <el-tag :type="statusTagType(item.status)" size="small">
              {{ statusLabel(item.status) }}
            </el-tag>
          </div>

          <template v-if="item.has_snapshot">
            <div class="cycle-meta">
              <span class="meta-item" :title="`计费周期 ${item.cycle_start} ~ ${item.cycle_end}`">
                <el-icon><Calendar /></el-icon>
                {{ shortDate(item.cycle_start) }} ~ {{ shortDate(item.cycle_end) }}
              </span>
              <span class="meta-sep">·</span>
              <span class="meta-item" title="距下次重置">
                <el-icon><Timer /></el-icon>
                {{ item.days_until_reset }}天后重置
              </span>
              <template v-if="item.projected_exhaustion_date">
                <span class="meta-sep">·</span>
                <span
                  class="meta-item"
                  :class="{ danger: item.exhausts_before_reset }"
                  :title="item.exhausts_before_reset ? '预计早于重置耗尽' : '预计耗尽日期'"
                >
                  <el-icon><WarningFilled /></el-icon>
                  预计{{ shortDate(item.projected_exhaustion_date) }}耗尽
                </span>
              </template>
            </div>

            <div class="usage-section">
              <div class="progress-block">
                <div class="progress-label">
                  <span>Total</span>
                  <span>{{ cursorPct(item.total_pct) }}</span>
                </div>
                <el-progress
                  :percentage="cursorPctNum(item.total_pct)"
                  :status="item.status === 'exhausted' ? 'exception' : item.status === 'warning' ? 'warning' : undefined"
                  :show-text="false"
                />
              </div>
              <div class="progress-block sub">
                <div class="progress-label">
                  <span>Auto + Composer</span>
                  <span>{{ cursorPct(item.auto_pct) }}</span>
                </div>
                <el-progress :percentage="cursorPctNum(item.auto_pct)" :show-text="false" />
              </div>
              <div class="progress-block sub">
                <div class="progress-label">
                  <span>
                    API
                    <span v-if="item.api_limit_usd" class="api-inline-note muted">
                      · 套餐含至少 ${{ item.api_limit_usd.toFixed(0) }}
                    </span>
                  </span>
                  <span>{{ cursorPct(item.api_pct) }}</span>
                </div>
                <el-progress :percentage="cursorPctNum(item.api_pct)" :show-text="false" />
              </div>
            </div>

            <div v-if="summaryMap[item.account_id]?.cursor_pools" class="spend-section">
              <button
                type="button"
                class="spend-title"
                @click="toggleSpendExpand(item.account_id)"
              >
                <span>本周期用量明细</span>
                <span class="spend-toggle">{{ spendExpanded[item.account_id] ? '收起' : '展开' }}</span>
              </button>
              <div
                class="usage-pool"
                :class="{ 'has-tokens': poolHasTokens(summaryMap[item.account_id], 'auto_composer') }"
              >
                <span class="pool-label">Auto+Composer</span>
                <span class="model-tokens">{{ formatCompactTokens(poolTotalTokens(summaryMap[item.account_id], 'auto_composer')) || '—' }}</span>
                <span class="pool-value">{{ formatSpend(autoComposerSpend(summaryMap[item.account_id])) }}</span>
              </div>
              <div
                v-if="spendExpanded[item.account_id]"
                class="usage-model-table"
                :class="{ 'has-tokens': poolHasTokens(summaryMap[item.account_id], 'auto_composer') }"
              >
                <div
                  v-for="m in poolModelBreakdown(summaryMap[item.account_id], 'auto_composer')"
                  :key="'ac-' + m.name"
                  class="usage-model-row muted"
                >
                  <span class="model-name" :title="m.name">{{ m.name }}</span>
                  <span class="model-tokens">{{ formatCompactTokens(m.tokens) || '—' }}</span>
                  <span class="model-cost">{{ formatSpend(m.value) }}</span>
                </div>
              </div>
              <div
                class="usage-pool"
                :class="{ 'has-tokens': poolHasTokens(summaryMap[item.account_id], 'api') }"
              >
                <span class="pool-label">高级模型 API</span>
                <span class="model-tokens">{{ formatCompactTokens(poolTotalTokens(summaryMap[item.account_id], 'api')) || '—' }}</span>
                <span class="pool-value">{{ formatSpend(premiumApiSpend(summaryMap[item.account_id])) }}<template v-if="apiQuotaUsd(summaryMap[item.account_id])"> / {{ formatSpend(apiQuotaUsd(summaryMap[item.account_id])) }}</template></span>
              </div>
              <div
                v-if="spendExpanded[item.account_id]"
                class="usage-model-table"
                :class="{ 'has-tokens': poolHasTokens(summaryMap[item.account_id], 'api') }"
              >
                <div
                  v-for="m in poolModelBreakdown(summaryMap[item.account_id], 'api')"
                  :key="'api-' + m.name"
                  class="usage-model-row muted"
                >
                  <span class="model-name" :title="m.name">{{ m.name }}</span>
                  <span class="model-tokens">{{ formatCompactTokens(m.tokens) || '—' }}</span>
                  <span class="model-cost">{{ formatSpend(m.value) }}</span>
                </div>
              </div>
              <template v-if="thirdPartySpend(summaryMap[item.account_id]) > 0">
                <div
                  class="usage-pool"
                  :class="{ 'has-tokens': poolHasTokens(summaryMap[item.account_id], 'third_party') }"
                >
                  <span class="pool-label">三方模型</span>
                  <span class="model-tokens">{{ formatCompactTokens(poolTotalTokens(summaryMap[item.account_id], 'third_party')) || '—' }}</span>
                  <span class="pool-value">
                    {{ formatSpend(thirdPartySpend(summaryMap[item.account_id])) }}
                  </span>
                </div>
                <div
                  v-if="spendExpanded[item.account_id]"
                  class="usage-model-table"
                  :class="{ 'has-tokens': poolHasTokens(summaryMap[item.account_id], 'third_party') }"
                >
                  <div
                    v-for="m in poolModelBreakdown(summaryMap[item.account_id], 'third_party')"
                    :key="'tp-' + m.name"
                    class="usage-model-row muted"
                  >
                    <span class="model-name" :title="m.name">{{ m.name }}</span>
                    <span class="model-tokens">{{ formatCompactTokens(m.tokens) || '—' }}</span>
                    <span class="model-cost">{{ formatSpend(m.value) }}</span>
                  </div>
                </div>
              </template>
            </div>

            <div class="card-actions">
              <span class="muted updated-at">最后更新 {{ formatChinaTime(item.captured_at) }}</span>
              <div class="card-action-btns">
                <el-button link type="primary" @click="openDailyUsage(item)">明细</el-button>
                <el-button
                  v-if="canWrite"
                  link
                  :loading="syncingId === item.account_id"
                  @click="syncAccount(item)"
                >同步</el-button>
              </div>
            </div>
          </template>

          <div v-else class="empty-snapshot muted">
            暂无同步快照，请先在账号台账绑定 API Key
            <div v-if="canWrite" class="card-actions">
              <el-button link type="primary" @click="syncAccount(item)" :loading="syncingId === item.account_id">
                尝试同步
              </el-button>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

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
        <el-radio-group v-model="dailyBarMode" size="small" class="daily-bar-mode">
          <el-radio-button value="daily">按日</el-radio-button>
          <el-radio-button value="period">按月</el-radio-button>
        </el-radio-group>
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
              <div class="daily-bar-fill" :style="{ width: dailyBarWidth(row.tokens_total, dailyBarDenominator(group)) }" />
            </div>
            <span class="daily-model-cost muted">{{ formatSpend(row.total_cost_usd) }}</span>
            <span class="daily-model-tokens">{{ formatTokens(row.tokens_total) }}</span>
          </div>
        </section>
      </div>
      <template #footer>
        <el-button @click="dailyVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import client from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import {
  autoComposerSpend,
  apiQuotaUsd,
  billingCycleDateRange,
  formatCompactTokens,
  formatSpend,
  formatTokens,
  poolHasTokens,
  poolModelBreakdown,
  poolTotalTokens,
  premiumApiSpend,
  thirdPartySpend,
  type UsageSummary,
} from '@/utils/usage'
import { formatChinaTime } from '@/utils/time'

const auth = useAuthStore()
const canWrite = computed(() => auth.hasPermission('accounts:write'))

interface BoardItem {
  account_id: string
  account_identifier: string
  primary_member_id: string | null
  primary_member_name: string | null
  vendor_name: string
  plan_name: string
  status: string
  has_snapshot: boolean
  cycle_start: string | null
  cycle_end: string | null
  usage_resets_on: string | null
  total_pct: number | null
  auto_pct: number | null
  api_pct: number | null
  remaining_headroom_pct: number | null
  api_limit_usd: number | null
  projected_exhaustion_date: string | null
  exhausts_before_reset: boolean | null
  days_until_reset: number | null
  captured_at: string | null
}

const loading = ref(false)
const board = ref<BoardItem[]>([])
const summaryMap = ref<Record<string, UsageSummary>>({})
const syncingId = ref<string | null>(null)
const spendExpanded = ref<Record<string, boolean>>({})

function toggleSpendExpand(accountId: string) {
  spendExpanded.value[accountId] = !spendExpanded.value[accountId]
}

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
const dailyAccount = ref<BoardItem | null>(null)
const dailyRows = ref<DailyUsageRow[]>([])
const dailyRange = ref<[string, string] | null>(null)
const dailyBarMode = ref<'daily' | 'period'>('period')

const dailyPeriodTotalTokens = computed(() =>
  dailyRows.value.reduce(
    (sum, row) => sum + row.tokens_input + row.tokens_output + row.tokens_cache_read,
    0,
  ),
)

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
      const withTokens = models.map((m) => ({
        ...m,
        tokens_total: m.tokens_input + m.tokens_output + m.tokens_cache_read,
      }))
      const sorted = [...withTokens].sort((a, b) => b.tokens_total - a.tokens_total)
      const total_cost_usd = sorted.reduce((sum, m) => sum + m.total_cost_usd, 0)
      const event_count = sorted.reduce((sum, m) => sum + m.event_count, 0)
      const total_tokens = sorted.reduce((sum, m) => sum + m.tokens_total, 0)
      return {
        date,
        models: sorted,
        total_cost_usd,
        event_count,
        total_tokens,
      }
    })
})

function statusLabel(status: string) {
  return { healthy: '正常', warning: '预警', exhausted: '已耗尽', unknown: '未知' }[status] || status
}

function statusTagType(status: string) {
  return { healthy: 'success', warning: 'warning', exhausted: 'danger', unknown: 'info' }[status] || 'info'
}

/** YYYY-MM-DD → MM-DD，便于卡片一行展示 */
function shortDate(value: string | null | undefined) {
  if (!value) return '—'
  const m = value.match(/(\d{4})-(\d{2})-(\d{2})/)
  return m ? `${m[2]}-${m[3]}` : value
}

function cursorPct(v: number | null) {
  if (v == null) return '—'
  return `${Math.round(v)}%`
}

function cursorPctNum(v: number | null) {
  if (v == null) return 0
  return Math.min(Math.round(v), 100)
}

function dailyBarDenominator(group: { total_tokens: number }) {
  if (dailyBarMode.value === 'period') {
    return dailyPeriodTotalTokens.value
  }
  return group.total_tokens
}

function dailyBarWidth(tokens: number, totalTokens: number) {
  if (!totalTokens || totalTokens <= 0) return '0%'
  return `${Math.round((tokens / totalTokens) * 100)}%`
}

function currentPeriod() {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
}

async function loadSummaries() {
  const res = await client.get('/api/v2/usage-summaries', {
    params: { period: currentPeriod() },
  })
  summaryMap.value = Object.fromEntries(
    (res.data as UsageSummary[]).map((s) => [s.account_id, s]),
  )
}

async function loadAll() {
  loading.value = true
  try {
    const boardRes = await client.get('/api/v2/quota-board')
    await loadSummaries()
    board.value = boardRes.data
  } finally {
    loading.value = false
  }
}

function openDailyUsage(item: BoardItem) {
  dailyAccount.value = item
  dailyRange.value = billingCycleDateRange(item.cycle_start, item.cycle_end)
  dailyVisible.value = true
  loadDailyUsage()
}

async function loadDailyUsage() {
  if (!dailyAccount.value || !dailyRange.value) return
  const [start, end] = dailyRange.value
  dailyLoading.value = true
  try {
    const res = await client.get(`/api/v2/accounts/${dailyAccount.value.account_id}/usage/daily`, {
      params: { start, end },
    })
    dailyRows.value = res.data
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '加载用量明细失败')
    dailyRows.value = []
  } finally {
    dailyLoading.value = false
  }
}

async function syncAccount(item: BoardItem) {
  syncingId.value = item.account_id
  try {
    const res = await client.post(`/api/v2/accounts/${item.account_id}/sync`)
    ElMessage.success(`同步完成，${res.data.event_count} 条事件`)
    await loadAll()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '同步失败')
  } finally {
    syncingId.value = null
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
  gap: 16px;
}
.page-header h2 {
  margin: 0 0 8px;
}
.desc {
  margin: 0;
  color: var(--el-text-color-secondary);
  font-size: 14px;
}
.header-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}
.card-col {
  margin-bottom: 16px;
}
.quota-card {
  height: 100%;
}
.quota-card.exhausted {
  border-left: 3px solid var(--el-color-danger);
}
.quota-card.warning {
  border-left: 3px solid var(--el-color-warning);
}
.card-top {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 12px;
}
.account-id {
  font-weight: 600;
  word-break: break-all;
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px;
}
.primary-member {
  font-weight: 500;
  color: var(--el-text-color-regular);
  font-size: 14px;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.cycle-meta {
  display: flex;
  flex-wrap: nowrap;
  align-items: center;
  gap: 0;
  margin-bottom: 10px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.meta-item {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  min-width: 0;
}
.meta-sep {
  margin: 0 6px;
  color: var(--el-border-color);
  flex-shrink: 0;
}
.meta-item .el-icon {
  font-size: 12px;
  color: var(--el-text-color-placeholder);
  flex-shrink: 0;
}
.meta-item.danger {
  color: var(--el-color-danger);
  font-weight: 500;
}
.meta-item.danger .el-icon {
  color: var(--el-color-danger);
}
.progress-block {
  margin-bottom: 10px;
}
.progress-label {
  display: flex;
  justify-content: space-between;
  font-size: 13px;
  margin-bottom: 4px;
}
.usage-section {
  margin: 8px 0 4px;
}
.progress-block.sub {
  margin-left: 8px;
}
.api-inline-note {
  font-weight: 400;
  font-size: 12px;
  margin-left: 2px;
}
.empty-snapshot {
  padding: 24px 0;
  text-align: center;
}
.spend-section {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dashed var(--el-border-color-lighter);
  font-size: 12px;
}
.spend-title {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
  padding: 0;
  margin-bottom: 6px;
  border: none;
  background: transparent;
  cursor: pointer;
  font: inherit;
  font-weight: 600;
  color: var(--el-text-color-regular);
  text-align: left;
}
.spend-toggle {
  font-weight: 400;
  font-size: 12px;
  color: var(--el-color-primary);
}
.usage-pool {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 8px;
  align-items: baseline;
  margin-top: 4px;
  padding: 4px 6px;
  font-size: 13px;
  border-radius: 4px;
  background: var(--el-fill-color);
}
.usage-pool.has-tokens {
  grid-template-columns: minmax(0, 1fr) 48px auto;
}
.usage-pool:not(.has-tokens) .model-tokens {
  display: none;
}
.pool-label {
  color: var(--el-text-color-regular);
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pool-value {
  font-weight: 600;
  text-align: right;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.usage-model-table {
  margin-top: 2px;
  margin-bottom: 2px;
  border-radius: 4px;
  overflow: hidden;
}
.usage-model-row {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 8px;
  align-items: baseline;
  padding: 3px 6px;
  font-size: 12px;
}
.usage-model-row:nth-child(odd) {
  background: var(--el-fill-color-lighter);
}
.usage-model-row:nth-child(even) {
  background: transparent;
}
.usage-model-table.has-tokens .usage-model-row {
  grid-template-columns: minmax(0, 1fr) 48px auto;
}
.usage-model-table:not(.has-tokens) .model-tokens {
  display: none;
}
.model-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.model-tokens {
  text-align: right;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.model-cost {
  text-align: right;
  font-variant-numeric: tabular-nums;
  font-weight: 500;
  color: var(--el-text-color-regular);
  white-space: nowrap;
}
.card-actions {
  display: flex;
  gap: 8px;
  margin-top: 10px;
  align-items: center;
  justify-content: space-between;
}
.card-action-btns {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}
.updated-at {
  font-size: 12px;
}
.manual-hint {
  margin-bottom: 12px;
  color: var(--el-text-color-secondary);
}
.daily-toolbar {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
  margin-bottom: 16px;
}
.daily-bar-mode {
  flex-shrink: 0;
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
  border-bottom: 1px solid var(--el-border-color-lighter);
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
  font-weight: 400;
}
.daily-model-tokens {
  text-align: right;
  font-size: 12px;
  font-weight: 600;
}
</style>
