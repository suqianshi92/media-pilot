import { expect, it, vi } from 'vitest'

import { createUserService } from '@/services/user-service'

it('uses server pagination and CSRF for user mutations', async () => {
  document.cookie = 'media_pilot_csrf=user-csrf; path=/'
  vi.spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(new Response(JSON.stringify({ status: 'success', data: { items: [] }, meta: { page: 2, page_size: 20, total: 0 } }), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ status: 'success', data: { user: {} } }), { status: 200 }))

  const service = createUserService()
  await service.list(2, 20)
  await service.update('user-1', { is_enabled: false })

  expect(String(vi.mocked(fetch).mock.calls[0][0])).toContain('page=2&page_size=20')
  const update = vi.mocked(fetch).mock.calls[1][1]
  expect(update?.method).toBe('PATCH')
  expect(new Headers(update?.headers).get('X-CSRF-Token')).toBe('user-csrf')
})
