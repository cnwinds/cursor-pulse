<template>
  <el-card shadow="never" class="stat-card">
    <div class="stat-label">{{ label }}</div>
    <div class="stat-value">{{ value }}</div>
    <div v-if="sub" class="stat-sub">{{ sub }}</div>
  </el-card>
</template>

<script setup lang="ts">
defineProps<{
  label: string
  value: string | number
  sub?: string
}>()
</script>

<style scoped>
.stat-card {
  margin-bottom: var(--pulse-space-section);
  border-top: 3px solid var(--el-color-primary) !important;
  transition:
    transform var(--pulse-transition),
    box-shadow var(--pulse-transition);
}

.stat-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 20px rgba(15, 23, 42, 0.08) !important;
}

.stat-label {
  margin-bottom: 0;
}

.stat-value {
  margin-top: 8px;
}

.stat-sub {
  margin-top: 6px;
  line-height: var(--pulse-leading-normal);
}
</style>
