<template>
  <div class="rules-panel" v-loading="loading">
    <LoanSelectionRules
      v-if="loaded"
      :selection="loanSelection"
      :jev-enabled="jevEnabled"
      @saved="onSaved"
    />
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useSettingsStore } from '@/stores/settings'
import LoanSelectionRules from '@/components/LoanSelectionRules.vue'

const route = useRoute()

const store = useSettingsStore()
const loading = ref(false)
const loaded = ref(false)
const loanSelection = ref<Record<string, unknown>>({})
const jevEnabled = ref(false)

async function load(silent = false) {
  if (!silent) loading.value = true
  try {
    const data = await store.load()
    loanSelection.value = { ...(data?.tool_center?.loan_selection || {}) }
    jevEnabled.value = data?.jev?.enabled === true
    loaded.value = true
  } finally {
    loading.value = false
  }
}

watch(
  () => route.query.tab,
  (tab) => {
    if (tab === 'rules' && loaded.value) load(true)
  },
)

function onSaved(data: {
  tool_center?: { loan_selection?: Record<string, unknown> }
}) {
  loanSelection.value = { ...(data?.tool_center?.loan_selection || loanSelection.value) }
}

onMounted(load)
</script>

<style scoped>
.rules-panel {
  min-height: 120px;
}
</style>
