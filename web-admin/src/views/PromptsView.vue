<template>
  <div class="prompts-page" v-loading="loading">
    <header class="page-header">
      <div>
        <h2>Prompt 一览</h2>
        <p class="desc">查看当前部署的人设文案片段与拼接预览。</p>
      </div>
      <el-button @click="loadPrompts">刷新</el-button>
    </header>

    <el-alert
      type="info"
      show-icon
      :closable="false"
      title="人设文案以 assistant_platform/prompts/docs 为准。"
      class="source-alert"
    />

    <el-tabs v-model="activeTab" class="prompt-tabs">
      <el-tab-pane label="片段列表" name="fragments">
        <el-table :data="fragments" stripe>
          <el-table-column prop="key" label="片段" min-width="160" />
          <el-table-column prop="description" label="说明" min-width="200" />
          <el-table-column prop="path" label="仓库路径" min-width="280">
            <template #default="{ row }"><code>{{ row.path }}</code></template>
          </el-table-column>
          <el-table-column prop="content_preview" label="内容摘要" min-width="240" show-overflow-tooltip />
        </el-table>
        <el-empty v-if="!fragments.length && !loading" description="暂无 Prompt 片段" />
      </el-tab-pane>

      <el-tab-pane label="拼接预览" name="preview">
        <el-card shadow="never">
          <template #header>运行时 Prompt 预览</template>
          <div v-if="previewMarkdown" class="markdown-body" v-html="renderMarkdown(previewMarkdown)" />
          <el-empty v-else-if="!loading" description="暂无预览内容" />
        </el-card>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import client from '@/api/client'
import { renderMarkdown } from '@/utils/markdown'

interface PromptFragment {
  key: string
  path: string
  description: string
  content_preview: string
}

const loading = ref(false)
const activeTab = ref('fragments')
const fragments = ref<PromptFragment[]>([])
const previewMarkdown = ref('')

function errorMessage(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail
  return detail || fallback
}

async function loadPrompts() {
  loading.value = true
  try {
    const [fragmentsResponse, previewResponse] = await Promise.all([
      client.get<{ fragments: PromptFragment[] }>('/api/v2/assistant/prompts'),
      client.get<{ markdown: string }>('/api/v2/assistant/prompts/preview'),
    ])
    fragments.value = fragmentsResponse.data.fragments || []
    previewMarkdown.value = previewResponse.data.markdown || ''
  } catch (error) {
    ElMessage.error(errorMessage(error, '加载 Prompt 内容失败'))
  } finally {
    loading.value = false
  }
}

onMounted(loadPrompts)
</script>

<style scoped>
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}
.desc {
  margin: 4px 0 0;
  color: #64748b;
  line-height: 1.5;
}
.source-alert {
  margin-bottom: 16px;
}
.prompt-tabs {
  min-height: 360px;
}
.markdown-body {
  line-height: 1.7;
  overflow-wrap: anywhere;
}
:deep(.markdown-body pre) {
  overflow-x: auto;
  padding: 12px;
  border-radius: 6px;
  background: #f1f5f9;
}
:deep(.markdown-body code) {
  padding: 1px 4px;
  border-radius: 3px;
  background: #f1f5f9;
}
</style>
