<template>
  <div>
    <el-alert
      v-if="notice"
      :title="notice"
      type="info"
      :closable="false"
      show-icon
      class="notice"
    />
    <el-form label-width="180px" @submit.prevent="onSave">
      <template v-for="field in visibleFields" :key="field.key">
        <el-divider v-if="field.type === 'divider'" content-position="left">
          {{ field.label }}
        </el-divider>
        <el-form-item v-else :label="field.label">
          <el-switch
            v-if="field.type === 'switch'"
            v-model="local[field.key]"
            :disabled="field.readonly"
          />
          <el-input
            v-else-if="field.type === 'textarea'"
            v-model="local[field.key]"
            type="textarea"
            :rows="3"
            :disabled="field.readonly"
          />
          <el-input-number
            v-else-if="field.type === 'number'"
            v-model="local[field.key]"
            :disabled="field.readonly"
            :min="field.min"
            :max="field.max"
            :step="field.step ?? 1"
            :precision="field.precision"
            style="width: 100%"
          />
          <el-select
            v-else-if="field.type === 'select'"
            v-model="local[field.key]"
            :disabled="field.readonly"
            style="width: 100%"
          >
            <el-option
              v-for="opt in field.options || []"
              :key="String(opt.value)"
              :label="opt.label"
              :value="opt.value"
            />
          </el-select>
          <el-input
            v-else-if="field.type === 'secret'"
            v-model="local[field.key]"
            :type="secretVisible[field.key] ? 'text' : 'password'"
            :placeholder="isSecretMasked(local[field.key]) ? '已配置' : ''"
            :disabled="field.readonly"
            class="secret-input"
          >
            <template v-if="!field.readonly" #suffix>
              <el-icon
                class="secret-eye"
                :class="{ 'is-loading': secretLoading[field.key] }"
                @click="toggleSecret(field)"
              >
                <View v-if="!secretVisible[field.key]" />
                <Hide v-else />
              </el-icon>
            </template>
          </el-input>
          <el-input v-else v-model="local[field.key]" :disabled="field.readonly" />
          <div v-if="field.hint" class="field-hint">{{ field.hint }}</div>
        </el-form-item>
      </template>
      <el-form-item v-if="canWrite && !readOnly">
        <el-button type="primary" native-type="submit">{{ saveLabel }}</el-button>
      </el-form-item>
      <el-alert
        v-else-if="readOnly"
        type="info"
        :closable="false"
        title="此项通过环境变量配置，后台仅展示状态"
      />
      <el-alert
        v-else
        type="info"
        :closable="false"
        title="当前账号无 settings:write 权限，仅可查看"
      />
    </el-form>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, watch } from 'vue'
import { Hide, View } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import client from '@/api/client'
import { useAuthStore } from '@/stores/auth'

export type SettingsField = {
  key: string
  label: string
  type?: 'switch' | 'textarea' | 'number' | 'divider' | 'select' | 'secret'
  hint?: string
  readonly?: boolean
  secretSection?: string
  showWhen?: (model: Record<string, unknown>) => boolean
  options?: Array<{ value: string | number; label: string }>
  min?: number
  max?: number
  step?: number
  precision?: number
}

const props = withDefaults(
  defineProps<{
    model: Record<string, unknown>
    fields: SettingsField[]
    notice?: string
    readOnly?: boolean
    saveLabel?: string
  }>(),
  {
    notice: '',
    readOnly: false,
    saveLabel: '保存本节',
  },
)

const emit = defineEmits<{ save: [Record<string, unknown>] }>()
const auth = useAuthStore()
const canWrite = auth.hasPermission('settings:write')
const local = reactive<Record<string, unknown>>({})
const secretVisible = reactive<Record<string, boolean>>({})
const secretLoading = reactive<Record<string, boolean>>({})
const secretLoaded = reactive<Record<string, boolean>>({})

const visibleFields = computed(() =>
  props.fields.filter((field) => !field.showWhen || field.showWhen(local)),
)

function isSecretMasked(value: unknown): boolean {
  return value === '***' || value === '' || value == null
}

function secretSection(field: SettingsField): string {
  return field.secretSection || ''
}

async function fetchSecretValue(field: SettingsField): Promise<string> {
  const section = secretSection(field)
  if (!section) throw new Error('缺少 secretSection')
  const res = await client.get(`/api/settings/${section}/reveal/${field.key}`)
  return String(res.data?.value ?? '')
}

async function ensureSecretLoaded(field: SettingsField): Promise<string> {
  if (secretLoaded[field.key] && !isSecretMasked(local[field.key])) {
    return String(local[field.key] ?? '')
  }
  secretLoading[field.key] = true
  try {
    const value = await fetchSecretValue(field)
    local[field.key] = value
    secretLoaded[field.key] = true
    return value
  } finally {
    secretLoading[field.key] = false
  }
}

async function toggleSecret(field: SettingsField) {
  if (secretVisible[field.key]) {
    secretVisible[field.key] = false
    return
  }
  try {
    await ensureSecretLoaded(field)
    secretVisible[field.key] = true
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '无法查看密钥')
  }
}

watch(
  () => props.model,
  (m) => {
    Object.assign(local, m)
    for (const key of Object.keys(secretVisible)) {
      secretVisible[key] = false
      secretLoaded[key] = false
    }
  },
  { immediate: true, deep: true },
)

function onSave() {
  if (!canWrite || props.readOnly) return
  const patch: Record<string, unknown> = {}
  for (const field of visibleFields.value) {
    if (field.type === 'divider') continue
    patch[field.key] = local[field.key]
  }
  emit('save', patch)
}
</script>

<style scoped>
.notice {
  margin-bottom: 16px;
}
.field-hint {
  margin-top: 4px;
  font-size: 12px;
  color: #94a3b8;
  line-height: 1.4;
}
.secret-input {
  width: 100%;
}
.secret-eye {
  cursor: pointer;
  color: #94a3b8;
}
.secret-eye:hover {
  color: #64748b;
}
.secret-eye.is-loading {
  pointer-events: none;
  opacity: 0.5;
}
</style>
