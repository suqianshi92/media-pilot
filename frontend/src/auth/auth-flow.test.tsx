import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { RouterProvider } from 'react-router-dom'

import { createAppRouter } from '@/app/router'
import '@/i18n'

function response(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function renderRoute(path: string) {
  const router = createAppRouter({ initialEntries: [path] })
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return router
}

beforeEach(() => {
  vi.stubEnv('VITE_API_MODE', 'real')
})

afterEach(() => {
  cleanup()
  vi.unstubAllEnvs()
  vi.restoreAllMocks()
})

it('redirects an uninitialized installation to initial admin creation', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(response({
    status: 'success', data: { initialized: false },
  }))

  const router = renderRoute('/tasks')

  await screen.findByRole('heading', { name: '创建初始管理员' })
  expect(router.state.location.pathname).toBe('/initialize')
})

it('redirects an anonymous user to login and preserves the target', async () => {
  vi.spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(response({ status: 'success', data: { initialized: true } }))
    .mockResolvedValueOnce(response({ detail: 'Authentication required' }, 401))

  const router = renderRoute('/tasks?filter=failed')

  await screen.findByRole('heading', { name: '登录 Media Pilot' })
  expect(router.state.location.pathname).toBe('/login')
  expect(router.state.location.search).toContain(encodeURIComponent('/tasks?filter=failed'))
})

it('blocks a normal user from administrator routes', async () => {
  vi.spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(response({ status: 'success', data: { initialized: true } }))
    .mockResolvedValueOnce(response({ status: 'success', data: { user: {
      id: 'alice', username: 'Alice', role: 'user', can_access_adult: false, is_enabled: true,
    } } }))

  renderRoute('/users')

  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('无权访问此页面'))
  expect(screen.queryByText('创建普通用户')).not.toBeInTheDocument()
})
