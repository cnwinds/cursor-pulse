<template>
  <div v-loading="loading">
    <el-tabs v-model="tab">
      <el-tab-pane label="集成状态" name="integrations">
        <el-descriptions v-if="integrations" :column="1" border>
          <el-descriptions-item label="钉钉应用">
            <StatusTag :ok="integrations.dingtalk.app_configured" />
          </el-descriptions-item>
          <el-descriptions-item label="机器人 Code">
            <StatusTag :ok="integrations.dingtalk.robot_code" />
          </el-descriptions-item>
          <el-descriptions-item label="群 openConversationId">
            <StatusTag :ok="integrations.dingtalk.group_configured" />
          </el-descriptions-item>
          <el-descriptions-item label="LLM 对话">
            <StatusTag :ok="integrations.llm.enabled" :label="integrations.llm.model" />
          </el-descriptions-item>
          <el-descriptions-item label="LLM 视觉">
            <StatusTag :ok="integrations.llm.vision_enabled" />
          </el-descriptions-item>
          <el-descriptions-item label="API Key">
            <StatusTag :ok="integrations.llm.api_key_configured" />
          </el-descriptions-item>
          <el-descriptions-item label="BI Webhook">
            <StatusTag :ok="integrations.integrations.bi_webhook" />
          </el-descriptions-item>
          <el-descriptions-item label="对象存储">
            <StatusTag :ok="integrations.object_storage.enabled" />
          </el-descriptions-item>
          <el-descriptions-item label="Cursor Teams API">
            <StatusTag :ok="integrations.cursor_teams.enabled" />
          </el-descriptions-item>
          <el-descriptions-item label="记忆自进化">
            <StatusTag :ok="integrations.memory.evolution_enabled" />
          </el-descriptions-item>
          <el-descriptions-item label="小脉人格">
            {{ integrations.persona.name }} · {{ integrations.persona.role }}
          </el-descriptions-item>
          <el-descriptions-item label="数据库">
            {{ integrations.database.kind }} — {{ integrations.database.url_hint }}
          </el-descriptions-item>
        </el-descriptions>
      </el-tab-pane>

      <el-tab-pane label="调度计划" name="schedule">
        <el-alert
          v-if="schedule?.note"
          :title="schedule.note"
          type="info"
          :closable="false"
          class="mb"
        />
        <el-table v-if="schedule" :data="schedule.jobs" stripe>
          <el-table-column prop="name" label="任务" />
          <el-table-column prop="cron" label="计划" />
          <el-table-column prop="process" label="运行进程" width="140" />
        </el-table>
        <p v-if="schedule" class="meta">
          时区 {{ schedule.timezone }} · 当前账期 {{ schedule.current_period }} ·
          收集窗口 {{ schedule.collection_window.start_day }}–{{ schedule.collection_window.deadline_day }} 日
        </p>
      </el-tab-pane>

      <el-tab-pane label="进程说明" name="processes">
        <el-descriptions v-if="integrations?.processes" :column="1" border>
          <el-descriptions-item
            v-for="(val, key) in integrations.processes"
            :key="key"
            :label="key"
          >
            {{ val }}
          </el-descriptions-item>
        </el-descriptions>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import client from '@/api/client'
import StatusTag from '@/components/StatusTag.vue'

const loading = ref(false)
const tab = ref('integrations')
const integrations = ref<any>(null)
const schedule = ref<any>(null)

onMounted(async () => {
  loading.value = true
  try {
    const [intRes, schRes] = await Promise.all([
      client.get('/api/system/integrations'),
      client.get('/api/system/schedule'),
    ])
    integrations.value = intRes.data
    schedule.value = schRes.data
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.mb {
  margin-bottom: 16px;
}
.meta {
  margin-top: 12px;
  color: #64748b;
  font-size: 13px;
}
</style>
