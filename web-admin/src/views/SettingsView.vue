<template>
  <div v-loading="loading">
    <el-tabs v-model="tab">
      <el-tab-pane label="收集节奏" name="collection">
        <SettingsSectionForm
          v-if="forms.collection"
          :model="forms.collection"
          :fields="collectionFields"
          @save="save('collection', $event)"
        />
      </el-tab-pane>
      <el-tab-pane label="人格" name="persona">
        <SettingsSectionForm
          v-if="forms.persona"
          :model="forms.persona"
          :fields="personaFields"
          @save="save('persona', $event)"
        />
      </el-tab-pane>
      <el-tab-pane label="记忆" name="memory">
        <SettingsSectionForm
          v-if="forms.memory"
          :model="forms.memory"
          :fields="memoryFields"
          @save="save('memory', $event)"
        />
      </el-tab-pane>
      <el-tab-pane label="告警" name="alerts">
        <SettingsSectionForm
          v-if="forms.alerts"
          :model="forms.alerts"
          :fields="alertsFields"
          @save="save('alerts', $event)"
        />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useSettingsStore } from '@/stores/settings'
import SettingsSectionForm from '@/components/SettingsSectionForm.vue'

const store = useSettingsStore()
const tab = ref('collection')
const loading = computed(() => store.loading)

const forms = reactive({
  collection: null as Record<string, unknown> | null,
  persona: null as Record<string, unknown> | null,
  memory: null as Record<string, unknown> | null,
  alerts: null as Record<string, unknown> | null,
})

const collectionFields = [
  { key: 'timezone', label: '时区' },
  { key: 'start_day', label: '开始日（每月）', type: 'number' },
  { key: 'start_time', label: '开始时间' },
  { key: 'deadline_day', label: '截止日（每月）', type: 'number' },
  { key: 'deadline_time', label: '截止时间' },
  { key: 'daily_check_time', label: '每日检查时间' },
  { key: 'report_day', label: '月报日', type: 'number' },
  { key: 'report_time', label: '月报时间' },
]

const personaFields = [
  { key: 'name', label: '名称' },
  { key: 'role', label: '角色' },
  { key: 'tone', label: '语气', type: 'textarea' },
  { key: 'work_hours', label: '工作时间说明' },
  { key: 'work_start', label: '工作开始' },
  { key: 'work_end', label: '工作结束' },
]

const memoryFields = [
  { key: 'evolution_enabled', label: '启用自进化', type: 'switch' },
  { key: 'evolution_day_of_week', label: '进化星期（0=周一）', type: 'number' },
  { key: 'evolution_time', label: '进化时间' },
  { key: 'retrieval_top_k', label: '检索 Top K', type: 'number' },
  { key: 'conversation_turn_limit', label: '对话轮次上限', type: 'number' },
  { key: 'conversation_keep', label: '保留轮次', type: 'number' },
]

const alertsFields = [
  { key: 'enabled', label: '启用告警', type: 'switch' },
  { key: 'member_events_spike_pct', label: '成员事件激增 %', type: 'number' },
  { key: 'team_events_spike_pct', label: '团队事件激增 %', type: 'number' },
  { key: 'member_cost_spike_usd', label: '成员费用激增 USD', type: 'number' },
]

onMounted(async () => {
  const data = await store.load()
  forms.collection = { ...data.collection }
  forms.persona = { ...data.persona }
  forms.memory = { ...data.memory }
  forms.alerts = { ...data.alerts }
})

async function save(section: string, patch: Record<string, unknown>) {
  try {
    await store.patchSection(section, patch)
    ElMessage.success('已保存')
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '保存失败')
  }
}
</script>
