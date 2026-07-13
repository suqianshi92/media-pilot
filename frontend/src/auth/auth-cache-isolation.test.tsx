import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'

import { AuthProvider, useAuth } from '@/auth/auth-context'
import { apiFetch } from '@/services/http-client'

function LogoutButton() {
  const auth = useAuth()
  return <button onClick={() => void auth.logout()}>logout</button>
}

function LoginButton() {
  const auth = useAuth()
  return <button onClick={() => void auth.login('Bob', 'bob password')}>login-bob</button>
}

function AuthState() {
  return <div>{useAuth().state}</div>
}

afterEach(() => {
  cleanup()
  vi.unstubAllEnvs()
  vi.restoreAllMocks()
})

it('clears user-scoped query data when the browser account logs out', async () => {
  const queryClient = new QueryClient()
  queryClient.setQueryData(['flows', 'alice'], { private: 'alice-data' })
  vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(JSON.stringify({
    status: 'success', data: {},
  }), { status: 200, headers: { 'Content-Type': 'application/json' } }))

  render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider><LogoutButton /></AuthProvider>
    </QueryClientProvider>,
  )
  await userEvent.click(screen.getByText('logout'))

  await waitFor(() => {
    expect(queryClient.getQueryData(['flows', 'alice'])).toBeUndefined()
  })
})

it('clears the previous account cache before exposing a newly logged-in user', async () => {
  const queryClient = new QueryClient()
  queryClient.setQueryData(['task-detail', 'alice-task'], { private: 'alice-data' })
  vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(JSON.stringify({
    status: 'success',
    data: { user: {
      id: 'bob', username: 'Bob', role: 'user', can_access_adult: false, is_enabled: true,
    } },
  }), { status: 200, headers: { 'Content-Type': 'application/json' } }))

  render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider><LoginButton /></AuthProvider>
    </QueryClientProvider>,
  )
  await userEvent.click(screen.getByText('login-bob'))

  await waitFor(() => {
    expect(queryClient.getQueryData(['task-detail', 'alice-task'])).toBeUndefined()
  })
})

it('aborts old-session requests and resets the request controller on a 401', async () => {
  vi.stubEnv('VITE_API_MODE', 'real')
  const queryClient = new QueryClient()
  vi.spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(new Response(JSON.stringify({
      status: 'success', data: { initialized: true },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    .mockResolvedValueOnce(new Response(JSON.stringify({
      status: 'success', data: { user: {
        id: 'alice', username: 'Alice', role: 'user', can_access_adult: false, is_enabled: true,
      } },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
  render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider><AuthState /></AuthProvider>
    </QueryClientProvider>,
  )
  await screen.findByText('authenticated')

  let oldSignal: AbortSignal | undefined
  vi.mocked(fetch).mockImplementationOnce((_input, init) => {
    oldSignal = init?.signal ?? undefined
    return new Promise<Response>(() => {})
  })
  void apiFetch('/api/v1/tasks/old-session')

  vi.mocked(fetch).mockResolvedValueOnce(new Response(null, { status: 401 }))
  await act(async () => {
    await apiFetch('/api/v1/tasks/expired-session')
  })

  expect(oldSignal?.aborted).toBe(true)

  let newSignal: AbortSignal | undefined
  vi.mocked(fetch).mockImplementationOnce((_input, init) => {
    newSignal = init?.signal ?? undefined
    return Promise.resolve(new Response('{}', { status: 200 }))
  })
  await apiFetch('/api/v1/tasks/new-session')

  expect(newSignal).not.toBe(oldSignal)
  expect(newSignal?.aborted).toBe(false)
})
