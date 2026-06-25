import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, useLocation } from 'react-router-dom'

import '@/i18n'
import { BackgroundAgentCard, type BackgroundAgentService } from './background-agent-card'

function renderCard(
  service: BackgroundAgentService,
  initialPath = '/',
): { getPath: () => string } {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  let currentPath = initialPath
  function PathProbe() {
    currentPath = useLocation().pathname + useLocation().search
    return null
  }
  render(
    <MemoryRouter initialEntries={[initialPath]}>
      <PathProbe />
      <QueryClientProvider client={queryClient}>
        <BackgroundAgentCard service={service} />
      </QueryClientProvider>
    </MemoryRouter>,
  )
  return { getPath: () => currentPath }
}

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('BackgroundAgentCard', () => {
  it('renders idle state with summary', async () => {
    const service: BackgroundAgentService = {
      async getBackgroundStatus() {
        return {
          status: 'success' as const,
          data: {
            enabled: true,
            state: 'idle',
            summary: 'mock 后台 Agent 空闲',
            disabled_reasons: [],
            waiting_user_count: 0,
            agent_failed_count: 0,
            last_run: null,
            history: [],
            current_task_id: null,
            current_download_id: null,
          },
          messages: [],
          meta: {},
        }
      },
    }
    renderCard(service)
    await waitFor(() => {
      expect(screen.getByTestId('background-agent-card')).toHaveAttribute('data-state', 'idle')
    })
    expect(screen.getByText('mock 后台 Agent 空闲')).toBeInTheDocument()
    expect(screen.getByText('空闲')).toBeInTheDocument()
  })

  it('renders disabled state with reasons', async () => {
    const service: BackgroundAgentService = {
      async getBackgroundStatus() {
        return {
          status: 'success' as const,
          data: {
            enabled: false,
            state: 'disabled',
            summary: '后台 Agent 未启用',
            disabled_reasons: ['llm_api_key 缺失', '工作目录不可写'],
            waiting_user_count: 0,
            agent_failed_count: 0,
            last_run: null,
            history: [],
            current_task_id: null,
            current_download_id: null,
          },
          messages: [],
          meta: {},
        }
      },
    }
    renderCard(service)
    await waitFor(() => {
      expect(screen.getByTestId('background-agent-card')).toHaveAttribute('data-state', 'disabled')
    })
    expect(screen.getByText('llm_api_key 缺失')).toBeInTheDocument()
    expect(screen.getByText('工作目录不可写')).toBeInTheDocument()
    // 禁用原因不应包含密钥值或敏感字段 (测试用例: 原因列表只展示给定的字符串)
    expect(screen.queryByText('sk-')).not.toBeInTheDocument()
  })

  it('shows needs_attention when waiting_user_count > 0', async () => {
    const service: BackgroundAgentService = {
      async getBackgroundStatus() {
        return {
          status: 'success' as const,
          data: {
            enabled: true,
            state: 'needs_attention',
            summary: '有 1 个入库任务等待用户处理',
            disabled_reasons: [],
            waiting_user_count: 1,
            agent_failed_count: 0,
            last_run: null,
            history: [],
            current_task_id: null,
            current_download_id: null,
          },
          messages: [],
          meta: {},
        }
      },
    }
    renderCard(service)
    await waitFor(() => {
      expect(screen.getByTestId('background-agent-card')).toHaveAttribute('data-state', 'needs_attention')
    })
    // 提供查看任务入口
    expect(screen.getByText('查看任务')).toBeInTheDocument()
  })

  it('shows recently_failed when agent_failed_count > 0', async () => {
    const service: BackgroundAgentService = {
      async getBackgroundStatus() {
        return {
          status: 'success' as const,
          data: {
            enabled: true,
            state: 'recently_failed',
            summary: '有 2 个入库任务处于失败态',
            disabled_reasons: [],
            waiting_user_count: 0,
            agent_failed_count: 2,
            last_run: null,
            history: [],
            current_task_id: null,
            current_download_id: null,
          },
          messages: [],
          meta: {},
        }
      },
    }
    renderCard(service)
    await waitFor(() => {
      expect(screen.getByTestId('background-agent-card')).toHaveAttribute('data-state', 'recently_failed')
    })
    expect(screen.getByText('查看失败')).toBeInTheDocument()
  })

  it('renders history list and redacted summary', async () => {
    const service: BackgroundAgentService = {
      async getBackgroundStatus() {
        return {
          status: 'success' as const,
          data: {
            enabled: true,
            state: 'idle',
            summary: 'mock 空闲',
            disabled_reasons: [],
            waiting_user_count: 0,
            agent_failed_count: 0,
            last_run: '2026-06-04T00:00:00+00:00',
            history: [
              {
                timestamp: '2026-06-04T00:00:00+00:00',
                phase: 'processing_task',
                level: 'success',
                summary: '任务处理完成',
                task_id: 'abcdef00',
                download_id: null,
              },
            ],
            current_task_id: null,
            current_download_id: null,
          },
          messages: [],
          meta: {},
        }
      },
    }
    renderCard(service)
    await waitFor(() => {
      expect(screen.getByText('任务处理完成')).toBeInTheDocument()
    })
    // 不应出现完整堆栈 / 原始 JSON 字段名
    expect(screen.queryByText('stacktrace')).not.toBeInTheDocument()
  })

  it('does not render history list when empty', async () => {
    const service: BackgroundAgentService = {
      async getBackgroundStatus() {
        return {
          status: 'success' as const,
          data: {
            enabled: true,
            state: 'idle',
            summary: 'mock 空闲',
            disabled_reasons: [],
            waiting_user_count: 0,
            agent_failed_count: 0,
            last_run: null,
            history: [],
            current_task_id: null,
            current_download_id: null,
          },
          messages: [],
          meta: {},
        }
      },
    }
    renderCard(service)
    await waitFor(() => {
      expect(screen.getByTestId('background-agent-card')).toBeInTheDocument()
    })
    expect(screen.queryByText('SUCCESS')).not.toBeInTheDocument()
  })

  it('navigates to full task ID when "查看当前任务" is clicked', async () => {
    const user = userEvent.setup()
    // 后端返回的 current_task_id 是完整 ID (UUID), 截短 8 位会导致
    // /tasks/abcdef00 找不到任务. 这里显式断言路由跳转到完整 ID.
    const fullTaskId = 'abcdef00-1111-2222-3333-444455556666'
    const service: BackgroundAgentService = {
      async getBackgroundStatus() {
        return {
          status: 'success' as const,
          data: {
            enabled: true,
            state: 'processing_task',
            summary: '正在处理入库任务',
            disabled_reasons: [],
            waiting_user_count: 0,
            agent_failed_count: 0,
            last_run: null,
            history: [],
            current_task_id: fullTaskId,
            current_download_id: null,
          },
          messages: [],
          meta: {},
        }
      },
    }
    const { getPath } = renderCard(service, '/')
    await waitFor(() => {
      expect(screen.getByTestId('background-agent-card')).toHaveAttribute(
        'data-state', 'processing_task',
      )
    })
    const viewCurrent = screen.getByText('查看当前任务')
    await user.click(viewCurrent)
    expect(getPath()).toBe(`/tasks/${fullTaskId}`)
  })

  it('does not render current-task link when current_task_id is null', async () => {
    const service: BackgroundAgentService = {
      async getBackgroundStatus() {
        return {
          status: 'success' as const,
          data: {
            enabled: true,
            state: 'idle',
            summary: 'mock 空闲',
            disabled_reasons: [],
            waiting_user_count: 0,
            agent_failed_count: 0,
            last_run: null,
            history: [],
            current_task_id: null,
            current_download_id: null,
          },
          messages: [],
          meta: {},
        }
      },
    }
    renderCard(service)
    await waitFor(() => {
      expect(screen.getByTestId('background-agent-card')).toBeInTheDocument()
    })
    expect(screen.queryByText('查看当前任务')).not.toBeInTheDocument()
  })

  it('navigates to waiting_user task list when waiting tasks exist', async () => {
    const user = userEvent.setup()
    const service: BackgroundAgentService = {
      async getBackgroundStatus() {
        return {
          status: 'success' as const,
          data: {
            enabled: true,
            state: 'needs_attention',
            summary: '有 1 个入库任务等待用户处理',
            disabled_reasons: [],
            waiting_user_count: 1,
            agent_failed_count: 0,
            last_run: null,
            history: [],
            current_task_id: null,
            current_download_id: null,
          },
          messages: [],
          meta: {},
        }
      },
    }
    const { getPath } = renderCard(service, '/')
    await waitFor(() => {
      expect(screen.getByText('查看任务')).toBeInTheDocument()
    })
    await user.click(screen.getByText('查看任务'))
    expect(getPath()).toBe('/tasks?filter=waiting_user')
  })
})
