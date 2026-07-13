import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'

import { AuthProvider, useAuth } from '@/auth/auth-context'

function LogoutButton() {
  const auth = useAuth()
  return <button onClick={() => void auth.logout()}>logout</button>
}

function LoginButton() {
  const auth = useAuth()
  return <button onClick={() => void auth.login('Bob', 'bob password')}>login-bob</button>
}

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
