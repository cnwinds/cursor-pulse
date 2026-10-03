<template>
  <div class="credit-statement" v-loading="loading">
    <div class="summary-header">
      <div class="balance-block">
        <span class="balance-label">当前余额</span>
        <span class="balance-value" :class="{ negative: balanceCents <= 0 }">
          {{ formatUsdCents(balanceCents) }}
        </span>
      </div>
      <div v-if="summary" class="summary-stats">
        <span>扣费 {{ formatUsdCents(summary.total_charge_cents) }}</span>
        <span>退还 {{ formatUsdCents(summary.total_refund_cents) }}</span>
      </div>
    </div>

    <div class="filters">
      <el-select v-model="kindFilter" clearable placeholder="全部类型" style="width: 120px" @change="reload">
        <el-option label="充值" value="grant" />
        <el-option label="扣费" value="charge" />
        <el-option label="退还" value="refund" />
        <el-option label="调整" value="adjust" />
      </el-select>
      <el-date-picker
        v-model="dateRange"
        type="daterange"
        range-separator="至"
        start-placeholder="开始日期"
        end-placeholder="结束日期"
        value-format="YYYY-MM-DD"
        :shortcuts="dateShortcuts"
        @change="reload"
      />
      <el-button @click="exportCsv" :loading="exporting">导出 CSV</el-button>
    </div>

    <div v-if="summary" class="summary-tables">
      <div v-if="summary.by_day.length" class="summary-section">
        <h4>按天汇总</h4>
        <el-table :data="summary.by_day" size="small" stripe max-height="160">
          <el-table-column prop="day" label="日期" width="120" />
          <el-table-column label="扣费" width="100" align="right">
            <template #default="{ row }">{{ formatUsdCents(row.charge_cents) }}</template>
          </el-table-column>
          <el-table-column label="退还" width="100" align="right">
            <template #default="{ row }">{{ formatUsdCents(row.refund_cents) }}</template>
          </el-table-column>
        </el-table>
      </div>
      <div v-if="summary.by_model.length" class="summary-section">
        <h4>按模型汇总</h4>
        <el-table :data="summary.by_model" size="small" stripe max-height="160">
          <el-table-column prop="model" label="模型" min-width="140" show-overflow-tooltip />
          <el-table-column prop="count" label="笔数" width="72" align="right" />
          <el-table-column label="扣费" width="100" align="right">
            <template #default="{ row }">{{ formatUsdCents(row.charge_cents) }}</template>
          </el-table-column>
        </el-table>
      </div>
    </div>

    <el-table :data="items" stripe row-key="id" class="txn-table">
      <el-table-column type="expand" width="40">
        <template #default="{ row }">
          <div class="expand-body">
            <template v-if="row.usage">
              <p class="expand-line">用量时间：{{ formatChinaTime(row.usage.ts) }}</p>
              <p class="expand-line">
                Token：输入 {{ row.usage.tokens.input }} · 输出 {{ row.usage.tokens.output }} ·
                缓存读 {{ row.usage.tokens.cache_read }} · 缓存写 {{ row.usage.tokens.cache_write }} ·
                推理 {{ row.usage.tokens.reasoning }}
              </p>
            </template>
            <p v-if="row.note" class="expand-line">备注：{{ row.note }}</p>
            <p v-if="row.actor_name" class="expand-line">操作人：{{ row.actor_name }}</p>
            <p v-if="row.kind === 'refund' && row.ref_transaction_id" class="expand-line">
              关联扣费：
              <el-button link type="primary" @click="scrollToTxn(row.ref_transaction_id!)">
                #{{ row.ref_transaction_id }}
              </el-button>
            </p>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="时间" width="168">
        <template #default="{ row }">
          <span :id="`txn-row-${row.id}`">{{ formatChinaTime(row.created_at) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="类型" width="80" align="center">
        <template #default="{ row }">
          <el-tag :type="kindTagType(row.kind)" size="small">{{ kindLabel(row.kind) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="模型" min-width="120" show-overflow-tooltip>
        <template #default="{ row }">{{ row.usage?.model || '—' }}</template>
      </el-table-column>
      <el-table-column label="桶" width="72" align="center">
        <template #default="{ row }">{{ poolLabel(row.usage?.pool) }}</template>
      </el-table-column>
      <el-table-column label="客户端" width="72" align="center">
        <template #default="{ row }">{{ clientLabel(row.usage?.client) }}</template>
      </el-table-column>
      <el-table-column label="来源" min-width="100" show-overflow-tooltip>
        <template #default="{ row }">{{ row.usage?.source?.label || '—' }}</template>
      </el-table-column>
      <el-table-column label="Token" width="80" align="right">
        <template #default="{ row }">
          {{ row.usage ? row.usage.tokens.total : '—' }}
        </template>
      </el-table-column>
      <el-table-column label="金额" width="96" align="right">
        <template #default="{ row }">
          <span :class="{ 'amount-negative': row.amount_cents < 0 }">
            {{ formatUsdCents(row.amount_cents) }}
          </span>
        </template>
      </el-table-column>
      <el-table-column label="余额" width="96" align="right">
        <template #default="{ row }">
          <span :class="{ 'amount-negative': row.balance_after_cents <= 0 }">
            {{ formatUsdCents(row.balance_after_cents) }}
          </span>
        </template>
      </el-table-column>
      <el-table-column v-if="allowRefund" label="" width="88" align="center" fixed="right">
        <template #default="{ row }">
          <template v-if="row.kind === 'charge'">
            <span v-if="row.refunded_by_transaction_id" class="refunded-hint">
              已退还 #{{ row.refunded_by_transaction_id }}
            </span>
            <el-button v-else link type="warning" size="small" @click="refundCharge(row)">
              退还
            </el-button>
          </template>
        </template>
      </el-table-column>
    </el-table>

    <div v-if="nextCursor" class="load-more">
      <el-button :loading="loadingMore" @click="loadMore">加载更多</el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import client from '@/api/client'
import { formatUsdCents } from '@/utils/money'
import {
  addCalendarDays,
  calendarDateInTimeZone,
  formatChinaTime,
  formatYmd,
  DEFAULT_DISPLAY_TIMEZONE,
} from '@/utils/time'

export type TransactionKind = 'grant' | 'charge' | 'refund' | 'adjust'

export interface TransactionUsage {
  usage_id: string
  ts: string
  model: string
  pool: 'auto' | 'api' | null
  client: 'cli' | 'ide' | null
  source: { kind: 'proxy_key' | 'loan'; label: string }
  tokens: {
    input: number
    output: number
    cache_read: number
    cache_write: number
    reasoning: number
    total: number
  }
}

export interface TransactionOut {
  id: number
  created_at: string
  kind: TransactionKind
  amount_cents: number
  balance_after_cents: number
  note: string
  actor_name: string | null
  ref_transaction_id: number | null
  refunded_by_transaction_id: number | null
  usage: TransactionUsage | null
}

interface Summary {
  from: string
  to: string
  total_charge_cents: number
  total_refund_cents: number
  by_day: { day: string; charge_cents: number; refund_cents: number }[]
  by_model: { model: string; charge_cents: number; count: number }[]
}

const props = defineProps<{
  baseUrl: string
  allowRefund?: boolean
}>()

const emit = defineEmits<{ refunded: [] }>()

const loading = ref(false)
const loadingMore = ref(false)
const exporting = ref(false)
const items = ref<TransactionOut[]>([])
const nextCursor = ref<number | null>(null)
const balanceCents = ref(0)
const summary = ref<Summary | null>(null)
const kindFilter = ref<TransactionKind | ''>('')
const dateRange = ref<[string, string] | null>(null)

const today = calendarDateInTimeZone(DEFAULT_DISPLAY_TIMEZONE)
const monthStart = formatYmd(today.year, today.month, 1)
const todayStr = formatYmd(today.year, today.month, today.day)
dateRange.value = [monthStart, todayStr]

const dateShortcuts = [
  {
    text: '本月',
    value: () => {
      const t = calendarDateInTimeZone(DEFAULT_DISPLAY_TIMEZONE)
      return [new Date(formatYmd(t.year, t.month, 1)), new Date(formatYmd(t.year, t.month, t.day))]
    },
  },
  {
    text: '近 7 天',
    value: () => {
      const t = calendarDateInTimeZone(DEFAULT_DISPLAY_TIMEZONE)
      const start = addCalendarDays(t.year, t.month, t.day, -6)
      return [
        new Date(formatYmd(start.year, start.month, start.day)),
        new Date(formatYmd(t.year, t.month, t.day)),
      ]
    },
  },
]

function beijingDayStartIso(ymd: string): string {
  return new Date(`${ymd}T00:00:00+08:00`).toISOString()
}

function beijingDayEndExclusiveIso(ymd: string): string {
  const [y, m, d] = ymd.split('-').map(Number)
  const next = addCalendarDays(y, m, d, 1)
  return beijingDayStartIso(formatYmd(next.year, next.month, next.day))
}

function queryParams(): Record<string, string | number> {
  const params: Record<string, string | number> = { limit: 50 }
  if (kindFilter.value) params.kind = kindFilter.value
  if (dateRange.value?.[0]) params.from = beijingDayStartIso(dateRange.value[0])
  if (dateRange.value?.[1]) params.to = beijingDayEndExclusiveIso(dateRange.value[1])
  return params
}

function kindLabel(kind: TransactionKind): string {
  return { grant: '充值', charge: '扣费', refund: '退还', adjust: '调整' }[kind] || kind
}

function kindTagType(kind: TransactionKind): 'success' | 'danger' | 'warning' | 'info' {
  return (
    { grant: 'success', charge: 'danger', refund: 'warning', adjust: 'info' } as Record<
      string,
      'success' | 'danger' | 'warning' | 'info'
    >
  )[kind] || 'info'
}

function poolLabel(pool: string | null | undefined): string {
  if (!pool) return '—'
  return { auto: 'Auto', api: 'API' }[pool] || pool
}

function clientLabel(client: string | null | undefined): string {
  if (!client) return '—'
  return { cli: 'CLI', ide: 'IDE' }[client] || client
}

function scrollToTxn(id: number) {
  const el = document.getElementById(`txn-row-${id}`)
  el?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}

async function loadSummary() {
  if (!dateRange.value?.[0] || !dateRange.value?.[1]) {
    summary.value = null
    return
  }
  try {
    const res = await client.get(`${props.baseUrl}/summary`, { params: queryParams() })
    summary.value = res.data
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '加载汇总失败')
  }
}

async function loadTransactions(cursor?: number) {
  const params = { ...queryParams() }
  if (cursor != null) params.cursor = cursor
  const res = await client.get(`${props.baseUrl}/transactions`, { params })
  return res.data as {
    items: TransactionOut[]
    next_cursor: number | null
    balance_cents: number
  }
}

async function reload() {
  loading.value = true
  items.value = []
  nextCursor.value = null
  try {
    await loadSummary()
    const data = await loadTransactions()
    items.value = data.items
    nextCursor.value = data.next_cursor
    balanceCents.value = data.balance_cents
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '加载账单失败')
  } finally {
    loading.value = false
  }
}

async function loadMore() {
  if (!nextCursor.value) return
  loadingMore.value = true
  try {
    const data = await loadTransactions(nextCursor.value)
    items.value.push(...data.items)
    nextCursor.value = data.next_cursor
    balanceCents.value = data.balance_cents
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '加载失败')
  } finally {
    loadingMore.value = false
  }
}

function parseFilename(contentDisposition: string | undefined): string {
  if (!contentDisposition) return 'credit-transactions.csv'
  const match = /filename\*?=(?:UTF-8'')?["']?([^"';]+)/i.exec(contentDisposition)
  return match?.[1]?.trim() || 'credit-transactions.csv'
}

async function exportCsv() {
  exporting.value = true
  try {
    const res = await client.get(`${props.baseUrl}/transactions.csv`, {
      params: queryParams(),
      responseType: 'blob',
    })
    const blob = res.data as Blob
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = parseFilename(res.headers['content-disposition'])
    link.click()
    URL.revokeObjectURL(url)
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '导出失败')
  } finally {
    exporting.value = false
  }
}

async function refundCharge(row: TransactionOut) {
  try {
    const { value } = await ElMessageBox.prompt('可选备注', `退还扣费 #${row.id}`, {
      confirmButtonText: '确认退还',
      cancelButtonText: '取消',
      inputPlaceholder: '备注（可选）',
    })
    await client.post(`${props.baseUrl}/transactions/${row.id}/refund`, {
      note: value?.trim() || null,
    })
    ElMessage.success('已退还')
    emit('refunded')
    await reload()
  } catch (e: any) {
    if (e === 'cancel' || e === 'close') return
    ElMessage.error(e.response?.data?.detail || '退还失败')
  }
}

onMounted(reload)

defineExpose({ reload })
</script>

<style scoped>
.credit-statement {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.summary-header {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 16px 24px;
}

.balance-block {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.balance-label {
  font-size: var(--pulse-text-sm);
  color: var(--pulse-text-secondary);
}

.balance-value {
  font-family: var(--pulse-font-mono);
  font-size: var(--pulse-text-lg);
  font-weight: var(--pulse-font-semibold);
}

.balance-value.negative,
.amount-negative {
  color: var(--el-color-danger);
}

.summary-stats {
  display: flex;
  gap: 16px;
  font-size: var(--pulse-text-sm);
  color: var(--pulse-text-secondary);
}

.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
}

.summary-tables {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 16px;
}

.summary-section h4 {
  margin: 0 0 8px;
  font-size: var(--pulse-text-sm);
  font-weight: var(--pulse-font-semibold);
  color: var(--pulse-text-secondary);
}

.expand-body {
  padding: 8px 12px 12px 48px;
  font-size: var(--pulse-text-sm);
  color: var(--pulse-text-secondary);
  line-height: 1.5;
}

.expand-line {
  margin: 0 0 4px;
}

.refunded-hint {
  font-size: var(--pulse-text-xs);
  color: var(--el-color-warning);
}

.load-more {
  text-align: center;
  padding: 8px 0;
}
</style>
