import { defineStore } from 'pinia'
import { ref } from 'vue'
import client from '@/api/client'

export const useSettingsStore = defineStore('settings', () => {
  const data = ref<Record<string, any> | null>(null)
  const loading = ref(false)

  async function load() {
    loading.value = true
    try {
      const res = await client.get('/api/settings')
      data.value = res.data
      return res.data
    } finally {
      loading.value = false
    }
  }

  async function patchSection(section: string, patch: Record<string, unknown>) {
    const res = await client.patch(`/api/settings/${section}`, { data: patch })
    data.value = res.data
    return res.data
  }

  return { data, loading, load, patchSection }
})
