<template>
  <div class="ingestions-page" v-loading="loading">
    <header class="page-header">
      <div>
        <h2>摄取记录</h2>
        <p class="desc">查看各账号用量摄取批次；手工厂商待审记录可在此审核。</p>
      </div>
      <div class="header-actions">
        <el-select v-model="period" clearable placeholder="全部账期" style="width: 140px" @change="load">
          <el-option v-for="p in periodOptions" :key="p" :label="p" :value="p" />
        </el-select>
        <el-select v-model="statusFilter" clearable placeholder="全部状态" style="width: 140px" @change="load">
          <el-option label="待审核" value="pending_review" />
          <el-option label="已确认" value="confirmed" />
          <el-option label="已拒绝" value="rejected" />
        </el-select>
        <el-checkbox v-model="manualOnly" @change="applyClientFilter">仅手工厂商</el-checkbox>
        <el-button type="primary" @click="load">刷新</el-button>
      </div>
    </header>

    <el-table :data="displayRows" stripe row-key="id">
      <el-table-column label="账号" min-width="200">
        <template #default="{ row }">
          <div class="account-cell">
            <span class="identifier">{{ row.account_identifier || '—' }}</span>
            <span v-if="row.vendor_name" class="vendor">{{ row.vendor_name }}</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="账期" width="100" prop="period" />
      <el-table-column label="来源" width="130">
        <template #default="{ row }">
          {{ sourceLabel(row.source_type) }}
        </template>
      </el-table-column>
      <el-table-column label="渠道" width="100" prop="channel" />
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag :type="statusTagType(row.status)" size="small">
            {{ statusLabel(row.status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="事件数" width="90" align="right" prop="event_count" />
      <el-table-column label="摄取时间" width="170">
        <template #default="{ row }">
          {{ formatChinaTime(row.ingested_at) }}
        </template>
      </el-table-column>
      <el-table-column label="提交人" width="120">
        <template #default="{ row }">
          {{ row.member_name || '—' }}
        </template>
      </el-table-column>
      <el-table-column v-if="canReview" label="操作" width="160" fixed="right">
        <template #default="{ row }">
          <template v-if="row.status === 'pending_review' && isManualSource(row.source_type)">
            <el-button
              link
              type="success"
              :loading="actingId === row.id"
              @click="confirmIngestion(row.id)"
            >通过</el-button>
            <el-button
              link
              type="danger"
              :loading="actingId === row.id"
              @click="rejectIngestion(row.id)"
            >拒绝</el-button>
          </template>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import client from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { formatChinaTime } from '@/utils/time'

const auth = useAuthStore()
const loading = ref(false)
const period = ref('')
const statusFilter = ref<string>('')
const manualOnly = ref(true)
const rows = ref<IngestionRow[]>([])
const actingId = ref<string | null>(null)

const canReview = computed(() => auth.hasPermission('submissions:review'))

interface IngestionRow {
  id: string
  account_identifier: string | null
  vendor_name: string | null
  period: string
  source_type: string
  channel: string
  status: string
  event_count: number
  ingested_at: string
  member_name: string | null
}

const SOURCE_LABELS: Record<string, string> = {
  manual_csv: 'CSV 导出',
  manual_vision: '控制台截图',
  manual_text: '手工录入',
  api_sync: 'API 自动同步',
}

const periodOptions = computed(() => {
  const list: string[] = []
  const now = new Date()
  for (let i = 0; i < 24; i++) {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1)
    list.push(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`)
  }
  return list
})

const displayRows = computed(() => {
  if (!manualOnly.value) return rows.value
  return rows.value.filter((row) => isManualSource(row.source_type))
})

function isManualSource(sourceType: string) {
  return sourceType.startsWith('manual_')
}

function sourceLabel(sourceType: string) {
  return SOURCE_LABELS[sourceType] || sourceType
}

function statusLabel(status: string) {
  const map: Record<string, string> = {
    pending_review: '待审核',
    confirmed: '已确认',
    rejected: '已拒绝',
  }
  return map[status] || status
}

function statusTagType(status: string) {
  if (status === 'confirmed') return 'success'
  if (status === 'pending_review') return 'warning'
  if (status === 'rejected') return 'info'
  return ''
}

function currentPeriod(): string {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
}

function applyClientFilter() {
  // client-side only; no reload needed
}

async function load() {
  loading.value = true
  try {
    if (!period.value) {
      try {
        const cfg = await client.get('/api/config/summary')
        period.value = cfg.data.current_period
      } catch {
        period.value = currentPeriod()
      }
    }
    const params: Record<string, string> = { period: period.value }
    if (statusFilter.value) params.status = statusFilter.value
    const res = await client.get('/api/v2/ingestions', { params })
    rows.value = res.data
  } finally {
    loading.value = false
  }
}

async function confirmIngestion(id: string) {
  await ElMessageBox.confirm('确认通过该摄取并计入用量？', '审核通过')
  actingId.value = id
  try {
    await client.post(`/api/v2/ingestions/${id}/confirm`)
    ElMessage.success('已通过')
    await load()
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } }
    ElMessage.error(err.response?.data?.detail || '操作失败')
  } finally {
    actingId.value = null
  }
}

async function rejectIngestion(id: string) {
  await ElMessageBox.confirm('拒绝后将删除该待审摄取，确定？', '拒绝摄取', { type: 'warning' })
  actingId.value = id
  try {
    await client.post(`/api/v2/ingestions/${id}/reject`)
    ElMessage.success('已拒绝')
    await load()
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } }
    ElMessage.error(err.response?.data?.detail || '操作失败')
  } finally {
    actingId.value = null
  }
}

onMounted(load)
</script>

<style scoped>
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 16px;
}
.page-header h2 {
  margin: 0 0 4px;
}
.desc {
  margin: 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.header-actions {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}
.account-cell {
  display: flex;
  flex-direction: column;
}
.identifier {
  font-weight: 500;
}
.vendor {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
