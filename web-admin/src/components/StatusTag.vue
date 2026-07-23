<template>
  <el-tag :type="ok ? 'success' : 'info'" size="small">
    {{ ok ? (label || '已配置') : '未配置' }}
  </el-tag>
</template>

<script setup lang="ts">
defineProps<{ ok: boolean; label?: string }>()
</script>
