/** Shared portal session keys + localStorage helpers (used by auth store and API client). */

export const ACCESS_KEY = 'pulse_token'
export const REFRESH_KEY = 'pulse_refresh_token'
export const USER_KEY = 'pulse_user'

export interface SessionUser {
  id: string
  display_name: string
  channel_user_id: string
  portal_role: string | null
  permissions: string[]
}

export function readAccessToken(): string {
  return localStorage.getItem(ACCESS_KEY) || ''
}

export function readRefreshToken(): string {
  return localStorage.getItem(REFRESH_KEY) || ''
}

export function readStoredUser(): SessionUser | null {
  const raw = localStorage.getItem(USER_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw) as SessionUser
  } catch {
    return null
  }
}

export function applySession(
  accessToken: string,
  user: SessionUser,
  refreshToken?: string | null,
): void {
  localStorage.setItem(ACCESS_KEY, accessToken)
  localStorage.setItem(USER_KEY, JSON.stringify(user))
  if (refreshToken) {
    localStorage.setItem(REFRESH_KEY, refreshToken)
  }
}

export function clearStoredSession(): void {
  localStorage.removeItem(ACCESS_KEY)
  localStorage.removeItem(REFRESH_KEY)
  localStorage.removeItem(USER_KEY)
}
