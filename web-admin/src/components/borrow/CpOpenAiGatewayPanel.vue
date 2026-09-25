<template>
  <div class="cp-openai-panel" v-loading="loading">
    <p class="hint">
      Coding Plan 专用 OpenAI 网关（与 Cursor MITM 同进程、不同路径）。客户端
      <code>base_url</code> 指向 Go 代理地址下的 <code>/openai/v1</code>（默认
      <code>http://127.0.0.1:8317/openai/v1</code>），API Key 使用签发的
      <code>pkcp_</code>。入池账号按额度压力调度，429 时自动换号重试。
    </p>
    <el-tabs v-model="vendorTab" class="vendor-tabs" @tab-change="onVendorChange">
      <el-tab-pane label="GLM" name="glm" />
      <el-tab-pane label="MiniMax" name="minimax" />
      <el-tab-pane label="Kimi" name="kimi" />
    </el-tabs>

    <div class="section">
      <div class="section-head">
        <h3>入池账号</h3>
        <el-button size="small" @click="loadAccounts">刷新</el-button>
      </div>
      <el-table :data="accounts" stripe size="small">
        <el-table-column label="账号" prop="account_identifier" min-width="160" />
        <el-table-column label="区域" prop="api_region" width="100" />
        <el-table-column label="额度压力" width="100">
          <template #default="{ row }">{{ row.tier_pressure_pct?.toFixed?.(1) ?? row.tier_pressure_pct }}%</template>
        </el-table-column>
        <el-table-column label="就绪" width="100">
          <template #default="{ row }">
            <el-tag :type="row.pool_ready ? 'success' : 'danger'" size="small">
              {{ row.pool_ready ? '就绪' : '未就绪' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="入池" width="88" align="center">
          <template #default="{ row }">
            <el-switch
              :model-value="row.cp_proxy_enabled"
              :disabled="!canWrite || (!row.cp_proxy_enabled && !row.pool_ready)"
              @change="(v: boolean) => togglePool(row, v)"
            />
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div class="section">
      <div class="section-head">
        <h3>pkcp_ 接入密钥</h3>
        <el-button v-if="canWrite" type="primary" size="small" @click="openCreateKey">签发密钥</el-button>
      </div>
      <el-table :data="keys" stripe size="small">
        <el-table-column label="名称" prop="name" min-width="120" />
        <el-table-column label="厂家" prop="coding_plan_vendor" width="88" />
        <el-table-column label="归属" prop="member_name" width="100" />
        <el-table-column label="Hint" prop="key_hint" width="120" />
        <el-table-column label="状态" prop="status" width="88" />
        <el-table-column label="Tokens" prop="total_tokens" width="88" />
      </el-table>
      <p v-if="openaiBase" class="base-url">OpenAI Base URL：<code>{{ openaiBase }}</code></p>
    </div>

    <el-dialog v-model="createVisible" title="签发 Coding Plan OpenAI 密钥" width="480px">
      <el-form label-width="100px">
        <el-form-item label="厂家">
          <el-select v-model="createForm.coding_plan_vendor" style="width: 100%">
            <el-option label="GLM" value="glm" />
            <el-option label="MiniMax" value="minimax" />
            <el-option label="Kimi" value="kimi" />
          </el-select>
        </el-form-item>
        <el-form-item label="归属成员">
          <el-select v-model="createForm.member_id" filterable style="width: 100%">
            <el-option v-for="m in members" :key="m.id" :label="m.display_name" :value="m.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="名称">
          <el-input v-model="createForm.name" placeholder="可选" />
        </el-form-item>
      </el-form>
      <template v-if="createdKey">
        <el-alert type="success" :closable="false" show-icon title="请立即保存密钥（仅显示一次）" />
        <el-input class="key-block" :model-value="createdKey.plaintext_key" readonly />
        <p class="base-url"><code>{{ createdKey.openai_base_url }}</code></p>
      </template>
      <template #footer>
        <el-button @click="createVisible = false">关闭</el-button>
        <el-button v-if="!createdKey" type="primary" :loading="creating" @click="submitCreate">签发</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import client from '@/api/client'
import { useAuthStore } from '@/stores/auth'

interface CpAccount {
  id: string
  account_identifier: string
  api_region: string | null
  cp_proxy_enabled: boolean
  pool_ready: boolean
  pool_ready_reason: string | null
  tier_pressure_pct: number
}

interface CpKey {
  id: string
  name: string
  coding_plan_vendor: string | null
  member_name: string | null
  key_hint: string
  status: string
  total_tokens: number
}

interface MemberOption {
  id: string
  display_name: string
}

const auth = useAuthStore()
const canWrite = computed(() => auth.hasPermission('proxy:write'))
const loading = ref(false)
const vendorTab = ref('glm')
const accounts = ref<CpAccount[]>([])
const keys = ref<CpKey[]>([])
const openaiBase = ref('')
const members = ref<MemberOption[]>([])

const createVisible = ref(false)
const creating = ref(false)
const createdKey = ref<{ plaintext_key: string; openai_base_url: string } | null>(null)
const createForm = reactive({
  coding_plan_vendor: 'glm',
  member_id: '',
  name: '',
})

watch(vendorTab, (v) => {
  createForm.coding_plan_vendor = v
})

async function loadMembers() {
  const res = await client.get('/api/v2/members')
  members.value = res.data
}

async function loadAccounts() {
  loading.value = true
  try {
    const res = await client.get('/api/v2/openai-proxy/accounts', { params: { vendor: vendorTab.value } })
    accounts.value = res.data
  } catch {
    ElMessage.error('入池账号加载失败')
  } finally {
    loading.value = false
  }
}

async function loadKeys() {
  try {
    const res = await client.get('/api/v2/openai-proxy/keys')
    keys.value = res.data.filter(
      (k: CpKey & { coding_plan_vendor?: string }) => k.coding_plan_vendor === vendorTab.value,
    )
    if (res.data.length && res.data[0].openai_base_url) {
      openaiBase.value = res.data[0].openai_base_url
    }
  } catch {
    ElMessage.error('密钥列表加载失败')
  }
}

async function loadAll() {
  await Promise.all([loadAccounts(), loadKeys()])
}

function onVendorChange() {
  void loadAll()
}

async function togglePool(row: CpAccount, val: boolean) {
  try {
    await client.post(`/api/v2/openai-proxy/accounts/${row.id}`, { cp_proxy_enabled: val })
    row.cp_proxy_enabled = val
  } catch (err: unknown) {
    const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    ElMessage.error(typeof detail === 'string' ? detail : '操作失败')
  }
}

function openCreateKey() {
  createdKey.value = null
  createForm.coding_plan_vendor = vendorTab.value
  createForm.member_id = members.value[0]?.id || ''
  createForm.name = ''
  createVisible.value = true
}

async function submitCreate() {
  if (!createForm.member_id) {
    ElMessage.warning('请选择归属成员')
    return
  }
  creating.value = true
  try {
    const res = await client.post('/api/v2/openai-proxy/keys', {
      member_id: createForm.member_id,
      coding_plan_vendor: createForm.coding_plan_vendor,
      name: createForm.name || undefined,
    })
    createdKey.value = {
      plaintext_key: res.data.plaintext_key,
      openai_base_url: res.data.openai_base_url,
    }
    openaiBase.value = res.data.openai_base_url
    await loadKeys()
  } catch (err: unknown) {
    const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    ElMessage.error(typeof detail === 'string' ? detail : '签发失败')
  } finally {
    creating.value = false
  }
}

onMounted(async () => {
  await loadMembers()
  await loadAll()
})

defineExpose({ load: loadAll })
</script>

<style scoped>
.hint {
  margin: 0 0 12px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  line-height: 1.55;
}
.section {
  margin-top: 16px;
}
.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.section-head h3 {
  margin: 0;
  font-size: 15px;
}
.base-url {
  margin: 8px 0 0;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
.key-block {
  margin-top: 12px;
}
</style>
