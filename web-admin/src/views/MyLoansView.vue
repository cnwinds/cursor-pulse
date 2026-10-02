<template>
  <div class="my-loans-page" v-loading="loading">
    <header class="page-header">
      <div>
        <h2>我的借用</h2>
        <p class="desc">
          额度不足时可自助申请临时 Key（代理别名）。进行中 {{ activeCount }} 条。
        </p>
      </div>
      <div class="header-actions">
        <el-button @click="loadLoans">刷新</el-button>
        <el-button type="primary" :loading="requesting" @click="requestSelf">
          自助申请 Key
        </el-button>
      </div>
    </header>

    <el-table :data="loans" stripe>
      <el-table-column label="借出账号" min-width="220">
        <template #default="{ row }">
          {{ row.routing_mode === 'pool' ? '账号池（使用中轮换）' : row.source_account_identifier }}
        </template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="loanStatusType(row.status)" size="small">{{ row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="分配方式" width="108" align="center">
        <template #default="{ row }">
          <el-tag :type="loanAssignmentTagType(row)" size="small">
            {{ loanAssignmentLabel(row) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="用量封顶" min-width="200">
        <template #default="{ row }">
          <span v-if="!row.usage_cap_period" class="muted">未启用</span>
          <template v-else>
            <div>{{ usageCapPeriodLabel(row.usage_cap_period) }}</div>
            <div class="muted">
              Auto：{{ formatCapBucket(row.usage_cap_auto_used_cents, row.auto_cost_usd) }}
              · API：{{ formatCapBucket(row.usage_cap_api_used_cents, row.api_cost_usd) }}
            </div>
          </template>
        </template>
      </el-table-column>
      <el-table-column label="创建时间" width="180">
        <template #default="{ row }">{{ formatChinaTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="260" fixed="right">
        <template #default="{ row }">
          <CopyCommandDropdown
            v-if="row.status === 'active'"
            size="small"
            :setup-url="`/api/v2/loans/${row.id}/client-setup`"
          />
          <el-button
            v-if="row.status === 'active'"
            link
            type="danger"
            @click="revokeLoan(row)"
          >
            归还
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="keyRevealVisible" title="Key 已生成（仅显示一次）" width="560px" :close-on-click-modal="false">
      <el-alert type="warning" :closable="false" show-icon class="mb">
        已下发代理别名 Key（pka_）。请立即复制；关闭后可用「复制命令」再次获取。须配置 HTTPS_PROXY。
      </el-alert>
      <div class="key-reveal">
        <div class="muted">
          借出账号：{{ revealedKey?.routing_mode === 'pool' ? '账号池（使用中轮换）' : revealedKey?.source_account_identifier }}
        </div>
        <el-input :model-value="revealedKey?.api_key" readonly>
          <template #append>
            <el-button @click="copyKey">复制 Key</el-button>
          </template>
        </el-input>
        <div class="reveal-actions">
          <CopyCommandDropdown
            v-if="revealedKey?.loan_id"
            :setup-url="`/api/v2/loans/${revealedKey.loan_id}/client-setup`"
          />
        </div>
      </div>
      <template #footer>
        <el-button type="primary" @click="closeKeyReveal">我已保存</el-button>
      </template>
    </el-dialog>

  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import client from '@/api/client'
import CopyCommandDropdown from '@/components/CopyCommandDropdown.vue'
import { copyText } from '@/utils/clipboard'
import { formatChinaTime } from '@/utils/time'
import { loanAssignmentLabel, loanAssignmentTagType } from '@/utils/loanAssignment'

interface LoanRow {
  id: string
  source_account_identifier: string
  routing_mode?: string | null
  delivery_mode: string | null
  assignment_label?: string | null
  lender_mode?: string | null
  status: string
  created_at: string
  usage_cap_period?: string | null
  auto_cost_usd?: number | null
  api_cost_usd?: number | null
  usage_cap_auto_used_cents?: number | null
  usage_cap_api_used_cents?: number | null
}

function usageCapPeriodLabel(period: string) {
  return (
    { '5h': '滚动 5 小时', week: '滚动 7 天', month: '滚动 30 天' }[period] || period
  )
}

function formatCapBucket(usedCents: number | null | undefined, limitUsd: number | null | undefined) {
  const used = ((usedCents ?? 0) / 100).toFixed(2)
  const limit = limitUsd != null ? `$${limitUsd}` : '不限'
  return `$${used} / ${limit}`
}

const loading = ref(false)
const requesting = ref(false)
const loans = ref<LoanRow[]>([])
const activeCount = ref(0)
const keyRevealVisible = ref(false)
const revealedKey = ref<{
  loan_id: string
  api_key: string
  source_account_identifier: string
  routing_mode?: string
} | null>(null)

function loanStatusType(status: string) {
  return { active: 'primary', revoked: 'info', expired: 'warning' }[status] || 'info'
}

async function loadLoans() {
  loading.value = true
  try {
    const res = await client.get('/api/v2/loans/mine', { params: { limit: 50 } })
    loans.value = res.data.items
    activeCount.value = res.data.active_count
  } finally {
    loading.value = false
  }
}

async function requestSelf() {
  try {
    await ElMessageBox.confirm(
      '仅在你名下 Cursor 账号额度告警/耗尽，且存在可借出富余账号时可用。确认申请？',
      '自助申请 Key',
      { type: 'warning' },
    )
  } catch {
    return
  }
  requesting.value = true
  try {
    const res = await client.post('/api/v2/loans/request-self', { note: 'Web 自助借 Key' })
    revealedKey.value = res.data
    keyRevealVisible.value = true
    await loadLoans()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '申请失败')
  } finally {
    requesting.value = false
  }
}

async function copyKey() {
  if (!revealedKey.value?.api_key) return
  try {
    await copyText(revealedKey.value.api_key)
    ElMessage.success('已复制 Key')
  } catch (err: any) {
    ElMessage.error(err?.message || '复制失败')
  }
}

function closeKeyReveal() {
  keyRevealVisible.value = false
  revealedKey.value = null
}

async function revokeLoan(row: LoanRow) {
  try {
    await ElMessageBox.confirm('确认归还该借用 Key？', '归还', { type: 'warning' })
  } catch {
    return
  }
  try {
    await client.post(`/api/v2/loans/${row.id}/revoke`)
    ElMessage.success('已归还')
    await loadLoans()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '归还失败')
  }
}

onMounted(loadLoans)
</script>

<style scoped>
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 16px;
}
.desc {
  margin: 4px 0 0;
  color: var(--el-text-color-secondary);
  font-size: var(--pulse-text-base);
}
.header-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}
.mb {
  margin-bottom: 12px;
}
.key-reveal .muted {
  color: var(--el-text-color-secondary);
  font-size: var(--pulse-text-base);
  margin-bottom: 6px;
}
.reveal-actions {
  margin-top: 12px;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
</style>
