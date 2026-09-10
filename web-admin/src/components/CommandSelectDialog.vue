<template>
  <el-dialog
    :model-value="open"
    title="选择复制命令"
    width="680px"
    @close="emit('close')"
  >
    <div v-if="commands.length === 0" class="empty-state">
      <el-empty description="无可用代理地址" />
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        style="margin-top: 16px"
      >
        请在「系统设置」中配置代理地址
      </el-alert>
    </div>
    <div v-else class="commands-list">
      <div
        v-for="(cmd, index) in commands"
        :key="index"
        class="command-item"
      >
        <div class="command-header">
          <div class="proxy-info">
            <el-tag type="info" size="small">{{ cmd.proxy_name }}</el-tag>
            <el-tag
              :type="cmd.shell === 'powershell' ? 'primary' : 'success'"
              size="small"
              style="margin-left: 8px"
            >
              {{ cmd.shell === 'powershell' ? 'PowerShell / Windows' : 'Bash / Linux' }}
            </el-tag>
          </div>
          <el-button
            size="small"
            type="primary"
            @click="copyCommand(cmd)"
          >
            复制
          </el-button>
        </div>
        <div class="command-text">
          <code>{{ cmd.command }}</code>
        </div>
      </div>
    </div>
  </el-dialog>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'

export interface CommandOption {
  proxy_url: string
  proxy_name: string
  shell: string
  command: string
}

const props = defineProps<{
  open: boolean
  commands: CommandOption[]
}>()

const emit = defineEmits<{
  close: []
}>()

async function copyCommand(cmd: CommandOption) {
  try {
    await navigator.clipboard.writeText(cmd.command)
    ElMessage.success(
      `已复制 ${cmd.proxy_name} - ${cmd.shell === 'powershell' ? 'PowerShell' : 'Linux'} 命令`
    )
    emit('close')
  } catch (err: any) {
    ElMessage.error(err?.message || '复制失败')
  }
}
</script>

<style scoped>
.empty-state {
  padding: 24px;
  text-align: center;
}

.commands-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
  max-height: 500px;
  overflow-y: auto;
}

.command-item {
  border: 1px solid var(--el-border-color);
  border-radius: 4px;
  padding: 12px;
  background-color: var(--el-bg-color-page);
}

.command-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.proxy-info {
  display: flex;
  align-items: center;
}

.command-text {
  font-family: 'Courier New', monospace;
  font-size: 12px;
  padding: 8px;
  background-color: var(--el-fill-color-light);
  border-radius: 4px;
  overflow-x: auto;
  word-break: break-all;
  white-space: pre-wrap;
}

.command-text code {
  color: var(--el-text-color-primary);
}
</style>
