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
      <el-table-column label="借出账号" min-width="220" prop="source_account_identifier" />
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="loanStatusType(row.status)" size="small">{{ row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="交付模式" width="120">
        <template #default="{ row }">
          {{ row.delivery_mode === 'proxy_alias' ? '代理别名' : 'Cursor Key' }}
        </template>
      </el-table-column>
      <el-table-column label="创建时间" width="180">
        <template #default="{ row }">{{ formatChinaTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="260" fixed="right">
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
        <div class="muted">借出账号：{{ revealedKey?.source_account_identifier }}</div>
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
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import client from '@/api/client'
import { copyText } from '@/utils/clipboard'
import { formatChinaTime } from '@/utils/time'

type ShellKind = 'bash' | 'powershell'

interface LoanRow {
  id: string
  source_account_identifier: string
  delivery_mode: string | null
  status: string
  created_at: string
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

async function copyCommand(loanId: string, shell: ShellKind) {
  try {
    const res = await client.get(`/api/v2/loans/${loanId}/client-setup`, {
      params: { shell },
    })
    await copyText(res.data.command)
    ElMessage.success(shell === 'powershell' ? '已复制 PowerShell 命令' : '已复制 Linux 命令')
  } catch (err: any) {
    const detail = err?.response?.data?.detail
    ElMessage.error(typeof detail === 'string' ? detail : err?.message || '复制失败')
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
  font-size: 13px;
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
  font-size: 13px;
  margin-bottom: 6px;
}
.reveal-actions {
  margin-top: 12px;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
</style>
