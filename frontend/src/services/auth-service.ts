import { apiFetch } from '@/services/http-client'

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''

export interface AuthUser {
  id: string
  username: string
  role: 'admin' | 'user'
  can_access_adult: boolean
  is_enabled: boolean
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(`${BASE_URL}/api/v1/auth${path}`, init)
  const body = await response.json()
  if (!response.ok) throw new Error(body.detail ?? '请求失败')
  return body.data as T
}

export function createAuthService() {
  const credentials = (username: string, password: string) => ({
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  return {
    status: () => request<{ initialized: boolean }>('/status'),
    me: () => request<{ user: AuthUser }>('/me'),
    initialize: (username: string, password: string) =>
      request<{ user: AuthUser }>('/initialize', credentials(username, password)),
    login: (username: string, password: string) =>
      request<{ user: AuthUser }>('/login', credentials(username, password)),
    logout: () => request<Record<string, never>>('/logout', { method: 'POST' }),
    changePassword: (currentPassword: string, newPassword: string) =>
      request<Record<string, never>>('/change-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      }),
  }
}
