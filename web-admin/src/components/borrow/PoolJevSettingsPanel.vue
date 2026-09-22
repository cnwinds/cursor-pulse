<template>
  <div class="jev-panel" v-loading="loading">
    <div class="panel-card">
      <header class="panel-head">
        <div>
          <p class="eyebrow">Auto Lender · Jev</p>
          <p class="lead">
            开启后，Jev 对通过硬过滤的池内候选重排（见「选号规则」中的 Top-N 与护栏）。
            调用失败、置信度不足或护栏不通过时自动回落算法分；不在每次代理请求上调用。
          </p>
        </div>
      </header>
      <SettingsSectionForm
        v-if="loaded"
        :model="jevModel"
        :fields="fields"
        notice="保存后下一次打分表刷新与自动分配选号即生效。需 OpenRouter 预充值额度。"
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
  --jev-accent: #0d9488;
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
.panel-head {
  margin-bottom: 8px;
}
.eyebrow {
  margin: 0 0 6px;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--jev-accent);
}
.lead {
  margin: 0 0 16px;
  font-size: 14px;
  line-height: 1.6;
  color: #334155;
}
</style>
