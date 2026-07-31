import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import axios from 'axios'
import client from '@/api/client'
import {
  applySession,
  clearStoredSession,
  readAccessToken,
  readRefreshToken,
  readStoredUser,
  type SessionUser,
} from '@/utils/authSession'

export type PortalUser = SessionUser

export const useAuthStore = defineStore('auth', () => {
  const token = ref(readAccessToken())
  const refreshToken = ref(readRefreshToken())
  const user = ref<PortalUser | null>(readStoredUser())

  const isLoggedIn = computed(() => Boolean(token.value && user.value))

  function setSession(accessToken: string, portalUser: PortalUser, refresh?: string | null) {
    applySession(accessToken, portalUser, refresh)
    token.value = accessToken
    user.value = portalUser
    if (refresh) {
      refreshToken.value = refresh
    }
  }

  function syncFromStorage() {
    token.value = readAccessToken()
    refreshToken.value = readRefreshToken()
    user.value = readStoredUser()
  }

  function clearSession() {
    clearStoredSession()
    token.value = ''
    refreshToken.value = ''
    user.value = null
  }

  async function logout() {
    const currentRefresh = readRefreshToken() || refreshToken.value
    try {
      if (currentRefresh) {
        // Use bare axios to avoid the shared client's 401→refresh interceptor.
        await axios.post('/api/auth/logout', { refresh_token: currentRefresh }, { timeout: 10000 })
      }
    } catch {
      // Best-effort revoke; always clear local session.
    } finally {
      clearSession()
    }
  }

  function hasPermission(code: string) {
    return user.value?.permissions.includes(code) ?? false
  }

  async function fetchMe() {
    const { data } = await client.get('/api/auth/me')
    user.value = data
    applySession(token.value || readAccessToken(), data, readRefreshToken() || null)
    return data
  }

  return {
    token,
    refreshToken,
    user,
    isLoggedIn,
    setSession,
    syncFromStorage,
    clearSession,
    logout,
    hasPermission,
    fetchMe,
  }
})
