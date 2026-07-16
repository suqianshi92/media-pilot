import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'

import { UserManagementPage } from '@/pages/user-management-page'
import '@/i18n'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

it('renders users in the shared server-paginated table and protects admin row', async () => {
  const service = {
    list: vi.fn().mockResolvedValue({
      data: { items: [
        { id: 'admin', username: 'Owner', role: 'admin', is_enabled: true, can_access_adult: true, created_at: '2026-01-01', updated_at: '2026-01-01' },
        { id: 'alice', username: 'Alice', role: 'user', is_enabled: true, can_access_adult: false, created_at: '2026-01-02', updated_at: '2026-01-02' },
      ] },
      meta: { page: 1, page_size: 10, total: 2 },
    }),
    create: vi.fn(), update: vi.fn(), resetPassword: vi.fn(),
  }
  render(<QueryClientProvider client={new QueryClient()}><UserManagementPage service={service} /></QueryClientProvider>)

  await waitFor(() => expect(screen.getAllByText('Owner').length).toBeGreaterThan(0))
  expect(screen.getAllByText('Alice').length).toBeGreaterThan(0)
  expect(screen.getByTestId('user-admin-actions')).toHaveTextContent('受保护')
  const actions = within(screen.getByTestId('user-alice-actions'))
  expect(actions.getByRole('button', { name: '停用' })).toHaveClass('border-destructive', 'text-destructive')
  expect(actions.getByRole('button', { name: '开启成人权限' })).toHaveClass('border-warning', 'text-warning')
  expect(service.list).toHaveBeenCalledWith(1, 10)
})

it('creates a user through a dialog and refreshes the list after success', async () => {
  const service = {
    list: vi.fn().mockResolvedValue({ data: { items: [] }, meta: { page: 1, page_size: 10, total: 0 } }),
    create: vi.fn().mockResolvedValue({ data: {} }),
    update: vi.fn(),
    resetPassword: vi.fn(),
  }
  render(<QueryClientProvider client={new QueryClient()}><UserManagementPage service={service} /></QueryClientProvider>)

  await waitFor(() => expect(service.list).toHaveBeenCalledOnce())
  expect(screen.queryByRole('alertdialog', { name: '创建用户' })).not.toBeInTheDocument()

  await userEvent.click(screen.getByRole('button', { name: '创建用户' }))

  const dialog = screen.getByRole('alertdialog', { name: '创建用户' })
  expect(dialog).toHaveTextContent('密码至少 8 个字符')
  await userEvent.type(screen.getByLabelText('用户名'), 'Alice')
  await userEvent.type(screen.getByLabelText('密码'), 'alice-password')
  await userEvent.click(screen.getByLabelText('成人权限'))
  await userEvent.click(screen.getByRole('button', { name: '确认创建' }))

  await waitFor(() => expect(service.create).toHaveBeenCalledOnce())
  expect(service.create.mock.calls[0][0]).toEqual({
    username: 'Alice',
    password: 'alice-password',
    can_access_adult: true,
  })
  await waitFor(() => expect(service.list).toHaveBeenCalledTimes(2))
  expect(screen.queryByRole('alertdialog', { name: '创建用户' })).not.toBeInTheDocument()
})
