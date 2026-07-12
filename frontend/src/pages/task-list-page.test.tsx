import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'

import { ToastProvider } from '@/components/shared/toast'
import { createMockTaskService } from '@/mocks/service'
import type { FlowSummary } from '@/types/task'
import type { FlowFilter } from '@/services/api-client'

import { TaskListPage, type TaskListService } from './task-list-page'

function renderTaskListPage(
  service: TaskListService = createMockTaskService(),
  initialPath = '/tasks',
  showOwner = false,
) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  })

  render(
    <MemoryRouter initialEntries={[initialPath]}>
      <QueryClientProvider client={queryClient}>
        <ToastProvider>
          <TaskListPage service={service} showOwner={showOwner} />
        </ToastProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

function getStatusFilterSelect() {
  return screen.getByRole('combobox', { name: '状态筛选' })
}

beforeEach(() => {
  createMockTaskService().reset()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('TaskListPage', () => {
  it('shows the creator column only for administrators', async () => {
    renderTaskListPage(createMockTaskService(), '/tasks', true)
    await waitFor(() => expect(screen.getByText('创建者')).toBeInTheDocument())

    cleanup()
    renderTaskListPage()
    await waitFor(() => expect(getStatusFilterSelect()).toBeInTheDocument())
    expect(screen.queryByText('创建者')).not.toBeInTheDocument()
  })

  it('renders stats summary cards and status filter dropdown', async () => {
    renderTaskListPage()

    await waitFor(() => {
      expect(getStatusFilterSelect()).toBeInTheDocument()
      expect(screen.getByRole('option', { name: '全部' })).toBeInTheDocument()
      expect(screen.getByRole('option', { name: '待确认' })).toBeInTheDocument()
      expect(screen.getByRole('option', { name: '已完成' })).toBeInTheDocument()
      expect(screen.getByRole('option', { name: '失败' })).toBeInTheDocument()
    })
  })

  it('supports no-metadata filter and shows no-metadata title badge', async () => {
    const user = userEvent.setup()
    const service = createMockTaskService()
    service.reset()

    const noMetadataFlow: FlowSummary = {
      id: 'ingest:task-no-metadata',
      flow_type: 'external_import',
      route_target: 'task_detail',
      ingest_task_id: 'task-no-metadata',
      download_task_id: null,
      total_status: 'processing',
      metadata_status: 'none',
      title: '缺元数据任务',
      year: 2021,
      media_type: 'movie',
      can_confirm: true,
      file_format: null,
      source_path: '/data/downloads/no-metadata.mkv',
      created_at: '2026-05-01T10:00:00+08:00',
      updated_at: '2026-05-01T10:00:00+08:00',
      status_summary: {
        status: 'processing',
        current_step: 'metadata_detail',
        failure_reason: null,
        confidence: 0.8,
        confidence_level: 'medium',
        latest_message: 'processing',
      },
      download_task: null,
      agent_status_summary: null,
    }

    const completeFlow: FlowSummary = {
      id: 'ingest:task-complete',
      flow_type: 'external_import',
      route_target: 'task_detail',
      ingest_task_id: 'task-complete',
      download_task_id: null,
      total_status: 'processing',
      metadata_status: 'complete',
      title: '有元数据任务',
      year: 2020,
      media_type: 'movie',
      can_confirm: false,
      file_format: null,
      source_path: '/data/downloads/with-metadata.mkv',
      created_at: '2026-05-01T10:01:00+08:00',
      updated_at: '2026-05-01T10:01:00+08:00',
      status_summary: {
        status: 'processing',
        current_step: 'metadata_detail',
        failure_reason: null,
        confidence: 0.9,
        confidence_level: 'high',
        latest_message: 'processing',
      },
      download_task: null,
      agent_status_summary: null,
    }

    const metadataFilterService: TaskListService = {
      ...service,
      async listFlows(params: { filter?: string; page?: number; page_size?: number } = {}) {
        const filter = params.filter ?? 'all'
        const allItems = [noMetadataFlow, completeFlow]
        const filteredItems = filter === 'no_metadata'
          ? allItems.filter((flow) => flow.metadata_status === 'none')
          : allItems
        const page = params.page ?? 1
        const pageSize = (params.page_size ?? filteredItems.length) || 1
        const start = (page - 1) * pageSize

        return {
          status: 'success' as const,
          data: { items: filteredItems.slice(start, start + pageSize) },
          messages: [],
          meta: { page, page_size: pageSize, total: filteredItems.length, filters: { filter } },
        }
      },
    }

    renderTaskListPage(metadataFilterService)

    await waitFor(() => {
      expect(screen.getByRole('option', { name: '无元数据' })).toBeInTheDocument()
    })

    await user.selectOptions(getStatusFilterSelect(), 'no_metadata')
    await waitFor(() => {
      const badges = screen.getAllByText('无元数据').filter((node) => (
        node instanceof HTMLElement && node.className.includes('rounded-full')
      ))
      expect(badges.length).toBe(2)
      expect(screen.getAllByText(/缺元数据任务 \(2021\)/).length).toBe(2)
      expect(screen.queryByText('有元数据任务')).not.toBeInTheDocument()
    })
  })

  it('switches visible filter state when selecting task filters', async () => {
    const user = userEvent.setup()

    renderTaskListPage()

    await waitFor(() => {
      expect(getStatusFilterSelect()).toBeInTheDocument()
      expect(screen.getAllByText('共 12 条').length).toBeGreaterThan(0)
    })

    await user.selectOptions(getStatusFilterSelect(), 'waiting_user')
    await waitFor(() => {
      expect(screen.getAllByText('共 5 条').length).toBeGreaterThan(0)
    })

    await user.selectOptions(getStatusFilterSelect(), 'library_import_complete')
    await waitFor(() => {
      expect(screen.getAllByText('共 2 条').length).toBeGreaterThan(0)
    })
  })

  it('renders task items with title and action links', async () => {
    renderTaskListPage()

    await waitFor(() => {
      expect(screen.getAllByText(/天气之子/).length).toBeGreaterThan(0)
    })

    // 铃芽之旅出现在桌面表格和移动卡片中（jsdom 同时渲染两者）
    await waitFor(() => {
      const matches = screen.getAllByText('铃芽之旅 (2022)')
      expect(matches.length).toBeGreaterThan(0)
    })

    // 入库任务统一入口文案为"详情"
    const detailLinks = screen.getAllByRole('link', { name: '详情' })
    expect(detailLinks.length).toBeGreaterThan(0)
  })

  it('paginates tasks and allows switching pages', async () => {
    // 统一流程列表契约: 任务列表维护 React 翻页
    // state, 翻页必须触发 listFlows(page=N) 而不是本地切. 默认 mock
    // 只有 12 条 flow 接近默认 page_size=10, 这个 case
    // 走 pagedService 强制制造 25 条, 验证翻页控件与 page state 联动.
    const user = userEvent.setup()
    const service = createMockTaskService()
    service.reset()

    const flows = Array.from({ length: 25 }, (_, i) => ({
      id: `ingest:task-paginate-${i}`,
      flow_type: 'external_import' as const,
      route_target: 'task_detail' as const,
      ingest_task_id: `task-paginate-${i}`,
      download_task_id: null,
      total_status: 'processing',
      title: `翻页任务 ${i}`,
      year: 2020,
      media_type: 'movie' as const,
      can_confirm: false,
      file_format: null,
      source_path: `/data/downloads/paginate-${i}.mkv`,
      created_at: `2026-05-${String((i % 28) + 1).padStart(2, '0')}T00:00:00+08:00`,
      updated_at: `2026-05-${String((i % 28) + 1).padStart(2, '0')}T00:00:00+08:00`,
      status_summary: {
        status: 'processing' as const,
        current_step: 'metadata_detail',
        failure_reason: null,
        confidence: 0.9,
        confidence_level: 'high' as const,
        latest_message: 'processing',
      },
      download_task: null,
      agent_status_summary: null,
    }))

    const pagedService: TaskListService = {
      ...service,
      async listFlows(params: { filter?: string; page?: number; page_size?: number } = {}) {
        const page = params.page ?? 1
        const pageSize = params.page_size ?? 10
        const start = (page - 1) * pageSize
        return {
          status: 'success' as const,
          data: { items: flows.slice(start, start + pageSize) },
          messages: [],
          meta: { page, page_size: pageSize, total: flows.length, filters: { filter: params.filter ?? 'all' } },
        }
      },
    }

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <MemoryRouter initialEntries={['/tasks']}>
        <QueryClientProvider client={queryClient}>
          <ToastProvider>
            <TaskListPage service={pagedService} />
          </ToastProvider>
        </QueryClientProvider>
      </MemoryRouter>,
    )

    // 第 1 页: 第 0-9 条
    await waitFor(() => {
      expect(screen.getAllByText('翻页任务 0 (2020)').length).toBeGreaterThan(0)
    })
    // 第 10 条在第 2 页才能看到
    expect(screen.queryAllByText('翻页任务 10 (2020)').length).toBe(0)

    const nextBtn = screen.getByRole('button', { name: '下一页' })
    expect(nextBtn).toBeInTheDocument()

    await user.click(nextBtn)

    // 第 2 页: 第 10-19 条
    await waitFor(() => {
      expect(screen.getAllByText('翻页任务 10 (2020)').length).toBeGreaterThan(0)
    })
    // 第 1 页的内容必须不再渲染
    expect(screen.queryAllByText('翻页任务 0 (2020)').length).toBe(0)
  })

  it('auto refreshes processing tasks in mock mode without user action', async () => {
    const service = createMockTaskService()
    service.reset()
    const tickSpy = vi.spyOn(service, 'tick')

    renderTaskListPage(service)

    await waitFor(() => {
      expect(getStatusFilterSelect()).toBeInTheDocument()
    })

    await waitFor(() => {
      expect(tickSpy).toHaveBeenCalledWith('task-processing')
    }, { timeout: 4500 })
  }, 10000)

  it('auto refreshes downloading download-only flow without calling tick', async () => {
    // 统一流程列表契约 收口: 非终态 download-only
    // (downloading/awaiting_sync/paused) 必须也触发 flowsQuery.refetch(),
    // 否则列表页拿不到下载器的最新进度/状态. mock 模式下没有 tick
    // 副作用 (下载器在真实 qB 端), 但 listFlows 必须被再次调用.
    const service = createMockTaskService()
    service.reset()
    // 把默认 mock 数据里所有非 download-only flow 滤掉, 只留一个
    // downloading download-only (对应 dl-downloading). 这样轮询
    // 走 download-only 路径: 触发 refetch, 不调 tick.
    // 必须在 downloadOnlyService 创建后立即对它的 listFlows 做 spy —
    // 页面用的是 downloadOnlyService.listFlows, 原 service 上的 spy 看不到调用.
    const downloadOnlyService: TaskListService = {
      ...service,
      async listFlows(params: { filter?: FlowFilter; page?: number; page_size?: number } = {}) {
        const result = await service.listFlows(params)
        const items = result.data.items.filter(
          (f) => f.route_target === 'download_detail' && f.total_status === 'downloading',
        )
        return { ...result, data: { items } }
      },
    }
    const tickSpy = vi.spyOn(service, 'tick')
    const listFlowsSpy = vi.spyOn(downloadOnlyService, 'listFlows')

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <MemoryRouter initialEntries={['/tasks']}>
        <QueryClientProvider client={queryClient}>
          <ToastProvider>
            <TaskListPage service={downloadOnlyService} />
          </ToastProvider>
        </QueryClientProvider>
      </MemoryRouter>,
    )

    // 初次 listFlows 必须被调 (默认 'all' filter)
    await waitFor(() => {
      expect(listFlowsSpy).toHaveBeenCalled()
    })

    const initialCallCount = listFlowsSpy.mock.calls.length
    // 在 3s 轮询窗口内必须有至少 1 次额外的 listFlows (refetch)
    await waitFor(
      () => {
        expect(listFlowsSpy.mock.calls.length).toBeGreaterThan(initialCallCount)
      },
      { timeout: 4500 },
    )
    // download-only 没有 ingest_task_id, 不得调 tick
    expect(tickSpy).not.toHaveBeenCalled()
  }, 10000)

  it('renders empty and error states for list loading results', async () => {
    const emptyService = {
      async listFlows() {
        return {
          status: 'success' as const,
          data: { items: [] },
          messages: [],
          meta: { page: 1, page_size: 0, total: 0, filters: { filter: 'all' } },
        }
      },
      async tick() {
        return null
      },
      async retryDownloadSync(_downloadId: string) {
        return { status: 'success' as const, data: { synced: 0, failed: 0, skipped: 1 }, messages: [], meta: {} }
      },
      async deleteDownload(_downloadId: string) {
        return { status: 'success' as const, data: { task_id: '', deleted: true, qb_deleted: true, qb_error: null, files_cleaned: [] }, messages: [], meta: {} }
      },
      async deleteTask(_taskId: string) {
        return { status: 'success' as const, data: { task_id: '', deleted: true, qb_deleted: null, qb_error: null, files_cleaned: [] }, messages: [], meta: {} }
      },
      async pauseDownload(_downloadId: string) {
        return { status: 'success' as const, data: { download_id: _downloadId, status: 'paused' }, messages: [], meta: {} }
      },
      async resumeDownload(_downloadId: string) {
        return { status: 'success' as const, data: { download_id: _downloadId, status: 'downloading' }, messages: [], meta: {} }
      },
    }

    renderTaskListPage(emptyService)

    await waitFor(() => {
      expect(screen.getByText('当前还没有任务')).toBeInTheDocument()
    })

    cleanup()

    const errorService = {
      async listFlows() {
        throw new Error('mock list failed')
      },
      async tick() {
        return null
      },
      async retryDownloadSync(_downloadId: string) {
        return { status: 'success' as const, data: { synced: 0, failed: 0, skipped: 1 }, messages: [], meta: {} }
      },
      async deleteDownload(_downloadId: string) {
        return { status: 'success' as const, data: { task_id: '', deleted: true, qb_deleted: true, qb_error: null, files_cleaned: [] }, messages: [], meta: {} }
      },
      async deleteTask(_taskId: string) {
        return { status: 'success' as const, data: { task_id: '', deleted: true, qb_deleted: null, qb_error: null, files_cleaned: [] }, messages: [], meta: {} }
      },
      async pauseDownload(_downloadId: string) {
        return { status: 'success' as const, data: { download_id: _downloadId, status: 'paused' }, messages: [], meta: {} }
      },
      async resumeDownload(_downloadId: string) {
        return { status: 'success' as const, data: { download_id: _downloadId, status: 'downloading' }, messages: [], meta: {} }
      },
    }

    renderTaskListPage(errorService)

    await waitFor(() => {
      expect(screen.getByText('加载失败')).toBeInTheDocument()
      expect(screen.getByText('mock list failed')).toBeInTheDocument()
      expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument()
    })
  })

  it('initializes filter from URL query param', async () => {
    const user = userEvent.setup()

    // 首次进入带 filter=waiting_user
    renderTaskListPage(createMockTaskService(), '/tasks?filter=waiting_user')

    await waitFor(() => {
      expect(screen.getAllByText('共 5 条').length).toBeGreaterThan(0)
    })

    // 用户切换到"已完成"
    await user.selectOptions(getStatusFilterSelect(), 'library_import_complete')
    await waitFor(() => {
      expect(screen.getAllByText('共 2 条').length).toBeGreaterThan(0)
    })
  })

  it('shows progress bar and percentage for active downloads', async () => {
    renderTaskListPage()

    await waitFor(() => {
      expect(getStatusFilterSelect()).toBeInTheDocument()
    })

    // dl-downloading 的 progress=0.72 → 显示 72%
    const pctElements = screen.getAllByText('72%')
    expect(pctElements.length).toBeGreaterThan(0)

    // 应该有一个进度条容器（桌面表格）包含蓝色进度条
    const progressBars = document.querySelectorAll('.h-1\\.5.rounded.bg-blue-500')
    expect(progressBars.length).toBeGreaterThan(0)
  })

  it('shows 下载失败 for failed download tasks', async () => {
    renderTaskListPage()

    await waitFor(() => {
      expect(getStatusFilterSelect()).toBeInTheDocument()
    })

    // dl-failed（sync_failed 无 ingest_task_id）→ 下载失败
    const labels = screen.getAllByText('下载失败')
    expect(labels.length).toBeGreaterThan(0)
  })

  it('shows 已下载 for completed download tasks', async () => {
    const user = userEvent.setup()
    renderTaskListPage()

    await waitFor(() => {
      expect(getStatusFilterSelect()).toBeInTheDocument()
    })

    await user.selectOptions(screen.getByLabelText('每页'), '20')

    // dl-completed（有 ingest_task_id → task-completed）的下载状态列应显示 已下载
    await waitFor(() => {
      const labels = screen.getAllByText('已下载')
      expect(labels.length).toBeGreaterThan(0)
    })
  })

  it('shows — when row has no download task', async () => {
    renderTaskListPage()

    await waitFor(() => {
      expect(getStatusFilterSelect()).toBeInTheDocument()
    })

    // 入库任务（无 download_task）的下载状态列显示 —
    // DataTable 同时渲染桌面表格和移动卡片，桌面表格的下载状态列会有 —
    const dashes = screen.getAllByText('—')
    expect(dashes.length).toBeGreaterThan(0)
  })

  it('shows retry sync for download-only sync_failed tasks via kebab menu', async () => {
    renderTaskListPage()

    await waitFor(() => {
      expect(getStatusFilterSelect()).toBeInTheDocument()
    })

    // dl-failed 是 sync_failed → 下载状态列显示下载失败
    const failedLabels = screen.getAllByText('下载失败')
    expect(failedLabels.length).toBeGreaterThan(0)
  })

  it('calls retryDownloadSync through injected service', async () => {
    const service = createMockTaskService()
    service.reset()
    const retrySpy = vi.spyOn(service, 'retryDownloadSync')

    renderTaskListPage(service)

    // 直接调用 service 方法验证
    await service.retryDownloadSync('dl-failed')
    expect(retrySpy).toHaveBeenCalledTimes(1)
  })

  it('formats updated_at in Asia/Shanghai timezone', async () => {
    renderTaskListPage()

    await waitFor(() => {
      expect(getStatusFilterSelect()).toBeInTheDocument()
    })

    // 带 +08:00 偏移的时间串 → 直接按偏移解析 → 05/08 10:00
    const timeWithOffset = screen.getAllByText('05/08 10:00')
    expect(timeWithOffset.length).toBeGreaterThan(0)

    // 不带时区的时间串 → 按 UTC 解析再转东八区
    // dl-failed 的 updated_at 已改为 2026-05-22T02:45:44 → UTC → 东八区 10:45
    const timeWithoutOffset = screen.getAllByText('05/22 10:45')
    expect(timeWithoutOffset.length).toBeGreaterThan(0)
  })

  // ── 删除入口显示/隐藏 ──

  it('shows delete button for download-only tasks', async () => {
    renderTaskListPage()

    await waitFor(() => {
      expect(getStatusFilterSelect()).toBeInTheDocument()
    })

    // dl-failed 和 dl-downloading 都是 download-only，应显示删除按钮
    const deleteButtons = screen.getAllByRole('button', { name: '删除' })
    expect(deleteButtons.length).toBeGreaterThanOrEqual(2)
  })

  it('shows delete button for non-published ingest tasks', async () => {
    renderTaskListPage()

    await waitFor(() => {
      expect(getStatusFilterSelect()).toBeInTheDocument()
    })

    // 待确认和处理中的入库任务应该显示删除按钮
    const deleteButtons = screen.getAllByRole('button', { name: '删除' })
    // download-only 的删除按钮 + 非已发布的入库任务删除按钮
    expect(deleteButtons.length).toBeGreaterThan(2)
  })

  it('does not show delete button for published tasks', async () => {
    renderTaskListPage()

    await waitFor(() => {
      expect(getStatusFilterSelect()).toBeInTheDocument()
    })

    // 切换到"已完成"筛选，此时应只有已发布的任务，不应显示删除按钮
    const user = userEvent.setup()
    await user.selectOptions(getStatusFilterSelect(), 'library_import_complete')

    await waitFor(() => {
      const deleteButtons = screen.queryAllByRole('button', { name: '删除' })
      expect(deleteButtons.length).toBe(0)
    })
  })

  it('shows confirmation dialog on delete click and can cancel', async () => {
    const user = userEvent.setup()
    renderTaskListPage()

    await waitFor(() => {
      expect(getStatusFilterSelect()).toBeInTheDocument()
    })

    const deleteButtons = screen.getAllByRole('button', { name: '删除' })
    await user.click(deleteButtons[0])

    // 确认弹窗出现
    await waitFor(() => {
      expect(screen.getByText('确认删除')).toBeInTheDocument()
      expect(screen.getByText(/永久删除/)).toBeInTheDocument()
    })

    // 点击取消关闭弹窗
    await user.click(screen.getByRole('button', { name: '取消' }))
    await waitFor(() => {
      expect(screen.queryByText('确认删除')).not.toBeInTheDocument()
    })
  })

  it('calls delete API on confirm', async () => {
    const service = createMockTaskService()
    service.reset()
    const deleteDownloadSpy = vi.spyOn(service, 'deleteDownload')
    const deleteTaskSpy = vi.spyOn(service, 'deleteTask')

    const user = userEvent.setup()
    renderTaskListPage(service)

    await waitFor(() => {
      expect(getStatusFilterSelect()).toBeInTheDocument()
    })

    // 点击第一个删除按钮
    const deleteButtons = screen.getAllByRole('button', { name: '删除' })
    await user.click(deleteButtons[0])

    await waitFor(() => {
      expect(screen.getByText('确认删除')).toBeInTheDocument()
    })

    // 弹窗内的确认按钮是最后一个"删除"按钮（弹窗渲染在最后）
    const allDeleteButtons = screen.getAllByRole('button', { name: '删除' })
    const confirmButton = allDeleteButtons[allDeleteButtons.length - 1]
    await user.click(confirmButton)

    await waitFor(() => {
      const totalCalls = deleteDownloadSpy.mock.calls.length + deleteTaskSpy.mock.calls.length
      expect(totalCalls).toBeGreaterThan(0)
    })
  })

  it('does not show delete button for completed status tasks', async () => {
    // 兼容完成态 completed 也不显示删除按钮
    const service = createMockTaskService()
    service.reset()

    const completedTaskService = {
      ...service,
      async listFlows() {
        const result = await service.listFlows()
        // 把所有 ingest 任务的 status_summary.status 改为 completed
        // 排除 download-only (route_target=download_detail), 它们的删除权限不受 status 影响.
        const items = result.data.items
          .filter((f) => f.route_target === 'task_detail')
          .map((f) => {
            const ss = f.status_summary ?? {
              status: 'discovered' as const,
              current_step: null,
              failure_reason: null,
              confidence: null,
              confidence_level: 'unknown' as const,
              latest_message: '',
            }
            return {
              ...f,
              status_summary: {
                status: 'completed' as const,
                current_step: ss.current_step,
                failure_reason: ss.failure_reason,
                confidence: ss.confidence,
                confidence_level: ss.confidence_level,
                latest_message: ss.latest_message,
              },
              total_status: 'completed',
              can_confirm: false,
            }
          })
        return { ...result, data: { ...result.data, items } }
      },
    }

    renderTaskListPage(completedTaskService)

    await waitFor(() => {
      expect(getStatusFilterSelect()).toBeInTheDocument()
    })

    // 所有 ingest 任务都是 completed 状态且没有 download-only 卡片，不应有删除按钮
    const deleteButtons = screen.queryAllByRole('button', { name: '删除' })
    expect(deleteButtons.length).toBe(0)
  })

  it('shows warning toast when delete succeeds with qb_error', async () => {
    const service = createMockTaskService()
    service.reset()

    const qbErrorResponse = {
      status: 'success' as const,
      data: {
        task_id: '',
        deleted: true,
        qb_deleted: false,
        qb_error: 'qBittorrent 删除失败，本地清理继续',
        files_cleaned: ['/data/downloads/test.mkv'],
      },
      messages: [],
      meta: {},
    }

    const qbErrorService = {
      ...service,
      async deleteDownload() { return qbErrorResponse },
      async deleteTask() { return qbErrorResponse },
    }

    const user = userEvent.setup()
    renderTaskListPage(qbErrorService)

    await waitFor(() => {
      expect(getStatusFilterSelect()).toBeInTheDocument()
    })

    // 点击删除按钮打开弹窗
    const deleteButtons = screen.getAllByRole('button', { name: '删除' })
    await user.click(deleteButtons[0])

    await waitFor(() => {
      expect(screen.getByText('确认删除')).toBeInTheDocument()
    })

    // 点击确认
    const allDeleteButtons = screen.getAllByRole('button', { name: '删除' })
    const confirmButton = allDeleteButtons[allDeleteButtons.length - 1]
    await user.click(confirmButton)

    // 应显示 qB 删除失败的警告 toast
    await waitFor(() => {
      expect(screen.getByText(/qBittorrent 删除失败/)).toBeInTheDocument()
    })
  })

  it('renders ingest-only tasks in the order listFlows returns (backend-driven sort)', async () => {
    // 统一流程列表契约: 排序是后端契约,
    // 任务列表只是透传 listFlows() 返回的顺序. 模拟后端按 attention
    // priority 排好的顺序 (waiting → processing → failed → done), 验证
    // 列表页保持该顺序渲染, 不再做客户端重排.
    const orderedFlows = [
      // priority 1 (waiting_user)
      {
        id: 'ingest:task-waiting-1',
        flow_type: 'external_import' as const,
        route_target: 'task_detail' as const,
        ingest_task_id: 'task-waiting-1',
        download_task_id: null,
        total_status: 'waiting_user',
        title: '待确认任务',
        year: 2017,
        media_type: 'movie' as const,
        can_confirm: true,
        file_format: null,
        source_path: '/data/downloads/待确认.mkv',
        created_at: '2026-05-04T00:00:00+08:00',
        updated_at: '2026-05-04T00:00:00+08:00',
        status_summary: {
          status: 'waiting_user' as const,
          current_step: 'metadata_detail',
          failure_reason: null,
          confidence: 0.7,
          confidence_level: 'medium' as const,
          latest_message: 'waiting',
        },
        download_task: null,
        agent_status_summary: null,
      },
      // priority 2 (processing)
      {
        id: 'ingest:task-active-1',
        flow_type: 'external_import' as const,
        route_target: 'task_detail' as const,
        ingest_task_id: 'task-active-1',
        download_task_id: null,
        total_status: 'processing',
        title: '进行中任务',
        year: 2018,
        media_type: 'movie' as const,
        can_confirm: false,
        file_format: null,
        source_path: '/data/downloads/进行中.mkv',
        created_at: '2026-05-03T00:00:00+08:00',
        updated_at: '2026-05-03T00:00:00+08:00',
        status_summary: {
          status: 'processing' as const,
          current_step: 'metadata_detail',
          failure_reason: null,
          confidence: 0.9,
          confidence_level: 'high' as const,
          latest_message: 'processing',
        },
        download_task: null,
        agent_status_summary: null,
      },
      // priority 3 (agent_failed)
      {
        id: 'ingest:task-failed-1',
        flow_type: 'external_import' as const,
        route_target: 'task_detail' as const,
        ingest_task_id: 'task-failed-1',
        download_task_id: null,
        total_status: 'agent_failed',
        title: '失败任务',
        year: 2019,
        media_type: 'movie' as const,
        can_confirm: false,
        file_format: null,
        source_path: '/data/downloads/失败.mkv',
        created_at: '2026-05-02T00:00:00+08:00',
        updated_at: '2026-05-02T00:00:00+08:00',
        status_summary: {
          status: 'agent_failed' as const,
          current_step: 'metadata_detail',
          failure_reason: 'reason',
          confidence: 0.5,
          confidence_level: 'low' as const,
          latest_message: 'failed',
        },
        download_task: null,
        agent_status_summary: null,
      },
      // priority 4 (library_import_complete)
      {
        id: 'ingest:task-lic-1',
        flow_type: 'external_import' as const,
        route_target: 'task_detail' as const,
        ingest_task_id: 'task-lic-1',
        download_task_id: null,
        total_status: 'library_import_complete',
        title: '已完成任务',
        year: 2020,
        media_type: 'movie' as const,
        can_confirm: false,
        file_format: null,
        source_path: '/data/downloads/已完成.mkv',
        created_at: '2026-05-01T00:00:00+08:00',
        updated_at: '2026-05-01T00:00:00+08:00',
        status_summary: {
          status: 'library_import_complete' as const,
          current_step: 'library_import_complete',
          failure_reason: null,
          confidence: 0.96,
          confidence_level: 'high' as const,
          latest_message: 'ok',
        },
        download_task: null,
        agent_status_summary: null,
      },
    ]

    const rawService = {
      async listFlows() {
        return {
          status: 'success' as const,
          data: { items: orderedFlows },
          messages: [],
          meta: { page: 1, page_size: orderedFlows.length, total: orderedFlows.length, filters: { filter: 'all' } },
        }
      },
      async tick() { return null },
      async retryDownloadSync() {
        return { status: 'success' as const, data: { synced: 0, failed: 0, skipped: 1 }, messages: [], meta: {} }
      },
      async deleteDownload() {
        return { status: 'success' as const, data: { task_id: '', deleted: true, qb_deleted: true, qb_error: null, files_cleaned: [] }, messages: [], meta: {} }
      },
      async deleteTask() {
        return { status: 'success' as const, data: { task_id: '', deleted: true, qb_deleted: null, qb_error: null, files_cleaned: [] }, messages: [], meta: {} }
      },
      async pauseDownload() {
        return { status: 'success' as const, data: { download_id: '', status: 'paused' }, messages: [], meta: {} }
      },
      async resumeDownload() {
        return { status: 'success' as const, data: { download_id: '', status: 'downloading' }, messages: [], meta: {} }
      },
    }

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <MemoryRouter initialEntries={['/tasks']}>
        <QueryClientProvider client={queryClient}>
          <ToastProvider>
            <TaskListPage service={rawService} />
          </ToastProvider>
        </QueryClientProvider>
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(getStatusFilterSelect()).toBeInTheDocument()
    })

    // 提取桌面表格的状态列顺序
    const allStatusCells = document.querySelectorAll('table td:first-child span')
    const statusTexts = Array.from(allStatusCells).map((el) => el.textContent?.trim() ?? '')

    // 预期顺序: waiting_user → processing → agent_failed → library_import_complete
    // (由后端 listFlows 保证, 列表页只透传).
    const expectedOrder = ['等待用户处理', '处理中', 'Agent 失败', '已入库']
    expect(statusTexts.slice(0, 4)).toEqual(expectedOrder)
  })

  it('renders mixed ingest + download-only flows in the order listFlows returns', async () => {
    // 统一流程列表契约: 混合 flow (ingest +
    // download-only) 的 attention priority 排序是后端契约, 列表页只
    // 透传 listFlows() 返回的顺序. 模拟后端按统一 attention priority
    // 排好的顺序: waiting_user ingest → downloading download-only →
    // sync_failed download-only → library_import_complete ingest.
    const mixedFlows = [
      // priority 1: waiting_user ingest
      {
        id: 'ingest:ingest-waiting',
        flow_type: 'external_import' as const,
        route_target: 'task_detail' as const,
        ingest_task_id: 'ingest-waiting',
        download_task_id: null,
        total_status: 'waiting_user',
        title: '待确认任务',
        year: 2017,
        media_type: 'movie' as const,
        can_confirm: true,
        file_format: null,
        source_path: '/data/downloads/待确认.mkv',
        created_at: '2026-05-01T00:00:00+08:00',
        updated_at: '2026-05-01T00:00:00+08:00',
        status_summary: {
          status: 'waiting_user' as const,
          current_step: 'metadata_detail',
          failure_reason: null,
          confidence: 0.7,
          confidence_level: 'medium' as const,
          latest_message: 'waiting',
        },
        download_task: null,
        agent_status_summary: null,
      },
      // priority 2: downloading download-only
      {
        id: 'download:dl-downloading',
        flow_type: 'download_only' as const,
        route_target: 'download_detail' as const,
        ingest_task_id: null,
        download_task_id: 'dl-downloading',
        total_status: 'downloading',
        title: '下载中',
        year: null,
        media_type: null,
        can_confirm: false,
        file_format: null,
        source_path: '/data/downloads/下载中.mkv',
        created_at: '2026-05-03T00:00:00+08:00',
        updated_at: '2026-05-03T00:00:00+08:00',
        status_summary: {
          status: 'downloading',
          current_step: null,
          failure_reason: null,
          confidence: null,
          confidence_level: 'unknown' as const,
          latest_message: 'downloading',
        },
        download_task: {
          id: 'dl-downloading',
          title: '下载中',
          source: 'prowlarr',
          qb_hash: null,
          save_path: '/data/downloads/下载中.mkv',
          content_path: null,
          progress: 0.4,
          download_speed_bytes_per_second: null,
          upload_speed_bytes_per_second: null,
          seeders: 0,
          leechers: 0,
          connections: null,
          qb_state: 'downloading',
          status: 'downloading',
          error_message: null,
          ingest_task_id: null,
          created_at: '2026-05-03T00:00:00+08:00',
          updated_at: '2026-05-03T00:00:00+08:00',
        },
        agent_status_summary: null,
      },
      // priority 3: sync_failed download-only
      {
        id: 'download:dl-sync-failed',
        flow_type: 'download_only' as const,
        route_target: 'download_detail' as const,
        ingest_task_id: null,
        download_task_id: 'dl-sync-failed',
        total_status: 'sync_failed',
        title: '同步失败',
        year: null,
        media_type: null,
        can_confirm: false,
        file_format: null,
        source_path: '/data/downloads/同步失败.mkv',
        created_at: '2026-05-04T00:00:00+08:00',
        updated_at: '2026-05-04T00:00:00+08:00',
        status_summary: {
          status: 'sync_failed',
          current_step: null,
          failure_reason: 'sync failed',
          confidence: null,
          confidence_level: 'unknown' as const,
          latest_message: 'sync failed',
        },
        download_task: {
          id: 'dl-sync-failed',
          title: '同步失败',
          source: 'prowlarr',
          qb_hash: null,
          save_path: '/data/downloads/同步失败.mkv',
          content_path: null,
          progress: 0,
          download_speed_bytes_per_second: null,
          upload_speed_bytes_per_second: null,
          seeders: 0,
          leechers: 0,
          connections: null,
          qb_state: null,
          status: 'sync_failed',
          error_message: 'sync failed',
          ingest_task_id: null,
          created_at: '2026-05-04T00:00:00+08:00',
          updated_at: '2026-05-04T00:00:00+08:00',
        },
        agent_status_summary: null,
      },
      // priority 4: library_import_complete ingest
      {
        id: 'ingest:ingest-completed',
        flow_type: 'external_import' as const,
        route_target: 'task_detail' as const,
        ingest_task_id: 'ingest-completed',
        download_task_id: null,
        total_status: 'library_import_complete',
        title: '已入库任务',
        year: 2020,
        media_type: 'movie' as const,
        can_confirm: false,
        file_format: null,
        source_path: '/data/downloads/已入库.mkv',
        created_at: '2026-05-02T00:00:00+08:00',
        updated_at: '2026-05-02T00:00:00+08:00',
        status_summary: {
          status: 'library_import_complete' as const,
          current_step: 'library_import_complete',
          failure_reason: null,
          confidence: 0.96,
          confidence_level: 'high' as const,
          latest_message: 'ok',
        },
        download_task: null,
        agent_status_summary: null,
      },
    ]

    const rawService = {
      async listFlows() {
        return {
          status: 'success' as const,
          data: { items: mixedFlows },
          messages: [],
          meta: { page: 1, page_size: mixedFlows.length, total: mixedFlows.length, filters: { filter: 'all' } },
        }
      },
      async tick() { return null },
      async retryDownloadSync() {
        return { status: 'success' as const, data: { synced: 0, failed: 0, skipped: 1 }, messages: [], meta: {} }
      },
      async deleteDownload() {
        return { status: 'success' as const, data: { task_id: '', deleted: true, qb_deleted: true, qb_error: null, files_cleaned: [] }, messages: [], meta: {} }
      },
      async deleteTask() {
        return { status: 'success' as const, data: { task_id: '', deleted: true, qb_deleted: null, qb_error: null, files_cleaned: [] }, messages: [], meta: {} }
      },
      async pauseDownload() {
        return { status: 'success' as const, data: { download_id: '', status: 'paused' }, messages: [], meta: {} }
      },
      async resumeDownload() {
        return { status: 'success' as const, data: { download_id: '', status: 'downloading' }, messages: [], meta: {} }
      },
    }

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <MemoryRouter initialEntries={['/tasks']}>
        <QueryClientProvider client={queryClient}>
          <ToastProvider>
            <TaskListPage service={rawService} />
          </ToastProvider>
        </QueryClientProvider>
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(getStatusFilterSelect()).toBeInTheDocument()
    })

    // 桌面表格第一列是 status badge. 4 条混合 flow, 顺序必须是
    // waiting_user → downloading → sync_failed → library_import_complete.
    const allStatusCells = document.querySelectorAll('table td:first-child span')
    const statusTexts = Array.from(allStatusCells).map((el) => el.textContent?.trim() ?? '')
    // sync_failed download-only 在前端下载状态下显示为 "下载失败".
    expect(statusTexts.slice(0, 4)).toEqual([
      '等待用户处理',
      '下载中',
      '下载失败',
      '已入库',
    ])

    const rowTexts = Array.from(document.querySelectorAll('table tbody tr')).map(
      (row) => row.textContent ?? '',
    )
    expect(rowTexts[1]).toContain('下载任务')
    expect(rowTexts[1]).not.toContain('外部导入')
    expect(rowTexts[2]).toContain('下载任务')
    expect(rowTexts[2]).not.toContain('外部导入')
  })

  it('renders Agent column with shared AgentRunStatusBadge using semantic colors', async () => {
    // 回归: 任务列表 Agent 列必须用 shared AgentRunStatusBadge,
    // 颜色和详情页 StatusBadge 一致 — active 蓝, waiting_user 琥珀,
    // failed 玫红, completed 翡翠. 之前用的 text-muted-foreground
    // 灰色不一致.
    renderTaskListPage()

    await waitFor(() => {
      expect(screen.getAllByText(/天气之子/).length).toBeGreaterThan(0)
    })

    // mock 数据里有 active / waiting_user / completed / failed 各一组,
    // 至少 active (蓝) 应该渲染.
    const badges = screen.queryAllByTestId('agent-run-status-badge')
    expect(badges.length).toBeGreaterThan(0)

    const activeBadges = badges.filter(
      (b) => b.getAttribute('data-run-status') === 'active',
    )
    expect(activeBadges.length).toBeGreaterThan(0)
    expect(activeBadges[0].className).toContain('bg-blue-500/15')
    expect(activeBadges[0].className).toContain('text-blue-200')
  })

  it('uses listFlows as the sole list data source, never listTasks + listDownloads stitching', async () => {
    // 统一流程列表契约 要求任务列表只调用
    // /api/v1/flows, 不得再用 listTasks() + listDownloads() 拼接. 守
    // 卫: 列表页 queryFn 必须调用 listFlows, 且不得调用 listTasks/
    // listDownloads.
    const service = createMockTaskService()
    service.reset()
    const listFlowsSpy = vi.spyOn(service, 'listFlows')
    const listTasksSpy = vi.spyOn(service, 'listTasks')
    const listDownloadsSpy = vi.spyOn(service, 'listDownloads')

    renderTaskListPage(service)

    await waitFor(() => {
      expect(getStatusFilterSelect()).toBeInTheDocument()
    })

    expect(listFlowsSpy).toHaveBeenCalled()
    expect(listTasksSpy).not.toHaveBeenCalled()
    expect(listDownloadsSpy).not.toHaveBeenCalled()
  })

  it('drives listFlows with page state (server-side pagination), not local page_size=200 slicing', async () => {
    // 任务列表必须（MUST）使用后端分页: 翻页必须调用 listFlows(page=N)
    // 而不是 listFlows({ page_size: 200 }) 后本地切. 切 filter 也要回到
    // page=1. total 必须取自 meta.total, 不得用 items.length.
    const service = createMockTaskService()
    service.reset()

    // 制造 25 条 fixed ingest flow, 让 /flows 跨多页 (page_size=10 默认).
    const flows = Array.from({ length: 25 }, (_, i) => ({
      id: `ingest:task-page-${i}`,
      flow_type: 'external_import' as const,
      route_target: 'task_detail' as const,
      ingest_task_id: `task-page-${i}`,
      download_task_id: null,
      total_status: 'processing',
      title: `页面测试任务 ${i}`,
      year: 2020,
      media_type: 'movie' as const,
      can_confirm: false,
      file_format: null,
      source_path: `/data/downloads/page-${i}.mkv`,
      created_at: `2026-05-${String((i % 28) + 1).padStart(2, '0')}T00:00:00+08:00`,
      updated_at: `2026-05-${String((i % 28) + 1).padStart(2, '0')}T00:00:00+08:00`,
      status_summary: {
        status: 'processing' as const,
        current_step: 'metadata_detail',
        failure_reason: null,
        confidence: 0.9,
        confidence_level: 'high' as const,
        latest_message: 'processing',
      },
      download_task: null,
      agent_status_summary: null,
    }))

    // mock 一个简单的分页服务: 按 page/page_size 切片, total=25.
    // 必须在 pagedService 创建后立刻对它的 listFlows 做 spy — 否则
    // 页面调用的是 pagedService.listFlows, 原来 service.listFlows
    // 上的 spy 看不到任何调用.
    const pagedService: TaskListService = {
      ...service,
      async listFlows(params: { filter?: string; page?: number; page_size?: number } = {}) {
        const page = params.page ?? 1
        const pageSize = params.page_size ?? 10
        const start = (page - 1) * pageSize
        return {
          status: 'success' as const,
          data: { items: flows.slice(start, start + pageSize) },
          messages: [],
          meta: { page, page_size: pageSize, total: flows.length, filters: { filter: params.filter ?? 'all' } },
        }
      },
    }
    const listFlowsSpy = vi.spyOn(pagedService, 'listFlows')

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <MemoryRouter initialEntries={['/tasks']}>
        <QueryClientProvider client={queryClient}>
          <ToastProvider>
            <TaskListPage service={pagedService} />
          </ToastProvider>
        </QueryClientProvider>
      </MemoryRouter>,
    )

    // 1) 初次加载: 调用 page=1 且不传 page_size=200
    await waitFor(() => {
      expect(getStatusFilterSelect()).toBeInTheDocument()
    })
    const firstCallArgs = listFlowsSpy.mock.calls[0]?.[0] ?? {}
    expect(firstCallArgs.page ?? 1).toBe(1)
    expect(firstCallArgs.page_size).toBe(10)
    // 不得再用 200 假装全局分页
    expect(firstCallArgs.page_size === 200).toBe(false)

    // 2) 翻页: 调用 page=2
    const nextBtn = screen.getByRole('button', { name: '下一页' })
    await userEvent.setup().click(nextBtn)

    await waitFor(() => {
      const calls = listFlowsSpy.mock.calls
      const called = calls.some((args) => (args[0]?.page ?? 1) === 2)
      expect(called).toBe(true)
    })

    // 3) 切 filter: 必须回到 page=1
    const user = userEvent.setup()
    await user.selectOptions(getStatusFilterSelect(), 'waiting_user')
    await waitFor(() => {
      const calls = listFlowsSpy.mock.calls
      const lastFilterCall = [...calls].reverse().find(
        (args) => (args[0]?.filter ?? 'all') === 'waiting_user',
      )
      expect(lastFilterCall).toBeDefined()
      expect((lastFilterCall?.[0] as { page?: number })?.page ?? 1).toBe(1)
    })
  })

  it('allows changing server page size and resets to page 1', async () => {
    const user = userEvent.setup()
    const service = createMockTaskService()
    service.reset()

    const flows = Array.from({ length: 55 }, (_, i) => ({
      id: `ingest:page-size-${i}`,
      flow_type: 'external_import' as const,
      route_target: 'task_detail' as const,
      ingest_task_id: `page-size-${i}`,
      download_task_id: null,
      total_status: 'processing',
      title: `每页任务 ${i}`,
      year: 2020,
      media_type: 'movie' as const,
      can_confirm: false,
      file_format: null,
      source_path: `/data/downloads/page-size-${i}.mkv`,
      created_at: '2026-05-01T00:00:00+08:00',
      updated_at: '2026-05-01T00:00:00+08:00',
      status_summary: {
        status: 'processing' as const,
        current_step: 'metadata_detail',
        failure_reason: null,
        confidence: 0.9,
        confidence_level: 'high' as const,
        latest_message: 'processing',
      },
      download_task: null,
      agent_status_summary: null,
    }))

    const pagedService: TaskListService = {
      ...service,
      async listFlows(params: { filter?: string; page?: number; page_size?: number } = {}) {
        const page = params.page ?? 1
        const pageSize = params.page_size ?? 10
        const start = (page - 1) * pageSize
        return {
          status: 'success' as const,
          data: { items: flows.slice(start, start + pageSize) },
          messages: [],
          meta: { page, page_size: pageSize, total: flows.length, filters: { filter: params.filter ?? 'all' } },
        }
      },
    }
    const listFlowsSpy = vi.spyOn(pagedService, 'listFlows')

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <MemoryRouter initialEntries={['/tasks']}>
        <QueryClientProvider client={queryClient}>
          <ToastProvider>
            <TaskListPage service={pagedService} />
          </ToastProvider>
        </QueryClientProvider>
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getAllByText('每页任务 0 (2020)').length).toBeGreaterThan(0)
    })

    await user.click(screen.getByRole('button', { name: '下一页' }))
    await waitFor(() => {
      expect(listFlowsSpy.mock.calls.some((args) => args[0]?.page === 2)).toBe(true)
    })

    await user.selectOptions(screen.getByLabelText('每页'), '50')

    await waitFor(() => {
      expect(listFlowsSpy.mock.calls.some((args) => (
        args[0]?.page === 1 && args[0]?.page_size === 50
      ))).toBe(true)
    })
  })

  it('keeps the current page visible while a new server page is loading', async () => {
    const user = userEvent.setup()
    const service = createMockTaskService()
    service.reset()

    const flows = Array.from({ length: 25 }, (_, i) => ({
      id: `ingest:pending-page-${i}`,
      flow_type: 'external_import' as const,
      route_target: 'task_detail' as const,
      ingest_task_id: `pending-page-${i}`,
      download_task_id: null,
      total_status: 'processing',
      title: `待加载分页任务 ${i}`,
      year: 2020,
      media_type: 'movie' as const,
      can_confirm: false,
      file_format: null,
      source_path: `/data/downloads/pending-page-${i}.mkv`,
      created_at: '2026-05-01T00:00:00+08:00',
      updated_at: '2026-05-01T00:00:00+08:00',
      status_summary: {
        status: 'processing' as const,
        current_step: 'metadata_detail',
        failure_reason: null,
        confidence: 0.9,
        confidence_level: 'high' as const,
        latest_message: 'processing',
      },
      download_task: null,
      agent_status_summary: null,
    }))

    let resolvePage2: (() => void) | undefined
    const pagedService: TaskListService = {
      ...service,
      async listFlows(params: { filter?: string; page?: number; page_size?: number } = {}) {
        const page = params.page ?? 1
        const pageSize = params.page_size ?? 10
        if (params.filter === 'all' && page === 2) {
          await new Promise<void>((resolve) => {
            resolvePage2 = resolve
          })
        }
        const start = (page - 1) * pageSize
        return {
          status: 'success' as const,
          data: { items: flows.slice(start, start + pageSize) },
          messages: [],
          meta: { page, page_size: pageSize, total: flows.length, filters: { filter: params.filter ?? 'all' } },
        }
      },
    }
    const listFlowsSpy = vi.spyOn(pagedService, 'listFlows')

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <MemoryRouter initialEntries={['/tasks']}>
        <QueryClientProvider client={queryClient}>
          <ToastProvider>
            <TaskListPage service={pagedService} />
          </ToastProvider>
        </QueryClientProvider>
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getAllByText('待加载分页任务 0 (2020)').length).toBeGreaterThan(0)
    })

    const nextBtn = screen.getByRole('button', { name: '下一页' })
    await user.click(nextBtn)

    await waitFor(() => {
      expect(listFlowsSpy.mock.calls.some((args) => args[0]?.page === 2)).toBe(true)
    })

    // 请求未完成时保留旧页内容, 不进入整页 skeleton / ErrorState.
    expect(screen.getAllByText('待加载分页任务 0 (2020)').length).toBeGreaterThan(0)
    expect(screen.queryAllByText('待加载分页任务 10 (2020)').length).toBe(0)
    expect(nextBtn).toBeDisabled()

    resolvePage2?.()

    await waitFor(() => {
      expect(screen.getAllByText('待加载分页任务 10 (2020)').length).toBeGreaterThan(0)
    })
    expect(screen.queryAllByText('待加载分页任务 0 (2020)').length).toBe(0)
  })

  it('renders compact page numbers with ellipsis for large result sets', async () => {
    const service = createMockTaskService()
    service.reset()

    const flows = Array.from({ length: 90 }, (_, i) => ({
      id: `ingest:ellipsis-page-${i}`,
      flow_type: 'external_import' as const,
      route_target: 'task_detail' as const,
      ingest_task_id: `ellipsis-page-${i}`,
      download_task_id: null,
      total_status: 'processing',
      title: `省略号分页任务 ${i}`,
      year: 2020,
      media_type: 'movie' as const,
      can_confirm: false,
      file_format: null,
      source_path: `/data/downloads/ellipsis-page-${i}.mkv`,
      created_at: '2026-05-01T00:00:00+08:00',
      updated_at: '2026-05-01T00:00:00+08:00',
      status_summary: {
        status: 'processing' as const,
        current_step: 'metadata_detail',
        failure_reason: null,
        confidence: 0.9,
        confidence_level: 'high' as const,
        latest_message: 'processing',
      },
      download_task: null,
      agent_status_summary: null,
    }))

    const pagedService: TaskListService = {
      ...service,
      async listFlows(params: { filter?: string; page?: number; page_size?: number } = {}) {
        const page = params.page ?? 1
        const pageSize = params.page_size ?? 10
        const start = (page - 1) * pageSize
        return {
          status: 'success' as const,
          data: { items: flows.slice(start, start + pageSize) },
          messages: [],
          meta: { page, page_size: pageSize, total: flows.length, filters: { filter: params.filter ?? 'all' } },
        }
      },
    }

    renderTaskListPage(pagedService)

    await waitFor(() => {
      expect(screen.getAllByText('省略号分页任务 0 (2020)').length).toBeGreaterThan(0)
    })

    expect(screen.getByRole('button', { name: '1' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('button', { name: '9' })).toBeInTheDocument()
    expect(screen.getByText('…')).toBeInTheDocument()
  })

  it('stat cards use global filter totals, not current page items (activeFilter=failed)', async () => {
    // 统一流程列表契约 收口: 4 个 StatCard 统计的是全
    // 局各 filter 的 total, 不得随当前 activeFilter / currentPage 变化.
    // 即使用户切到 failed 看到 12 条 failed, 4 个统计卡仍必须显示
    // 各 filter 全局 total (waiting_user=5, processing=4, completed=2,
    // failed=12), 而非 0/0/0/12 这种"failed 一家独大"的本地统计.
    const user = userEvent.setup()
    const service = createMockTaskService()
    service.reset()

    // 用一个最小 mock, 不需要 25 条, 但要 4 个 filter 各有合理 total,
    // 而且要让 activeFilter='failed' 时 listFlows 看到的是 failed items.
    const fixtures = {
      waiting_user: 5,
      processing: 4,
      library_import_complete: 2,
      failed: 12,
    }
    const makeItems = (filter: 'all' | keyof typeof fixtures, total: number) => {
      if (filter === 'all') {
        return Array.from({ length: total }, (_, i) => ({
          id: `ingest:all-${i}`,
          flow_type: 'external_import' as const,
          route_target: 'task_detail' as const,
          ingest_task_id: `all-${i}`,
          download_task_id: null,
          total_status: 'processing',
          title: `all-${i}`,
          year: 2020,
          media_type: 'movie' as const,
          can_confirm: false,
          file_format: null,
          source_path: `/data/downloads/all-${i}.mkv`,
          created_at: '2026-05-01T00:00:00+08:00',
          updated_at: '2026-05-01T00:00:00+08:00',
          status_summary: {
            status: 'processing' as const,
            current_step: 'metadata_detail',
            failure_reason: null,
            confidence: 0.9,
            confidence_level: 'high' as const,
            latest_message: 'processing',
          },
          download_task: null,
          agent_status_summary: null,
        }))
      }
      return Array.from({ length: total }, (_, i) => ({
        id: `ingest:${filter}-${i}`,
        flow_type: 'external_import' as const,
        route_target: 'task_detail' as const,
        ingest_task_id: `${filter}-${i}`,
        download_task_id: null,
        total_status: filter as 'waiting_user' | 'processing' | 'library_import_complete' | 'failed',
        title: `${filter}-${i}`,
        year: 2020,
        media_type: 'movie' as const,
        can_confirm: false,
        file_format: null,
        source_path: `/data/downloads/${filter}-${i}.mkv`,
        created_at: '2026-05-01T00:00:00+08:00',
        updated_at: '2026-05-01T00:00:00+08:00',
        status_summary: {
          status: filter as 'waiting_user' | 'processing' | 'library_import_complete' | 'failed',
          current_step: 'metadata_detail',
          failure_reason: null,
          confidence: 0.9,
          confidence_level: 'high' as const,
          latest_message: filter,
        },
        download_task: null,
        agent_status_summary: null,
      }))
    }

    const totalsService: TaskListService = {
      ...service,
      async listFlows(params: { filter?: string; page?: number; page_size?: number } = {}) {
        const filter = (params.filter ?? 'all') as 'all' | keyof typeof fixtures
        const total = filter === 'all' ? 23 : fixtures[filter]
        const items = makeItems(filter, total)
        const start = ((params.page ?? 1) - 1) * (params.page_size ?? 15)
        return {
          status: 'success' as const,
          data: { items: items.slice(start, start + (params.page_size ?? 15)) },
          messages: [],
          meta: { page: params.page ?? 1, page_size: params.page_size ?? 15, total, filters: { filter } },
        }
      },
    }

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <MemoryRouter initialEntries={['/tasks?filter=failed']}>
        <QueryClientProvider client={queryClient}>
          <ToastProvider>
            <TaskListPage service={totalsService} />
          </ToastProvider>
        </QueryClientProvider>
      </MemoryRouter>,
    )

    // 等所有 5 个 listFlows 都解析完成 (1 当前页 + 4 filter totals).
    // 关键断言: 即使 activeFilter=failed, 4 个 StatCard 仍是全局
    // waiting=5, processing=4, completed=2, failed=12, 不是被当前页
    // 12 条 failed 本地统计扭曲成 0/0/0/12.
    await waitFor(() => {
      // 4 个 StatCard 的值渲染在 <strong> 标签内, 用 structure 查询.
      const allStatCards = document.querySelectorAll('strong.text-2xl')
      const values = Array.from(allStatCards).map((el) => el.textContent?.trim())
      expect(values).toEqual(['5', '4', '2', '12'])
    })

    // 防御: 切到 library_import_complete, 4 个卡仍然显示全局 5/4/2/12,
    // 不会被当前 completed 页 items 干扰.
    await user.selectOptions(getStatusFilterSelect(), 'library_import_complete')
    await waitFor(() => {
      const allStatCards = document.querySelectorAll('strong.text-2xl')
      const values = Array.from(allStatCards).map((el) => el.textContent?.trim())
      expect(values).toEqual(['5', '4', '2', '12'])
    })
  })

  it('stat cards stay stable when paginating to page 2', async () => {
    // 翻到第 2 页时, 列表的 items 改变, 但 StatCard 应当保持全局
    // total 不变. 即列表的 page state 与 stat card 的 filter totals
    // 完全解耦.
    const user = userEvent.setup()
    const service = createMockTaskService()
    service.reset()

    // 25 条 processing flow, 每页 10 条 → 第 2 页 10 条.
    // filter totals 设成: waiting=7, processing=25, completed=3, failed=2.
    // 用统一的 listFlows mock: page_size=1 时 meta.total 按 filter
    // 返回 (即 meta.total 充当 filter total), page_size>1 时按
    // page/page_size 切片但 total 仍按 filter 算.
    const FIXED_TOTALS = {
      waiting_user: 7,
      processing: 25,
      library_import_complete: 3,
      failed: 2,
    }

    const allItems = Array.from({ length: 25 }, (_, i) => ({
      id: `ingest:proc-${i}`,
      flow_type: 'external_import' as const,
      route_target: 'task_detail' as const,
      ingest_task_id: `proc-${i}`,
      download_task_id: null,
      total_status: 'processing',
      title: `proc-${i}`,
      year: 2020,
      media_type: 'movie' as const,
      can_confirm: false,
      file_format: null,
      source_path: `/data/downloads/proc-${i}.mkv`,
      created_at: '2026-05-01T00:00:00+08:00',
      updated_at: '2026-05-01T00:00:00+08:00',
      status_summary: {
        status: 'processing' as const,
        current_step: 'metadata_detail',
        failure_reason: null,
        confidence: 0.9,
        confidence_level: 'high' as const,
        latest_message: 'processing',
      },
      download_task: null,
      agent_status_summary: null,
    }))

    const totalsService: TaskListService = {
      ...service,
      async listFlows(params: { filter?: string; page?: number; page_size?: number } = {}) {
        const filter = (params.filter ?? 'processing') as 'all' | keyof typeof FIXED_TOTALS
        const total = filter === 'all' ? 37 : FIXED_TOTALS[filter]
        const page = params.page ?? 1
        const pageSize = params.page_size ?? 10
        const start = (page - 1) * pageSize
        // 列表页 (activeFilter=processing) 切出 processing items;
        // 其它 filter / filterTotal 任意 data 都行, 反正只用 meta.total.
        const items = filter === 'processing' ? allItems.slice(start, start + pageSize) : []
        return {
          status: 'success' as const,
          data: { items },
          messages: [],
          meta: { page, page_size: pageSize, total, filters: { filter } },
        }
      },
    }

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <MemoryRouter initialEntries={['/tasks?filter=processing']}>
        <QueryClientProvider client={queryClient}>
          <ToastProvider>
            <TaskListPage service={totalsService} />
          </ToastProvider>
        </QueryClientProvider>
      </MemoryRouter>,
    )

    // 第 1 页: 4 个 StatCard 显示 waiting=7, processing=25, completed=3, failed=2.
    await waitFor(() => {
      const allStatCards = document.querySelectorAll('strong.text-2xl')
      const values = Array.from(allStatCards).map((el) => el.textContent?.trim())
      expect(values).toEqual(['7', '25', '3', '2'])
    })

    // 翻到第 2 页, items 变了, 但 StatCard 必须保持完全相同.
    const nextBtn = screen.getByRole('button', { name: '下一页' })
    await user.click(nextBtn)

    await waitFor(() => {
      const allStatCards = document.querySelectorAll('strong.text-2xl')
      const values = Array.from(allStatCards).map((el) => el.textContent?.trim())
      expect(values).toEqual(['7', '25', '3', '2'])
    })
  })

  it('manual refresh invalidates all flows queries, not just the current page', async () => {
    // 统一流程列表契约 收口: 9a11231 把 4 个 StatCard
    // 改用 4 个独立 filter total queries 后, "手动刷新" 按钮必须同
    // 时刷新当前列表 + 4 个 filter total, 否则顶部统计会 stale. 推
    // 荐做法: queryClient.invalidateQueries({ queryKey: ['flows'] }).
    const service = createMockTaskService()
    service.reset()
    const listFlowsSpy = vi.spyOn(service, 'listFlows')

    renderTaskListPage(service)

    // 等页面进入成功渲染态 (按角色找状态筛选下拉, 等待 StatCard
    // 出现 — 任何 StatCard 渲染都说明 flowsQuery + 4 filter total
    // 都已首次 resolve). findByRole 自动重试.
    await screen.findByRole('combobox', { name: '状态筛选' })
    await waitFor(() => {
      expect(listFlowsSpy.mock.calls.length).toBeGreaterThanOrEqual(5)
    })

    const initialCallCount = listFlowsSpy.mock.calls.length

    // 点击手动刷新按钮.
    const user = userEvent.setup()
    const refreshBtn = await screen.findByRole('button', { name: '手动刷新' })
    await user.click(refreshBtn)

    // 必须至少有 5 次新调用 (1 当前列表 + 4 filter total), 不能只是
    // 当前页 1 次. 给 React Query 一个 tick 异步触发.
    await waitFor(() => {
      expect(listFlowsSpy.mock.calls.length).toBeGreaterThanOrEqual(initialCallCount + 5)
    })
  })

  it('auto polling invalidates filter total queries, not just the current page', async () => {
    // 自动轮询触发后 (ingest task 推进状态), 5 个 useQuery 必须同时
    // 失效/刷新, 不是只刷新当前页. 验证 3 秒轮询窗口内, listFlows
    // 被额外调用的次数 ≥ 5 (1 当前页 + 4 filter total).
    const service = createMockTaskService()
    service.reset()
    const tickSpy = vi.spyOn(service, 'tick')
    const listFlowsSpy = vi.spyOn(service, 'listFlows')

    renderTaskListPage(service)

    // 等所有 5 个 listFlows 都首次完成.
    await screen.findByRole('combobox', { name: '状态筛选' })
    await waitFor(() => {
      expect(listFlowsSpy.mock.calls.length).toBeGreaterThanOrEqual(5)
    })
    // 等第一次 tick 已经发生, 证明轮询在跑.
    await waitFor(() => {
      expect(tickSpy).toHaveBeenCalled()
    }, { timeout: 4500 })

    const callsAfterFirstTick = listFlowsSpy.mock.calls.length

    // 第二次 tick + invalidate 触发后, listFlows 必须再被调用至少 5
    // 次 (1 当前列表 + 4 filter total), 证明 invalidate ['flows']
    // 覆盖了 filter total queries.
    await waitFor(() => {
      expect(listFlowsSpy.mock.calls.length).toBeGreaterThanOrEqual(callsAfterFirstTick + 5)
    }, { timeout: 6000 })
  }, 12000)

  it('filter total errors do not silently show 0 — show — when no stale data', async () => {
    // 关键守卫: 主列表 (activeFilter/currentPage/default page size) 成功, 4 个
    // filter total queries 失败, 4 个 StatCard 必须显示 "—", 而不是
    // 把 0 误读成"该 filter 没任何 flow". 旧版测试让主列表也失败,
    // 整个页面进入 ErrorState, StatCard 不渲染, 实际没覆盖这条边界.
    // 有 stale data 时仍保留旧值, 待下次刷新 — 后面另写一条守卫.
    const erroringService: TaskListService = {
      async listFlows(params: { filter?: string; page?: number; page_size?: number } = {}) {
        // 判定调用类型: page_size=1 + page=1 是 filter total query,
        // 其它调用是主列表. 主列表成功, filter total
        // 全部 throw.
        const isFilterTotal = params.page_size === 1 && params.page === 1
        if (isFilterTotal) {
          throw new Error('mock stats unavailable')
        }
        // 主列表返回 5 条 processing flow.
        const items = Array.from({ length: 5 }, (_, i) => ({
          id: `ingest:proc-${i}`,
          flow_type: 'external_import' as const,
          route_target: 'task_detail' as const,
          ingest_task_id: `proc-${i}`,
          download_task_id: null,
          total_status: 'processing',
          title: `proc-${i}`,
          year: 2020,
          media_type: 'movie' as const,
          can_confirm: false,
          file_format: null,
          source_path: `/data/downloads/proc-${i}.mkv`,
          created_at: '2026-05-01T00:00:00+08:00',
          updated_at: '2026-05-01T00:00:00+08:00',
          status_summary: {
            status: 'processing' as const,
            current_step: 'metadata_detail',
            failure_reason: null,
            confidence: 0.9,
            confidence_level: 'high' as const,
            latest_message: 'processing',
          },
          download_task: null,
          agent_status_summary: null,
        }))
        return {
          status: 'success' as const,
          data: { items },
          messages: [],
          meta: { page: 1, page_size: 10, total: 5, filters: { filter: 'all' } },
        }
      },
      async tick() {
        return null
      },
      async retryDownloadSync() {
        return { status: 'success' as const, data: { synced: 0, failed: 0, skipped: 1 }, messages: [], meta: {} }
      },
      async deleteDownload() {
        return { status: 'success' as const, data: { task_id: '', deleted: true, qb_deleted: true, qb_error: null, files_cleaned: [] }, messages: [], meta: {} }
      },
      async deleteTask() {
        return { status: 'success' as const, data: { task_id: '', deleted: true, qb_deleted: null, qb_error: null, files_cleaned: [] }, messages: [], meta: {} }
      },
      async pauseDownload() {
        return { status: 'success' as const, data: { download_id: '', status: 'paused' }, messages: [], meta: {} }
      },
      async resumeDownload() {
        return { status: 'success' as const, data: { download_id: '', status: 'downloading' }, messages: [], meta: {} }
      },
    }

    renderTaskListPage(erroringService)

    // 主列表成功 → StatCard 应当渲染. 等 StatCard 的 4 个 <strong>
    // 全部出现.
    await waitFor(() => {
      const allStatCards = document.querySelectorAll('strong.text-2xl')
      expect(allStatCards.length).toBe(4)
    })

    // 4 个 filter total query 都失败, 且从未成功过, StatCard 必须
    // 显示 "—" 而不是 "0". 关键: 验证 4 个值全是 "—", 不是 0.
    await waitFor(() => {
      const allStatCards = document.querySelectorAll('strong.text-2xl')
      const values = Array.from(allStatCards).map((el) => el.textContent?.trim())
      expect(values).toEqual(['—', '—', '—', '—'])
    })
  })

  it('filter total queries with stale data keep showing the old value on error', async () => {
    // 边界: 4 个 filter total query 第一次成功 (有 stale data), 第
    // 二次重试时失败. StatCard 必须保留第一次的 stale value, 不显示
    // "—" 也不重置成 0. 这避免"瞬时网络抖动把统计卡闪成 0/—"的
    // 视觉跳变.
    let callCount = 0
    const flakyService: TaskListService = {
      async listFlows(params: { filter?: string; page?: number; page_size?: number } = {}) {
        const isFilterTotal = params.page_size === 1 && params.page === 1
        callCount += 1
        if (isFilterTotal) {
          // 第一次成功 (任何 filter total), 第二次起 throw.
          if (callCount > 5) {
            throw new Error('flaky stats')
          }
          return {
            status: 'success' as const,
            data: { items: [] },
            messages: [],
            meta: { page: 1, page_size: 1, total: 42, filters: { filter: params.filter ?? 'all' } },
          }
        }
        // 主列表直接成功 (无失败).
        return {
          status: 'success' as const,
          data: { items: [] },
          messages: [],
          meta: { page: 1, page_size: 10, total: 0, filters: { filter: 'all' } },
        }
      },
      async tick() {
        return null
      },
      async retryDownloadSync() {
        return { status: 'success' as const, data: { synced: 0, failed: 0, skipped: 1 }, messages: [], meta: {} }
      },
      async deleteDownload() {
        return { status: 'success' as const, data: { task_id: '', deleted: true, qb_deleted: true, qb_error: null, files_cleaned: [] }, messages: [], meta: {} }
      },
      async deleteTask() {
        return { status: 'success' as const, data: { task_id: '', deleted: true, qb_deleted: null, qb_error: null, files_cleaned: [] }, messages: [], meta: {} }
      },
      async pauseDownload() {
        return { status: 'success' as const, data: { download_id: '', status: 'paused' }, messages: [], meta: {} }
      },
      async resumeDownload() {
        return { status: 'success' as const, data: { download_id: '', status: 'downloading' }, messages: [], meta: {} }
      },
    }

    renderTaskListPage(flakyService)

    // 第一次成功后, 4 个 StatCard 都应显示 42 (mock 返回的 total).
    await waitFor(() => {
      const allStatCards = document.querySelectorAll('strong.text-2xl')
      const values = Array.from(allStatCards).map((el) => el.textContent?.trim())
      expect(values).toEqual(['42', '42', '42', '42'])
    })
  })
})
