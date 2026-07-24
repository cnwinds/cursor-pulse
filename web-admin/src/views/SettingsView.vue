<template>
  <div v-loading="loading">
    <el-tabs v-model="tab">
      <el-tab-pane label="收集与调度" name="collection">
        <el-table
          :data="collectionRows"
          stripe
          class="settings-table"
          @row-click="openItem"
        >
          <el-table-column prop="name" label="任务" min-width="160" />
          <el-table-column prop="summary" label="计划" min-width="220" />
          <el-table-column prop="process" label="进程" width="120" />
          <el-table-column label="" width="56" align="center">
            <template #default="{ row }">
              <el-icon v-if="row.editable" class="edit-icon"><Edit /></el-icon>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="告警" name="alerts">
        <el-table :data="alertRows" stripe class="settings-table" @row-click="openItem">
          <el-table-column prop="name" label="项目" min-width="160" />
          <el-table-column prop="summary" label="当前配置" min-width="220" />
          <el-table-column prop="process" label="进程" width="120" />
          <el-table-column label="" width="56" align="center">
            <template #default>
              <el-icon class="edit-icon"><Edit /></el-icon>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="集成与 LLM" name="integrations">
        <el-table :data="integrationRows" stripe class="settings-table" @row-click="openItem">
          <el-table-column prop="name" label="项目" min-width="160" />
          <el-table-column prop="summary" label="状态" min-width="220" />
          <el-table-column prop="process" label="来源" width="120" />
          <el-table-column label="" width="56" align="center">
            <template #default="{ row }">
              <el-icon v-if="row.editable" class="edit-icon"><Edit /></el-icon>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="定价" name="pricing" lazy>
        <CursorPricingSettings />
      </el-tab-pane>
    </el-tabs>

    <SettingEditDialog
      :open="dialog.open"
      :title="dialog.title"
      :model="dialog.model"
      :fields="dialog.fields"
      :notice="dialog.notice"
      :read-only="dialog.readOnly"
      @close="closeDialog"
      @save="saveDialog"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Edit } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import client from '@/api/client'
import { useSettingsStore } from '@/stores/settings'
import SettingEditDialog from '@/components/SettingEditDialog.vue'
import CursorPricingSettings from '@/components/CursorPricingSettings.vue'
import type { SettingsField } from '@/components/SettingsSectionForm.vue'

type SettingRow = {
  id: string
  name: string
  summary: string
  process: string
  editable: boolean
}

type DialogState = {
  open: boolean
  title: string
  model: Record<string, unknown> | null
  fields: SettingsField[]
  notice: string
  readOnly: boolean
  itemId: string
}

type SavePlan = {
  section: string
  keys: string[]
}

const store = useSettingsStore()
const route = useRoute()
const router = useRouter()
const tab = ref('collection')
const integrations = ref<any>(null)
const schedule = ref<any>(null)
const runtimeLoading = ref(true)
const members = ref<Array<{ id: string; display_name: string; dingtalk_user_id: string }>>([])

const loading = computed(() => store.loading || runtimeLoading.value)

const forms = reactive({
  collection: {} as Record<string, unknown>,
  memory: {} as Record<string, unknown>,
  alerts: {} as Record<string, unknown>,
  llm: {} as Record<string, unknown>,
  assistant_llm: {} as Record<string, unknown>,
  chat_memory: {} as Record<string, Record<string, unknown>>,
  web_search: {} as Record<string, unknown>,
  integrations: {} as Record<string, unknown>,
  cursor_sync: {} as Record<string, unknown>,
  dingtalk: {} as Record<string, unknown>,
  admin: {} as Record<string, unknown>,
})

const memberSelectOptions = computed(() =>
  members.value.map((m) => ({
    value: m.id,
    label: m.display_name || m.dingtalk_user_id || m.id,
  })),
)

function adminFallbackMemberIds(): string[] {
  const adminIds = new Set(
    ((forms.admin.dingtalk_user_ids as string[]) || [])
      .map((id) => String(id || '').trim())
      .filter(Boolean),
  )
  if (!adminIds.size) return []
  return members.value
    .filter((m) => adminIds.has(m.dingtalk_user_id))
    .map((m) => m.id)
}

const dialog = reactive<DialogState>({
  open: false,
  title: '',
  model: null,
  fields: [],
  notice: '',
  readOnly: false,
  itemId: '',
})

const ITEM_PLANS: Record<string, SavePlan[]> = {
  collection_basics: [{ section: 'collection', keys: ['timezone', 'start_day', 'deadline_day'] }],
  reminders: [{ section: 'collection', keys: ['reminders_enabled', 'start_day', 'start_time', 'deadline_day', 'deadline_time', 'daily_check_time'] }],
  monthly_report: [
    { section: 'collection', keys: ['report_time', 'report_on_first_business_day', 'report_day', 'publish_report_to_group'] },
    { section: 'cursor_sync', keys: ['pre_publish_start_time'] },
  ],
  cursor_sync_tick: [{
    section: 'cursor_sync',
    keys: [
      'enabled',
      'default_interval_minutes',
      'tick_interval_minutes',
      'enforce_on_demand_disabled',
      'on_demand_notify_member_ids',
      'on_demand_notify_primary',
      'on_demand_notify_admins_on_api_failure',
    ],
  }],
  memory_evolution: [{ section: 'memory', keys: ['evolution_enabled', 'evolution_day_of_week', 'evolution_time'] }],
  alerts: [{ section: 'alerts', keys: ['enabled', 'member_events_spike_pct', 'team_events_spike_pct', 'member_cost_spike_usd'] }],
  pulse_llm: [{ section: 'llm', keys: ['enabled', 'base_url', 'api_key', 'model', 'vision_enabled', 'vision_model'] }],
  assistant_llm: [
    {
      section: 'assistant_llm',
      keys: ['enabled', 'base_url', 'api_key', 'model'],
    },
  ],
  bi_push: [{ section: 'integrations', keys: ['webhook_url', 'push_on_report', 'webhook_secret'] }],
  dingtalk: [
    {
      section: 'dingtalk',
      keys: [
        'app_key',
        'app_secret',
        'robot_code',
        'group_open_conversation_id',
        'sync_root_dept_id',
      ],
    },
  ],
  web_search: [
    {
      section: 'web_search',
      keys: [
        'enabled',
        'api_key',
        'timeout_seconds',
        'max_results',
        'rate_limit_per_minute',
        'fetch_max_bytes',
        'fetch_max_redirects',
      ],
    },
  ],
}

function formatIntervalMinutes(minutes: unknown): string {
  const value = Number(minutes)
  if (!Number.isFinite(value) || value <= 0) return '—'
  if (value < 60) return `${value} 分钟`
  if (value % 60 === 0) return `${value / 60} 小时`
  return `${value} 分钟`
}

function syncRowSummary(): string {
  if (!forms.cursor_sync.enabled) return '已关闭'
  const tick = forms.cursor_sync.tick_interval_minutes ?? 2
  const interval = formatIntervalMinutes(forms.cursor_sync.default_interval_minutes ?? 1440)
  const parts = [`每 ${tick} 分钟巡检`, `账号间隔 ${interval}`]
  if (forms.cursor_sync.enforce_on_demand_disabled !== false) {
    parts.push('强制关 On-Demand')
    const ids = forms.cursor_sync.on_demand_notify_member_ids
    const n = Array.isArray(ids) ? ids.length : null
    const primary = forms.cursor_sync.on_demand_notify_primary !== false
    if (n != null) {
      parts.push(primary ? `通知 ${n} 人+主使用人` : `通知 ${n} 人`)
    } else if (primary) {
      parts.push('通知管理员+主使用人')
    } else {
      parts.push('通知管理员')
    }
  }
  return parts.join(' · ')
}

function jobCron(id: string): string {
  const job = schedule.value?.jobs?.find((row: any) => row.id === id)
  return job?.cron ?? '—'
}

function collectionBasicsSummary(): string {
  const tz = forms.collection.timezone || schedule.value?.timezone || '—'
  const start = forms.collection.start_day ?? schedule.value?.collection_window?.start_day ?? '—'
  const end = forms.collection.deadline_day ?? schedule.value?.collection_window?.deadline_day ?? '—'
  return `${tz} · 每月 ${start}–${end} 日收集`
}

function monthlyReportSummary(): string {
  const onFirstBizDay = forms.collection.report_on_first_business_day !== false
  if (onFirstBizDay) {
    return jobCron('monthly_report')
  }
  const day = forms.collection.report_day ?? '—'
  const time = forms.collection.report_time ?? '—'
  return `每月 ${day} 日 ${time}`
}

const collectionRows = computed<SettingRow[]>(() => {
  const remindersOn = Boolean(schedule.value?.reminders_enabled)
  const rows: SettingRow[] = [
    {
      id: 'collection_basics',
      name: '时区与收集窗口',
      summary: collectionBasicsSummary(),
      process: '团队设置',
      editable: true,
    },
    {
      id: 'current_period',
      name: '当前账期',
      summary: schedule.value?.current_period ?? '—',
      process: '自动计算',
      editable: false,
    },
    {
      id: 'reminders',
      name: '用量提交催办',
      summary: remindersOn ? '已开启（收集开始 / 每日私聊 / 截止提醒）' : '已关闭',
      process: 'pulse channel',
      editable: true,
    },
    {
      id: 'monthly_report',
      name: '月报发送',
      summary: monthlyReportSummary(),
      process: 'pulse channel',
      editable: true,
    },
  ]
  rows.push(
    {
      id: 'cursor_sync_tick',
      name: 'Cursor账号同步',
      summary: syncRowSummary(),
      process: 'pulse channel',
      editable: true,
    },
    {
      id: 'memory_evolution',
      name: '记忆自进化',
      summary: forms.memory.evolution_enabled
        ? jobCron('memory_evolution')
        : '已关闭',
      process: 'pulse channel',
      editable: true,
    },
  )
  return rows
})

const alertRows = computed<SettingRow[]>(() => [
  {
    id: 'alerts',
    name: '异常告警',
    summary: forms.alerts.enabled
      ? `已开启 · 成员事件 +${forms.alerts.member_events_spike_pct}% · 团队 +${forms.alerts.team_events_spike_pct}% · 费用 $${forms.alerts.member_cost_spike_usd}`
      : '已关闭',
    process: 'pulse channel',
    editable: true,
  },
])

function chatMemorySummary(): string {
  const archive = forms.chat_memory.archive || {}
  const features = forms.chat_memory.features || {}
  const backfill = forms.chat_memory.backfill || {}
  if (!archive.enabled) return '未启用'
  const parts: string[] = ['归档开']
  if (features.archive_pipeline) parts.push('流水线')
  if (features.auto_recall_per_turn) {
    const recall = forms.chat_memory.recall || {}
    parts.push(`召回 · ${recall.fragment_top_k ?? 3} 片段`)
  }
  if (features.backfill && backfill.enabled) parts.push('回填')
  return parts.join(' · ')
}

function webSearchSummary(): string {
  const ws = forms.web_search
  if (!ws.enabled) return '未启用'
  const keyOk = ws.api_key === '***' || Boolean(ws.api_key)
  return keyOk ? `已启用 · 最多 ${ws.max_results ?? 5} 条` : '已启用但未配置 Key'
}

const integrationRows = computed<SettingRow[]>(() => {
  const intg = integrations.value
  if (!intg) return []
  return [
    {
      id: 'dingtalk',
      name: '钉钉集成',
      summary: intg.dingtalk.app_configured && intg.dingtalk.group_configured
        ? intg.dingtalk.group_title
          ? `工作群：${intg.dingtalk.group_title}`
          : '应用与群已配置'
        : intg.dingtalk.app_configured
          ? '应用已配置，群未配置'
          : '未完整配置',
      process: '团队设置',
      editable: true,
    },
    {
      id: 'assistant_llm',
      name: '助手 LLM（对话）',
      summary: forms.assistant_llm.enabled && (forms.assistant_llm.api_key === '***' || forms.assistant_llm.api_key)
        ? [
            forms.assistant_llm.model || '—',
            forms.chat_memory.embedding?.enabled
              ? `嵌入 ${forms.chat_memory.embedding?.model || '—'}`
              : '嵌入关',
          ].join(' · ')
        : forms.assistant_llm.enabled
          ? '已启用但未配置 Key'
          : '未启用',
      process: '团队设置',
      editable: true,
    },
    {
      id: 'chat_memory',
      name: '聊天记忆',
      summary: chatMemorySummary(),
      process: '团队设置',
      editable: true,
    },
    {
      id: 'web_search',
      name: '联网搜索（Tavily）',
      summary: webSearchSummary(),
      process: '团队设置',
      editable: true,
    },
    {
      id: 'pulse_llm',
      name: 'Pulse LLM（月报 / 截图）',
      summary: forms.llm.enabled
        ? `${forms.llm.model}${forms.llm.vision_enabled ? ' · 含视觉' : ''}`
        : '未启用',
      process: '团队设置',
      editable: true,
    },
    {
      id: 'bi_push',
      name: 'BI 推送',
      summary: forms.integrations.webhook_url
        ? `已配置${forms.integrations.push_on_report ? ' · 月报推送' : ''}`
        : '未配置',
      process: '团队设置',
      editable: true,
    },
  ]
})

const FIELD_DEFS: Record<string, SettingsField> = {
  timezone: { key: 'timezone', label: '时区', hint: '如 Asia/Shanghai' },
  start_day: { key: 'start_day', label: '收集开始日', type: 'number' },
  deadline_day: { key: 'deadline_day', label: '收集截止日', type: 'number' },
  start_time: { key: 'start_time', label: '收集开始时间' },
  reminders_enabled: { key: 'reminders_enabled', label: '启用催办', type: 'switch' },
  deadline_time: { key: 'deadline_time', label: '收集截止时间' },
  daily_check_time: { key: 'daily_check_time', label: '每日检查时间' },
  report_day: {
    key: 'report_day',
    label: '月报日',
    type: 'number',
    showWhen: (model) => model.report_on_first_business_day === false,
  },
  report_time: { key: 'report_time', label: '月报发送时间' },
  report_on_first_business_day: { key: 'report_on_first_business_day', label: '月报在首个工作日', type: 'switch' },
  publish_report_to_group: { key: 'publish_report_to_group', label: '月报群发钉钉群', type: 'switch' },
  pre_publish_start_time: {
    key: 'pre_publish_start_time',
    label: '发布前数据刷新时间',
    showWhen: (model) => model.report_on_first_business_day !== false,
    hint: '首个工作日上午先刷新 Cursor 用量，再按发送时间发月报',
  },
  sync_enabled: { key: 'enabled', label: '启用同步', type: 'switch' },
  enforce_on_demand_disabled: {
    key: 'enforce_on_demand_disabled',
    label: 'On-Demand 强制关闭',
    type: 'switch',
    hint: '用量同步时检测并关闭 Cursor On-Demand Spending，避免超额扣费',
  },
  on_demand_notify_member_ids: {
    key: 'on_demand_notify_member_ids',
    label: '关闭时通知这些人',
    type: 'member_multi',
    hint: '可搜索选择钉钉成员；未保存过时默认预填管理员',
    showWhen: (model) => model.enforce_on_demand_disabled !== false,
  },
  on_demand_notify_primary: {
    key: 'on_demand_notify_primary',
    label: '同时通知主使用人',
    type: 'switch',
    hint: '向该账号的主使用人发送钉钉私聊（与上表去重）',
    showWhen: (model) => model.enforce_on_demand_disabled !== false,
  },
  on_demand_notify_admins_on_api_failure: {
    key: 'on_demand_notify_admins_on_api_failure',
    label: '接口失败时通知管理员',
    type: 'switch',
    hint: 'GetHardLimit 失败时单独通知平台管理员（DINGTALK_ADMIN_USER_IDS），应对 API 变更',
    showWhen: (model) => model.enforce_on_demand_disabled !== false,
  },
  default_interval_minutes: {
    key: 'default_interval_minutes',
    label: '账号同步间隔',
    type: 'select',
    hint: '每个账号两次同步之间的最短间隔（另加错峰抖动）。保存后立即生效，到期账号会在下一次巡检时同步。',
    options: [
      { value: 30, label: '30 分钟' },
      { value: 60, label: '60 分钟' },
      { value: 120, label: '2 小时' },
      { value: 360, label: '6 小时' },
      { value: 720, label: '12 小时' },
      { value: 1440, label: '24 小时' },
    ],
  },
  tick_interval_minutes: {
    key: 'tick_interval_minutes',
    label: '巡检间隔（分钟）',
    type: 'number',
    hint: '调度器多久检查一次到期账号，默认 2。修改后需重启 pulse channel 进程。',
  },
  evolution_enabled: { key: 'evolution_enabled', label: '启用记忆自进化', type: 'switch' },
  evolution_day_of_week: {
    key: 'evolution_day_of_week',
    label: '进化频率',
    type: 'select',
    options: [
      { label: '每天', value: -1 },
      { label: '周一', value: 0 },
      { label: '周二', value: 1 },
      { label: '周三', value: 2 },
      { label: '周四', value: 3 },
      { label: '周五', value: 4 },
      { label: '周六', value: 5 },
      { label: '周日', value: 6 },
    ],
  },
  evolution_time: { key: 'evolution_time', label: '进化时间' },
  member_events_spike_pct: { key: 'member_events_spike_pct', label: '成员事件激增 %', type: 'number' },
  team_events_spike_pct: { key: 'team_events_spike_pct', label: '团队事件激增 %', type: 'number' },
  member_cost_spike_usd: { key: 'member_cost_spike_usd', label: '成员费用激增 USD', type: 'number' },
  alerts_enabled: { key: 'enabled', label: '启用告警', type: 'switch' },
  pulse_enabled: { key: 'enabled', label: '启用 Pulse LLM', type: 'switch' },
  base_url: { key: 'base_url', label: 'API Base URL', hint: 'OpenAI 兼容接口，如 https://api.openai.com/v1' },
  model: { key: 'model', label: '对话模型' },
  vision_enabled: { key: 'vision_enabled', label: '启用视觉解析', type: 'switch' },
  vision_model: { key: 'vision_model', label: '视觉模型' },
  api_key: {
    key: 'api_key',
    label: 'API Key',
    type: 'secret',
    secretSection: 'llm',
    hint: '留空或 *** 表示不修改',
  },
  assistant_enabled: { key: 'enabled', label: '启用助手 LLM', type: 'switch' },
  assistant_model: { key: 'model', label: '对话模型' },
  assistant_api_key: {
    key: 'api_key',
    label: 'API Key',
    type: 'secret',
    secretSection: 'assistant_llm',
    hint: '留空或 *** 表示不修改',
  },
  assistant_memory_enabled: { key: 'memory_enabled', label: '启用记忆', type: 'switch' },
  webhook_url: { key: 'webhook_url', label: 'Webhook URL' },
  push_on_report: { key: 'push_on_report', label: '月报时推送', type: 'switch' },
  webhook_secret: {
    key: 'webhook_secret',
    label: 'Webhook Secret',
    type: 'secret',
    secretSection: 'integrations',
    hint: '留空或 *** 表示不修改',
  },
  app_key: { key: 'app_key', label: 'AppKey', hint: '钉钉开放平台 → 应用凭证' },
  app_secret: {
    key: 'app_secret',
    label: 'AppSecret',
    type: 'secret',
    secretSection: 'dingtalk',
    hint: '留空或 *** 表示不修改',
  },
  robot_code: { key: 'robot_code', label: 'Robot Code', hint: '机器人 ID，私聊与群消息需要' },
  group_open_conversation_id: {
    key: 'group_open_conversation_id',
    label: '群 openConversationId',
    hint: '机器人群 ID；也可在群内 @ 机器人自动绑定',
  },
  group_title: {
    key: 'group_title',
    label: '群名称',
    readonly: true,
    hint: '绑定工作群时自动记录；未显示时在群内 @ 机器人发「启动」更新',
  },
  sync_root_dept_id: {
    key: 'sync_root_dept_id',
    label: '通讯录同步根部门 ID',
    type: 'number',
    hint: '默认 1（全公司）',
  },
  cm_archive_enabled: {
    key: 'enabled',
    label: '启用永久归档',
    type: 'switch',
    hint: '总开关：开启后才会持久化聊天消息并支持召回、回填等能力',
  },
  cm_index_version: {
    key: 'index_version',
    label: '索引版本',
    type: 'number',
    hint: '变更后需重建索引',
    showWhen: (model) => model.enabled === true,
  },
  cm_ledger_retention_days: {
    key: 'ledger_retention_days',
    label: '运行账本保留天数',
    type: 'number',
    hint: '仅清理已成功归档的消息',
    showWhen: (model) => model.enabled === true,
  },
  cm_feature_archive_pipeline: {
    key: 'archive_pipeline',
    label: '关闭后归档流水线',
    type: 'switch',
    hint: '会话关闭时自动分块、嵌入并写入永久档案',
    showWhen: (model) => model.enabled === true,
  },
  cm_feature_auto_recall: {
    key: 'auto_recall_per_turn',
    label: '每回合自动召回',
    type: 'switch',
    hint: '每轮对话前从档案中检索相关片段与事实注入上下文',
    showWhen: (model) => model.enabled === true,
  },
  cm_feature_distill: {
    key: 'distill_on_close',
    label: '关闭时提炼摘要/事实',
    type: 'switch',
    hint: '会话关闭时生成摘要与结构化事实，供后续召回',
    showWhen: (model) => model.enabled === true && model.archive_pipeline === true,
  },
  cm_feature_profile: {
    key: 'profile_compile',
    label: '私聊交互画像编译',
    type: 'switch',
    hint: '私聊关闭后更新用户交互画像（需开启归档流水线）',
    showWhen: (model) => model.enabled === true && model.archive_pipeline === true,
  },
  cm_history_backfill: {
    key: 'history_backfill',
    label: '历史回填',
    type: 'switch',
    hint: '允许并执行历史会话的批量归档与索引（适合首次启用）',
    showWhen: (model) => model.enabled === true,
  },
  cm_fragment_top_k: {
    key: 'fragment_top_k',
    label: '片段 Top-K',
    type: 'number',
    showWhen: (model) => model.enabled === true && model.auto_recall_per_turn === true,
  },
  cm_fact_top_k: {
    key: 'fact_top_k',
    label: '事实 Top-K',
    type: 'number',
    showWhen: (model) => model.enabled === true && model.auto_recall_per_turn === true,
  },
  cm_max_fragments_per_session: {
    key: 'max_fragments_per_session',
    label: '单会话片段上限',
    type: 'number',
    showWhen: (model) => model.enabled === true && model.auto_recall_per_turn === true,
  },
  cm_context_token_budget: {
    key: 'context_token_budget',
    label: '上下文 Token 预算',
    type: 'number',
    showWhen: (model) => model.enabled === true && model.auto_recall_per_turn === true,
  },
  cm_expand_neighbor_count: {
    key: 'expand_neighbor_count',
    label: '展开相邻片段数',
    type: 'number',
    showWhen: (model) => model.enabled === true && model.auto_recall_per_turn === true,
  },
  cm_recall_timeout_ms: {
    key: 'timeout_ms',
    label: '召回超时（毫秒）',
    type: 'number',
    showWhen: (model) => model.enabled === true && model.auto_recall_per_turn === true,
  },
  cm_fts_weight: {
    key: 'fts_weight',
    label: 'FTS 权重',
    type: 'number',
    min: 0,
    max: 1,
    step: 0.1,
    precision: 1,
    showWhen: (model) => model.enabled === true && model.auto_recall_per_turn === true,
  },
  cm_vector_weight: {
    key: 'vector_weight',
    label: '向量权重',
    type: 'number',
    min: 0,
    max: 1,
    step: 0.1,
    precision: 1,
    showWhen: (model) => model.enabled === true && model.auto_recall_per_turn === true,
  },
  cm_max_tokens_per_chunk: {
    key: 'max_tokens_per_chunk',
    label: '分块最大 Token',
    type: 'number',
    showWhen: (model) => model.enabled === true,
  },
  cm_overlap_tokens: {
    key: 'overlap_tokens',
    label: '分块重叠 Token',
    type: 'number',
    showWhen: (model) => model.enabled === true,
  },
  cm_embedding_enabled: { key: 'embedding_enabled', label: '启用向量嵌入', type: 'switch' },
  cm_embedding_model: {
    key: 'embedding_model',
    label: '嵌入模型',
    hint: '复用上方 API Key 与 Base URL；OpenAI 兼容，如 text-embedding-3-small',
  },
  cm_embedding_batch_size: {
    key: 'embedding_batch_size',
    label: '嵌入批大小',
    type: 'number',
    showWhen: (model) => model.enabled === true,
  },
  cm_embedding_dedupe: {
    key: 'dedupe_by_content_hash',
    label: '按内容哈希去重',
    type: 'switch',
    showWhen: (model) => model.enabled === true,
  },
  cm_backfill_batch_size: {
    key: 'backfill_batch_size',
    label: '回填批大小',
    type: 'number',
    showWhen: (model) => model.enabled === true && model.history_backfill === true,
  },
  web_search_enabled: {
    key: 'enabled',
    label: '启用联网搜索',
    type: 'switch',
    hint: '开启后助手可调用 web.search / web.fetch 能力（Tavily）',
  },
  web_search_api_key: {
    key: 'api_key',
    label: 'Tavily API Key',
    type: 'secret',
    secretSection: 'web_search',
    hint: '留空或 *** 表示不修改；密钥仅存 Pulse 层',
    showWhen: (model) => model.enabled === true,
  },
  web_search_timeout: {
    key: 'timeout_seconds',
    label: '搜索超时（秒）',
    type: 'number',
    min: 1,
    step: 1,
    showWhen: (model) => model.enabled === true,
  },
  web_search_max_results: {
    key: 'max_results',
    label: '默认最大结果数',
    type: 'number',
    min: 1,
    max: 20,
    showWhen: (model) => model.enabled === true,
  },
  web_search_rate_limit: {
    key: 'rate_limit_per_minute',
    label: '每分钟速率限制',
    type: 'number',
    min: 1,
    showWhen: (model) => model.enabled === true,
  },
  web_search_fetch_max_bytes: {
    key: 'fetch_max_bytes',
    label: '网页抓取最大字节',
    type: 'number',
    hint: 'web.fetch 单页大小上限',
    showWhen: (model) => model.enabled === true,
  },
  web_search_fetch_max_redirects: {
    key: 'fetch_max_redirects',
    label: '最大重定向次数',
    type: 'number',
    min: 0,
    showWhen: (model) => model.enabled === true,
  },
}

watch(
  () => route.query.tab,
  (value) => {
    if (typeof value === 'string' && value) tab.value = value
  },
  { immediate: true },
)

watch(tab, (value) => {
  if (route.query.tab !== value) {
    router.replace({ query: { ...route.query, tab: value } })
  }
})

onMounted(loadAll)

async function loadAll() {
  await Promise.all([applySettings(await store.load()), loadRuntime(), loadMembers()])
}

async function loadMembers() {
  try {
    const res = await client.get('/api/v2/members')
    members.value = Array.isArray(res.data) ? res.data : []
  } catch {
    members.value = []
  }
}

function applySettings(data: Record<string, any>) {
  forms.collection = { ...data.collection }
  forms.memory = {
    evolution_enabled: data.memory?.evolution_enabled,
    evolution_day_of_week: data.memory?.evolution_day_of_week,
    evolution_time: data.memory?.evolution_time,
  }
  forms.alerts = { ...data.alerts }
  forms.llm = { ...data.llm }
  forms.assistant_llm = { ...data.assistant_llm }
  forms.web_search = { ...(data.web_search || {}) }
  forms.chat_memory = {
    archive: { ...(data.chat_memory?.archive || {}) },
    features: { ...(data.chat_memory?.features || {}) },
    recall: { ...(data.chat_memory?.recall || {}) },
    chunking: { ...(data.chat_memory?.chunking || {}) },
    embedding: { ...(data.chat_memory?.embedding || {}) },
    backfill: { ...(data.chat_memory?.backfill || {}) },
  }
  forms.integrations = { ...data.integrations }
  forms.dingtalk = { ...data.dingtalk }
  forms.admin = { ...(data.admin || {}) }
  const cursorSync = { ...(data.cursor_sync || {}) }
  if (cursorSync.default_interval_minutes == null && cursorSync.default_interval_hours != null) {
    cursorSync.default_interval_minutes = Number(cursorSync.default_interval_hours) * 60
  }
  if (cursorSync.default_interval_minutes == null) {
    cursorSync.default_interval_minutes = 1440
  }
  forms.cursor_sync = cursorSync
}

async function loadRuntime() {
  runtimeLoading.value = true
  try {
    const [intRes, schRes] = await Promise.all([
      client.get('/api/system/integrations'),
      client.get('/api/system/schedule'),
    ])
    integrations.value = intRes.data
    schedule.value = schRes.data
  } finally {
    runtimeLoading.value = false
  }
}

function sectionModel(section: string): Record<string, unknown> {
  if (section === 'memory') return { ...forms.memory }
  if (section === 'dingtalk') return { ...forms.dingtalk }
  return { ...(forms as Record<string, Record<string, unknown>>)[section] }
}

function fieldsForItem(itemId: string): SettingsField[] {
  const archiveOn = (model: Record<string, unknown>) => model.enabled === true
  if (itemId === 'chat_memory') {
    return [
      { type: 'divider', key: 'd_archive', label: '永久归档' },
      FIELD_DEFS.cm_archive_enabled,
      FIELD_DEFS.cm_index_version,
      FIELD_DEFS.cm_ledger_retention_days,
      { type: 'divider', key: 'd_pipeline', label: '关闭后流水线', showWhen: archiveOn },
      FIELD_DEFS.cm_feature_archive_pipeline,
      FIELD_DEFS.cm_feature_distill,
      FIELD_DEFS.cm_feature_profile,
      { type: 'divider', key: 'd_recall', label: '每回合召回', showWhen: archiveOn },
      FIELD_DEFS.cm_feature_auto_recall,
      FIELD_DEFS.cm_fragment_top_k,
      FIELD_DEFS.cm_fact_top_k,
      FIELD_DEFS.cm_max_fragments_per_session,
      FIELD_DEFS.cm_context_token_budget,
      FIELD_DEFS.cm_expand_neighbor_count,
      FIELD_DEFS.cm_recall_timeout_ms,
      FIELD_DEFS.cm_fts_weight,
      FIELD_DEFS.cm_vector_weight,
      {
        type: 'divider',
        key: 'd_backfill',
        label: '历史回填',
        showWhen: archiveOn,
      },
      FIELD_DEFS.cm_history_backfill,
      FIELD_DEFS.cm_backfill_batch_size,
      {
        type: 'divider',
        key: 'd_advanced',
        label: '高级 · 分块与嵌入批处理',
        showWhen: archiveOn,
      },
      FIELD_DEFS.cm_max_tokens_per_chunk,
      FIELD_DEFS.cm_overlap_tokens,
      FIELD_DEFS.cm_embedding_batch_size,
      FIELD_DEFS.cm_embedding_dedupe,
    ]
  }
  if (itemId === 'assistant_llm') {
    return [
      FIELD_DEFS.assistant_enabled,
      FIELD_DEFS.base_url,
      FIELD_DEFS.assistant_api_key,
      FIELD_DEFS.assistant_model,
      { type: 'divider', key: 'd_embed', label: '向量嵌入' },
      FIELD_DEFS.cm_embedding_enabled,
      FIELD_DEFS.cm_embedding_model,
    ]
  }
  if (itemId === 'web_search') {
    return [
      FIELD_DEFS.web_search_enabled,
      FIELD_DEFS.web_search_api_key,
      FIELD_DEFS.web_search_timeout,
      FIELD_DEFS.web_search_max_results,
      FIELD_DEFS.web_search_rate_limit,
      FIELD_DEFS.web_search_fetch_max_bytes,
      FIELD_DEFS.web_search_fetch_max_redirects,
    ]
  }
  const plans = ITEM_PLANS[itemId]
  if (!plans) return []
  const fields = plans.flatMap((plan) =>
    plan.keys
      .map((key) => {
        if (itemId === 'alerts' && key === 'enabled') return FIELD_DEFS.alerts_enabled
        if (itemId === 'cursor_sync_tick' && key === 'enabled') return FIELD_DEFS.sync_enabled
        if (itemId === 'pulse_llm' && key === 'enabled') return FIELD_DEFS.pulse_enabled
        if (itemId === 'assistant_llm' && key === 'enabled') return FIELD_DEFS.assistant_enabled
        if (itemId === 'assistant_llm' && key === 'model') return FIELD_DEFS.assistant_model
        if (itemId === 'assistant_llm' && key === 'api_key') return FIELD_DEFS.assistant_api_key
        const field = FIELD_DEFS[key]
        if (
          itemId === 'cursor_sync_tick' &&
          key === 'on_demand_notify_member_ids' &&
          field
        ) {
          return { ...field, options: memberSelectOptions.value }
        }
        return field
      })
      .filter(Boolean),
  )
  if (itemId !== 'dingtalk') return fields

  const ordered: SettingsField[] = []
  for (const field of fields) {
    ordered.push(field)
    if (field.key === 'group_open_conversation_id') {
      ordered.push(FIELD_DEFS.group_title)
    }
  }
  return ordered
}

function modelForItem(itemId: string): Record<string, unknown> {
  if (itemId === 'chat_memory') {
    const archive = forms.chat_memory.archive || {}
    const features = forms.chat_memory.features || {}
    const recall = forms.chat_memory.recall || {}
    const chunking = forms.chat_memory.chunking || {}
    const embedding = forms.chat_memory.embedding || {}
    const backfill = forms.chat_memory.backfill || {}
    return {
      ...archive,
      ...features,
      ...recall,
      ...chunking,
      embedding_batch_size: embedding.batch_size,
      dedupe_by_content_hash: embedding.dedupe_by_content_hash,
      history_backfill: Boolean(features.backfill && backfill.enabled),
      backfill_batch_size: backfill.batch_size,
    }
  }
  if (itemId === 'assistant_llm') {
    const model: Record<string, unknown> = {}
    const llm = forms.assistant_llm
    for (const key of [
      'enabled',
      'base_url',
      'api_key',
      'model',
    ]) {
      model[key] = llm[key]
    }
    const embedding = forms.chat_memory.embedding || {}
    model.embedding_enabled = embedding.enabled
    model.embedding_model = embedding.model
    return model
  }
  const plans = ITEM_PLANS[itemId]
  if (!plans) return {}
  const model: Record<string, unknown> = {}
  for (const plan of plans) {
    const source = sectionModel(plan.section)
    for (const key of plan.keys) {
      model[key] = source[key]
    }
  }
  if (itemId === 'cursor_sync_tick') {
    if (model.enforce_on_demand_disabled == null) {
      model.enforce_on_demand_disabled = true
    }
    if (model.on_demand_notify_primary == null) {
      model.on_demand_notify_primary = true
    }
    if (model.on_demand_notify_admins_on_api_failure == null) {
      model.on_demand_notify_admins_on_api_failure = true
    }
    if (model.on_demand_notify_member_ids == null) {
      model.on_demand_notify_member_ids = adminFallbackMemberIds()
    }
  }
  return model
}

function openItem(row: SettingRow) {
  dialog.itemId = row.id
  dialog.title = row.name
  dialog.readOnly = !row.editable

  if (row.id === 'dingtalk') {
    dialog.fields = fieldsForItem('dingtalk')
    dialog.model = {
      ...modelForItem('dingtalk'),
      group_title:
        String(forms.dingtalk.group_title || '').trim() ||
        '（未记录，请在群内 @ 机器人发「启动」）',
    }
    dialog.notice =
      '保存后 pulse web 扫码登录立即生效；修改应用凭证或机器人群后需重启 pulse channel。'
  } else if (row.id === 'current_period') {
    dialog.fields = [
      { key: 'period', label: '当前账期', readonly: true },
      { key: 'timezone', label: '计算时区', readonly: true },
    ]
    dialog.model = {
      period: schedule.value?.current_period ?? '—',
      timezone: forms.collection.timezone || schedule.value?.timezone || '—',
    }
    dialog.notice = '账期由系统按当前日期和时区自动计算，不可手动修改。'
  } else {
    dialog.fields = fieldsForItem(row.id)
    dialog.model = modelForItem(row.id)
    dialog.notice = {
      pulse_llm: '用于月报叙事与钉钉截图解析，不影响助手对话。修改后 pulse channel 下次调用时生效。',
      assistant_llm:
        '用于助手对话、意图理解与向量嵌入。嵌入 API 复用上方 Key 与 Base URL。保存后新请求即生效（无需重启 assistant）。',
      chat_memory:
        '聊天记忆一站式配置：先开永久归档，再按需开启流水线、召回、回填。嵌入模型在「助手 LLM」中配置。保存后 assistant 新回合/关闭会话任务即生效。',
      web_search:
        '联网搜索 capability（Tavily）：开启总开关后配置 Key 与限流。保存后下一次 web.search / web.fetch 即生效。',
      bi_push: '月报生成后可推送到 BI Webhook。',
      monthly_report:
        '首个工作日：先按「发布前数据刷新时间」提升 Cursor 同步优先级，再按「月报发送时间」发群。修改后需重启 pulse channel。',
    }[row.id] || ''
  }
  dialog.open = true
}

function closeDialog() {
  dialog.open = false
}

async function saveDialog(patch: Record<string, unknown>) {
  const itemId = dialog.itemId
  const plans = ITEM_PLANS[itemId]

  const cleaned = { ...patch }
  delete cleaned.group_title
  for (const [key, value] of Object.entries(cleaned)) {
    if (value === '***') delete cleaned[key]
    if ((key === 'api_key' || key === 'webhook_secret' || key === 'app_secret') && value === '') {
      delete cleaned[key]
    }
  }

  try {
    if (itemId === 'chat_memory') {
      const archiveKeys = new Set(['enabled', 'index_version', 'ledger_retention_days'])
      const featureKeys = new Set([
        'archive_pipeline',
        'auto_recall_per_turn',
        'distill_on_close',
        'profile_compile',
      ])
      const recallKeys = new Set([
        'fragment_top_k',
        'fact_top_k',
        'max_fragments_per_session',
        'context_token_budget',
        'expand_neighbor_count',
        'timeout_ms',
        'fts_weight',
        'vector_weight',
      ])
      const chunkKeys = new Set(['max_tokens_per_chunk', 'overlap_tokens'])

      const archivePatch: Record<string, unknown> = {}
      const featuresPatch: Record<string, unknown> = {}
      const recallPatch: Record<string, unknown> = {}
      const chunkingPatch: Record<string, unknown> = {}
      const embeddingPatch: Record<string, unknown> = {}
      const backfillPatch: Record<string, unknown> = {}

      for (const [key, value] of Object.entries(cleaned)) {
        if (archiveKeys.has(key)) archivePatch[key] = value
        if (featureKeys.has(key)) featuresPatch[key] = value
        if (recallKeys.has(key)) recallPatch[key] = value
        if (chunkKeys.has(key)) chunkingPatch[key] = value
        if (key === 'embedding_batch_size') embeddingPatch.batch_size = value
        if (key === 'dedupe_by_content_hash') embeddingPatch.dedupe_by_content_hash = value
        if (key === 'history_backfill') {
          const on = Boolean(value)
          featuresPatch.backfill = on
          backfillPatch.enabled = on
        }
        if (key === 'backfill_batch_size') backfillPatch.batch_size = value
      }

      const sectionPatch: Record<string, unknown> = {}
      if (Object.keys(archivePatch).length) sectionPatch.archive = archivePatch
      if (Object.keys(featuresPatch).length) sectionPatch.features = featuresPatch
      if (Object.keys(recallPatch).length) sectionPatch.recall = recallPatch
      if (Object.keys(chunkingPatch).length) sectionPatch.chunking = chunkingPatch
      if (Object.keys(embeddingPatch).length) sectionPatch.embedding = embeddingPatch
      if (Object.keys(backfillPatch).length) sectionPatch.backfill = backfillPatch
      if (Object.keys(sectionPatch).length) {
        await store.patchSection('chat_memory', sectionPatch)
      }
    } else if (itemId === 'assistant_llm') {
      const assistantKeys = new Set([
        'enabled',
        'base_url',
        'api_key',
        'model',
      ])
      const assistantPatch: Record<string, unknown> = {}
      const embeddingPatch: Record<string, unknown> = {}
      for (const [key, value] of Object.entries(cleaned)) {
        if (assistantKeys.has(key)) assistantPatch[key] = value
        if (key === 'embedding_enabled') embeddingPatch.enabled = value
        if (key === 'embedding_model') embeddingPatch.model = value
      }
      if (Object.keys(assistantPatch).length) {
        await store.patchSection('assistant_llm', assistantPatch)
      }
      if (Object.keys(embeddingPatch).length) {
        await store.patchSection('chat_memory', { embedding: embeddingPatch })
      }
    } else if (!plans) {
      return
    } else {
      for (const plan of plans) {
        const sectionPatch: Record<string, unknown> = {}
        for (const key of plan.keys) {
          if (key in cleaned) sectionPatch[key] = cleaned[key]
        }
        if (Object.keys(sectionPatch).length) {
          await store.patchSection(plan.section, sectionPatch)
        }
      }
    }
    applySettings(await store.load())
    await loadRuntime()
    ElMessage.success('已保存')
    closeDialog()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '保存失败')
  }
}
</script>

<style scoped>
.settings-table {
  cursor: pointer;
}
.settings-table :deep(.el-table__row:hover) {
  background: #f1f5f9;
}
.edit-icon {
  color: #94a3b8;
}
</style>
