import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import client from '@/api/client'

export interface PortalUser {
  id: string
  display_name: string
  dingtalk_user_id: string
  portal_role: string | null
  permissions: string[]
}

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('pulse_token') || '')
  const user = ref<PortalUser | null>(
    localStorage.getItem('pulse_user') ? JSON.parse(localStorage.getItem('pulse_user')!) : null,
  )

  const isLoggedIn = computed(() => Boolean(token.value && user.value))

  function setSession(accessToken: string, portalUser: PortalUser) {
    token.value = accessToken
    user.value = portalUser
    localStorage.setItem('pulse_token', accessToken)
    localStorage.setItem('pulse_user', JSON.stringify(portalUser))
  }

  function logout() {
    token.value = ''
    user.value = null
    localStorage.removeItem('pulse_token')
    localStorage.removeItem('pulse_user')
  }

  function hasPermission(code: string) {
    return user.value?.permissions.includes(code) ?? false
  }

  async function fetchMe() {
    const { data } = await client.get('/api/auth/me')
    user.value = data
    localStorage.setItem('pulse_user', JSON.stringify(data))
    return data
  }

  return { token, user, isLoggedIn, setSession, logout, hasPermission, fetchMe }
})
