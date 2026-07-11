import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'

import { UserManagementPage } from '@/pages/user-management-page'
import '@/i18n'

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
  expect(screen.getByTestId('user-alice-actions')).toHaveTextContent('停用')
  expect(service.list).toHaveBeenCalledWith(1, 10)
})
