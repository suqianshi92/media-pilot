import type { ApiEnvelope } from '@/types/api'
import type {
  AppSettingsResponse,
  AppSettingsUpdateRequest,
  ConnectivityResponse,
} from '@/types/settings'

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''

async function apiGet<T>(path: string): Promise<T> {
  const url = `${BASE_URL}/api/v1${path}`
  const resp = await fetch(url)
  const body = await resp.json()
  if (body.status === 'error') {
    const msg = body.messages?.[0]?.text ?? 'unknown error'
    throw new Error(msg)
  }
  return body as T
}

async function apiPut<T>(path: string, data: unknown): Promise<T> {
  const url = `${BASE_URL}/api/v1${path}`
  const resp = await fetch(url, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  const body = await resp.json()
  if (body.status === 'error') {
    const msg = body.messages?.[0]?.text ?? 'unknown error'
    throw new Error(msg)
  }
  return body as T
}

export function createSettingsService() {
  return {
    getSettings(): Promise<ApiEnvelope<AppSettingsResponse>> {
      return apiGet<ApiEnvelope<AppSettingsResponse>>('/settings')
    },

    updateSettings(
      data: AppSettingsUpdateRequest,
    ): Promise<ApiEnvelope<AppSettingsResponse['app_settings']>> {
      return apiPut<ApiEnvelope<AppSettingsResponse['app_settings']>>('/settings', data)
    },

    getConnectivity(): Promise<ApiEnvelope<ConnectivityResponse>> {
      return apiGet<ApiEnvelope<ConnectivityResponse>>('/settings/connectivity')
    },
  }
}

export type SettingsService = ReturnType<typeof createSettingsService>
