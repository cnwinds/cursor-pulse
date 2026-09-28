<template>
  <div v-if="parts" class="time-stack">
    <span class="time-date">{{ parts.date }}</span>
    <span class="time-clock">{{ parts.time }}</span>
  </div>
  <span v-else class="muted">—</span>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { formatChinaDateTimeParts } from '@/utils/time'

const props = defineProps<{
  iso?: string | null
}>()

const parts = computed(() => formatChinaDateTimeParts(props.iso))
</script>

<style scoped>
.time-stack {
  display: inline-flex;
  flex-direction: column;
  align-items: center;
  line-height: 1.2;
  font-variant-numeric: tabular-nums;
}
.time-date {
  font-size: var(--pulse-text-sm);
  color: var(--el-text-color-primary);
}
.time-clock {
  font-size: var(--pulse-text-xs);
  color: var(--el-text-color-secondary);
}
.muted {
  color: var(--el-text-color-secondary);
}
</style>
