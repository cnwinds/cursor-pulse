<template>
  <el-drawer
    v-model="open"
    title="Jev Decisions 报文"
    direction="rtl"
    size="min(820px, 96vw)"
    class="jev-trace-drawer"
  >
    <div v-if="!trace" class="jev-trace-empty">暂无 Jev 追踪数据。请刷新打分表或确认 Jev 已启用。</div>
    <template v-else>
      <div class="jev-trace-meta">
        <el-tag :type="statusTagType" effect="plain">{{ statusLabel }}</el-tag>
        <el-tag v-if="trace.meta.force_refresh" type="warning" effect="plain">强制外呼</el-tag>
        <span v-if="trace.meta.model" class="meta-model">{{ trace.meta.model }}</span>
        <span v-if="trace.meta.error_message" class="meta-error">{{ trace.meta.error_message }}</span>
      </div>

      <el-tabs v-model="tab" class="jev-trace-tabs">
        <el-tab-pane label="摘要" name="summary">
          <JevTraceSummary :trace="trace" :lookup="accountLookup" />
        </el-tab-pane>
        <el-tab-pane label="请求" name="request">
          <div v-if="!trace.input" class="jev-trace-empty">本次未构造请求（Jev 未启用或无可问候选）。</div>
          <template v-else>
            <p class="section-hint">state 与 questions 即发往 OpenRouter <code>/alpha/decisions</code> 的内容（state 在 wire 上为 JSON 字符串）。</p>
            <JevStateCandidates
              v-if="stateCandidates.length"
              :rows="stateCandidates"
              :lookup="accountLookup"
            />
            <JevQuestionsList :questions="trace.input.questions" :lookup="accountLookup" />
            <details class="json-block">
              <summary>完整 input JSON</summary>
              <pre class="json-pre">{{ formatJson(trace.input) }}</pre>
            </details>
          </template>
        </el-tab-pane>
        <el-tab-pane label="响应" name="response">
          <div v-if="!trace.output" class="jev-trace-empty">
            {{ responseEmptyHint }}
          </div>
          <template v-else>
            <JevAnswersTable :answers="trace.output.answers || {}" :lookup="accountLookup" />
            <details class="json-block">
              <summary>完整 output JSON</summary>
              <pre class="json-pre">{{ formatJson(trace.output) }}</pre>
            </details>
          </template>
        </el-tab-pane>
      </el-tabs>
    </template>
  </el-drawer>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { JevTrace, JevTraceAccountLookup } from './jevTraceTypes'
import JevTraceSummary from './JevTraceSummary.vue'
import JevStateCandidates from './JevStateCandidates.vue'
import JevQuestionsList from './JevQuestionsList.vue'
import JevAnswersTable from './JevAnswersTable.vue'

const props = defineProps<{
  modelValue: boolean
  trace: JevTrace | null | undefined
  accounts?: JevTraceAccountLookup[]
}>()

const emit = defineEmits<{ 'update:modelValue': [boolean] }>()

const open = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

const tab = ref<'summary' | 'request' | 'response'>('summary')

watch(
  () => props.modelValue,
  (visible) => {
    if (visible) tab.value = 'summary'
  },
)

const accountLookup = computed(() => {
  const map = new Map<string, JevTraceAccountLookup>()
  for (const row of props.accounts || []) {
    map.set(row.account_id, row)
  }
  return map
})

const STATUS_LABELS: Record<string, string> = {
  called: '已外呼',
  cached: '缓存命中',
  skipped: '未外呼',
}

const SKIP_LABELS: Record<string, string> = {
  jev_unavailable: 'Jev 未启用或未配置 Key',
  insufficient_candidates: '存活候选不足 2 个',
  circuit_open: '熔断中',
  jev_error: '调用失败',
}

const statusLabel = computed(() => {
  const t = props.trace
  if (!t) return ''
  const base = STATUS_LABELS[t.meta.status] || t.meta.status
  if (t.meta.status === 'skipped' && t.meta.skip_reason) {
    return `${base} · ${SKIP_LABELS[t.meta.skip_reason] || t.meta.skip_reason}`
  }
  return base
})

const statusTagType = computed(() => {
  const s = props.trace?.meta.status
  if (s === 'called') return 'success'
  if (s === 'cached') return 'info'
  return 'warning'
})

const stateCandidates = computed(() => {
  const c = props.trace?.input?.state?.candidates
  return Array.isArray(c) ? (c as Record<string, unknown>[]) : []
})

const responseEmptyHint = computed(() => {
  const t = props.trace
  if (!t) return ''
  if (t.meta.status === 'cached') return '缓存命中但未保存响应快照（旧缓存）。请等待 TTL 过期或变更候选特征后重试。'
  if (t.meta.skip_reason === 'jev_error') return '调用失败，无响应体。'
  if (t.meta.skip_reason === 'circuit_open') return '熔断期间未发起请求。'
  return '无响应数据。'
})

function formatJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}
</script>

<style scoped>
.jev-trace-empty {
  color: var(--el-text-color-secondary);
  font-size: 14px;
  padding: 8px 0;
}
.jev-trace-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-bottom: 16px;
}
.meta-model {
  font-size: 13px;
  color: var(--el-text-color-regular);
}
.meta-error {
  font-size: 13px;
  color: var(--el-color-danger);
  flex: 1 1 100%;
}
.section-hint {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  margin: 0 0 12px;
  line-height: 1.5;
}
.section-hint code {
  font-size: 12px;
}
.json-block {
  margin-top: 16px;
}
.json-block summary {
  cursor: pointer;
  font-size: 13px;
  color: var(--el-color-primary);
  margin-bottom: 8px;
}
.json-pre {
  margin: 0;
  padding: 12px;
  background: #0f172a;
  color: #e2e8f0;
  border-radius: 8px;
  font-size: 12px;
  line-height: 1.45;
  overflow: auto;
  max-height: 420px;
}
</style>
