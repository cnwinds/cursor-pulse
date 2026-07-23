<template>
  <div v-loading="loading" class="pricing-settings">
    <el-alert type="info" :closable="false" show-icon class="notice">
      <template #title>
        仅用于 Cursor 返回 Included / 无官方金额时的本地估算；有 chargedCents 时仍用 Cursor 返回值。
        保存后下次同步生效，已落库明细不会自动重算。
      </template>
    </el-alert>

    <div class="toolbar">
      <div class="meta">
        <el-form inline>
          <el-form-item label="版本">
            <el-input v-model="draft.version" :disabled="!canWrite" style="width: 180px" />
          </el-form-item>
          <el-form-item label="来源">
            <el-tag :type="source === 'override' ? 'warning' : 'info'" size="small">
              {{ source === 'override' ? '团队覆盖' : '内置默认' }}
            </el-tag>
          </el-form-item>
          <el-form-item v-if="updatedAt" label="更新">
            <span class="muted">{{ updatedAt }}{{ updatedByName ? ` · ${updatedByName}` : '' }}</span>
          </el-form-item>
        </el-form>
      </div>
      <div class="actions">
        <el-button v-if="canWrite" @click="addRule">新增规则</el-button>
        <el-button v-if="canWrite" :loading="resetting" @click="onReset">恢复内置默认</el-button>
        <el-button v-if="canWrite" type="primary" :loading="saving" @click="onSave">保存</el-button>
      </div>
    </div>

    <h4 class="section-title">匹配规则（按顺序命中）</h4>
    <el-table :data="draft.rules" border size="small" class="pricing-table" table-layout="fixed">
      <el-table-column label="匹配模式" min-width="160">
        <template #default="{ row }">
          <el-input v-model="row.pattern" :disabled="!canWrite" placeholder="如 composer-*" />
        </template>
      </el-table-column>
      <el-table-column label="方式" width="128">
        <template #default="{ row }">
          <el-select v-model="row.match_type" :disabled="!canWrite" class="cell-select">
            <el-option label="exact" value="exact" />
            <el-option label="glob" value="glob" />
            <el-option label="contains" value="contains" />
          </el-select>
        </template>
      </el-table-column>
      <el-table-column label="池" width="108">
        <template #default="{ row }">
          <el-select v-model="row.pool" :disabled="!canWrite" class="cell-select">
            <el-option label="auto" value="auto" />
            <el-option label="api" value="api" />
          </el-select>
        </template>
      </el-table-column>
      <el-table-column label="Input" width="88" align="right">
        <template #default="{ row }">
          <el-input-number
            v-model="row.rates.input_no_cache"
            :disabled="!canWrite"
            :min="0"
            :step="0.1"
            :controls="false"
            class="rate-input"
          />
        </template>
      </el-table-column>
      <el-table-column label="Cache W" width="88" align="right">
        <template #default="{ row }">
          <el-input-number
            v-model="row.rates.input_cache_write"
            :disabled="!canWrite"
            :min="0"
            :step="0.1"
            :controls="false"
            class="rate-input"
          />
        </template>
      </el-table-column>
      <el-table-column label="Cache R" width="88" align="right">
        <template #default="{ row }">
          <el-input-number
            v-model="row.rates.cache_read"
            :disabled="!canWrite"
            :min="0"
            :step="0.1"
            :controls="false"
            class="rate-input"
          />
        </template>
      </el-table-column>
      <el-table-column label="Output" width="88" align="right">
        <template #default="{ row }">
          <el-input-number
            v-model="row.rates.output"
            :disabled="!canWrite"
            :min="0"
            :step="0.1"
            :controls="false"
            class="rate-input"
          />
        </template>
      </el-table-column>
      <el-table-column v-if="canWrite" label="操作" width="112" fixed="right" align="center">
        <template #default="{ $index }">
          <div class="row-actions">
            <el-tooltip content="上移" placement="top">
              <el-button
                :icon="Top"
                link
                :disabled="$index === 0"
                @click="moveRule($index, -1)"
              />
            </el-tooltip>
            <el-tooltip content="下移" placement="top">
              <el-button
                :icon="Bottom"
                link
                :disabled="$index >= draft.rules.length - 1"
                @click="moveRule($index, 1)"
              />
            </el-tooltip>
            <el-tooltip content="删除" placement="top">
              <el-button :icon="Delete" link type="danger" @click="removeRule($index)" />
            </el-tooltip>
          </div>
        </template>
      </el-table-column>
    </el-table>
    <p class="hint muted">单价单位：美元 / 百万 token</p>

    <h4 class="section-title">Fallback（未命中时）</h4>
    <el-table :data="fallbackRows" border size="small" class="pricing-table" table-layout="fixed">
      <el-table-column label="匹配模式" min-width="160">
        <template #default="{ row }">
          <el-input v-model="row.pattern" :disabled="!canWrite" />
        </template>
      </el-table-column>
      <el-table-column label="方式" width="128">
        <template #default="{ row }">
          <el-select v-model="row.match_type" :disabled="!canWrite" class="cell-select">
            <el-option label="exact" value="exact" />
            <el-option label="glob" value="glob" />
            <el-option label="contains" value="contains" />
          </el-select>
        </template>
      </el-table-column>
      <el-table-column label="池" width="108">
        <template #default="{ row }">
          <el-select v-model="row.pool" :disabled="!canWrite" class="cell-select">
            <el-option label="auto" value="auto" />
            <el-option label="api" value="api" />
          </el-select>
        </template>
      </el-table-column>
      <el-table-column label="Input" width="88" align="right">
        <template #default="{ row }">
          <el-input-number
            v-model="row.rates.input_no_cache"
            :disabled="!canWrite"
            :min="0"
            :step="0.1"
            :controls="false"
            class="rate-input"
          />
        </template>
      </el-table-column>
      <el-table-column label="Cache W" width="88" align="right">
        <template #default="{ row }">
          <el-input-number
            v-model="row.rates.input_cache_write"
            :disabled="!canWrite"
            :min="0"
            :step="0.1"
            :controls="false"
            class="rate-input"
          />
        </template>
      </el-table-column>
      <el-table-column label="Cache R" width="88" align="right">
        <template #default="{ row }">
          <el-input-number
            v-model="row.rates.cache_read"
            :disabled="!canWrite"
            :min="0"
            :step="0.1"
            :controls="false"
            class="rate-input"
          />
        </template>
      </el-table-column>
      <el-table-column label="Output" width="88" align="right">
        <template #default="{ row }">
          <el-input-number
            v-model="row.rates.output"
            :disabled="!canWrite"
            :min="0"
            :step="0.1"
            :controls="false"
            class="rate-input"
          />
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { Bottom, Delete, Top } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import client from '@/api/client'
import { useAuthStore } from '@/stores/auth'

type Rates = {
  input_no_cache: number
  input_cache_write: number
  cache_read: number
  output: number
  max_mode_multiplier?: number
}

type Rule = {
  pattern: string
  match_type: string
  pool: string
  rates: Rates
}

type PricingPayload = {
  vendor_slug: string
  version: string
  effective_from?: string | null
  rules: Rule[]
  fallback: Rule
  source?: string
  updated_at?: string | null
  updated_by?: { member_id: string; display_name?: string | null } | null
}

function emptyRates(): Rates {
  return {
    input_no_cache: 0,
    input_cache_write: 0,
    cache_read: 0,
    output: 0,
    max_mode_multiplier: 1,
  }
}

function emptyRule(): Rule {
  return {
    pattern: '',
    match_type: 'glob',
    pool: 'api',
    rates: emptyRates(),
  }
}

function cloneRule(rule: Rule | null | undefined): Rule {
  if (!rule) return emptyRule()
  return {
    pattern: rule.pattern || '',
    match_type: rule.match_type || 'glob',
    pool: rule.pool || 'api',
    rates: {
      input_no_cache: Number(rule.rates?.input_no_cache ?? 0),
      input_cache_write: Number(rule.rates?.input_cache_write ?? 0),
      cache_read: Number(rule.rates?.cache_read ?? 0),
      output: Number(rule.rates?.output ?? 0),
      max_mode_multiplier: Number(rule.rates?.max_mode_multiplier ?? 1),
    },
  }
}

const auth = useAuthStore()
const canWrite = computed(() => auth.hasPermission('settings:write'))

const loading = ref(false)
const saving = ref(false)
const resetting = ref(false)
const source = ref('builtin')
const updatedAt = ref<string | null>(null)
const updatedByName = ref<string | null>(null)

const draft = reactive<{
  vendor_slug: string
  version: string
  effective_from: string | null
  rules: Rule[]
  fallback: Rule
}>({
  vendor_slug: 'cursor',
  version: '',
  effective_from: null,
  rules: [],
  fallback: emptyRule(),
})

const fallbackRows = computed(() => [draft.fallback])

function applyPayload(data: PricingPayload) {
  draft.vendor_slug = data.vendor_slug || 'cursor'
  draft.version = data.version || ''
  draft.effective_from = data.effective_from || null
  draft.rules = (data.rules || []).map(cloneRule)
  draft.fallback = cloneRule(data.fallback)
  source.value = data.source || 'builtin'
  updatedAt.value = data.updated_at || null
  updatedByName.value = data.updated_by?.display_name || null
}

async function load() {
  loading.value = true
  try {
    const res = await client.get('/api/v2/pricing/cursor')
    applyPayload(res.data)
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '加载定价表失败')
  } finally {
    loading.value = false
  }
}

function addRule() {
  draft.rules.push(emptyRule())
}

function removeRule(index: number) {
  draft.rules.splice(index, 1)
}

function moveRule(index: number, delta: number) {
  const next = index + delta
  if (next < 0 || next >= draft.rules.length) return
  const tmp = draft.rules[index]
  draft.rules[index] = draft.rules[next]
  draft.rules[next] = tmp
}

async function onSave() {
  if (!draft.fallback?.pattern?.trim()) {
    ElMessage.warning('Fallback 匹配模式不能为空')
    return
  }
  for (const rule of draft.rules) {
    if (!rule.pattern?.trim()) {
      ElMessage.warning('存在空的匹配模式，请填写或删除')
      return
    }
  }
  saving.value = true
  try {
    const res = await client.put('/api/v2/pricing/cursor', {
      vendor_slug: draft.vendor_slug,
      version: draft.version,
      effective_from: draft.effective_from,
      rules: draft.rules,
      fallback: draft.fallback,
    })
    applyPayload(res.data)
    ElMessage.success('已保存，下次同步起生效')
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

async function onReset() {
  try {
    await ElMessageBox.confirm(
      '将删除团队覆盖，恢复为内置默认定价表。确定继续？',
      '恢复内置默认',
      { type: 'warning' },
    )
  } catch {
    return
  }
  resetting.value = true
  try {
    const res = await client.post('/api/v2/pricing/cursor/reset')
    applyPayload(res.data)
    ElMessage.success('已恢复内置默认')
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '恢复失败')
  } finally {
    resetting.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.pricing-settings {
  width: 100%;
}
.notice {
  margin-bottom: 16px;
}
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}
.actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}
.section-title {
  margin: 16px 0 8px;
  font-size: 14px;
  font-weight: 600;
}
.hint {
  margin: 6px 0 0;
  font-size: 12px;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.pricing-table {
  width: 100%;
}
.pricing-table :deep(.el-table__cell) {
  padding: 6px 8px;
  vertical-align: middle;
}
.cell-select {
  width: 100%;
}
.rate-input {
  width: 100%;
}
.pricing-table :deep(.rate-input .el-input__wrapper) {
  padding-left: 8px;
  padding-right: 8px;
}
.pricing-table :deep(.rate-input .el-input__inner) {
  text-align: right;
}
.row-actions {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 2px;
  white-space: nowrap;
}
.row-actions :deep(.el-button) {
  margin: 0;
  padding: 4px;
}
</style>
