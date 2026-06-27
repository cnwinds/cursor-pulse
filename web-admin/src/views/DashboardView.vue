<template>
  <div v-loading="loading">
    <el-row :gutter="16" class="stats">
      <el-col :span="6" v-for="card in statCards" :key="card.label">
        <el-card shadow="never">
          <div class="stat-label">{{ card.label }}</div>
          <div class="stat-value">{{ card.value }}</div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" class="mt">
      <el-col :span="12">
        <el-card shadow="never" header="提交进度">
          <el-progress
            :percentage="submitPct"
            :stroke-width="16"
            :text-inside="true"
          />
          <div class="chart-list">
            <div v-for="m in data?.submission?.members || []" :key="m.display_name" class="bar-row">
              <span class="name">{{ m.display_name }}</span>
              <el-tag :type="m.submitted ? 'success' : 'danger'" size="small">
                {{ m.submitted ? '已交' : '未交' }}
              </el-tag>
            </div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card shadow="never" header="成员费用 Top（USD）">
          <div v-if="!data?.member_costs?.length" class="empty">暂无聚合快照</div>
          <div v-for="row in data?.member_costs || []" :key="row.display_name" class="cost-row">
            <span class="name">{{ row.display_name }}</span>
            <div class="bar-track">
              <div class="bar-fill" :style="{ width: barWidth(row.cost_usd) }" />
            </div>
            <span class="val">${{ row.cost_usd?.toFixed(2) }}</span>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" class="mt">
      <el-col :span="12">
        <el-card shadow="never" header="最近告警">
          <el-empty v-if="!data?.recent_alerts?.length" description="无告警" />
          <div v-for="a in data?.recent_alerts || []" :key="a.created_at" class="alert-item">
            <el-tag :type="a.severity === 'critical' ? 'danger' : 'warning'" size="small">
              {{ a.severity }}
            </el-tag>
            <span>{{ a.message }}</span>
          </div>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card shadow="never" header="系统状态">
          <el-descriptions :column="1" border size="small">
            <el-descriptions-item label="账期">{{ data?.period }}</el-descriptions-item>
            <el-descriptions-item label="团队">{{ data?.summary?.team_slug }}</el-descriptions-item>
            <el-descriptions-item label="Tokens">
              {{ data?.metrics_highlights?.total_tokens?.toLocaleString() ?? '—' }}
            </el-descriptions-item>
            <el-descriptions-item label="总费用">
              {{ costLabel }}
            </el-descriptions-item>
            <el-descriptions-item label="钉钉群">
              {{ data?.summary?.group_configured ? '已配置' : '未配置' }}
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

const loading = ref(false)
const data = ref<any>(null)

const submitPct = computed(() => {
  const s = data.value?.submission
  if (!s?.active_count) return 0
  return Math.round((s.submitted_count / s.active_count) * 100)
})

const costLabel = computed(() => {
  const c = data.value?.metrics_highlights?.total_cost_usd
  return c != null ? `$${Number(c).toFixed(2)}` : '—'
})

const statCards = computed(() => [
  { label: '活跃成员', value: data.value?.submission?.active_count ?? '—' },
  { label: '已提交', value: data.value?.submission?.submitted_count ?? '—' },
  { label: '提交率', value: submitPct.value ? `${submitPct.value}%` : '—' },
  {
    label: '总事件',
    value: data.value?.metrics_highlights?.total_events?.toLocaleString() ?? '—',
  },
])

const maxCost = computed(() => {
  const rows = data.value?.member_costs || []
  return Math.max(...rows.map((r: any) => r.cost_usd || 0), 0.01)
})

function barWidth(cost: number) {
  return `${Math.round((cost / maxCost.value) * 100)}%`
}

onMounted(async () => {
  loading.value = true
  try {
    const res = await client.get('/api/dashboard/overview')
    data.value = res.data
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
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
.bar-row,
.cost-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 8px 0;
}
.name {
  width: 88px;
  flex-shrink: 0;
  font-size: 13px;
}
.bar-track {
  flex: 1;
  height: 8px;
  background: #e2e8f0;
  border-radius: 4px;
  overflow: hidden;
}
.bar-fill {
  height: 100%;
  background: linear-gradient(90deg, #6366f1, #38bdf8);
}
.val {
  width: 64px;
  text-align: right;
  font-size: 12px;
}
.alert-item {
  display: flex;
  gap: 8px;
  margin: 8px 0;
  font-size: 13px;
}
.empty {
  color: #94a3b8;
  font-size: 13px;
}
</style>
