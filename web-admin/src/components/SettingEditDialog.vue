<template>
  <el-dialog
    :model-value="open"
    :title="title"
    width="520px"
    destroy-on-close
    @close="emit('close')"
  >
    <el-alert
      v-if="notice"
      :title="notice"
      type="info"
      :closable="false"
      show-icon
      class="notice"
    />
    <SettingsSectionForm
      v-if="model"
      :model="model"
      :fields="fields"
      :read-only="readOnly"
      save-label="保存"
      @save="emit('save', $event)"
    />
  </el-dialog>
</template>

<script setup lang="ts">
import SettingsSectionForm, { type SettingsField } from '@/components/SettingsSectionForm.vue'

defineProps<{
  open: boolean
  title: string
  model: Record<string, unknown> | null
  fields: SettingsField[]
  notice?: string
  readOnly?: boolean
}>()

const emit = defineEmits<{
  close: []
  save: [Record<string, unknown>]
}>()
</script>

<style scoped>
.notice {
  margin-bottom: 12px;
}
</style>
