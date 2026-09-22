<template>
  <div class="ranking-layout">
    <div class="ranking-main">
      <div class="ranking-toolbar">
        <p class="hint">
          排序驱动「自动分配」与池轮换借用。使用过程中按额度桶耗尽再换号，名次会变，不是锁定。
          快到期优先消化（urgency），剩余额度多者优先（surplus + headroom）。
          人工分加在算法综合分上微调；主负责人保留会硬排除侵占账号。Jev 开启时在右侧规则说明中查看护栏。
        </p>
        <el-button size="small" @click="loadRanking">刷新</el-button>
      </div>
      <el-alert
        v-if="ranking.decision"
        :type="ranking.decision.picked_by === 'jev' ? 'success' : 'info'"
        :closable="false"
        class="ranking-decision"
      >
        <template #title>
          <span v-if="ranking.decision.picked_by === 'jev'">
            Jev 主判 · {{ ranking.decision.model || 'typesafe/jev' }} · 置信度
            {{ ranking.decision.confidence ?? '—' }}
            <el-tag v-if="ranking.decision.cached" size="small" type="info">缓存</el-tag>
          </span>
          <span v-else>
            算法分保底 · {{ decisionFallbackLabel(ranking.decision.fallback_reason) }}
          </span>
        </template>
      </el-alert>
      <h4 class="section-title">入选排序</h4>
      <el-table v-loading="rankingLoading" :data="ranking.ranked" stripe class="rank-table">
        <el-table-column label="#" width="52" fixed>
          <template #default="{ $index }">{{ $index + 1 }}</template>
        </el-table-column>
        <el-table-column label="账号" min-width="140" fixed>
          <template #default="{ row }">
            {{ row.account_identifier }}
            <el-tag v-if="row.picked" size="small" type="success">选用</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="综合分" width="108">
          <template #default="{ row }">
            <el-tooltip
              v-if="row.score_adjust != null"
              :content="`算法分 ${row.computed_score}，人工 ${row.score_adjust >= 0 ? '+' : ''}${row.score_adjust}`"
            >
              <span>{{ row.score }} <el-tag size="small" type="warning">微调</el-tag></span>
            </el-tooltip>
            <span v-else>{{ row.score }}</span>
          </template>
        </el-table-column>
        <el-table-column label="人工分" width="150">
          <template #default="{ row }">
            <el-input-number
              :model-value="row.score_adjust ?? undefined"
              :disabled="!canWrite"
              :min="-10"
              :max="10"
              :step="0.05"
              :precision="4"
              :value-on-clear="null"
              controls-position="right"
              placeholder="微调"
              size="small"
              class="score-input"
              @change="(val: number | undefined | null) => setScoreAdjust(row, val ?? null)"
            />
          </template>
        </el-table-column>
        <el-table-column prop="surplus_cents" label="预计余量" width="92" />
        <el-table-column label="主负责人保留" width="150">
          <template #default="{ row }">
            <el-input-number
              :model-value="row.reserve_pct ?? undefined"
              :disabled="!canWrite"
              :min="0"
              :max="100"
              :step="5"
              :precision="0"
              :value-on-clear="null"
              controls-position="right"
              placeholder="不保留"
              size="small"
              class="score-input"
              @change="(val: number | undefined | null) => setReservePct(row, val ?? null)"
            />
          </template>
        </el-table-column>
        <el-table-column prop="urgency_cents_per_day" label="消化压力/日" width="100" />
        <el-table-column label="额度进度" min-width="180">
          <template #default="{ row }">
            <QuotaProgressBars
              :total_pct="row.total_pct"
              :auto_pct="row.auto_pct"
              :api_pct="row.api_pct"
              :status="row.status"
            />
          </template>
        </el-table-column>
        <el-table-column prop="hours_to_deadline" label="距作废(h)" width="96" />
        <el-table-column label="作废时刻" min-width="160">
          <template #default="{ row }">{{ formatDeadline(row) }}</template>
        </el-table-column>
        <el-table-column prop="active_loans" label="在借" width="64" />
        <el-table-column prop="snapshot_freshness" label="快照" width="72" />
      </el-table>
      <h4 class="section-title">已排除</h4>
      <el-table v-loading="rankingLoading" :data="ranking.excluded" stripe class="rank-table excluded-table">
        <el-table-column prop="account_identifier" label="账号" min-width="140" />
        <el-table-column label="额度进度" min-width="180">
          <template #default="{ row }">
            <QuotaProgressBars
              :total_pct="row.total_pct"
              :auto_pct="row.auto_pct"
              :api_pct="row.api_pct"
              :status="row.status"
            />
          </template>
        </el-table-column>
        <el-table-column label="原因" min-width="140">
          <template #default="{ row }">{{ exclusionReasonLabel(row.reason) }}</template>
        </el-table-column>
        <el-table-column label="人工分" width="150">
          <template #default="{ row }">
            <el-input-number
              :model-value="row.score_adjust ?? undefined"
              :disabled="!canWrite"
              :min="-10"
              :max="10"
              :step="0.05"
              :precision="4"
              :value-on-clear="null"
              controls-position="right"
              placeholder="微调"
              size="small"
              class="score-input"
              @change="(val: number | undefined | null) => setScoreAdjust(row, val ?? null)"
            />
          </template>
        </el-table-column>
        <el-table-column prop="active_loans" label="在借" width="64" />
        <el-table-column prop="status" label="额度状态" width="100" />
      </el-table>
    </div>
    <aside class="rules-aside">
      <div class="rules-card">
        <h3 class="rules-heading">选号规则</h3>
        <LoanSelectionRules
          v-if="selectionLoaded"
          layout="sidebar"
          :selection="loanSelection"
          @saved="onRulesSaved"
        />
        <div v-else v-loading="true" class="rules-loading" />
      </div>
    </aside>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import client from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useSettingsStore } from '@/stores/settings'
import QuotaProgressBars from '@/components/QuotaProgressBars.vue'
import LoanSelectionRules from '@/components/LoanSelectionRules.vue'
import { formatChinaTime } from '@/utils/time'

interface RankingRow {
  account_id: string
  account_identifier: string
  score?: number
  computed_score?: number
  score_adjust?: number | null
  surplus_cents?: number
  urgency_cents_per_day?: number
  total_pct?: number | null
  auto_pct?: number | null
  api_pct?: number | null
  deadline?: string | null
  deadline_at?: string | null
  hours_to_deadline?: number | null
  active_loans?: number
  snapshot_freshness?: number
  reason?: string
  status?: string | null
  reserve_pct?: number | null
  picked?: boolean
}

interface RankingDecision {
  picked_by: string
  fallback_reason?: string | null
  model?: string | null
  confidence?: number | null
  cached?: boolean
}

interface RankingBoard {
  ranked: RankingRow[]
  excluded: RankingRow[]
  decision?: RankingDecision | null
}

const auth = useAuthStore()
const settingsStore = useSettingsStore()
const canWrite = computed(() => auth.hasPermission('proxy:write'))
const rankingLoading = ref(false)
const ranking = ref<RankingBoard>({ ranked: [], excluded: [], decision: null })
const loanSelection = ref<Record<string, unknown>>({})
const selectionLoaded = ref(false)

const DECISION_FALLBACK_LABELS: Record<string, string> = {
  auto_mode_off: 'Auto Lender 未开启',
  jev_unavailable: 'Jev 未配置或未启用',
  insufficient_candidates: '可用候选不足',
  jev_error: 'Jev 调用失败',
  circuit_open: 'Jev 熔断中',
  no_pick_answer: 'Jev 未返回选择',
  no_choice: 'Jev 选择为空',
  unknown_account: 'Jev 返回了候选外的账号',
  low_confidence: 'Jev 置信度不足',
  narrow_margin: 'Jev 首选与次优差距过小',
  owner_unsafe: 'Jev 判定会侵占主负责人预留',
  no_credentials: '池内无可用账号',
}

function decisionFallbackLabel(reason?: string | null): string {
  if (!reason) return '—'
  return DECISION_FALLBACK_LABELS[reason] || reason
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

function formatDeadline(row: RankingRow): string {
  if (row.deadline_at) return formatChinaTime(row.deadline_at)
  return row.deadline || '—'
}

async function loadSelection() {
  const data = await settingsStore.load()
  loanSelection.value = { ...(data?.tool_center?.loan_selection || {}) }
  selectionLoaded.value = true
}

async function loadRanking() {
  rankingLoading.value = true
  try {
    const res = await client.get('/api/v2/proxy-pool/ranking')
    ranking.value = {
      ranked: res.data.ranked || [],
      excluded: res.data.excluded || [],
      decision: res.data.decision || null,
    }
  } catch {
    ElMessage.error('打分表加载失败')
  } finally {
    rankingLoading.value = false
  }
}

async function onRulesSaved(data: Record<string, unknown>) {
  loanSelection.value = { ...(data?.tool_center?.loan_selection || loanSelection.value) }
  await loadRanking()
}

async function setScoreAdjust(row: RankingRow, val: number | null) {
  if (!row.account_id) return
  const prev = row.score_adjust ?? null
  if (val === prev) return
  try {
    await client.post(`/api/v2/proxy-pool/accounts/${row.account_id}/score`, {
      score_adjust: val,
    })
    await loadRanking()
    ElMessage.success(val == null ? '已恢复自动打分' : '已保存人工微调')
  } catch (err: any) {
    const detail = err?.response?.data?.detail
    ElMessage.error(typeof detail === 'string' ? detail : '设置失败')
  }
}

async function setReservePct(row: RankingRow, val: number | null) {
  if (!row.account_id) return
  const prev = row.reserve_pct ?? null
  if (val === prev) return
  try {
    await client.post(`/api/v2/proxy-pool/accounts/${row.account_id}/score`, {
      clear_reserve: val == null,
      ...(val == null ? {} : { reserve_pct: val }),
    })
    await loadRanking()
    ElMessage.success(val == null ? '已取消主负责人保留' : '已保存主负责人保留')
  } catch (err: any) {
    const detail = err?.response?.data?.detail
    ElMessage.error(typeof detail === 'string' ? detail : '设置失败')
  }
}

async function refresh() {
  await Promise.all([loadSelection(), loadRanking()])
}

onMounted(refresh)

defineExpose({ refresh })
</script>

<style scoped>
.ranking-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
  gap: 20px;
  align-items: start;
}
.ranking-main {
  min-width: 0;
}
.ranking-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 8px;
}
.hint {
  margin: 0;
  flex: 1;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  line-height: 1.55;
}
.ranking-decision {
  margin: 8px 0 12px;
}
.section-title {
  margin: 16px 0 8px;
  font-size: 14px;
  font-weight: 600;
}
.rank-table {
  width: 100%;
  margin-bottom: 8px;
}
.excluded-table {
  margin-bottom: 0;
}
.score-input {
  width: 130px;
}
.rules-aside {
  position: sticky;
  top: 12px;
}
.rules-card {
  background: #fff;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 16px 16px 8px;
  max-height: calc(100vh - 160px);
  overflow-y: auto;
}
.rules-heading {
  margin: 0 0 12px;
  font-size: 15px;
  font-weight: 600;
}
.rules-loading {
  min-height: 200px;
}
@media (max-width: 1280px) {
  .ranking-layout {
    grid-template-columns: 1fr;
  }
  .rules-aside {
    position: static;
  }
  .rules-card {
    max-height: none;
  }
}
</style>
