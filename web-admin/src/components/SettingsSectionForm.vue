<template>
  <el-form label-width="160px" @submit.prevent="onSave">
    <el-form-item v-for="field in fields" :key="field.key" :label="field.label">
      <el-switch
        v-if="field.type === 'switch'"
        v-model="local[field.key]"
      />
      <el-input
        v-else-if="field.type === 'textarea'"
        v-model="local[field.key]"
        type="textarea"
        :rows="3"
      />
      <el-input-number
        v-else-if="field.type === 'number'"
        v-model="local[field.key]"
      />
      <el-input v-else v-model="local[field.key]" />
    </el-form-item>
    <el-form-item v-if="canWrite">
      <el-button type="primary" native-type="submit">保存本节</el-button>
    </el-form-item>
    <el-alert v-else type="info" :closable="false" title="当前账号无 settings:write 权限，仅可查看" />
  </el-form>
</template>

<script setup lang="ts">
import { reactive, watch } from 'vue'
import { useAuthStore } from '@/stores/auth'

const props = defineProps<{
  model: Record<string, unknown>
  fields: Array<{ key: string; label: string; type?: string }>
}>()

const emit = defineEmits<{ save: [Record<string, unknown>] }>()
const auth = useAuthStore()
const canWrite = auth.hasPermission('settings:write')
const local = reactive<Record<string, unknown>>({})

watch(
  () => props.model,
  (m) => Object.assign(local, m),
  { immediate: true, deep: true },
)

function onSave() {
  if (!canWrite) return
  const patch: Record<string, unknown> = {}
  for (const field of props.fields) {
    patch[field.key] = local[field.key]
  }
  emit('save', patch)
}
</script>
