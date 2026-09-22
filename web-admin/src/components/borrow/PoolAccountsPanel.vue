<template>
  <div class="pool-panel" v-loading="loading">
    <div class="panel-toolbar">
      <p class="hint">
        开启入池后，该账号主 Key 参与自动轮换。管理员「自动分配」与进行中的池轮换借用都按「打分表」顺序选号。
      </p>
      <el-button size="small" @click="load">刷新</el-button>
    </div>
    <el-table :data="pool" stripe style="width: 100%">
      <el-table-column label="账号" min-width="200">
        <template #default="{ row }">
          <div>{{ row.account_identifier }}</div>
          <div v-if="row.primary_member_name" class="account-sub">{{ row.primary_member_name }}</div>
        </template>
      </el-table-column>
      <el-table-column label="就绪" width="160">
        <template #default="{ row }">
          <el-tooltip v-if="poolReadyTooltip(row)" :content="poolReadyTooltip(row)!">
            <el-tag :type="poolReadyType(row)" size="small">{{ poolReadyLabel(row) }}</el-tag>
          </el-tooltip>
          <el-tag v-else :type="poolReadyType(row)" size="small">{{ poolReadyLabel(row) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="入池" width="100" align="center">
        <template #default="{ row }">
          <el-switch
            :model-value="row.proxy_enabled"
            :disabled="!canWrite || (!row.proxy_enabled && !row.pool_ready)"
            @change="(val: boolean) => toggleAccount(row, val)"
          />
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import client from '@/api/client'
import { useAuthStore } from '@/stores/auth'

interface PoolAccount {
  id: string
  account_identifier: string
  primary_member_name: string | null
  proxy_enabled: boolean
  pool_ready: boolean
  pool_ready_reason: string | null
  pool_effective: boolean
}

const auth = useAuthStore()
const canWrite = computed(() => auth.hasPermission('proxy:write'))
const loading = ref(false)
const pool = ref<PoolAccount[]>([])

function poolReadyLabel(row: PoolAccount) {
  if (row.proxy_enabled && !row.pool_ready) return '已入池·未就绪'
  if (!row.pool_ready) return '未就绪'
  return '就绪'
}

function poolReadyTooltip(row: PoolAccount) {
  if (row.proxy_enabled && !row.pool_ready) {
    return row.pool_ready_reason
      ? `开关仍开启，但不会进入轮换：${row.pool_ready_reason}`
      : '开关仍开启，但不会进入轮换'
  }
  return row.pool_ready_reason
}

function poolReadyType(row: PoolAccount) {
  if (row.proxy_enabled && !row.pool_ready) return 'warning'
  if (!row.pool_ready) return 'danger'
  return 'success'
}

async function load() {
  loading.value = true
  try {
    const res = await client.get('/api/v2/proxy-pool/accounts')
    pool.value = res.data
  } catch {
    ElMessage.error('入池账号加载失败')
  } finally {
    loading.value = false
  }
}

async function toggleAccount(row: PoolAccount, val: boolean) {
  try {
    await client.post(`/api/v2/proxy-pool/accounts/${row.id}`, { proxy_enabled: val })
    row.proxy_enabled = val
  } catch (err: any) {
    const detail = err?.response?.data?.detail
    ElMessage.error(typeof detail === 'string' ? detail : '操作失败')
  }
}

onMounted(load)

defineExpose({ load })
</script>

<style scoped>
.panel-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 12px;
}
.hint {
  margin: 0;
  flex: 1;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  line-height: 1.55;
}
.account-sub {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin-top: 2px;
}
</style>
