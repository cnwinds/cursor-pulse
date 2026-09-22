<template>
  <div class="ranking-panel">
    <div class="panel-card">
      <div class="ranking-toolbar">
        <el-button type="primary" plain :loading="rankingLoading" @click="loadRanking">
          刷新
        </el-button>
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

      <section class="table-block">
        <h4 class="section-title">
          <span class="title-mark" />
          入选排序
          <span class="title-count">{{ ranking.ranked.length }} 个账号</span>
        </h4>
        <el-table
          v-loading="rankingLoading"
          :data="ranking.ranked"
          stripe
          class="rank-table"
          :row-class-name="rankedRowClass"
        >
          <el-table-column width="44" fixed align="center">
            <template #header>
              <ColHeader label="#" tip="按当前综合分排序的名次，会随额度消耗与微调变化。" />
            </template>
            <template #default="{ $index }">
              <span class="rank-index">{{ $index + 1 }}</span>
            </template>
          </el-table-column>
          <el-table-column min-width="152" fixed>
            <template #header>
              <ColHeader
                label="账号"
                tip="已入池账号。绿色「选用」为当前排序首选（Jev 通过时为 Jev 选择，否则为算法第一名）。"
              />
            </template>
            <template #default="{ row }">
              <div class="account-cell">
                <span class="account-id">{{ row.account_identifier }}</span>
                <el-tag v-if="row.picked" size="small" type="success" effect="dark">选用</el-tag>
              </div>
            </template>
          </el-table-column>
          <el-table-column width="92" align="right">
            <template #header>
              <ColHeader
                label="综合分"
                tip="硬过滤后的算法综合分；有人工微分时为算法分 + 人工分。"
              />
            </template>
            <template #default="{ row }">
              <el-tooltip
                v-if="row.score_adjust != null"
                :content="`算法 ${row.computed_score} · 人工 ${row.score_adjust >= 0 ? '+' : ''}${row.score_adjust}`"
              >
                <span class="score-cell">
                  {{ row.score }}
                  <el-tag size="small" type="warning" effect="plain">调</el-tag>
                </span>
              </el-tooltip>
              <span v-else class="score-cell">{{ row.score }}</span>
            </template>
          </el-table-column>
          <el-table-column width="112" align="center">
            <template #header>
              <ColHeader
                label="人工"
                tip="人工微调分：加在算法综合分上，正数提前、负数延后；清空输入恢复纯算法排序。"
              />
            </template>
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
                placeholder="—"
                size="small"
                class="score-input score-input--manual"
                @change="(val: number | undefined | null) => setScoreAdjust(row, val ?? null)"
              />
            </template>
          </el-table-column>
          <el-table-column width="104" align="center">
            <template #header>
              <ColHeader
                label="保留%"
                tip="主负责人保留（%）：该账号 Quota 池必须为号主留出的余量比例。按当前用量推到重置日，若借出后留给主负责人的比例低于此值，账号会被硬过滤排除。留空用选号规则默认值；0 表示本账号不单独保留。"
              />
            </template>
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
                placeholder="默认"
                size="small"
                class="score-input reserve-input"
                @change="(val: number | undefined | null) => setReservePct(row, val ?? null)"
              />
            </template>
          </el-table-column>
          <el-table-column min-width="176">
            <template #header>
              <ColHeader
                label="额度"
                tip="额度进度：同步快照中 Total / Auto / API 用量占本周期上限的比例。"
              />
            </template>
            <template #default="{ row }">
              <QuotaProgressBars
                :total_pct="row.total_pct"
                :auto_pct="row.auto_pct"
                :api_pct="row.api_pct"
                :status="row.status"
              />
            </template>
          </el-table-column>
          <el-table-column width="64" align="center">
            <template #header>
              <ColHeader
                label="在借"
                tip="固定借用笔数：锁定出借账号、尚未结束的借用（指定账号 / 非池轮换）。受选号规则「同时在借上限」约束，与「在用」不是同一指标。"
              />
            </template>
            <template #default="{ row }">
              <span class="metric-pill">{{ row.active_loans ?? 0 }}</span>
            </template>
          </el-table-column>
          <el-table-column width="76" align="center">
            <template #header>
              <ColHeader label="在用" :tip="proxySeatsTip" />
            </template>
            <template #default="{ row }">
              <span class="seat-pill" :class="seatPillClass(row)">
                {{ formatProxySeats(row) }}
              </span>
            </template>
          </el-table-column>
          <el-table-column width="100" align="right" show-overflow-tooltip>
            <template #header>
              <ColHeader
                label="距作废"
                tip="距本周期额度作废（重置）的剩余时间；越近越优先消化剩余额度。"
              />
            </template>
            <template #default="{ row }">
              {{ formatHoursUntilDeadline(row.hours_to_deadline) }}
            </template>
          </el-table-column>
          <el-table-column prop="snapshot_freshness" width="80" align="center">
            <template #header>
              <ColHeader
                label="新鲜度"
                tip="额度快照新鲜度（0–1）：越接近 1 越刚同步；过久未同步会在算法分中降权，非硬过滤。"
              />
            </template>
          </el-table-column>
        </el-table>
      </section>

      <section v-if="ranking.excluded.length" class="table-block table-block--muted">
        <h4 class="section-title">
          <span class="title-mark title-mark--muted" />
          已排除
          <span class="title-count">{{ ranking.excluded.length }} 个账号</span>
        </h4>
        <el-table v-loading="rankingLoading" :data="ranking.excluded" stripe class="rank-table">
          <el-table-column min-width="140">
            <template #header>
              <ColHeader label="账号" tip="未通过硬过滤、未进入入选排序的入池账号。" />
            </template>
            <template #default="{ row }">{{ row.account_identifier }}</template>
          </el-table-column>
          <el-table-column min-width="176">
            <template #header>
              <ColHeader
                label="额度"
                tip="额度进度：Total / Auto / API 用量占本周期上限的比例。"
              />
            </template>
            <template #default="{ row }">
              <QuotaProgressBars
                :total_pct="row.total_pct"
                :auto_pct="row.auto_pct"
                :api_pct="row.api_pct"
                :status="row.status"
              />
            </template>
          </el-table-column>
          <el-table-column min-width="140">
            <template #header>
              <ColHeader label="原因" tip="未进入入选排序的硬过滤原因。" />
            </template>
            <template #default="{ row }">{{ exclusionReasonLabel(row.reason) }}</template>
          </el-table-column>
          <el-table-column width="56" align="center">
            <template #header>
              <ColHeader
                label="在借"
                tip="固定借用笔数：锁定出借账号、尚未结束的借用（指定账号 / 非池轮换）。"
              />
            </template>
            <template #default="{ row }">{{ row.active_loans ?? 0 }}</template>
          </el-table-column>
          <el-table-column width="72" align="center">
            <template #header>
              <ColHeader label="在用" :tip="proxySeatsTip" />
            </template>
            <template #default="{ row }">{{ row.proxy_active_seats ?? 0 }}</template>
          </el-table-column>
        </el-table>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, defineComponent, h, onMounted, ref } from 'vue'
import { ElMessage, ElTooltip } from 'element-plus'
import client from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import QuotaProgressBars from '@/components/QuotaProgressBars.vue'
import { formatHoursUntilDeadline } from '@/utils/time'

const ColHeader = defineComponent({
  name: 'ColHeader',
  props: {
    label: { type: String, required: true },
    tip: { type: String, required: true },
  },
  setup(props) {
    return () =>
      h('span', { class: 'col-header' }, [
        props.label,
        h(
          ElTooltip,
          { content: props.tip, placement: 'top', effect: 'dark' },
          {
            default: () => h('span', { class: 'col-header-q' }, '?'),
          },
        ),
      ])
  },
})

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
  proxy_active_seats?: number
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

interface SeatSnapshot {
  max_concurrent_users: number
  ttl_seconds: number
}

interface RankingBoard {
  ranked: RankingRow[]
  excluded: RankingRow[]
  decision?: RankingDecision | null
  seat_snapshot?: SeatSnapshot | null
}

const auth = useAuthStore()
const canWrite = computed(() => auth.hasPermission('proxy:write'))
const rankingLoading = ref(false)
const ranking = ref<RankingBoard>({ ranked: [], excluded: [], decision: null, seat_snapshot: null })

const seatSnapshot = computed(() => ranking.value.seat_snapshot)

const proxySeatsTip = computed(() => {
  const max = seatSnapshot.value?.max_concurrent_users ?? 0
  const ttl = seatSnapshot.value?.ttl_seconds ?? 180
  const limit = max <= 0 ? '不限制人数' : `同一账号最多 ${max} 人`
  return (
    `经本代理上报、当前仍占座的并发人数（按成员去重：同人在同号多会话算 1）。` +
    `主负责人直连 Cursor 不计入。${limit}；${ttl} 秒无上报视为离开。上限与超时在「选号规则」页签配置。`
  )
})

const DECISION_FALLBACK_LABELS: Record<string, string> = {
  auto_mode_off: '未启用 Jev 重排（请在选号规则中开启）',
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

function formatProxySeats(row: RankingRow): string {
  const n = row.proxy_active_seats ?? 0
  const max = seatSnapshot.value?.max_concurrent_users ?? 0
  if (max <= 0) return String(n)
  return `${n}/${max}`
}

function seatPillClass(row: RankingRow): string {
  const n = row.proxy_active_seats ?? 0
  const max = seatSnapshot.value?.max_concurrent_users ?? 0
  if (max <= 0) return ''
  if (n >= max) return 'seat-pill--full'
  if (n >= Math.max(1, max - 1)) return 'seat-pill--warn'
  return ''
}

function rankedRowClass({ row }: { row: RankingRow }) {
  return row.picked ? 'row-picked' : ''
}

async function loadRanking() {
  rankingLoading.value = true
  try {
    const res = await client.get('/api/v2/proxy-pool/ranking')
    ranking.value = {
      ranked: res.data.ranked || [],
      excluded: res.data.excluded || [],
      decision: res.data.decision || null,
      seat_snapshot: res.data.seat_snapshot || null,
    }
  } catch {
    ElMessage.error('打分表加载失败')
  } finally {
    rankingLoading.value = false
  }
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

onMounted(loadRanking)
</script>

<style scoped>
.ranking-panel {
  --rank-accent: #0d9488;
  --rank-surface: #ffffff;
  --rank-muted: #64748b;
  --rank-border: rgba(15, 23, 42, 0.08);
  font-family: 'DM Sans', 'Segoe UI', system-ui, sans-serif;
}
.panel-card {
  background: linear-gradient(165deg, #f8fafc 0%, #f1f5f9 42%, #ffffff 100%);
  border: 1px solid var(--rank-border);
  border-radius: 14px;
  padding: 20px 20px 8px;
  box-shadow:
    0 1px 2px rgba(15, 23, 42, 0.04),
    0 12px 40px rgba(15, 23, 42, 0.06);
}
.ranking-toolbar {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 12px;
}
.ranking-decision {
  margin-bottom: 16px;
  border-radius: 10px;
}
.table-block {
  margin-bottom: 20px;
}
.table-block--muted {
  padding-top: 4px;
  border-top: 1px dashed var(--rank-border);
}
.section-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 10px;
  font-size: 15px;
  font-weight: 650;
  color: #0f172a;
  letter-spacing: -0.02em;
}
.title-mark {
  width: 4px;
  height: 16px;
  border-radius: 2px;
  background: var(--rank-accent);
}
.title-mark--muted {
  background: #94a3b8;
}
.title-count {
  margin-left: auto;
  font-size: 12px;
  font-weight: 500;
  color: var(--rank-muted);
}
.rank-table {
  width: 100%;
  border-radius: 10px;
  overflow: hidden;
  --el-table-header-bg-color: #f1f5f9;
  --el-table-tr-bg-color: #fff;
}
.rank-table :deep(.el-table__header th .cell) {
  white-space: nowrap;
  line-height: 1.2;
  padding-top: 8px;
  padding-bottom: 8px;
}
.rank-table :deep(.row-picked) {
  --el-table-tr-bg-color: rgba(13, 148, 136, 0.06);
}
.rank-index {
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  color: #64748b;
}
.account-cell {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}
.account-id {
  font-size: 13px;
  word-break: break-all;
}
.score-cell {
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}
.score-input {
  width: 120px;
}
.score-input--manual {
  width: 88px;
}
.rank-table :deep(.score-input--manual.el-input-number) {
  width: 88px;
}
.reserve-input {
  width: 88px;
}
.rank-table :deep(.reserve-input.el-input-number) {
  width: 88px;
}
.metric-pill,
.seat-pill {
  display: inline-block;
  min-width: 2rem;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  background: #e2e8f0;
  color: #334155;
}
.seat-pill {
  background: rgba(13, 148, 136, 0.12);
  color: #0f766e;
}
.seat-pill--warn {
  background: rgba(245, 158, 11, 0.18);
  color: #b45309;
}
.seat-pill--full {
  background: rgba(239, 68, 68, 0.14);
  color: #b91c1c;
}
:deep(.col-header) {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  cursor: help;
  white-space: nowrap;
  font-size: 13px;
}
:deep(.col-header-q) {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  font-size: 10px;
  font-weight: 700;
  color: #64748b;
  background: #e2e8f0;
  cursor: help;
}
</style>
