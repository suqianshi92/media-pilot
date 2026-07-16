import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { expect, it, vi } from 'vitest'

import { SidebarNav } from '@/components/layout/sidebar-nav'
import '@/i18n'

vi.mock('@/auth/auth-context', () => ({
  useAuth: () => ({ user: { role: 'admin' } }),
}))

it('keeps settings as the final administrator navigation item', () => {
  render(<MemoryRouter><SidebarNav open onClose={() => {}} /></MemoryRouter>)

  const links = screen.getAllByRole('link')
  expect(links[links.length - 1]).toHaveTextContent('设置')
  expect(links[links.length - 2]).toHaveTextContent('用户管理')
})
