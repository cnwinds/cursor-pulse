<template>
  <form class="flow" :class="{ 'flow--sidebar': layout === 'sidebar' }" @submit.prevent="onSave">
    <ol class="rail">
      <li class="stage">
        <span class="node">1</span>
        <section class="stage-card">
          <header class="stage-head">
            <h3>硬过滤</h3>
            <Tip
              text="不通过的账号不进候选。另有固定条件：额度快照无余量，或按当前用量会在重置前耗尽。"
            />
          </header>
          <div class="controls">
            <label class="field">
              <span class="field-label">
                最少覆盖
                <Tip text="距本周期作废短于该小时数的账号，不再新借出。" />
              </span>
              <el-input-number
                v-model="draft.min_coverage_hours"
                :min="0"
                :max="720"
                :step="1"
                :precision="1"
                :disabled="!canWrite"
                controls-position="right"
                size="small"
              />
              <span class="unit">小时</span>
            </label>
            <label class="field">
              <span class="field-label">
                在借上限
                <Tip text="该账号上尚未结束的固定借用笔数。与下方「同时使用」不是同一指标。" />
              </span>
              <el-input-number
                v-model="draft.max_active_loans_per_account"
                :min="1"
                :max="100"
                :step="1"
                :precision="0"
                :disabled="!canWrite"
                controls-position="right"
                size="small"
              />
              <span class="unit">笔</span>
            </label>
            <label class="field">
              <span class="field-label">
                保留
                <Tip text="主负责人默认保留比例。账号上单独设过时以账号为准。0 为不保留。" />
              </span>
              <el-input-number
                v-model="draft.owner_reserve_pct"
                :min="0"
                :max="100"
                :step="5"
                :precision="0"
                :disabled="!canWrite"
                controls-position="right"
                size="small"
              />
              <span class="unit">%</span>
            </label>
          </div>
        </section>
      </li>

      <li class="stage">
        <span class="node">2</span>
        <section class="stage-card">
          <header class="stage-head">
            <h3>算法排序</h3>
            <Tip :text="scoreTip" />
          </header>
          <div class="controls">
            <label class="field">
              <span class="field-label">
                最短切换
                <Tip
                  text="绑定后这段时间内降权，避免来回跳号。代理进程内的强制停留由 PROXY_STICKY_MIN_DWELL 控制。登录失败或额度桶耗尽仍会换。"
                />
              </span>
              <el-input-number
                v-model="draft.min_switch_minutes"
                :min="0"
                :max="1440"
                :step="5"
                :precision="0"
                :disabled="!canWrite"
                controls-position="right"
                size="small"
              />
              <span class="unit">分钟</span>
            </label>
          </div>
        </section>
      </li>

      <li class="stage" :class="{ 'stage--off': !jevEnabled }">
        <span class="node">3</span>
        <section class="stage-card">
          <header class="stage-head">
            <h3>Jev 主判</h3>
            <el-tag :type="jevEnabled ? 'success' : 'info'" size="small" effect="plain">
              {{ jevEnabled ? '已启用' : '未启用' }}
            </el-tag>
            <Tip :text="jevTip" />
          </header>
        </section>
      </li>

      <li class="stage">
        <span class="node">4</span>
        <section class="stage-card">
          <header class="stage-head">
            <h3>占座</h3>
            <Tip
              text="经本代理同时在线的人数。同人同号多会话算 1；主负责人直连 Cursor 不计。已在座的人不被挤走。指定账号借用占一座。"
            />
          </header>
          <div class="controls">
            <label class="field">
              <span class="field-label">
                同时使用
                <Tip text="同一账号经本代理的并发人数上限。0 为不限制。" />
              </span>
              <el-input-number
                v-model="draft.max_concurrent_users"
                :min="0"
                :max="100"
                :step="1"
                :precision="0"
                :disabled="!canWrite"
                controls-position="right"
                size="small"
              />
              <span class="unit">人</span>
            </label>
            <label class="field">
              <span class="field-label">
                离开判定
                <Tip text="该秒数内没有再上报，视为已离开。应长于会话续期间隔（默认 120 秒）。" />
              </span>
              <el-input-number
                v-model="draft.concurrent_ttl_seconds"
                :min="30"
                :max="3600"
                :step="30"
                :precision="0"
                :disabled="!canWrite"
                controls-position="right"
                size="small"
              />
              <span class="unit">秒</span>
            </label>
          </div>
        </section>
      </li>
    </ol>

    <footer class="flow-actions">
      <el-button v-if="canWrite" type="primary" native-type="submit" :loading="saving">
        保存
      </el-button>
      <span v-else class="readonly">只读</span>
    </footer>
  </form>
</template>

<script setup lang="ts">
import { computed, defineComponent, h, reactive, ref, watch } from 'vue'
import { ElMessage, ElTooltip } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import { useSettingsStore } from '@/stores/settings'

const Tip = defineComponent({
  name: 'RuleTip',
  props: { text: { type: String, required: true } },
  setup(props) {
    return () =>
      h(
        ElTooltip,
        { content: props.text, placement: 'top', showAfter: 200 },
        {
          default: () =>
            h(
              'button',
              { type: 'button', class: 'q', 'aria-label': '说明' },
              '?',
            ),
        },
      )
  },
})

const props = withDefaults(
  defineProps<{
    selection: Record<string, unknown>
    jevEnabled?: boolean
    layout?: 'default' | 'sidebar'
  }>(),
  { jevEnabled: false, layout: 'default' },
)

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

const scoreTip = computed(() => {
  const s = props.selection
  return (
    '硬过滤之后按确定性分数排序。可在打分表对单个账号加人工分。' +
    `借用权重：紧迫 ${num(s.weight_urgency)} · 剩余 ${num(s.weight_surplus)} · 负载 ${num(s.weight_load)} · 新鲜度 ${num(s.weight_freshness)}。` +
    `账号池权重：紧迫 ${num(s.proxy_weight_urgency)} · 主负责人余量 ${num(s.proxy_weight_headroom)} · 剩余 ${num(s.proxy_weight_surplus)} · 新鲜度 ${num(s.proxy_weight_freshness)}。`
  )
})

const jevTip = computed(() => {
  const s = props.selection
  const state = props.jevEnabled
    ? '已在「Jev 决策」启用'
    : '未启用。请到「Jev 决策」打开主判并配好模型与 Key'
  return (
    `${state}。启用后只重排硬过滤后的前 ${s.auto_top_n ?? 8} 名。` +
    `调用失败、置信度低于 ${num(s.auto_min_confidence)}、与次优差距小于 ${num(s.auto_min_margin)}、` +
    '判定会侵占主负责人，或返回未知账号时，仍用算法第一名。不在每次代理请求上调用。'
  )
})

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
  const numericKeys = [
    'max_concurrent_users',
    'concurrent_ttl_seconds',
    'min_switch_minutes',
    'max_active_loans_per_account',
    'min_coverage_hours',
    'owner_reserve_pct',
  ] as const
  if (
    numericKeys.some((key) => {
      const value = patch[key]
      return value == null || Number.isNaN(Number(value))
    })
  ) {
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
.flow {
  --ink: #0f172a;
  --muted: #64748b;
  --line: #d6dee8;
  --accent: #0f766e;
  max-width: 760px;
  color: var(--ink);
}
.flow--sidebar {
  max-width: none;
}
.rail {
  list-style: none;
  margin: 0;
  padding: 4px 0 0;
}
.stage {
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr);
  column-gap: 14px;
  position: relative;
  padding-bottom: 16px;
}
.stage:not(:last-child)::before {
  content: '';
  position: absolute;
  left: 13px;
  top: 30px;
  bottom: 0;
  width: 2px;
  background: linear-gradient(var(--accent), var(--line));
  opacity: 0.55;
}
.node {
  width: 28px;
  height: 28px;
  margin-top: 12px;
  border-radius: 50%;
  background: var(--accent);
  color: #fff;
  font-size: 13px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  display: grid;
  place-items: center;
  position: relative;
  z-index: 1;
  box-shadow: 0 0 0 4px #f8fafc;
}
.stage--off .node {
  background: #94a3b8;
}
.stage-card {
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 12px 14px 14px;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
}
.stage--off .stage-card {
  background: #f8fafc;
}
.stage-head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 24px;
}
.stage-head h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 650;
  letter-spacing: -0.01em;
}
.controls {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}
.field {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  padding: 6px 10px;
  border-radius: 10px;
  background: #f8fafc;
  border: 1px solid #eef2f6;
}
.field-label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
  font-weight: 600;
  color: #334155;
  white-space: nowrap;
}
.unit {
  font-size: 12px;
  font-weight: 650;
  color: var(--muted);
}
.flow :deep(.el-input-number) {
  width: 108px;
}
.flow :deep(.q) {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 15px;
  height: 15px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: #e2e8f0;
  color: #64748b;
  font-size: 10px;
  font-weight: 700;
  line-height: 1;
  cursor: help;
}
.flow :deep(.q:hover) {
  background: #ccfbf1;
  color: var(--accent);
}
.flow-actions {
  display: flex;
  justify-content: flex-end;
  padding-left: 42px;
}
.readonly {
  font-size: 12px;
  font-weight: 600;
  color: var(--muted);
}
</style>
