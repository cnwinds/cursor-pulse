<template>
  <div class="loan-rules" :class="{ 'loan-rules--sidebar': layout === 'sidebar' }">
    <el-collapse v-if="layout === 'sidebar'" v-model="docOpen" class="rules-doc">
      <el-collapse-item title="规则说明（只读）" name="doc">
        <RulesDoc :selection="selection" :hours-text="hoursText" :num="num" />
      </el-collapse-item>
    </el-collapse>
    <template v-else>
      <p class="lead">
        借用和账号池按同一套顺序选号。下面是现在生效的规则；改完数字保存后，下一次选号和代理上报都会用新值。
      </p>
      <RulesDoc :selection="selection" :hours-text="hoursText" :num="num" />
    </template>

    <el-form :label-width="layout === 'sidebar' ? '120px' : '168px'" class="form" @submit.prevent="onSave">
      <el-form-item label="同时使用上限">
        <el-input-number
          v-model="draft.max_concurrent_users"
          :min="0"
          :max="100"
          :step="1"
          :precision="0"
          :disabled="!canWrite"
        />
        <span class="hint">人。0 为不限制，默认 3。</span>
      </el-form-item>
      <el-form-item label="在线判定超时">
        <el-input-number
          v-model="draft.concurrent_ttl_seconds"
          :min="30"
          :max="3600"
          :step="30"
          :precision="0"
          :disabled="!canWrite"
        />
        <span class="hint">秒。应长于会话续期间隔（默认 120 秒）。</span>
      </el-form-item>
      <el-form-item label="最短切换间隔">
        <el-input-number
          v-model="draft.min_switch_minutes"
          :min="0"
          :max="1440"
          :step="5"
          :precision="0"
          :disabled="!canWrite"
        />
        <span class="hint">分钟。只影响评分降权。代理进程里的停留用 PROXY_STICKY_MIN_DWELL。</span>
      </el-form-item>
      <el-form-item label="同时在借上限">
        <el-input-number
          v-model="draft.max_active_loans_per_account"
          :min="1"
          :max="100"
          :step="1"
          :precision="0"
          :disabled="!canWrite"
        />
        <span class="hint">笔。一个账号上尚未结束的借用。</span>
      </el-form-item>
      <el-form-item label="最少覆盖">
        <el-input-number
          v-model="draft.min_coverage_hours"
          :min="0"
          :max="720"
          :step="1"
          :precision="1"
          :disabled="!canWrite"
        />
        <span class="hint">小时。离重置太近的账号不新借出。</span>
      </el-form-item>
      <el-form-item label="主负责人保留">
        <el-input-number
          v-model="draft.owner_reserve_pct"
          :min="0"
          :max="100"
          :step="5"
          :precision="0"
          :disabled="!canWrite"
        />
        <span class="hint">%。账号未单独设置时用这个默认值。0 为不保留。</span>
      </el-form-item>
      <el-form-item :class="{ 'form-actions--sidebar': layout === 'sidebar' }">
        <el-button v-if="canWrite" type="primary" :loading="saving" @click="onSave">
          {{ layout === 'sidebar' ? '保存' : '保存选号规则' }}
        </el-button>
        <span v-else class="hint">当前账号只能查看。</span>
      </el-form-item>
    </el-form>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, watch, defineComponent, h } from 'vue'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import { useSettingsStore } from '@/stores/settings'

const props = withDefaults(
  defineProps<{
    selection: Record<string, unknown>
    layout?: 'default' | 'sidebar'
  }>(),
  { layout: 'default' },
)

const docOpen = ref<string[]>([])

const RulesDoc = defineComponent({
  name: 'RulesDoc',
  props: {
    selection: { type: Object, required: true },
    hoursText: { type: Function, required: true },
    num: { type: Function, required: true },
  },
  setup(p) {
    const draft = reactive({
      max_concurrent_users: 3,
      concurrent_ttl_seconds: 180,
      min_switch_minutes: 30,
      max_active_loans_per_account: 2,
      min_coverage_hours: 1,
      owner_reserve_pct: 0,
    })
    watch(
      () => p.selection,
      (value) => {
        const src = (value || {}) as Record<string, unknown>
        draft.max_concurrent_users = Number(src.max_concurrent_users ?? 3)
        draft.concurrent_ttl_seconds = Number(src.concurrent_ttl_seconds ?? 180)
        draft.min_switch_minutes = Number(src.min_switch_minutes ?? 30)
        draft.max_active_loans_per_account = Number(src.max_active_loans_per_account ?? 2)
        draft.min_coverage_hours = Number(src.min_coverage_hours ?? 1)
        draft.owner_reserve_pct = Number(src.owner_reserve_pct ?? 0)
      },
      { immediate: true, deep: true },
    )
    return () =>
      h(
        'ol',
        { class: 'rules' },
        [
          h('li', [
            h('h3', '硬过滤'),
            h('p', '过不了的账号不会进入候选。'),
            h('ul', [
              h('li', '额度快照没有余量。'),
              h('li', `离重置还够用的时间短于「最少覆盖」${p.hoursText(draft.min_coverage_hours)}。`),
              h(
                'li',
                `这个账号上尚未结束的借用已经有 ${draft.max_active_loans_per_account} 笔。这是借用名额，不是同时在线人数。`,
              ),
              h(
                'li',
                `主负责人保留量不足。按当前用量推到重置日，留给主负责人的比例低于 ${draft.owner_reserve_pct}%。账号上单独设过保留量时，以账号上的为准。`,
              ),
            ]),
          ]),
          h('li', [
            h('h3', '算法打分'),
            h('p', '硬过滤之后用确定性分数排序。可以在账号上加人工基础分，让指定账号靠前。'),
            h('p', { class: 'weights' }, [
              `借用权重：紧迫 ${p.num(p.selection.weight_urgency)} · 剩余 ${p.num(p.selection.weight_surplus)} · 负载 ${p.num(p.selection.weight_load)} · 新鲜度 ${p.num(p.selection.weight_freshness)}。`,
              ` 账号池权重：紧迫 ${p.num(p.selection.proxy_weight_urgency)} · 主负责人余量 ${p.num(p.selection.proxy_weight_headroom)} · 剩余 ${p.num(p.selection.proxy_weight_surplus)} · 新鲜度 ${p.num(p.selection.proxy_weight_freshness)}。`,
            ]),
          ]),
          h('li', [
            h('h3', 'Jev 重排'),
            h(
              'p',
              `开启后，只对通过硬过滤的前 ${p.selection.auto_top_n ?? 8} 个候选重排。调用失败、置信度低于 ${p.num(p.selection.auto_min_confidence)}、与次优的差距小于 ${p.num(p.selection.auto_min_margin)}、判定会侵占主负责人、或返回了未知账号时，仍用算法第一名。每一次代理请求不会单独问 Jev。`,
            ),
          ]),
          h('li', [
            h('h3', '切换驻留'),
            h(
              'p',
              `同一会话绑上账号后，至少停留 ${draft.min_switch_minutes} 分钟，不因为额度压力来回跳。账号真的不可用（登录失败，或正在用的额度桶耗尽）仍会换。请求侧的停留时钟在代理进程里，默认 30 分钟，可用环境变量 PROXY_STICKY_MIN_DWELL 调整。`,
            ),
          ]),
          h('li', [
            h('h3', '同时在线'),
            h(
              'p',
              '代理在换票、会话续期，以及因为额度耗尽要换号时，把当前连接的凭证报给 Web。Web 按人计座：能识别到成员时，同一成员在同一个账号上的多个会话只算 1 人；同时用两个账号则各占一席。识别不到成员时按借用单计。',
            ),
            h(
              'p',
              `同一个账号上，经过本代理同时在线的人数不超过 ${draft.max_concurrent_users}。填 0 表示不限制。只统计经过本代理的连接，主负责人直接打开 Cursor 不计入。已经坐在这个账号上的人不会被后来的人挤走。指定账号的借用始终留在原账号，并占一个座位；新加入账号池或自动轮换的人会避开已满的账号。${draft.concurrent_ttl_seconds} 秒内没有再上报，视为已经离开。座位记在单个 Web 进程的内存里。`,
            ),
          ]),
        ],
      )
  },
})

const emit = defineEmits<{
  saved: [data: Record<string, unknown>]
}>()

const auth = useAuthStore()
const store = useSettingsStore()
const canWrite = auth.hasPermission('settings:write')
const saving = ref(false)

const draft = reactive({
  max_concurrent_users: 3,
  concurrent_ttl_seconds: 180,
  min_switch_minutes: 30,
  max_active_loans_per_account: 2,
  min_coverage_hours: 1,
  owner_reserve_pct: 0,
})

function num(value: unknown): string {
  const n = Number(value)
  if (!Number.isFinite(n)) return '—'
  return String(Math.round(n * 100) / 100)
}

function hoursText(value: unknown): string {
  const n = Number(value)
  if (!Number.isFinite(n)) return '—'
  return `${n} 小时`
}

function readSelection(raw: Record<string, unknown> | undefined) {
  const src = raw || {}
  draft.max_concurrent_users = Number(src.max_concurrent_users ?? 3)
  draft.concurrent_ttl_seconds = Number(src.concurrent_ttl_seconds ?? 180)
  draft.min_switch_minutes = Number(src.min_switch_minutes ?? 30)
  draft.max_active_loans_per_account = Number(src.max_active_loans_per_account ?? 2)
  draft.min_coverage_hours = Number(src.min_coverage_hours ?? 1)
  draft.owner_reserve_pct = Number(src.owner_reserve_pct ?? 0)
}

watch(() => props.selection, (value) => readSelection(value), { immediate: true, deep: true })

async function onSave() {
  const patch = {
    max_concurrent_users: draft.max_concurrent_users,
    concurrent_ttl_seconds: draft.concurrent_ttl_seconds,
    min_switch_minutes: draft.min_switch_minutes,
    max_active_loans_per_account: draft.max_active_loans_per_account,
    min_coverage_hours: draft.min_coverage_hours,
    owner_reserve_pct: draft.owner_reserve_pct,
  }
  if (Object.values(patch).some((value) => value == null || Number.isNaN(Number(value)))) {
    ElMessage.error('请把参数填成数字')
    return
  }
  saving.value = true
  try {
    const data = await store.patchSection('tool_center', { loan_selection: patch })
    emit('saved', data)
    ElMessage.success('已保存')
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.loan-rules {
  max-width: 820px;
  color: var(--el-text-color-primary);
}
.lead {
  margin: 4px 0 8px;
  line-height: 1.6;
  color: var(--el-text-color-regular);
}
.rules {
  list-style: none;
  margin: 0 0 28px;
  padding: 0;
  counter-reset: rule;
}
.rules li {
  counter-increment: rule;
  padding: 14px 0 14px 44px;
  border-top: 1px solid var(--el-border-color-lighter);
  position: relative;
}
.rules li::before {
  content: counter(rule);
  position: absolute;
  left: 0;
  top: 16px;
  width: 28px;
  font-variant-numeric: tabular-nums;
  color: var(--el-text-color-secondary);
}
.rules h3 {
  margin: 0 0 6px;
  font-size: 15px;
  font-weight: 600;
}
.rules p,
.rules li ul {
  margin: 0 0 6px;
  line-height: 1.65;
  color: var(--el-text-color-regular);
}
.rules ul {
  padding-left: 18px;
}
.weights {
  font-variant-numeric: tabular-nums;
}
.form {
  padding-top: 8px;
  border-top: 1px solid var(--el-border-color);
}
.hint {
  margin-left: 12px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.loan-rules--sidebar {
  max-width: none;
}
.loan-rules--sidebar .form {
  padding-top: 12px;
}
.loan-rules--sidebar .hint {
  display: block;
  margin: 4px 0 0;
  margin-left: 0;
  line-height: 1.45;
}
.loan-rules--sidebar :deep(.el-form-item) {
  margin-bottom: 14px;
}
.loan-rules--sidebar :deep(.el-form-item__label) {
  font-size: 13px;
  line-height: 1.3;
  padding-right: 8px;
}
.form-actions--sidebar :deep(.el-form-item__content) {
  justify-content: stretch;
}
.form-actions--sidebar .el-button {
  width: 100%;
}
.rules-doc {
  margin-bottom: 4px;
  border: none;
}
.rules-doc :deep(.el-collapse-item__header) {
  font-size: 13px;
  font-weight: 600;
  border-bottom: 1px solid var(--el-border-color-lighter);
  height: 40px;
}
.rules-doc :deep(.el-collapse-item__wrap) {
  border-bottom: none;
}
.rules-doc :deep(.rules) {
  margin-bottom: 0;
}
.rules-doc :deep(.rules li) {
  padding-left: 36px;
}
</style>
