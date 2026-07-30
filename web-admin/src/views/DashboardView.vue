<template>
  <div v-loading="loading" class="dashboard">
    <el-alert v-if="loadError" type="error" :closable="false" class="load-error">
      <template #title>
        概览数据加载失败
        <el-button size="small" class="retry-btn" @click="reload">重试</el-button>
      </template>
    </el-alert>

    <template v-if="data">
      <div class="page-toolbar">
        <el-button
          size="small"
          :icon="Refresh"
          circle
          :loading="refreshing"
          title="刷新"
          @click="reload"
        />
      </div>

      <!-- ① 需要关注 -->
      <el-card v-if="attentionItems.length" shadow="never" class="block">
        <template #header>
          <div class="card-header">
            <span>需要关注</span>
            <el-badge :value="attentionItems.length" type="warning" />
          </div>
        </template>
        <div class="attention-list">
          <div v-for="item in attentionItems" :key="item.key" class="attention-item">
            <el-icon :color="item.level === 'danger' ? '#f56c6c' : '#e6a23c'">
              <WarningFilled />
            </el-icon>
            <span class="attention-text">{{ item.text }}</span>
            <router-link :to="item.to" class="attention-link">去处理 →</router-link>
          </div>
        </div>
      </el-card>

      <!-- ② KPI 卡片 -->
      <el-row v-if="statCards.length" :gutter="16">
        <el-col v-for="card in statCards" :key="card.label" :xs="12" :sm="8" :md="6">
          <StatCard :label="card.label" :value="card.value" :sub="card.sub" />
        </el-col>
      </el-row>

      <!-- ③ 用量趋势 -->
      <el-card v-if="usage" shadow="never" class="block" header="近 14 天用量趋势">
        <v-chart v-if="hasTrend" class="trend-chart" :option="trendOption" autoresize />
        <el-empty v-else description="近 14 天暂无用量数据" :image-size="60" />
      </el-card>

      <!-- ④ 额度风险 / 最近动态 -->
      <el-row v-if="quotaRiskTop.length || activityItems.length" :gutter="16">
        <el-col v-if="quotaRiskTop.length" :xs="24" :md="12">
          <el-card shadow="never" class="block">
            <template #header>
              <div class="card-header">
                <span>额度风险 Top 5</span>
                <router-link to="/quota-board" class="more-link">看板 →</router-link>
              </div>
            </template>
            <div v-for="row in quotaRiskTop" :key="row.account_id" class="risk-item">
              <div class="risk-head">
                <span class="risk-name">{{ row.account_identifier }}</span>
                <el-tag :type="row.status === 'exhausted' ? 'danger' : 'warning'" size="small">
                  {{ row.status === 'exhausted' ? '已耗尽' : '预警' }}
                </el-tag>
              </div>
              <el-progress
                :percentage="riskPct(row)"
                :status="row.status === 'exhausted' ? 'exception' : 'warning'"
              />
              <div class="risk-meta">
                <template v-if="row.primary_member_name">负责人 {{ row.primary_member_name }} · </template>
                <span v-if="row.projected_exhaustion_date">预计 {{ row.projected_exhaustion_date }} 耗尽</span>
                <span v-else-if="row.days_until_reset != null">{{ row.days_until_reset }} 天后重置</span>
              </div>
            </div>
          </el-card>
        </el-col>

        <el-col v-if="activityItems.length" :xs="24" :md="12">
          <el-card shadow="never" class="block">
            <template #header>
              <div class="card-header">
                <span>最近动态</span>
                <router-link to="/audit" class="more-link">审计 →</router-link>
              </div>
            </template>
            <div v-for="row in activityItems" :key="row.id" class="activity-item">
              <div class="activity-title">
                <span class="activity-operator">{{ row.operator_name }}</span>
                <span>{{ row.action_label }}</span>
                <span v-if="row.detail" class="activity-detail">{{ row.detail }}</span>
              </div>
              <div class="activity-time">{{ formatChinaTime(row.created_at) }}</div>
            </div>
          </el-card>
        </el-col>
      </el-row>

      <el-empty v-if="!hasAnyContent" description="暂无概览数据：你的账号暂无可查看的数据区块" />
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Refresh, WarningFilled } from '@element-plus/icons-vue'
import VChart from 'vue-echarts'
import '@/utils/echarts'
import client from '@/api/client'
import StatCard from '@/components/StatCard.vue'
import { formatChinaTime } from '@/utils/time'
import { formatCompactTokens, formatSpend, formatTokensM } from '@/utils/usage'

interface QuotaRiskItem {
  account_id: string
  account_identifier: string
  primary_member_name?: string | null
  status: string
  quota_progress?: number | null
  projected_exhaustion_date?: string | null
  days_until_reset?: number | null
}

interface QuotaSection {
  exhausted_count: number
  warning_count: number
  healthy_count: number
  unknown_count: number
  risk_top: QuotaRiskItem[]
}

interface TrendDay {
  date: string
  tokens_total: number
  cost_usd: number
}

interface UsageSection {
  period: string
  start: string
  end: string
  tokens_total: number
  cost_usd: number
  event_count: number
  series_by_day: TrendDay[]
}

interface LoansSection {
  active_count: number
}

interface SyncSection {
  total_accounts: number
  submitted_count: number
  synced: number
  sync_failed: number
  sync_stale: number
  no_credential: number
  missing_primary: number
  unsubmitted: number
}

interface ProxySection {
  active_key_count: number
  total_tokens: number
  total_cost_usd: number
}

interface IntegrationsSection {
  bot_platform: string
  im_group_configured: boolean
  issues: { key: string; label: string }[]
}

interface ActivityItem {
  id: number
  operator_name: string
  action_label: string
  detail?: string | null
  created_at: string
}

interface OverviewSections {
  quota?: QuotaSection | null
  usage?: UsageSection | null
  loans?: LoansSection | null
  sync?: SyncSection | null
  proxy?: ProxySection | null
  integrations?: IntegrationsSection | null
  recent_activity?: { items: ActivityItem[] } | null
}

interface OverviewData {
  period: string
  pending_actions?: { total_count: number } | null
  sections?: OverviewSections
}

interface AttentionItem {
  key: string
  text: string
  to: string
  level: 'danger' | 'warning'
}

const loading = ref(false)
const refreshing = ref(false)
const loadError = ref(false)
const data = ref<OverviewData | null>(null)

const sections = computed<OverviewSections>(() => data.value?.sections ?? {})
const quota = computed(() => sections.value.quota ?? null)
const usage = computed(() => sections.value.usage ?? null)
const loans = computed(() => sections.value.loans ?? null)
const sync = computed(() => sections.value.sync ?? null)
const proxy = computed(() => sections.value.proxy ?? null)
const integrations = computed(() => sections.value.integrations ?? null)
const activity = computed(() => sections.value.recent_activity ?? null)

const attentionItems = computed<AttentionItem[]>(() => {
  const items: AttentionItem[] = []
  const q = quota.value
  if (q) {
    if (q.exhausted_count > 0) {
      items.push({ key: 'quota-exhausted', text: `${q.exhausted_count} 个账号额度已耗尽`, to: '/quota-board', level: 'danger' })
    }
    if (q.warning_count > 0) {
      items.push({ key: 'quota-warning', text: `${q.warning_count} 个账号额度预警`, to: '/quota-board', level: 'warning' })
    }
  }
  const s = sync.value
  if (s) {
    if (s.sync_failed > 0) {
      items.push({ key: 'sync-failed', text: `${s.sync_failed} 个账号同步失败`, to: '/accounts', level: 'danger' })
    }
    if (s.no_credential > 0) {
      items.push({ key: 'no-credential', text: `${s.no_credential} 个账号未配置凭证`, to: '/accounts', level: 'warning' })
    }
    if (s.missing_primary > 0) {
      items.push({ key: 'missing-primary', text: `${s.missing_primary} 个账号待绑定负责人`, to: '/accounts', level: 'warning' })
    }
    if (s.unsubmitted > 0) {
      items.push({ key: 'unsubmitted', text: `${s.unsubmitted} 个账号本账期待同步`, to: '/accounts', level: 'warning' })
    }
  }
  const pending = data.value?.pending_actions
  if (pending && pending.total_count > 0) {
    items.push({ key: 'pending-users', text: `${pending.total_count} 个后台用户待审批`, to: '/users', level: 'warning' })
  }
  for (const issue of integrations.value?.issues ?? []) {
    items.push({ key: `integration-${issue.key}`, text: issue.label, to: '/settings', level: 'warning' })
  }
  return items
})

const statCards = computed(() => {
  const cards: { label: string; value: string; sub?: string }[] = []
  const s = sync.value
  if (s) {
    const pct = s.total_accounts ? Math.round((s.submitted_count / s.total_accounts) * 100) : 0
    cards.push({
      label: '活跃账号',
      value: String(s.total_accounts),
      sub: `已同步 ${s.submitted_count}/${s.total_accounts} · ${pct}%`,
    })
  }
  const u = usage.value
  if (u) {
    cards.push({ label: '本账期花费', value: formatSpend(u.cost_usd), sub: `账期 ${u.period}` })
    cards.push({
      label: '本账期 Tokens',
      value: formatTokensM(u.tokens_total),
      sub: `${u.event_count} 次事件`,
    })
  }
  if (loans.value) {
    cards.push({ label: '当前借出', value: String(loans.value.active_count), sub: '进行中的 Key 借用' })
  }
  const p = proxy.value
  if (p) {
    cards.push({
      label: '活跃接入密钥',
      value: String(p.active_key_count),
      sub: `累计 ${formatSpend(p.total_cost_usd)}`,
    })
  }
  const q = quota.value
  if (q) {
    cards.push({
      label: '额度告急',
      value: String(q.exhausted_count + q.warning_count),
      sub: `耗尽 ${q.exhausted_count} · 预警 ${q.warning_count}`,
    })
  }
  return cards
})

const trendDays = computed<TrendDay[]>(() => usage.value?.series_by_day ?? [])
const hasTrend = computed(() => trendDays.value.some((d) => d.tokens_total > 0 || d.cost_usd > 0))

const trendOption = computed(() => ({
  tooltip: { trigger: 'axis' },
  legend: { data: ['Tokens', '花费'] },
  grid: { left: 12, right: 12, top: 32, bottom: 8, containLabel: true },
  xAxis: { type: 'category', data: trendDays.value.map((d) => d.date.slice(5)) },
  yAxis: [
    {
      type: 'value',
      name: 'Tokens',
      axisLabel: { formatter: (v: number) => formatCompactTokens(v) || '0' },
    },
    {
      type: 'value',
      name: '花费 $',
      axisLabel: { formatter: (v: number) => `$${v}` },
    },
  ],
  series: [
    {
      name: 'Tokens',
      type: 'bar',
      data: trendDays.value.map((d) => d.tokens_total),
      itemStyle: { color: '#3b82f6' },
    },
    {
      name: '花费',
      type: 'line',
      yAxisIndex: 1,
      smooth: true,
      data: trendDays.value.map((d) => d.cost_usd),
      itemStyle: { color: '#10b981' },
    },
  ],
}))

const quotaRiskTop = computed<QuotaRiskItem[]>(() => quota.value?.risk_top ?? [])
const activityItems = computed<ActivityItem[]>(() => activity.value?.items ?? [])

const hasAnyContent = computed(
  () =>
    attentionItems.value.length > 0 ||
    statCards.value.length > 0 ||
    Boolean(usage.value) ||
    Boolean(quota.value) ||
    Boolean(activity.value),
)

function riskPct(row: QuotaRiskItem) {
  return Math.min(100, Math.round((row.quota_progress ?? 0) * 100))
}

async function reload() {
  refreshing.value = true
  loadError.value = false
  try {
    const res = await client.get('/api/dashboard/overview')
    data.value = res.data
  } catch {
    loadError.value = true
  } finally {
    refreshing.value = false
  }
}

onMounted(async () => {
  loading.value = true
  await reload()
  loading.value = false
})
</script>

<style scoped>
.block {
  margin-bottom: 16px;
}
.page-toolbar {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 12px;
}
.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
}
.more-link {
  margin-left: auto;
  font-size: 13px;
  font-weight: 400;
  color: var(--el-color-primary);
  text-decoration: none;
}
.attention-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.attention-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
.attention-text {
  color: #334155;
}
.attention-link {
  margin-left: auto;
  font-size: 13px;
  color: var(--el-color-primary);
  text-decoration: none;
  white-space: nowrap;
}
.trend-chart {
  height: 280px;
}
.risk-item + .risk-item {
  margin-top: 14px;
}
.risk-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}
.risk-name {
  font-size: 13px;
  font-weight: 500;
}
.risk-meta {
  font-size: 12px;
  color: #64748b;
  margin-top: 4px;
}
.activity-item {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 0;
  border-bottom: 1px solid #f1f5f9;
}
.activity-item:last-child {
  border-bottom: none;
}
.activity-title {
  font-size: 13px;
  color: #334155;
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.activity-operator {
  font-weight: 600;
}
.activity-detail {
  color: #64748b;
}
.activity-time {
  font-size: 12px;
  color: #94a3b8;
  white-space: nowrap;
}
.load-error {
  margin-bottom: 16px;
}
.retry-btn {
  margin-left: 8px;
}
</style>
