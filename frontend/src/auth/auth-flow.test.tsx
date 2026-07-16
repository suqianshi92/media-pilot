import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
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
  expect(screen.getByText('用户名', { selector: 'label' })).toBeInTheDocument()
  expect(screen.getByText('密码', { selector: 'label' })).toBeInTheDocument()
  expect(screen.getByText('确认密码', { selector: 'label' })).toBeInTheDocument()
  expect(screen.getByLabelText('用户名')).toBeInTheDocument()
  expect(screen.getByLabelText('密码', { selector: 'input' })).toBeInTheDocument()
  expect(screen.getByLabelText('确认密码')).toBeInTheDocument()
  expect(screen.getByText('密码至少 8 个字符')).toBeInTheDocument()
  expect(router.state.location.pathname).toBe('/initialize')
})

it('does not initialize when the password confirmation differs', async () => {
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(response({
    status: 'success', data: { initialized: false },
  }))

  renderRoute('/initialize')

  await userEvent.type(await screen.findByLabelText('用户名'), 'Owner')
  await userEvent.type(screen.getByLabelText('密码', { selector: 'input' }), 'password-one')
  await userEvent.type(screen.getByLabelText('确认密码'), 'password-two')
  await userEvent.click(screen.getByRole('button', { name: '创建并登录' }))

  expect(screen.getByRole('alert')).toHaveTextContent('两次输入的密码不一致')
  expect(fetchMock).toHaveBeenCalledOnce()
})

it('initializes when both password entries match', async () => {
  const fetchMock = vi.spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(response({ status: 'success', data: { initialized: false } }))
    .mockResolvedValueOnce(response({ status: 'success', data: { user: {
      id: 'owner', username: 'Owner', role: 'admin', can_access_adult: true, is_enabled: true,
    } } }))

  renderRoute('/initialize')

  await userEvent.type(await screen.findByLabelText('用户名'), 'Owner')
  await userEvent.type(screen.getByLabelText('密码', { selector: 'input' }), 'owner-password')
  await userEvent.type(screen.getByLabelText('确认密码'), 'owner-password')
  await userEvent.click(screen.getByRole('button', { name: '创建并登录' }))

  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
  expect(fetchMock.mock.calls[1][0]).toBe('/api/v1/auth/initialize')
  expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toEqual({
    username: 'Owner',
    password: 'owner-password',
  })
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
