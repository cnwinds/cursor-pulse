<template>
  <div class="capabilities-page" v-loading="loading">
    <header class="page-header">
      <div>
        <h2>工具授权</h2>
        <p class="desc">
          此处只控制谁能调用 Capability（Tool），与技能卡片说明书无关。
        </p>
      </div>
      <el-button @click="reloadActiveTab">刷新</el-button>
    </header>

    <el-tabs v-model="activeTab" @tab-change="onTabChange">
      <el-tab-pane label="能力目录" name="catalog">
        <el-table :data="catalog" stripe>
          <el-table-column prop="key" label="能力 Key" min-width="180" />
          <el-table-column prop="display_name" label="名称" min-width="140" />
          <el-table-column prop="version" label="版本" width="80" />
          <el-table-column prop="risk_level" label="风险" width="100" />
          <el-table-column prop="version_status" label="状态" width="100" />
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="分配" name="assignments">
        <el-alert
          type="info"
          :closable="false"
          show-icon
          class="assign-hint"
          title="分配说明"
          description="全员默认 = 所有成员基础能力；角色能力包 = 按 owner/operator 等角色追加；成员额外允许/禁止 = 针对个人的例外。保存后可在「解析预览」验证某成员最终可用能力。"
        />
        <div class="assign-toolbar">
          <el-button
            v-if="canWrite"
            type="primary"
            @click="openAssignmentDialog"
          >
            新增分配
          </el-button>
        </div>
        <el-table :data="assignments" stripe>
          <el-table-column label="范围类型" width="140">
            <template #default="{ row }">
              {{ scopeTypeLabel(row.scope_type) }}
            </template>
          </el-table-column>
          <el-table-column label="范围" min-width="180">
            <template #default="{ row }">
              {{ formatScopeTarget(row) }}
            </template>
          </el-table-column>
          <el-table-column label="授予内容" min-width="220">
            <template #default="{ row }">
              {{ formatGrantTarget(row) }}
            </template>
          </el-table-column>
          <el-table-column label="创建时间" width="180">
            <template #default="{ row }">{{ formatChinaTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column v-if="canWrite" label="操作" width="100" fixed="right">
            <template #default="{ row }">
              <el-button link type="danger" @click="removeAssignment(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="解析预览" name="resolved">
        <el-form :inline="true" class="resolved-form">
          <el-form-item label="成员">
            <el-select
              v-model="resolvedMemberId"
              filterable
              clearable
              placeholder="选择成员"
              style="width: 280px"
            >
              <el-option
                v-for="member in members"
                :key="member.id"
                :label="`${member.display_name}（${member.id.slice(0, 8)}…）`"
                :value="member.id"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="角色">
            <el-select v-model="resolvedRole" clearable placeholder="可选" style="width: 180px">
              <el-option
                v-for="role in roleOptions"
                :key="role.value"
                :label="role.label"
                :value="role.value"
              />
            </el-select>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="loadResolved">查询</el-button>
          </el-form-item>
        </el-form>
        <el-table :data="resolved" stripe>
          <el-table-column prop="key" label="能力 Key" min-width="180" />
          <el-table-column prop="version" label="版本" width="80" />
          <el-table-column prop="risk_level" label="风险" width="100" />
          <el-table-column prop="display_name" label="名称" min-width="140" />
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="assignmentDialog" title="新增分配" width="560px" @closed="resetAssignmentForm">
      <el-form label-width="108px">
        <el-form-item label="范围类型">
          <el-select v-model="assignmentForm.scope_type" style="width: 100%" @change="onScopeTypeChange">
            <el-option
              v-for="item in scopeTypeOptions"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
          <div class="field-hint">{{ currentScopeHint }}</div>
        </el-form-item>

        <el-form-item v-if="assignmentForm.scope_type === 'role_pack'" label="角色">
          <el-select v-model="assignmentForm.scope_id" style="width: 100%" placeholder="选择角色">
            <el-option
              v-for="role in roleOptions"
              :key="role.value"
              :label="role.label"
              :value="role.value"
            />
          </el-select>
        </el-form-item>

        <el-form-item
          v-else-if="assignmentForm.scope_type === 'user_allow' || assignmentForm.scope_type === 'user_deny'"
          label="成员"
        >
          <el-select
            v-model="assignmentForm.scope_id"
            filterable
            style="width: 100%"
            placeholder="选择成员"
          >
            <el-option
              v-for="member in members"
              :key="member.id"
              :label="`${member.display_name}（${member.id.slice(0, 8)}…）`"
              :value="member.id"
            />
          </el-select>
        </el-form-item>

        <el-form-item v-else-if="assignmentForm.scope_type === 'team_default'" label="范围">
          <span class="muted">全员，无需选择</span>
        </el-form-item>

        <el-form-item label="授予方式">
          <el-radio-group v-model="assignmentForm.target_type">
            <el-radio value="pack">能力包（推荐）</el-radio>
            <el-radio value="capability">单个能力</el-radio>
          </el-radio-group>
        </el-form-item>

        <el-form-item v-if="assignmentForm.target_type === 'pack'" label="能力包">
          <el-select v-model="assignmentForm.pack_id" filterable style="width: 100%" placeholder="选择能力包">
            <el-option
              v-for="pack in packs"
              :key="pack.id"
              :label="`${pack.display_name}（${pack.key}）`"
              :value="pack.id"
            />
          </el-select>
          <div v-if="selectedPack" class="field-hint">
            包含 {{ selectedPack.capability_keys.length }} 项：
            {{ selectedPack.capability_keys.slice(0, 4).join('、') }}
            <template v-if="selectedPack.capability_keys.length > 4">…</template>
          </div>
        </el-form-item>

        <el-form-item v-else label="能力">
          <el-select
            v-model="assignmentForm.capability_key"
            filterable
            style="width: 100%"
            placeholder="选择能力"
          >
            <el-option
              v-for="item in catalogOptions"
              :key="item.key"
              :label="`${item.display_name}（${item.key}）`"
              :value="item.key"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="assignmentDialog = false">取消</el-button>
        <el-button type="primary" @click="createAssignment">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import client from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { formatChinaTime } from '@/utils/time'

interface MemberOption {
  id: string
  display_name: string
}

interface PackOption {
  id: string
  key: string
  display_name: string
  capability_keys: string[]
}

interface CatalogItem {
  key: string
  display_name: string
}

const auth = useAuthStore()
const loading = ref(false)
const activeTab = ref('catalog')
const catalog = ref<CatalogItem[]>([])
const packs = ref<PackOption[]>([])
const members = ref<MemberOption[]>([])
const assignments = ref<any[]>([])
const resolved = ref<any[]>([])
const resolvedMemberId = ref('')
const resolvedRole = ref('')
const assignmentDialog = ref(false)

const canWrite = computed(() => auth.hasPermission('assistant:capabilities:write'))

const scopeTypeOptions = [
  { value: 'team_default', label: '全员默认', hint: '团队所有成员都会继承的基础能力包。' },
  { value: 'role_pack', label: '角色能力包', hint: '按后台角色（owner/operator 等）追加能力。' },
  { value: 'user_allow', label: '成员额外允许', hint: '为某个成员单独开放一项能力或能力包。' },
  { value: 'user_deny', label: '成员禁止', hint: '禁止某个成员使用某项能力（优先级高于允许）。' },
]

const roleOptions = [
  { value: 'owner', label: 'owner（超级管理员）' },
  { value: 'operator', label: 'operator（运营）' },
  { value: 'auditor', label: 'auditor（审计，只读后台）' },
  { value: 'ai_member', label: 'ai_member（普通成员）' },
]

const assignmentForm = reactive({
  scope_type: 'role_pack',
  scope_id: '',
  target_type: 'pack' as 'pack' | 'capability',
  capability_key: '',
  pack_id: '',
})

const catalogOptions = computed(() => {
  const seen = new Set<string>()
  return catalog.value.filter((item) => {
    if (seen.has(item.key)) return false
    seen.add(item.key)
    return true
  })
})

const currentScopeHint = computed(() => {
  return scopeTypeOptions.find((item) => item.value === assignmentForm.scope_type)?.hint || ''
})

const selectedPack = computed(() => packs.value.find((pack) => pack.id === assignmentForm.pack_id))

const packById = computed(() => Object.fromEntries(packs.value.map((pack) => [pack.id, pack])))
const catalogByKey = computed(() => Object.fromEntries(catalog.value.map((item) => [item.key, item])))
const memberById = computed(() => Object.fromEntries(members.value.map((member) => [member.id, member])))
const roleLabelByValue = computed(() => Object.fromEntries(roleOptions.map((role) => [role.value, role.label])))

function scopeTypeLabel(value: string) {
  return scopeTypeOptions.find((item) => item.value === value)?.label || value
}

function formatScopeTarget(row: { scope_type: string; scope_id: string }) {
  if (row.scope_type === 'team_default') return '全员'
  if (row.scope_type === 'role_pack') {
    return roleLabelByValue.value[row.scope_id] || row.scope_id
  }
  const member = memberById.value[row.scope_id]
  if (member) return `${member.display_name}`
  return row.scope_id || '—'
}

function formatGrantTarget(row: { pack_id?: string | null; capability_key?: string | null }) {
  if (row.pack_id) {
    const pack = packById.value[row.pack_id]
    return pack ? `${pack.display_name}（${pack.key}）` : row.pack_id
  }
  if (row.capability_key) {
    const cap = catalogByKey.value[row.capability_key]
    return cap ? `${cap.display_name}（${row.capability_key}）` : row.capability_key
  }
  return '—'
}

function onScopeTypeChange() {
  assignmentForm.scope_id = ''
}

function resetAssignmentForm() {
  assignmentForm.scope_type = 'role_pack'
  assignmentForm.scope_id = ''
  assignmentForm.target_type = 'pack'
  assignmentForm.capability_key = ''
  assignmentForm.pack_id = ''
}

function openAssignmentDialog() {
  resetAssignmentForm()
  assignmentDialog.value = true
}

async function loadCatalog() {
  const { data } = await client.get('/api/v2/assistant/capabilities/catalog')
  catalog.value = data
}

async function loadPacks() {
  const { data } = await client.get('/api/v2/assistant/capabilities/packs')
  packs.value = data
}

async function loadMembers() {
  const { data } = await client.get('/api/v2/members')
  members.value = data
}

async function loadAssignments() {
  const { data } = await client.get('/api/v2/assistant/capabilities/assignments')
  assignments.value = data
}

async function loadResolved() {
  if (!resolvedMemberId.value.trim()) {
    ElMessage.warning('请选择成员')
    return
  }
  const params: Record<string, string> = {}
  if (resolvedRole.value) params.role = resolvedRole.value
  const { data } = await client.get(
    `/api/v2/assistant/capabilities/members/${resolvedMemberId.value.trim()}/resolved`,
    { params },
  )
  resolved.value = data
}

async function createAssignment() {
  if (assignmentForm.scope_type !== 'team_default' && !assignmentForm.scope_id) {
    ElMessage.warning('请选择范围')
    return
  }
  if (assignmentForm.target_type === 'pack' && !assignmentForm.pack_id) {
    ElMessage.warning('请选择能力包')
    return
  }
  if (assignmentForm.target_type === 'capability' && !assignmentForm.capability_key) {
    ElMessage.warning('请选择能力')
    return
  }

  const body: Record<string, string> = {
    scope_type: assignmentForm.scope_type,
    scope_id: assignmentForm.scope_type === 'team_default' ? '' : assignmentForm.scope_id,
  }
  if (assignmentForm.target_type === 'pack') {
    body.pack_id = assignmentForm.pack_id
  } else {
    body.capability_key = assignmentForm.capability_key
  }

  try {
    await client.post('/api/v2/assistant/capabilities/assignments', body)
    ElMessage.success('已创建分配')
    assignmentDialog.value = false
    await loadAssignments()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '创建失败')
  }
}

async function removeAssignment(row: { id: string }) {
  await ElMessageBox.confirm('确定删除该分配？', '确认', { type: 'warning' })
  try {
    await client.delete(`/api/v2/assistant/capabilities/assignments/${row.id}`)
    ElMessage.success('已删除')
    await loadAssignments()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '删除失败')
  }
}

async function reloadActiveTab() {
  loading.value = true
  try {
    if (activeTab.value === 'catalog') await loadCatalog()
    else if (activeTab.value === 'assignments') {
      await Promise.all([loadAssignments(), loadPacks(), loadMembers(), loadCatalog()])
    } else if (activeTab.value === 'resolved' && resolvedMemberId.value) await loadResolved()
  } catch (err: any) {
    ElMessage.error(err.response?.data?.detail || err.message || '加载失败')
  } finally {
    loading.value = false
  }
}

function onTabChange(name: string | number) {
  if (name === 'catalog' && !catalog.value.length) void reloadActiveTab()
  if (name === 'assignments' && !assignments.value.length) void reloadActiveTab()
  if (name === 'resolved' && !members.value.length) {
    void loadMembers().catch(() => {})
  }
}

onMounted(async () => {
  loading.value = true
  try {
    await Promise.all([loadCatalog(), loadMembers()])
  } catch (err: any) {
    ElMessage.error(err.response?.data?.detail || err.message || '加载失败')
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 16px;
}
.desc {
  color: #64748b;
  font-size: 14px;
  margin-top: 4px;
  max-width: 720px;
  line-height: 1.5;
}
.assign-hint {
  margin-bottom: 12px;
}
.assign-toolbar {
  margin-bottom: 12px;
}
.resolved-form {
  margin-bottom: 16px;
}
.field-hint {
  margin-top: 6px;
  font-size: 12px;
  color: #94a3b8;
  line-height: 1.4;
}
.muted {
  color: #94a3b8;
  font-size: 13px;
}
</style>
