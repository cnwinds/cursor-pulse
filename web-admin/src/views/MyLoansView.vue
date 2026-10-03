<template>
  <div class="my-loans-page" v-loading="loading">
    <header class="page-header">
      <div>
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

    <el-card class="membership-card" shadow="never" v-loading="membershipLoading">
      <template #header>
        <div class="membership-card-head">
          <span>我的会员</span>
          <el-button
            v-if="
              myMembership &&
              (myMembership.membership?.credit_mode === 'prepaid' ||
                myMembership.balance_cents !== 0)
            "
            size="small"
            @click="statementVisible = true"
          >
            查看账单
          </el-button>
        </div>
      </template>
      <el-alert
        v-if="membershipRequired && !myMembership?.membership"
        type="error"
        :closable="false"
        show-icon
        class="mb"
        title="团队要求开通会员后才能使用代理服务，请联系管理员开通。"
      />
      <el-alert
        v-else-if="!myMembership?.membership"
        type="info"
        :closable="false"
        show-icon
        class="mb"
        title="尚未开通会员，如需使用额度规则或预付余额请联系管理员。"
      />
      <div v-if="myMembership?.membership" class="membership-body">
        <div class="membership-row">
          <span class="label">套餐</span>
          <span>
            {{
              myMembership.membership.plan_name ||
              (myMembership.membership.plan_id ? '—' : '自定义')
            }}
          </span>
        </div>
        <div class="membership-row">
          <span class="label">余额模式</span>
          <span>{{ creditModeLabel(myMembership.membership.credit_mode) }}</span>
        </div>
        <div
          v-if="
            myMembership.membership.credit_mode === 'prepaid' ||
            myMembership.balance_cents !== 0
          "
          class="membership-row"
        >
          <span class="label">余额</span>
          <span :class="{ 'balance-low': myMembership.balance_cents <= 0 }">
            {{ formatUsdCents(myMembership.balance_cents) }}
            <span v-if="myMembership.balance_cents <= 0" class="balance-hint">
              余额已用完
            </span>
          </span>
        </div>
        <div class="membership-row">
          <span class="label">窗口规则</span>
          <UsageCapStatus :rules="myMembership.membership.effective_rules" />
        </div>
      </div>
    </el-card>

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
      <el-table-column label="用量限制" width="108" align="center">
        <template #default="{ row }">
          <UsageCapStatus :rules="row.usage_caps" />
        </template>
      </el-table-column>
      <el-table-column label="创建时间" width="180">
        <template #default="{ row }">{{ formatChinaTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="320" fixed="right">
        <template #default="{ row }">
          <CopyCommandDropdown
            v-if="row.status === 'active'"
            size="small"
            :setup-url="`/api/v2/loans/${row.id}/client-setup`"
          />
          <el-button
            v-if="row.status === 'active' && row.delivery_mode === 'proxy_alias'"
            link
            type="primary"
            :loading="ideKeyRotatingId === row.id"
            @click="resetIdeKey(row)"
          >
            重置 IDE 密钥
          </el-button>
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

    <el-drawer v-model="statementVisible" title="我的账单" size="880px" destroy-on-close>
      <CreditStatement base-url="/api/v2/me/credit" />
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import client from '@/api/client'
import CopyCommandDropdown from '@/components/CopyCommandDropdown.vue'
import CreditStatement from '@/components/credit/CreditStatement.vue'
import UsageCapStatus, { type UsageCapSnapshot } from '@/components/borrow/UsageCapStatus.vue'
import { copyText } from '@/utils/clipboard'
import { formatUsdCents } from '@/utils/money'
import { formatChinaTime } from '@/utils/time'
import { loanAssignmentLabel, loanAssignmentTagType } from '@/utils/loanAssignment'
import { rotateLoanIdeKey } from '@/utils/rotateLoanIdeKey'

interface LoanRow {
  id: string
  source_account_identifier: string
  routing_mode?: string | null
  delivery_mode: string | null
  assignment_label?: string | null
  lender_mode?: string | null
  status: string
  created_at: string
  usage_caps?: UsageCapSnapshot[]
}

interface MyMembershipResponse {
  membership: {
    plan_id: string | null
    plan_name: string | null
    credit_mode: 'unlimited' | 'prepaid'
    effective_rules: UsageCapSnapshot[]
  } | null
  balance_cents: number
  membership_required: boolean
}

const loading = ref(false)
const membershipLoading = ref(false)
const requesting = ref(false)
const loans = ref<LoanRow[]>([])
const activeCount = ref(0)
const myMembership = ref<MyMembershipResponse | null>(null)
const membershipRequired = ref(false)
const statementVisible = ref(false)

function creditModeLabel(mode: 'unlimited' | 'prepaid'): string {
  return mode === 'prepaid' ? '预付余额' : '不限额'
}

async function loadMembership() {
  membershipLoading.value = true
  try {
    const res = await client.get('/api/v2/me/membership')
    myMembership.value = res.data
    membershipRequired.value = Boolean(res.data.membership_required)
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '加载会员信息失败')
  } finally {
    membershipLoading.value = false
  }
}
const ideKeyRotatingId = ref<string | null>(null)
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

function resetIdeKey(row: LoanRow) {
  void rotateLoanIdeKey(row.id, (busy) => {
    ideKeyRotatingId.value = busy ? row.id : null
  })
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

onMounted(() => {
  void loadLoans()
  void loadMembership()
})
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
.membership-card {
  margin-bottom: 16px;
}
.membership-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.membership-body {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.membership-row {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  font-size: var(--pulse-text-base);
}
.membership-row .label {
  flex-shrink: 0;
  width: 72px;
  color: var(--el-text-color-secondary);
}
.balance-low {
  color: var(--el-color-danger);
  font-weight: var(--pulse-font-medium);
}
.balance-hint {
  margin-left: 8px;
  font-size: var(--pulse-text-sm);
}
</style>
