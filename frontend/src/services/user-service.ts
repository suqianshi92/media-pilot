import { apiFetch } from '@/services/http-client'

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''

export interface ManagedUser {
  id: string
  username: string
  role: 'admin' | 'user'
  can_access_adult: boolean
  is_enabled: boolean
  created_at: string
  updated_at: string
}

interface UserEnvelope {
  status: string
  data: { user: ManagedUser }
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(`${BASE_URL}/api/v1/users${path}`, init)
  const body = await response.json()
  if (!response.ok) throw new Error(body.detail ?? '请求失败')
  return body as T
}

export function createUserService() {
  const body = (method: string, data: unknown): RequestInit => ({
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return {
    list: (page: number, pageSize: number) => json<{
      data: { items: ManagedUser[] }
      meta: { page: number; page_size: number; total: number }
    }>(`?page=${page}&page_size=${pageSize}`),
    create: (data: { username: string; password: string; can_access_adult: boolean }) =>
      json<UserEnvelope>('', body('POST', data)),
    update: (id: string, data: { is_enabled?: boolean; can_access_adult?: boolean }) =>
      json<UserEnvelope>(`/${id}`, body('PATCH', data)),
    resetPassword: (id: string, password: string) =>
      json(`/${id}/reset-password`, body('POST', { password })),
  }
}
