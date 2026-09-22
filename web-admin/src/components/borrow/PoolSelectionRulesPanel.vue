<template>
  <div class="rules-panel" v-loading="loading">
    <LoanSelectionRules
      v-if="loaded"
      :selection="loanSelection"
      @saved="onSaved"
    />
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useSettingsStore } from '@/stores/settings'
import LoanSelectionRules from '@/components/LoanSelectionRules.vue'

const store = useSettingsStore()
const loading = ref(false)
const loaded = ref(false)
const loanSelection = ref<Record<string, unknown>>({})

async function load() {
  loading.value = true
  try {
    const data = await store.load()
    loanSelection.value = { ...(data?.tool_center?.loan_selection || {}) }
    loaded.value = true
  } finally {
    loading.value = false
  }
}

function onSaved(data: Record<string, unknown>) {
  loanSelection.value = { ...(data?.tool_center?.loan_selection || loanSelection.value) }
}

onMounted(load)
</script>

<style scoped>
.rules-panel {
  min-height: 120px;
}
</style>
