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
  color: var(--pulse-text-secondary);
  font-size: 13px;
  font-weight: 500;
}

.stat-value {
  font-family: var(--pulse-font-mono);
  font-size: 1.65rem;
  font-weight: 600;
  letter-spacing: -0.03em;
  margin-top: 8px;
  color: var(--pulse-text-strong);
}

.stat-sub {
  font-size: 12px;
  color: var(--pulse-text-muted);
  margin-top: 6px;
  line-height: 1.45;
}
</style>
