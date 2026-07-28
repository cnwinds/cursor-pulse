<template>
  <div class="usage-analytics" v-loading="loading">
    <header class="page-header">
      <div>
        <h2>用量分析</h2>
        <p class="desc">
          选定日历区间内的 Cursor Token 规模与结构（与额度看板互补；池划分按模型名归类）。
          <span v-if="overview?.timezone" class="tz">时区 {{ overview.timezone }}</span>
        </p>
      </div>
      <el-button @click="loadOverview">刷新</el-button>
    </header>

    <el-card shadow="never" class="filter-card">
      <div class="filters">
        <el-radio-group v-model="rangePreset" size="small" class="filter-preset" @change="onPresetChange">
          <el-radio-button value="this_month">本月</el-radio-button>
          <el-radio-button value="last_month">上月</el-radio-button>
          <el-radio-button value="last_7">近 7 天</el-radio-button>
          <el-radio-button value="last_30">近 30 天</el-radio-button>
          <el-radio-button value="custom">自定义</el-radio-button>
        </el-radio-group>
        <el-date-picker
          v-model="dateRange"
          type="daterange"
          range-separator="至"
          start-placeholder="开始"
          end-placeholder="结束"
          value-format="YYYY-MM-DD"
          :disabled="rangePreset !== 'custom'"
          size="small"
          class="filter-dates"
          @change="loadOverview"
        />
        <el-select
          v-model="filterAccountIds"
          multiple
          collapse-tags
          collapse-tags-tooltip
          clearable
          filterable
          placeholder="账号"
          size="small"
          class="filter-select"
          @change="loadOverview"
        >
          <el-option
            v-for="a in accounts"
            :key="a.id"
            :label="accountLabel(a)"
            :value="a.id"
          />
        </el-select>
        <el-select
          v-model="filterMemberIds"
          multiple
          collapse-tags
          collapse-tags-tooltip
          clearable
          filterable
          placeholder="主使用人"
          size="small"
          class="filter-select"
          @change="loadOverview"
        >
          <el-option
            v-for="m in members"
            :key="m.id"
            :label="m.display_name"
            :value="m.id"
          />
        </el-select>
        <el-select v-model="dimension" size="small" class="dim-select" @change="onDimensionChange">
          <el-option label="按模型" value="model" />
          <el-option label="按账号" value="account" />
          <el-option label="按模型族" value="family" />
          <el-option label="按用量池" value="pool" />
        </el-select>
      </div>
    </el-card>

    <el-row :gutter="12" class="kpi-row">
      <el-col :xs="12" :sm="8" :md="4" v-for="item in kpiItems" :key="item.label">
        <el-card shadow="never" class="kpi-card">
          <div class="kpi-label">{{ item.label }}</div>
          <div class="kpi-value">{{ item.value }}</div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" class="chart-row">
      <el-col :xs="24" :lg="14">
        <el-card shadow="never" class="chart-card">
          <div class="chart-title">日趋势</div>
          <v-chart class="chart" :option="trendOption" autoresize />
        </el-card>
      </el-col>
      <el-col :xs="24" :lg="10">
        <el-card shadow="never" class="chart-card">
          <div class="chart-title">结构占比 · {{ dimensionLabel }}</div>
          <v-chart class="chart" :option="structOption" autoresize />
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" class="chart-row">
      <el-col :span="24">
        <el-card shadow="never" class="chart-card">
          <div class="chart-title">排行 Top {{ topN }} · {{ dimensionLabel }}</div>
          <v-chart class="chart chart-bar" :option="rankOption" autoresize />
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never" class="table-card">
      <div class="table-header">
        <div class="chart-title">明细汇总 · {{ dimensionLabel }}</div>
        <span v-if="overview?.note" class="muted">{{ overview.note }}</span>
      </div>
      <el-table
        :data="tableRows"
        stripe
        size="small"
        @row-click="onRowClick"
        empty-text="所选区间暂无用量"
      >
        <el-table-column :label="dimensionColumnLabel" min-width="180">
          <template #default="{ row }">{{ row.label }}</template>
        </el-table-column>
        <el-table-column
          v-if="dimension === 'account'"
          prop="primary_member_name"
          label="主使用人"
          min-width="120"
        />
        <el-table-column
          v-if="dimension === 'model'"
          prop="pool"
          label="池"
          width="120"
        >
          <template #default="{ row }">{{ poolLabel(row.pool) }}</template>
        </el-table-column>
        <el-table-column
          v-if="dimension === 'model'"
          prop="family"
          label="模型族"
          width="100"
        />
        <el-table-column prop="tokens_total" label="Token" width="110" sortable>
          <template #default="{ row }">{{ formatTokens(row.tokens_total) }}</template>
        </el-table-column>
        <el-table-column prop="tokens_input" label="输入" width="100" sortable>
          <template #default="{ row }">{{ formatTokens(row.tokens_input) }}</template>
        </el-table-column>
        <el-table-column prop="tokens_output" label="输出" width="100" sortable>
          <template #default="{ row }">{{ formatTokens(row.tokens_output) }}</template>
        </el-table-column>
        <el-table-column prop="tokens_cache_read" label="Cache" width="100" sortable>
          <template #default="{ row }">{{ formatTokens(row.tokens_cache_read) }}</template>
        </el-table-column>
        <el-table-column prop="event_count" label="事件" width="90" sortable />
        <el-table-column prop="cost_usd" label="花费 $" width="100" sortable>
          <template #default="{ row }">{{ formatSpend(row.cost_usd) }}</template>
        </el-table-column>
        <el-table-column label="" width="80" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="canDrill(row)"
              link
              type="primary"
              size="small"
              @click.stop="openDrill(row)"
            >
              下钻
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-drawer v-model="drillOpen" size="60%" :title="drillTitle" destroy-on-close>
      <el-table v-loading="drillLoading" :data="drillItems" stripe size="small" max-height="70vh">
        <el-table-column prop="date" label="日期" width="120" />
        <el-table-column prop="account_identifier" label="账号" min-width="160" />
        <el-table-column prop="model" label="模型" min-width="160" />
        <el-table-column prop="tokens_total" label="Token" width="100">
          <template #default="{ row }">{{ formatTokens(row.tokens_total) }}</template>
        </el-table-column>
        <el-table-column prop="event_count" label="事件" width="80" />
        <el-table-column prop="cost_usd" label="花费 $" width="90">
          <template #default="{ row }">{{ formatSpend(row.cost_usd) }}</template>
        </el-table-column>
      </el-table>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import VChart from 'vue-echarts'
import '@/utils/echarts'
import { ElMessage } from 'element-plus'
import client from '@/api/client'
import { formatSpend, formatTokens } from '@/utils/usage'

type Dimension = 'account' | 'model' | 'family' | 'pool'
type RangePreset = 'this_month' | 'last_month' | 'last_7' | 'last_30' | 'custom'

interface AccountRow {
  id: string
  account_identifier: string
  primary_member_id?: string | null
}

interface MemberRow {
  id: string
  display_name: string
}

interface MetricRow {
  tokens_total: number
  tokens_input: number
  tokens_output: number
  tokens_cache_read: number
  event_count: number
  cost_usd: number
}

interface Overview {
  start: string
  end: string
  timezone: string
  note?: string
  kpi: MetricRow
  series_by_day: Array<{
    date: string
    tokens_total: number
    tokens_input: number
    tokens_output: number
    tokens_cache_read: number
    cost_usd: number
    event_count: number
  }>
  by_account: Array<
    MetricRow & {
      account_id: string
      account_identifier: string
      primary_member_name?: string | null
    }
  >
  by_model: Array<MetricRow & { model: string; pool: string; family: string }>
  by_pool: Array<MetricRow & { pool: string; pool_label: string }>
  by_family: Array<MetricRow & { family: string }>
}

interface TableRow extends MetricRow {
  key: string
  label: string
  account_id?: string
  model?: string
  pool?: string
  family?: string
  primary_member_name?: string | null
}

interface DrillItem {
  date: string
  account_identifier: string
  model: string
  tokens_total: number
  event_count: number
  cost_usd: number
}

const POOL_LABELS: Record<string, string> = {
  auto_composer: 'Auto+Composer',
  api: 'API',
  third_party: '三方',
}

const topN = 10
const loading = ref(false)
const overview = ref<Overview | null>(null)
const accounts = ref<AccountRow[]>([])
const members = ref<MemberRow[]>([])
const rangePreset = ref<RangePreset>('this_month')
const dateRange = ref<[string, string] | null>(null)
const filterAccountIds = ref<string[]>([])
const filterMemberIds = ref<string[]>([])
const dimension = ref<Dimension>('model')

const drillOpen = ref(false)
const drillLoading = ref(false)
const drillTitle = ref('')
const drillItems = ref<DrillItem[]>([])

const dimensionLabel = computed(() => {
  const map: Record<Dimension, string> = {
    account: '账号',
    model: '模型',
    family: '模型族',
    pool: '用量池',
  }
  return map[dimension.value]
})

const dimensionColumnLabel = computed(() => dimensionLabel.value)

const kpiItems = computed(() => {
  const k = overview.value?.kpi
  if (!k) {
    return [
      { label: '总 Token', value: '—' },
      { label: '输入', value: '—' },
      { label: '输出', value: '—' },
      { label: 'Cache Read', value: '—' },
      { label: '事件数', value: '—' },
      { label: '估算花费', value: '—' },
    ]
  }
  return [
    { label: '总 Token', value: formatTokens(k.tokens_total) },
    { label: '输入', value: formatTokens(k.tokens_input) },
    { label: '输出', value: formatTokens(k.tokens_output) },
    { label: 'Cache Read', value: formatTokens(k.tokens_cache_read) },
    { label: '事件数', value: String(k.event_count) },
    { label: '估算花费', value: formatSpend(k.cost_usd) },
  ]
})

const dimensionRows = computed<TableRow[]>(() => {
  const o = overview.value
  if (!o) return []
  if (dimension.value === 'account') {
    return o.by_account.map((r) => ({
      ...r,
      key: r.account_id,
      label: r.account_identifier || r.account_id,
      account_id: r.account_id,
    }))
  }
  if (dimension.value === 'model') {
    return o.by_model.map((r) => ({
      ...r,
      key: r.model,
      label: r.model,
      model: r.model,
    }))
  }
  if (dimension.value === 'family') {
    return o.by_family.map((r) => ({
      ...r,
      key: r.family,
      label: r.family,
      family: r.family,
    }))
  }
  return o.by_pool.map((r) => ({
    ...r,
    key: r.pool,
    label: r.pool_label || poolLabel(r.pool),
    pool: r.pool,
  }))
})

const tableRows = computed(() => dimensionRows.value)

const chartSeries = computed(() => dimensionRows.value.slice(0, topN))

const trendOption = computed(() => {
  const days = overview.value?.series_by_day || []
  const tokenNames = ['输入', '输出', '输入(cache)'] as const
  return {
    color: ['#2563eb', '#0d9488', '#8b5cf6', '#f59e0b'],
    tooltip: {
      trigger: 'axis',
      formatter: (params: Array<{ seriesName: string; value: number; marker: string; axisValue: string }>) => {
        if (!params?.length) return ''
        const lines = params.map((p) => {
          const value =
            p.seriesName === '花费 $' ? formatSpend(p.value) : formatTokens(p.value)
          return `${p.marker}${p.seriesName}：${value}`
        })
        return `${params[0].axisValue}<br/>${lines.join('<br/>')}`
      },
    },
    legend: { data: [...tokenNames, '花费 $'] },
    grid: { left: 48, right: 48, top: 40, bottom: 28 },
    xAxis: {
      type: 'category',
      data: days.map((d) => d.date.slice(5)),
      boundaryGap: false,
    },
    yAxis: [
      { type: 'value', name: 'Token', axisLabel: { formatter: (v: number) => formatTokens(v) } },
      { type: 'value', name: '$', axisLabel: { formatter: (v: number) => `$${v}` } },
    ],
    series: [
      {
        name: '输入',
        type: 'line',
        stack: 'tokens',
        smooth: true,
        areaStyle: { opacity: 0.18 },
        emphasis: { focus: 'series' },
        data: days.map((d) => d.tokens_input ?? 0),
      },
      {
        name: '输出',
        type: 'line',
        stack: 'tokens',
        smooth: true,
        areaStyle: { opacity: 0.18 },
        emphasis: { focus: 'series' },
        data: days.map((d) => d.tokens_output ?? 0),
      },
      {
        name: '输入(cache)',
        type: 'line',
        stack: 'tokens',
        smooth: true,
        areaStyle: { opacity: 0.18 },
        emphasis: { focus: 'series' },
        data: days.map((d) => d.tokens_cache_read ?? 0),
      },
      {
        name: '花费 $',
        type: 'line',
        smooth: true,
        yAxisIndex: 1,
        data: days.map((d) => d.cost_usd),
      },
    ],
  }
})

const structOption = computed(() => {
  const rows = chartSeries.value.filter((r) => r.tokens_total > 0)
  return {
    color: ['#2563eb', '#0d9488', '#f59e0b', '#ef4444', '#8b5cf6', '#64748b', '#14b8a6', '#e11d48'],
    tooltip: {
      trigger: 'item',
      formatter: (p: { name: string; value: number; percent: number }) =>
        `${p.name}<br/>${formatTokens(p.value)}（${p.percent}%）`,
    },
    series: [
      {
        type: 'pie',
        radius: ['38%', '68%'],
        data: rows.map((r) => ({ name: r.label, value: r.tokens_total })),
        label: { formatter: '{b}\n{d}%' },
      },
    ],
  }
})

const rankOption = computed(() => {
  const rows = [...chartSeries.value].reverse()
  return {
    color: ['#2563eb'],
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params: Array<{ name: string; value: number }>) => {
        const p = params[0]
        return `${p?.name}<br/>${formatTokens(p?.value || 0)}`
      },
    },
    grid: { left: 120, right: 32, top: 16, bottom: 24 },
    xAxis: {
      type: 'value',
      axisLabel: { formatter: (v: number) => formatTokens(v) },
    },
    yAxis: {
      type: 'category',
      data: rows.map((r) => r.label),
      axisLabel: {
        width: 110,
        overflow: 'truncate',
      },
    },
    series: [
      {
        type: 'bar',
        data: rows.map((r) => r.tokens_total),
        barMaxWidth: 22,
      },
    ],
  }
})

function poolLabel(pool?: string) {
  if (!pool) return '—'
  return POOL_LABELS[pool] || pool
}

function accountLabel(a: AccountRow) {
  const member = members.value.find((m) => m.id === a.primary_member_id)
  return member ? `${a.account_identifier}（${member.display_name}）` : a.account_identifier
}

function pad(n: number) {
  return String(n).padStart(2, '0')
}

function toYmd(d: Date) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

function startOfMonth(d: Date) {
  return new Date(d.getFullYear(), d.getMonth(), 1)
}

function endOfMonth(d: Date) {
  return new Date(d.getFullYear(), d.getMonth() + 1, 0)
}

function applyPreset(preset: RangePreset) {
  const today = new Date()
  if (preset === 'this_month') {
    dateRange.value = [toYmd(startOfMonth(today)), toYmd(today)]
    return
  }
  if (preset === 'last_month') {
    const ref = new Date(today.getFullYear(), today.getMonth() - 1, 1)
    dateRange.value = [toYmd(startOfMonth(ref)), toYmd(endOfMonth(ref))]
    return
  }
  if (preset === 'last_7') {
    const start = new Date(today)
    start.setDate(start.getDate() - 6)
    dateRange.value = [toYmd(start), toYmd(today)]
    return
  }
  if (preset === 'last_30') {
    const start = new Date(today)
    start.setDate(start.getDate() - 29)
    dateRange.value = [toYmd(start), toYmd(today)]
    return
  }
}

function onPresetChange() {
  if (rangePreset.value !== 'custom') {
    applyPreset(rangePreset.value)
    loadOverview()
  }
}

function onDimensionChange() {
  // charts/table are computed from overview; no refetch
}

function canDrill(row: TableRow) {
  return dimension.value === 'account' || dimension.value === 'model'
}

function onRowClick(row: TableRow) {
  if (canDrill(row)) openDrill(row)
}

async function openDrill(row: TableRow) {
  if (!dateRange.value) return
  const [start, end] = dateRange.value
  drillTitle.value = `日 × 模型 · ${row.label}`
  drillOpen.value = true
  drillLoading.value = true
  drillItems.value = []
  try {
    const params: Record<string, string> = { start, end }
    if (dimension.value === 'account' && row.account_id) params.account_id = row.account_id
    if (dimension.value === 'model' && row.model) params.model = row.model
    const res = await client.get('/api/v2/usage-analytics/daily-breakdown', { params })
    drillItems.value = res.data.items || []
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } }
    ElMessage.error(err.response?.data?.detail || '加载下钻明细失败')
  } finally {
    drillLoading.value = false
  }
}

async function loadFilters() {
  const [accRes, memberRes] = await Promise.all([
    client.get('/api/v2/accounts'),
    client.get('/api/v2/members'),
  ])
  accounts.value = accRes.data
  members.value = memberRes.data
}

async function loadOverview() {
  if (!dateRange.value?.[0] || !dateRange.value?.[1]) {
    applyPreset(rangePreset.value === 'custom' ? 'this_month' : rangePreset.value)
  }
  if (!dateRange.value) return
  const [start, end] = dateRange.value
  loading.value = true
  try {
    const params: Record<string, string | number> = { start, end, top_n: topN }
    if (filterAccountIds.value.length) {
      params.account_ids = filterAccountIds.value.join(',')
    }
    if (filterMemberIds.value.length) {
      params.primary_member_ids = filterMemberIds.value.join(',')
    }
    const res = await client.get('/api/v2/usage-analytics/overview', { params })
    overview.value = res.data
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } }
    ElMessage.error(err.response?.data?.detail || '加载用量分析失败')
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  applyPreset('this_month')
  await loadFilters()
  await loadOverview()
})
</script>

<style scoped>
.usage-analytics {
  max-width: 1400px;
}
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 16px;
}
.page-header h2 {
  margin: 0 0 4px;
  font-size: 20px;
}
.desc {
  margin: 0;
  color: #64748b;
  font-size: 13px;
}
.tz {
  margin-left: 8px;
  color: #94a3b8;
}
.filter-card {
  margin-bottom: 12px;
}
.filter-card :deep(.el-card__body) {
  padding: 12px 16px;
}
.filters {
  display: flex;
  flex-wrap: nowrap;
  gap: 8px;
  align-items: center;
  overflow-x: auto;
}
.filter-preset {
  flex: 0 0 auto;
}
.filter-dates {
  width: 240px;
  flex: 0 0 auto;
}
.filter-select {
  width: 160px;
  flex: 0 0 auto;
}
.dim-select {
  width: 110px;
  flex: 0 0 auto;
}
.kpi-row {
  margin-bottom: 12px;
}
.kpi-card {
  margin-bottom: 12px;
}
.kpi-label {
  font-size: 12px;
  color: #64748b;
  margin-bottom: 6px;
}
.kpi-value {
  font-size: 20px;
  font-weight: 600;
  color: #0f172a;
}
.chart-row {
  margin-bottom: 12px;
}
.chart-card,
.table-card {
  margin-bottom: 12px;
}
.chart-title {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 8px;
  color: #0f172a;
}
.chart {
  height: 300px;
  width: 100%;
}
.chart-bar {
  height: 320px;
}
.table-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 8px;
}
.muted {
  color: #94a3b8;
  font-size: 12px;
}
</style>
