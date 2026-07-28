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
  margin-bottom: 16px;
}
.stat-label {
  color: #64748b;
  font-size: 13px;
}
.stat-value {
  font-size: 28px;
  font-weight: 600;
  margin-top: 8px;
}
.stat-sub {
  font-size: 12px;
  color: #94a3b8;
  margin-top: 6px;
}
</style>
