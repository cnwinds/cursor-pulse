<template>
  <div class="jev-panel" v-loading="loading">
    <div class="panel-card">
      <SettingsSectionForm
        v-if="loaded"
        :model="jevModel"
        :fields="fields"
        save-label="保存 Jev 配置"
        @save="onSave"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useSettingsStore } from '@/stores/settings'
import SettingsSectionForm, { type SettingsField } from '@/components/SettingsSectionForm.vue'

const store = useSettingsStore()
const loading = ref(false)
const loaded = ref(false)
const jevModel = ref<Record<string, unknown>>({})

const fields = computed((): SettingsField[] => {
  const enabled = jevModel.value.enabled === true
  return [
    {
      key: 'enabled',
      label: '启用 Jev 主判',
      type: 'switch',
      hint: '关闭时打分表与自动分配仅走确定性算法分',
    },
    {
      key: 'base_url',
      label: 'OpenRouter Base URL',
      hint: 'Decisions 端点拼为 {base_url}/alpha/decisions',
      showWhen: () => enabled,
    },
    {
      key: 'api_key',
      label: 'OpenRouter API Key',
      type: 'secret',
      secretSection: 'jev',
      hint: '留空或 *** 表示不修改',
      showWhen: () => enabled,
    },
    {
      key: 'model',
      label: '模型',
      hint: '如 typesafe/jev-1.13',
      showWhen: () => enabled,
    },
    {
      key: 'timeout_seconds',
      label: '超时（秒）',
      type: 'number',
      min: 0.5,
      step: 0.5,
      hint: '超时即回落算法分；端到端通常 70–500ms',
      showWhen: () => enabled,
    },
  ]
})

async function load() {
  loading.value = true
  try {
    const data = await store.load()
    jevModel.value = { ...(data.jev || {}) }
    loaded.value = true
  } finally {
    loading.value = false
  }
}

async function onSave(patch: Record<string, unknown>) {
  const cleaned = { ...patch }
  if (cleaned.api_key === '***') delete cleaned.api_key
  if (cleaned.api_key === '') delete cleaned.api_key
  try {
    const data = await store.patchSection('jev', cleaned)
    jevModel.value = { ...(data.jev || cleaned) }
    ElMessage.success('已保存')
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '保存失败')
  }
}

onMounted(load)
</script>

<style scoped>
.jev-panel {
  font-family: 'DM Sans', 'Segoe UI', system-ui, sans-serif;
}
.panel-card {
  background: linear-gradient(165deg, #f8fafc 0%, #f1f5f9 42%, #ffffff 100%);
  border: 1px solid rgba(15, 23, 42, 0.08);
  border-radius: 14px;
  padding: 20px 24px 8px;
  box-shadow:
    0 1px 2px rgba(15, 23, 42, 0.04),
    0 12px 40px rgba(15, 23, 42, 0.06);
  max-width: 640px;
}
</style>
