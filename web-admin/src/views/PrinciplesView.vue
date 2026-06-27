<template>
  <div v-loading="loading">
    <div class="toolbar">
      <el-button v-if="auth.hasPermission('memory:write')" type="primary" @click="dialog = true">
        新增原则
      </el-button>
      <el-button @click="load">刷新</el-button>
    </div>
    <el-table :data="rows" stripe>
      <el-table-column prop="tier" label="层级" width="120" />
      <el-table-column prop="rule" label="规则" min-width="320" />
      <el-table-column prop="origin" label="来源" width="160" show-overflow-tooltip />
      <el-table-column prop="created_at" label="创建" width="180" />
    </el-table>

    <el-dialog v-model="dialog" title="新增原则" width="480px">
      <el-form label-width="80px">
        <el-form-item label="层级">
          <el-select v-model="form.tier">
            <el-option label="底线" value="bottom_line" />
            <el-option label="习得偏好" value="learned" />
          </el-select>
        </el-form-item>
        <el-form-item label="规则">
          <el-input v-model="form.rule" type="textarea" :rows="4" />
        </el-form-item>
        <el-form-item label="来源">
          <el-input v-model="form.origin" placeholder="可选" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" @click="create">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import client from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const loading = ref(false)
const rows = ref<any[]>([])
const dialog = ref(false)
const form = reactive({ tier: 'learned', rule: '', origin: '' })

async function load() {
  loading.value = true
  try {
    const { data } = await client.get('/api/memory/principles')
    rows.value = data
  } finally {
    loading.value = false
  }
}

async function create() {
  if (!form.rule.trim()) {
    ElMessage.warning('请填写规则')
    return
  }
  try {
    await client.post('/api/memory/principles', form)
    dialog.value = false
    form.rule = ''
    form.origin = ''
    ElMessage.success('已添加')
    await load()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '添加失败')
  }
}

onMounted(load)
</script>

<style scoped>
.toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
}
</style>
