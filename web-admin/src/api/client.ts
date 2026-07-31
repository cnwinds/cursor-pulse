import axios, { type AxiosError, type InternalAxiosRequestConfig } from 'axios'
import {
  applySession,
  clearStoredSession,
  readAccessToken,
  readRefreshToken,
  type SessionUser,
} from '@/utils/authSession'

const client = axios.create({
  baseURL: '',
  timeout: 30000,
})

type RetriableConfig = InternalAxiosRequestConfig & { _retry?: boolean }

let refreshInFlight: Promise<string | null> | null = null

function redirectToLogin() {
  if (!window.location.pathname.startsWith('/login')) {
    window.location.href = '/login'
  }
}

function isAuthRefreshUrl(url?: string) {
  if (!url) return false
  return (
    url.includes('/api/auth/refresh') ||
    url.includes('/api/auth/login') ||
    url.includes('/api/auth/logout') ||
    url.includes('/api/auth/dingtalk/callback') ||
    url.includes('/api/auth/feishu/callback')
  )
}

async function syncPiniaFromStorage() {
  try {
    const { useAuthStore } = await import('@/stores/auth')
    useAuthStore().syncFromStorage()
  } catch {
    // Pinia may not be ready during early boot; localStorage is still updated.
  }
}

async function refreshAccessToken(): Promise<string | null> {
  const refresh = readRefreshToken()
  if (!refresh) return null
  try {
    const { data } = await axios.post(
      '/api/auth/refresh',
      { refresh_token: refresh },
      { timeout: 30000 },
    )
    const access = data?.access_token as string | undefined
    const nextRefresh = data?.refresh_token as string | undefined
    const user = data?.user as SessionUser | undefined
    if (!access || !nextRefresh || !user) {
      clearStoredSession()
      await syncPiniaFromStorage()
      return null
    }
    applySession(access, user, nextRefresh)
    await syncPiniaFromStorage()
    return access
  } catch {
    clearStoredSession()
    await syncPiniaFromStorage()
    return null
  }
}

function refreshOnce(): Promise<string | null> {
  if (!refreshInFlight) {
    refreshInFlight = refreshAccessToken().finally(() => {
      refreshInFlight = null
    })
  }
  return refreshInFlight
}

client.interceptors.request.use((config) => {
  const token = readAccessToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

client.interceptors.response.use(
  (res) => res,
  async (err: AxiosError) => {
    const status = err.response?.status
    const original = err.config as RetriableConfig | undefined
    if (status !== 401 || !original || isAuthRefreshUrl(original.url)) {
      return Promise.reject(err)
    }

    // Already retried once and still unauthorized → force re-login.
    if (original._retry) {
      clearStoredSession()
      await syncPiniaFromStorage()
      redirectToLogin()
      return Promise.reject(err)
    }

    original._retry = true
    const access = await refreshOnce()
    if (!access) {
      redirectToLogin()
      return Promise.reject(err)
    }
    original.headers = original.headers || {}
    original.headers.Authorization = `Bearer ${access}`
    return client.request(original)
  },
)

export default client
