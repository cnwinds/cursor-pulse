<template>
  <div class="coding-plan-quota" v-loading="loading">
    <p class="desc muted">
      与 {{ vendorLabel }} Coding Plan 官方窗口额度对齐（无历史用量明细）。
    </p>
    <el-row :gutter="16">
      <el-col v-for="item in board" :key="item.account_id" :xs="24" :sm="12" :lg="8" class="card-col">
        <el-card shadow="never" class="quota-card" :class="item.status">
          <div class="card-top">
            <div>
              <div class="account-email">{{ item.account_identifier }}</div>
              <div class="account-subline muted">
                <span v-if="item.primary_member_name">{{ item.primary_member_name }}</span>
                <span v-if="item.primary_member_name"> · </span>
                <span>{{ item.plan_name }} · {{ item.vendor_name }}</span>
              </div>
            </div>
            <el-tag :type="statusTagType(item.status)" size="small">{{ statusLabel(item.status) }}</el-tag>
          </div>

          <template v-if="item.has_snapshot">
            <div v-if="item.cycle_end_at || item.cycle_end" class="cycle-meta muted">
              <span v-if="item.cycle_end_at">下次重置 {{ formatChinaTime(item.cycle_end_at) }}</span>
              <span v-else>周期至 {{ item.cycle_end }}</span>
            </div>
            <div v-for="tier in item.quota_tiers || []" :key="tier.name" class="progress-block">
              <div class="progress-label">
                <span>{{ tier.label || tier.name }}</span>
                <span>{{ pctLabel(tier.utilization_pct) }}</span>
              </div>
              <el-progress
                :percentage="pctNum(tier.utilization_pct)"
                :status="tierProgressStatus(tier.utilization_pct, item.status)"
                :show-text="false"
              />
            </div>
            <div class="card-actions">
              <span class="muted">最后更新 {{ formatChinaTime(item.captured_at) }}</span>
              <el-button
                v-if="canWrite"
                link
                type="primary"
                :loading="syncingId === item.account_id"
                @click="syncAccount(item)"
              >
                同步
              </el-button>
            </div>
          </template>
          <div v-else class="muted empty">暂无额度快照，请绑定 Key 并同步</div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import client from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { formatChinaTime } from '@/utils/time'

const props = defineProps<{
  vendor: 'glm' | 'minimax'
}>()

const auth = useAuthStore()
const canWrite = computed(() => auth.hasPermission('accounts:write'))
const vendorLabel = computed(() => (props.vendor === 'glm' ? 'GLM' : 'MiniMax'))

interface QuotaTierRow {
  name: string
  label?: string
  utilization_pct: number
  resets_at?: string | null
}

interface BoardItem {
  account_id: string
  account_identifier: string
  primary_member_name?: string | null
  plan_name?: string
  vendor_name?: string
  status: string
  has_snapshot: boolean
  captured_at?: string | null
  cycle_end?: string | null
  cycle_end_at?: string | null
  quota_tiers?: QuotaTierRow[]
}

const loading = ref(false)
const board = ref<BoardItem[]>([])
const syncingId = ref<string | null>(null)

function statusLabel(s: string) {
  const map: Record<string, string> = {
    healthy: '正常',
    warning: '预警',
    exhausted: '已耗尽',
    unknown: '未知',
  }
  return map[s] || s
}

function statusTagType(s: string) {
  if (s === 'exhausted') return 'danger'
  if (s === 'warning') return 'warning'
  if (s === 'healthy') return 'success'
  return 'info'
}

function pctLabel(v: number | null | undefined) {
  if (v == null) return '—'
  return `${Math.round(v)}%`
}

function pctNum(v: number | null | undefined) {
  if (v == null) return 0
  return Math.min(100, Math.max(0, Math.round(v)))
}

function tierProgressStatus(pct: number, cardStatus: string) {
  if (pct >= 100 || cardStatus === 'exhausted') return 'exception'
  if (pct >= 80 || cardStatus === 'warning') return 'warning'
  return undefined
}

async function loadAll() {
  loading.value = true
  try {
    const res = await client.get('/api/v2/quota-board', { params: { vendor: props.vendor } })
    board.value = res.data
  } finally {
    loading.value = false
  }
}

async function syncAccount(item: BoardItem) {
  syncingId.value = item.account_id
  try {
    await client.post(`/api/v2/accounts/${item.account_id}/sync`)
    ElMessage.success('同步完成')
    await loadAll()
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } }
    ElMessage.error(err.response?.data?.detail || '同步失败')
  } finally {
    syncingId.value = null
  }
}

onMounted(loadAll)

defineExpose({ loadAll })
</script>

<style scoped>
.desc {
  margin: 0 0 16px;
  font-size: 13px;
}
.card-col {
  margin-bottom: 16px;
}
.card-top {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 8px;
  margin-bottom: 12px;
}
.account-email {
  font-weight: 600;
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
.card-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 12px;
  font-size: 12px;
}
.empty {
  padding: 12px 0;
}
</style>
