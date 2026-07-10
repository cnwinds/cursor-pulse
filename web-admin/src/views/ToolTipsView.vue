<template>
  <div class="tips-page" v-loading="loading">
    <header class="page-header">
      <div>
        <h2>使用技巧知识库</h2>
        <p class="desc">团队分享的 AI 工具心得。员工可通过钉钉发送「心得：…」自动整理入库。</p>
      </div>
      <div class="header-actions">
        <el-select v-model="period" style="width: 140px" @change="load">
          <el-option v-for="p in periodOptions" :key="p" :label="p" :value="p" />
        </el-select>
        <el-button v-if="canPublish" @click="publishDigest" :loading="publishing">发群精选</el-button>
        <el-button v-if="canWrite" type="primary" @click="openCreate">录入心得</el-button>
      </div>
    </header>

    <el-table :data="entries" stripe>
      <el-table-column label="标题" prop="title" min-width="180" />
      <el-table-column label="作者" prop="author_name" width="100" />
      <el-table-column label="工具" prop="vendor_name" width="90" />
      <el-table-column label="标签" width="160">
        <template #default="{ row }">
          <el-tag v-for="t in row.tags" :key="t" size="small" style="margin-right: 4px">{{ t }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="置顶" width="70">
        <template #default="{ row }">
          <el-tag v-if="row.pinned" type="warning">置顶</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="正文" min-width="240" show-overflow-tooltip prop="body" />
      <el-table-column v-if="canWrite" label="操作" width="160" fixed="right">
        <template #default="{ row }">
          <el-button link @click="togglePin(row)">{{ row.pinned ? '取消置顶' : '置顶' }}</el-button>
          <el-button link type="danger" @click="hideEntry(row)">隐藏</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" title="录入心得" width="520px">
      <el-input v-model="rawText" type="textarea" :rows="8" placeholder="心得：本月用 Cursor 的 Composer 做重构的几个技巧…" />
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submit">保存并整理</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import client from '@/api/client'
import { useAuthStore } from '@/stores/auth'

interface Entry {
  id: string
  title: string
  author_name: string
  vendor_name: string | null
  tags: string[]
  body: string
  pinned: boolean
}

const auth = useAuthStore()
const loading = ref(false)
const saving = ref(false)
const publishing = ref(false)
const entries = ref<Entry[]>([])
const dialogVisible = ref(false)
const rawText = ref('')

const now = new Date()
const period = ref(`${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`)
const periodOptions = computed(() => {
  const list: string[] = []
  for (let i = 0; i < 6; i++) {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1)
    list.push(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`)
  }
  return list
})

const canWrite = computed(() => auth.hasPermission('knowledge:write'))
const canPublish = computed(() => auth.hasPermission('reports:publish'))

async function load() {
  loading.value = true
  try {
    const res = await client.get('/api/v2/knowledge', { params: { period: period.value } })
    entries.value = res.data
  } finally {
    loading.value = false
  }
}

function openCreate() {
  rawText.value = ''
  dialogVisible.value = true
}

async function submit() {
  if (!rawText.value.trim()) return
  saving.value = true
  try {
    await client.post('/api/v2/knowledge', { raw_text: rawText.value, period: period.value })
    ElMessage.success('已收录')
    dialogVisible.value = false
    await load()
  } catch {
    ElMessage.error('保存失败')
  } finally {
    saving.value = false
  }
}

async function togglePin(row: Entry) {
  await client.patch(`/api/v2/knowledge/${row.id}`, { pinned: !row.pinned })
  await load()
}

async function hideEntry(row: Entry) {
  await client.patch(`/api/v2/knowledge/${row.id}`, { status: 'hidden' })
  ElMessage.success('已隐藏')
  await load()
}

async function publishDigest() {
  publishing.value = true
  try {
    const res = await client.post(`/api/v2/knowledge/digest/${period.value}/publish`)
    ElMessage.success('已发到钉钉群')
    console.log(res.data.text)
  } catch {
    ElMessage.error('发布失败')
  } finally {
    publishing.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.page-header {
  display: flex;
  justify-content: space-between;
  margin-bottom: 20px;
}
.desc {
  color: #64748b;
  font-size: 14px;
  margin-top: 4px;
}
.header-actions {
  display: flex;
  gap: 8px;
}
</style>
