<template>
  <div v-loading="loading">
    <el-card v-if="pendingActions?.total_count" shadow="never" class="pending-card">
      <template #header>
        <div class="pending-header">
          <span>待处理</span>
          <el-badge :value="pendingActions.total_count" type="warning" />
        </div>
      </template>

      <div v-if="pendingActions.portal_users.length" class="pending-section">
        <div class="section-title">后台用户审批</div>
        <div class="portal-pending-list">
          <div v-for="user in pendingActions.portal_users" :key="user.id" class="portal-pending-item">
            <div>
              <div class="portal-name">{{ user.display_name }}</div>
              <div class="portal-meta">钉钉 · {{ user.dingtalk_user_id }}</div>
            </div>
            <router-link to="/users" class="view-all">去审批 →</router-link>
          </div>
        </div>
      </div>
    </el-card>

    <el-row :gutter="16" class="stats" :class="{ 'stats-with-pending': pendingActions?.total_count }">
      <el-col :span="6" v-for="card in statCards" :key="card.label">
        <el-card shadow="never">
          <div class="stat-label">{{ card.label }}</div>
          <div class="stat-value">{{ card.value }}</div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" class="mt">
      <el-col :span="12">
        <el-card shadow="never" header="账号同步进度">
          <template v-if="ingestion?.active_count">
            <div class="sync-progress-track">
              <div class="sync-progress-fill" :style="{ width: `${syncPct}%` }" />
              <span class="sync-progress-label">{{ syncPct }}%</span>
            </div>
            <p class="hint">
              账期 {{ data?.period }} · 已同步 {{ ingestion.submitted_count }} / {{ ingestion.active_count }}
            </p>
            <div class="summary-chips">
              <el-tag type="success" size="small">已同步 {{ ingestion.submitted_count }}</el-tag>
              <el-tag type="warning" size="small">待同步 {{ ingestion.unsubmitted_count }}</el-tag>
            </div>
          </template>
          <el-empty v-else description="暂无活跃账号" />
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card shadow="never" header="费用概览（USD）">
          <template v-if="costSummary?.total_cost_usd">
            <el-descriptions :column="1" border size="small">
              <el-descriptions-item label="总费用">
                ${{ Number(costSummary.total_cost_usd).toFixed(2) }}
              </el-descriptions-item>
              <el-descriptions-item label="有费用成员">
                {{ costSummary.members_with_cost ?? '—' }}
              </el-descriptions-item>
              <el-descriptions-item label="人均费用">
                ${{ Number(costSummary.avg_cost_usd || 0).toFixed(2) }}
              </el-descriptions-item>
              <el-descriptions-item label="最高单笔">
                ${{ Number(costSummary.max_cost_usd || 0).toFixed(2) }}
              </el-descriptions-item>
            </el-descriptions>
          </template>
          <el-empty v-else description="本账期暂无费用数据" />
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" class="mt">
      <el-col :span="12">
        <el-card shadow="never" header="告警摘要">
          <template v-if="alertSummary?.total">
            <div class="summary-chips">
              <el-tag type="danger" size="small">严重 {{ alertSummary.critical }}</el-tag>
              <el-tag type="warning" size="small">警告 {{ alertSummary.warning }}</el-tag>
            </div>
            <p class="hint">最近 {{ alertSummary.total }} 条告警，详情请查看审计日志</p>
          </template>
          <el-empty v-else description="无告警" />
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card shadow="never" header="运行概览">
          <el-descriptions :column="1" border size="small">
            <el-descriptions-item label="账期">{{ data?.period ?? '—' }}</el-descriptions-item>
            <el-descriptions-item label="团队">{{ data?.summary?.team_slug ?? '—' }}</el-descriptions-item>
            <el-descriptions-item label="Tokens">
              {{ formatNumber(data?.metrics_highlights?.total_tokens) }}
            </el-descriptions-item>
            <el-descriptions-item label="总费用">{{ costLabel }}</el-descriptions-item>
            <el-descriptions-item label="钉钉群">
              {{ data?.summary?.group_configured ? '已配置' : '未配置' }}
            </el-descriptions-item>
            <el-descriptions-item label="异常告警">
              {{ data?.summary?.alerts_enabled ? '已开启' : '已关闭' }}
            </el-descriptions-item>
            <el-descriptions-item
              v-if="ingestion?.missing_primary_count"
              label="待绑定负责人"
            >
              {{ ingestion.missing_primary_count }} 个账号
            </el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import client from '@/api/client'

interface PendingActions {
  portal_users: { id: string; display_name: string; dingtalk_user_id: string }[]
  total_count: number
}

const loading = ref(false)
const data = ref<any>(null)

const pendingActions = computed<PendingActions | null>(() => data.value?.pending_actions ?? null)

const ingestion = computed(() => data.value?.ingestion ?? data.value?.submission ?? null)
const costSummary = computed(() => data.value?.cost_summary ?? null)
const alertSummary = computed(() => data.value?.alert_summary ?? null)

const syncPct = computed(() => {
  const s = ingestion.value
  if (!s?.active_count) return 0
  return Math.round((s.submitted_count / s.active_count) * 100)
})

const costLabel = computed(() => {
  const c = data.value?.metrics_highlights?.total_cost_usd
  return c != null ? `$${Number(c).toFixed(2)}` : '—'
})

const statCards = computed(() => {
  const s = ingestion.value
  const highlights = data.value?.metrics_highlights
  return [
    { label: '活跃账号', value: formatCount(s?.active_count) },
    { label: '本账期已同步', value: formatCount(s?.submitted_count) },
    { label: '同步率', value: s?.active_count != null ? `${syncPct.value}%` : '—' },
    { label: '总事件', value: formatNumber(highlights?.total_events) },
  ]
})

function formatCount(value: unknown) {
  return value == null ? '—' : String(value)
}

function formatNumber(value: unknown) {
  if (value == null) return '—'
  const n = Number(value)
  return Number.isFinite(n) ? n.toLocaleString() : '—'
}

async function reloadOverview() {
  const res = await client.get('/api/dashboard/overview')
  data.value = res.data
}

onMounted(async () => {
  loading.value = true
  try {
    await reloadOverview()
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.pending-card {
  margin-bottom: 16px;
}
.pending-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
}
.pending-section + .pending-section {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid #ebeef5;
}
.section-title {
  font-size: 13px;
  font-weight: 600;
  color: #475569;
  margin-bottom: 10px;
}
.view-all {
  display: inline-block;
  margin-top: 10px;
  font-size: 13px;
  color: var(--el-color-primary);
  text-decoration: none;
}
.portal-pending-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.portal-pending-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  background: #f8fafc;
  border-radius: 8px;
}
.portal-name {
  font-weight: 500;
}
.portal-meta {
  font-size: 12px;
  color: #64748b;
  margin-top: 2px;
}
.stats-with-pending {
  margin-top: 0;
}
.stat-label {
  color: #64748b;
  font-size: 13px;
}
.stat-value {
  font-size: 28px;
  font-weight: 600;
  margin-top: 8px;
}
.mt {
  margin-top: 16px;
}
.hint {
  margin: 10px 0 6px;
  font-size: 12px;
  color: #64748b;
}
.summary-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}
.sync-progress-track {
  position: relative;
  height: 16px;
  background: #e5e9f2;
  border-radius: 8px;
  overflow: hidden;
}
.sync-progress-fill {
  height: 100%;
  background: var(--el-color-primary);
  border-radius: 8px;
  transition: width 0.3s ease;
}
.sync-progress-label {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
  line-height: 1;
  color: #fff;
  text-shadow: 0 0 2px rgba(15, 23, 42, 0.45);
  pointer-events: none;
}
</style>
