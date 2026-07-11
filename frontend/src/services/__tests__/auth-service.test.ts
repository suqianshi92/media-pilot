import { beforeEach, expect, it, vi } from 'vitest'

import { createAuthService } from '@/services/auth-service'

beforeEach(() => {
  vi.restoreAllMocks()
})

it('initializes through the shared CSRF request layer', async () => {
  document.cookie = 'media_pilot_csrf=auth-csrf; path=/'
  vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(JSON.stringify({
    status: 'success',
    data: { user: { id: '1', username: 'Owner', role: 'admin', can_access_adult: true, is_enabled: true } },
  }), { status: 200, headers: { 'Content-Type': 'application/json' } }))

  await createAuthService().initialize('Owner', 'owner password')

  const init = vi.mocked(fetch).mock.calls[0][1]
  expect(new Headers(init?.headers).get('X-CSRF-Token')).toBe('auth-csrf')
})
