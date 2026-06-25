import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { RouterProvider } from 'react-router-dom'

import i18n from '@/i18n'
import { createAppRouter } from './app/router'

function renderApp(path: string) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  })

  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={createAppRouter({ initialEntries: [path] })} />
    </QueryClientProvider>,
  )
}

afterEach(() => {
  cleanup()
})

describe('App routes', () => {
  it('renders the admin shell at any route', async () => {
    renderApp('/')

    await waitFor(() => {
      expect(screen.getByTestId('refresh-btn')).toBeInTheDocument()
    })
    expect(screen.getByTestId('theme-toggle')).toBeInTheDocument()
    expect(screen.getByTestId('nav-首页')).toBeInTheDocument()
    expect(screen.getByTestId('nav-任务列表')).toBeInTheDocument()
    expect(screen.getByTestId('nav-资源搜索')).toBeInTheDocument()
  })

  it('uses the configured app base path for the top bar logo', async () => {
    renderApp('/')

    await waitFor(() => {
      expect(screen.getByTestId('refresh-btn')).toBeInTheDocument()
    })

    const logo = document.querySelector('header img[aria-hidden="true"]') as HTMLImageElement | null
    expect(logo).not.toBeNull()
    expect(logo?.getAttribute('src')).toBe(`${import.meta.env.BASE_URL}media-pilot-mark.svg`)
  })

  it('renders the dashboard at root route', async () => {
    renderApp('/')

    await waitFor(() => {
      expect(screen.getByText('首页概览')).toBeInTheDocument()
    })
  })

  it('renders the task list route', async () => {
    renderApp('/tasks')
  })

  it('renders the task detail route', async () => {
    renderApp('/tasks/task-completed')

    await waitFor(() => {
      expect(screen.getByText(/task-completed/)).toBeInTheDocument()
    })
  })

  it('renders the 404 route', () => {
    renderApp('/missing')

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('页面不存在')
  })

  it('shows language toggle with 中文 and English options', async () => {
    renderApp('/')

    await waitFor(() => {
      expect(screen.getByTestId('language-toggle')).toBeInTheDocument()
    })
    expect(screen.getByTestId('lang-zh')).toHaveTextContent('中文')
    expect(screen.getByTestId('lang-en')).toHaveTextContent('English')
  })

  it('theme toggle is present', async () => {
    renderApp('/')

    await waitFor(() => {
      expect(screen.getByTestId('theme-toggle')).toBeInTheDocument()
    })
  })

  it('shows English theme tooltip when language is switched to English', async () => {
    i18n.changeLanguage('en')
    renderApp('/')

    await waitFor(() => {
      expect(screen.getByTestId('theme-toggle')).toBeInTheDocument()
    })

    const themeBtn = screen.getByTestId('theme-toggle')
    // Default theme is 'system' -> the tooltip should show 'System' in English
    expect(themeBtn.getAttribute('title')).toBe('System')
    i18n.changeLanguage('zh')
  })

  it('renders the settings page at /settings', async () => {
    renderApp('/settings')

    await waitFor(() => {
      const headings = screen.getAllByRole('heading')
      expect(headings.some((h) => h.textContent === '设置')).toBe(true)
    })
  })

  it('renders settings page in English when language is switched', async () => {
    i18n.changeLanguage('en')
    renderApp('/settings')

    await waitFor(() => {
      const headings = screen.getAllByRole('heading')
      expect(headings.some((h) => h.textContent === 'Settings')).toBe(true)
    })
    i18n.changeLanguage('zh')
  })

  it('renders the discovery page at /discovery', async () => {
    renderApp('/discovery')

    await waitFor(() => {
      expect(screen.getByTestId('discovery-search-input')).toBeInTheDocument()
    })
  })
})
