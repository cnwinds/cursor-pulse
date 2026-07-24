<template>
  <div class="loans-page" v-loading="loading">
    <header class="page-header">
      <div>
        <h2>借用记录</h2>
        <p class="desc">
          管理临时 Key 借用。进行中 {{ activeCount }} 条；消耗为借出账号用量差值近似，经 proxy 的部分另有本地估算子计数（非 Cursor 账单）。
        </p>
      </div>
      <div class="header-actions">
        <div class="filter-switch">
          <span class="filter-label">仅显示正在借用</span>
          <el-switch v-model="activeOnly" @change="onFilterChange" />
        </div>
        <el-button @click="loadLoans">刷新</el-button>
        <el-button v-if="canWrite" type="primary" @click="openLoanDialog">为成员分配 Key</el-button>
      </div>
    </header>

    <el-table :data="loans" stripe>
      <el-table-column label="借出人" prop="borrower_name" width="120" />
      <el-table-column label="借出账号" min-width="240">
        <template #default="{ row }">
          <span>{{ row.source_account_identifier }}</span>
          <span v-if="row.primary_member_name" class="primary-member">
            {{ row.primary_member_name }}
          </span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="loanStatusType(row.status)" size="small">{{ row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="交付模式" width="110">
        <template #default="{ row }">
          <el-tag
            :type="row.delivery_mode === 'proxy_alias' ? 'success' : 'info'"
            size="small"
          >
            {{ row.delivery_mode === 'proxy_alias' ? '代理别名' : 'Cursor Key' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="创建时间" width="180">
        <template #default="{ row }">{{ formatChinaTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="归还时间" width="180">
        <template #default="{ row }">
          {{ row.revoked_at ? formatChinaTime(row.revoked_at) : '—' }}
        </template>
      </el-table-column>
      <el-table-column label="借用时长" width="120">
        <template #default="{ row }">
          {{ formatLoanDuration(row.created_at, row.revoked_at) }}
        </template>
      </el-table-column>
      <el-table-column label="近似消耗" width="110">
        <template #default="{ row }">
          ${{ (row.borrowed_cents / 100).toFixed(2) }}
        </template>
      </el-table-column>
      <el-table-column label="proxy 统计" width="120">
        <template #default="{ row }">
          <el-button link type="primary" @click="openUsages(row)">
            ${{ ((row.proxy_cost_cents ?? 0) / 100).toFixed(2) }}
          </el-button>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="280" fixed="right">
        <template #default="{ row }">
          <el-dropdown
            v-if="row.status === 'active'"
            trigger="click"
            @command="(shell: ShellKind) => copyCommand(row.id, shell)"
          >
            <el-button size="small" type="primary" plain>复制命令</el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="powershell">Windows PowerShell</el-dropdown-item>
                <el-dropdown-item command="bash">Linux / macOS</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <el-button
            v-if="canWrite && row.status === 'active' && row.delivery_mode === 'proxy_alias'"
            size="small"
            plain
            @click="revealCursorKey(row)"
          >
            底层 Key
          </el-button>
          <el-button
            v-if="row.status === 'active'"
            link
            type="danger"
            @click="revokeLoan(row)"
          >
            撤销
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <div class="pager">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[20, 50, 100]"
        layout="total, sizes, prev, pager, next"
        background
        @current-change="loadLoans"
        @size-change="onPageSizeChange"
      />
    </div>

    <el-dialog v-model="loanDialogVisible" title="为成员分配 Key" width="520px">
      <el-form label-width="100px">
        <el-form-item label="借用人" required>
          <el-select v-model="loanForm.borrower_member_id" filterable placeholder="选择成员" style="width: 100%">
            <el-option
              v-for="m in members"
              :key="m.id"
              :label="m.display_name"
              :value="m.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="借出账号" required>
          <el-select v-model="loanForm.source_account_id" filterable placeholder="推荐或手动选择" style="width: 100%">
            <el-option
              v-for="r in recommend"
              :key="r.account_id"
              :label="`${r.account_identifier}（剩 ${r.remaining_headroom_pct}% · ${r.days_until_reset}天）`"
              :value="r.account_id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="loanForm.note" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="交付模式" required>
          <el-radio-group v-model="loanForm.delivery_mode">
            <el-radio value="proxy_alias">代理别名 Key（pka_，推荐）</el-radio>
            <el-radio value="cursor_direct">Cursor Key（cr*，直接下发）</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="重置日回收">
          <el-switch v-model="loanForm.auto_revoke_on_reset" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="loanDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="loanSubmitting" @click="submitLoan">确认分配</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="usagesVisible" :title="`用量详情 - ${usagesTitle}`" size="720px">
      <div class="usage-summary" v-if="usageSummary">
        <div>近似消耗：${{ (usageSummary.borrowed_cents / 100).toFixed(2) }}</div>
        <div>proxy 估算（非账单）：${{ (usageSummary.proxy_cost_cents / 100).toFixed(2) }}（{{ usageSummary.request_count }} 次请求 · {{ formatTokensM(usageSummary.proxy_total_tokens) }}）</div>
      </div>
      <h4 class="usage-section-title">按模型汇总（本地估算）</h4>
      <el-table :data="usageByModel" style="width: 100%" v-loading="usagesLoading">
        <el-table-column prop="model" label="模型" min-width="140" />
        <el-table-column prop="request_count" label="请求数" width="80" />
        <el-table-column label="tokens" width="100">
          <template #default="{ row }">{{ formatTokensM(row.total_tokens) }}</template>
        </el-table-column>
        <el-table-column label="费用" width="100">
          <template #default="{ row }">${{ ((row.cost_cents ?? 0) / 100).toFixed(2) }}</template>
        </el-table-column>
      </el-table>
      <h4 class="usage-section-title">proxy 明细（最近 · 本地估算）</h4>
      <el-table :data="usages" style="width: 100%" v-loading="usagesLoading">
        <el-table-column label="时间" width="170">
          <template #default="{ row }">{{ formatChinaTime(row.ts) }}</template>
        </el-table-column>
        <el-table-column prop="model" label="模型" min-width="140" />
        <el-table-column label="tokens" width="100">
          <template #default="{ row }">{{ formatTokensM(row.total_tokens) }}</template>
        </el-table-column>
        <el-table-column label="费用" width="100">
          <template #default="{ row }">${{ ((row.cost_cents ?? 0) / 100).toFixed(2) }}</template>
        </el-table-column>
      </el-table>
    </el-drawer>

    <el-dialog v-model="keyRevealVisible" title="Key 已生成（仅显示一次）" width="560px" :close-on-click-modal="false">
      <el-alert type="warning" :closable="false" show-icon class="mb">
        <template v-if="revealedKey?.delivery_mode === 'proxy_alias'">
          已下发代理别名 Key（pka_）。请立即复制；关闭后可用「复制命令」再次获取。用户须配置 HTTPS_PROXY。底层 Cursor Key 仅管理员可通过「底层 Key」查看。
        </template>
        <template v-else>
          请立即复制保存。关闭后无法再次查看完整 Key。借用消耗为账号用量差值近似；经 proxy 走量的部分另有本地估算子计数（非 Cursor 账单），可复制下方启动命令。
        </template>
      </el-alert>
      <div class="key-reveal">
        <div class="muted">借出账号：{{ revealedKey?.source_account_identifier }}</div>
        <div class="muted">借用人：{{ revealedKey?.borrower_name }}</div>
        <div class="muted" v-if="revealedKey?.delivery_mode">
          交付模式：{{ revealedKey.delivery_mode === 'proxy_alias' ? '代理别名 Key' : 'Cursor Key' }}
        </div>
        <el-input :model-value="revealedKey?.api_key" readonly>
          <template #append>
            <el-button @click="copyKey">复制 Key</el-button>
          </template>
        </el-input>
        <div class="reveal-actions">
          <el-button type="primary" plain @click="copyRevealCommand('powershell')">复制 PowerShell 命令</el-button>
          <el-button type="primary" plain @click="copyRevealCommand('bash')">复制 Linux 命令</el-button>
        </div>
      </div>
      <template #footer>
        <el-button type="primary" @click="closeKeyReveal">我已保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="cursorKeyVisible" title="底层 Cursor Key（管理员）" width="560px">
      <el-alert type="warning" :closable="false" show-icon class="mb">
        此为绑定的 Cursor 官方 Key，权限较大，请勿发给借用人。
      </el-alert>
      <el-input :model-value="cursorKeyPlaintext" readonly>
        <template #append>
          <el-button @click="copyCursorKey">复制</el-button>
        </template>
      </el-input>
      <template #footer>
        <el-button type="primary" @click="cursorKeyVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import client from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { copyText } from '@/utils/clipboard'
import { formatChinaTime, formatLoanDuration } from '@/utils/time'
import { formatTokensM } from '@/utils/usage'

type ShellKind = 'bash' | 'powershell'

const auth = useAuthStore()
const canWrite = computed(() => auth.hasPermission('accounts:write'))

interface Member {
  id: string
  display_name: string
}

interface RecommendItem {
  account_id: string
  account_identifier: string
  remaining_headroom_pct: number
  days_until_reset: number
}

interface LoanRow {
  id: string
  borrower_name: string
  source_account_identifier: string
  primary_member_name: string | null
  delivery_mode: string | null
  status: string
  created_at: string
  revoked_at: string | null
  borrowed_cents: number
  proxy_cost_cents: number | null
}

const loading = ref(false)
const loans = ref<LoanRow[]>([])
const members = ref<Member[]>([])
const recommend = ref<RecommendItem[]>([])
const activeOnly = ref(true)
const activeCount = ref(0)
const total = ref(0)
const page = ref(1)
const pageSize = ref(50)

const loanDialogVisible = ref(false)
const loanSubmitting = ref(false)
const loanForm = ref({
  borrower_member_id: '',
  source_account_id: '',
  note: '',
  auto_revoke_on_reset: true,
  delivery_mode: 'proxy_alias' as 'proxy_alias' | 'cursor_direct',
})

const keyRevealVisible = ref(false)
const revealedKey = ref<{
  loan_id: string
  api_key: string
  borrower_name: string
  source_account_identifier: string
  delivery_mode?: string
} | null>(null)

const cursorKeyVisible = ref(false)
const cursorKeyPlaintext = ref('')

const usagesVisible = ref(false)
const usagesLoading = ref(false)
const usagesTitle = ref('')
const usages = ref<LoanUsageRow[]>([])
const usageByModel = ref<LoanUsageByModelRow[]>([])
const usageSummary = ref<LoanUsageSummary | null>(null)

interface LoanUsageRow {
  id: string
  model: string | null
  total_tokens: number
  cost_cents: number
  ts: string | null
}

interface LoanUsageByModelRow {
  model: string
  request_count: number
  total_tokens: number
  cost_cents: number
}

interface LoanUsageSummary {
  borrowed_cents: number
  proxy_cost_cents: number
  proxy_total_tokens: number
  request_count: number
}

function loanStatusType(status: string) {
  return { active: 'primary', revoked: 'info', expired: 'warning' }[status] || 'info'
}

async function loadLoans() {
  loading.value = true
  try {
    const params: Record<string, string | number> = {
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
    }
    if (activeOnly.value) {
      params.status = 'active'
    }
    const res = await client.get('/api/v2/loans', { params })
    loans.value = res.data.items
    total.value = res.data.total
    activeCount.value = res.data.active_count
  } finally {
    loading.value = false
  }
}

async function loadLoanDialogData() {
  const [membersRes, recommendRes] = await Promise.all([
    client.get('/api/v2/members'),
    client.get('/api/v2/quota-board/recommend'),
  ])
  members.value = membersRes.data
  recommend.value = recommendRes.data
  if (!loanForm.value.source_account_id && recommend.value.length) {
    loanForm.value.source_account_id = recommend.value[0].account_id
  }
}

function onFilterChange() {
  page.value = 1
  loadLoans()
}

function onPageSizeChange() {
  page.value = 1
  loadLoans()
}

async function openLoanDialog() {
  await loadLoanDialogData()
  loanDialogVisible.value = true
}

async function submitLoan() {
  if (!loanForm.value.borrower_member_id || !loanForm.value.source_account_id) {
    ElMessage.warning('请选择借用人和借出账号')
    return
  }
  loanSubmitting.value = true
  try {
    const res = await client.post(
      `/api/v2/accounts/${loanForm.value.source_account_id}/loan-key`,
      {
        borrower_member_id: loanForm.value.borrower_member_id,
        note: loanForm.value.note || null,
        auto_revoke_on_reset: loanForm.value.auto_revoke_on_reset,
        delivery_mode: loanForm.value.delivery_mode,
      },
    )
    loanDialogVisible.value = false
    revealedKey.value = res.data
    keyRevealVisible.value = true
    loanForm.value.note = ''
    await loadLoans()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '分配失败')
  } finally {
    loanSubmitting.value = false
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

async function revealCursorKey(row: LoanRow) {
  try {
    const res = await client.get(`/api/v2/loans/${row.id}/cursor-key`)
    cursorKeyPlaintext.value = res.data.cursor_api_key
    cursorKeyVisible.value = true
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '无法查看底层 Key')
  }
}

async function copyCursorKey() {
  if (!cursorKeyPlaintext.value) return
  try {
    await copyText(cursorKeyPlaintext.value)
    ElMessage.success('已复制底层 Cursor Key')
  } catch (err: any) {
    ElMessage.error(err?.message || '复制失败')
  }
}

async function copyCommand(loanId: string, shell: ShellKind) {
  try {
    const res = await client.get(`/api/v2/loans/${loanId}/client-setup`, {
      params: { shell },
    })
    await copyText(res.data.command)
    ElMessage.success(shell === 'powershell' ? '已复制 PowerShell 命令' : '已复制 Linux 命令')
  } catch (err: any) {
    const detail = err?.response?.data?.detail
    ElMessage.error(
      typeof detail === 'string' ? detail : err?.message || '复制失败'
    )
  }
}

async function copyRevealCommand(shell: ShellKind) {
  if (!revealedKey.value?.loan_id) return
  await copyCommand(revealedKey.value.loan_id, shell)
}

function closeKeyReveal() {
  keyRevealVisible.value = false
  revealedKey.value = null
}

async function openUsages(row: LoanRow) {
  usagesTitle.value = row.borrower_name || row.source_account_identifier || row.id.slice(0, 8)
  usages.value = []
  usageByModel.value = []
  usageSummary.value = null
  usagesVisible.value = true
  usagesLoading.value = true
  try {
    const res = await client.get(`/api/v2/loans/${row.id}/usages`)
    usageSummary.value = res.data.summary
    usageByModel.value = res.data.by_model || []
    usages.value = res.data.items || []
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '用量加载失败')
  } finally {
    usagesLoading.value = false
  }
}

async function revokeLoan(row: LoanRow) {
  await ElMessageBox.confirm(
    `确认撤销借给 ${row.borrower_name} 的 Key？撤销后该 Key 立即失效。`,
    '撤销借用',
    { type: 'warning' },
  )
  try {
    const res = await client.post(`/api/v2/loans/${row.id}/revoke`)
    ElMessage.success(
      res.data.borrowed_usd != null
        ? `已撤销，近似消耗 $${res.data.borrowed_usd.toFixed(2)}`
        : '已撤销',
    )
    await loadLoans()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '撤销失败')
  }
}

onMounted(loadLoans)
</script>

<style scoped>
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 20px;
  gap: 16px;
}
.page-header h2 {
  margin: 0 0 8px;
}
.desc {
  margin: 0;
  color: var(--el-text-color-secondary);
  font-size: 14px;
}
.header-actions {
  display: flex;
  gap: 12px;
  flex-shrink: 0;
  align-items: center;
}
.filter-switch {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-right: 4px;
}
.filter-label {
  font-size: 14px;
  color: var(--el-text-color-regular);
}
.primary-member {
  margin-left: 8px;
  font-weight: 500;
  color: var(--el-text-color-regular);
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
.key-reveal .el-input {
  margin-top: 12px;
}
.reveal-actions {
  display: flex;
  gap: 8px;
  margin-top: 12px;
  flex-wrap: wrap;
}
.usage-summary {
  margin-bottom: 16px;
  line-height: 1.7;
  color: var(--el-text-color-regular);
  font-size: 14px;
}
.usage-section-title {
  margin: 0 0 12px;
  font-size: 14px;
  font-weight: 600;
}
.usage-section-title + .el-table + .usage-section-title {
  margin-top: 20px;
}
.mb {
  margin-bottom: 12px;
}
</style>
