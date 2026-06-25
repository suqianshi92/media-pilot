import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'

import i18n from '@/i18n'
import { createMockTaskService } from '@/mocks/service'

import { DashboardPage, type DashboardService } from './dashboard-page'

function renderDashboard(service: DashboardService = createMockTaskService()) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  })

  render(
    <MemoryRouter initialEntries={['/']}>
      <QueryClientProvider client={queryClient}>
        <DashboardPage service={service} />
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
})

describe('DashboardPage', () => {
  it('renders stat cards with counts', async () => {
    renderDashboard()

    await waitFor(() => {
      expect(screen.getByText('首页概览')).toBeInTheDocument()
    })
    // 统计卡和最近流程状态徽章都会显示这些文字，用 getAllByText
    expect(screen.getAllByText('待确认').length).toBeGreaterThan(0)
    expect(screen.getAllByText('处理中').length).toBeGreaterThan(0)
    expect(screen.getAllByText('已完成').length).toBeGreaterThan(0)
    expect(screen.getAllByText('失败').length).toBeGreaterThan(0)
  })

  it('renders recent flows section', async () => {
    renderDashboard()

    await waitFor(() => {
      expect(screen.getByText('最近流程')).toBeInTheDocument()
    })
  })

  it('shows error state when listFlows query fails', async () => {
    const errorService: DashboardService = {
      async listFlows() {
        throw new Error('mock flows error')
      },
      async getBackgroundStatus() {
        return {
          status: 'success' as const,
          data: {
            enabled: true,
            state: 'idle' as const,
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

    renderDashboard(errorService)

    await waitFor(() => {
      expect(screen.getByText('加载失败')).toBeInTheDocument()
      expect(screen.getByText('mock flows error')).toBeInTheDocument()
      expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument()
    })
  })

  it('download-only items in recent flows are not clickable', async () => {
    renderDashboard()

    await waitFor(() => {
      expect(screen.getByText('最近流程')).toBeInTheDocument()
    })

    // 纯下载流程项（有 download_task 但无 ingest_task_id）不应有 cursor-pointer
    // 它们渲染出来但不响应点击跳转
    const flowItems = document.querySelectorAll('.divide-y > div')
    const downloadOnlyItem = Array.from(flowItems).find((el) =>
      el.textContent?.includes('下载中') || el.textContent?.includes('等待下载器同步'),
    )
    // 如果有下载流程项，验证它没有 cursor-pointer 样式
    if (downloadOnlyItem) {
      expect(downloadOnlyItem.className).not.toContain('cursor-pointer')
    }
  })

describe('DashboardPage i18n', () => {
    afterEach(() => {
      i18n.changeLanguage('zh')
    })

    it('shows English stat card labels when language is switched to English', async () => {
      i18n.changeLanguage('en')
      renderDashboard()

      await waitFor(() => {
        expect(screen.getByText('Dashboard')).toBeInTheDocument()
      })
      expect(screen.getByText('Pending')).toBeInTheDocument()
      expect(screen.getByText('Failed')).toBeInTheDocument()
      // "Processing" and "Completed" appear in both stat cards and status badges
      expect(screen.getAllByText('Processing').length).toBeGreaterThan(0)
      expect(screen.getAllByText('Completed').length).toBeGreaterThan(0)
    })

    it('shows English section titles in recent flows when language is switched', async () => {
      i18n.changeLanguage('en')
      renderDashboard()

      await waitFor(() => {
        expect(screen.getByText('Recent Flows')).toBeInTheDocument()
      })
    })
  })

  it('shows empty state when no flows exist', async () => {
    const emptyService: DashboardService = {
      async listFlows() {
        return {
          status: 'success' as const,
          data: { items: [] },
          messages: [],
          meta: { page: 1, page_size: 0, total: 0, filters: { filter: 'all' } },
        }
      },
      async getBackgroundStatus() {
        return {
          status: 'success' as const,
          data: {
            enabled: true,
            state: 'idle' as const,
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

    renderDashboard(emptyService)

    await waitFor(() => {
      expect(screen.getByText('暂无数据')).toBeInTheDocument()
    })
  })

  it('uses listFlows as the sole list data source, never listTasks + listDownloads stitching', async () => {
    // 统一流程列表契约 要求首页最近流程只调
    // listFlows, 不得再用 listTasks() + listDownloads() 拼接.
    const service = createMockTaskService()
    service.reset()
    const listFlowsSpy = vi.spyOn(service, 'listFlows')
    const listTasksSpy = vi.spyOn(service, 'listTasks')
    const listDownloadsSpy = vi.spyOn(service, 'listDownloads')

    renderDashboard(service)

    await waitFor(() => {
      expect(screen.getByText('最近流程')).toBeInTheDocument()
    })

    expect(listFlowsSpy).toHaveBeenCalled()
    expect(listTasksSpy).not.toHaveBeenCalled()
    expect(listDownloadsSpy).not.toHaveBeenCalled()
  })

  it('recent flows use the same unified flow attention priority as task list', async () => {
    // 统一流程列表契约: 首页最近流程与任务列表
    // 都从后端 listFlows 拉取, 后端按统一 attention priority 排序.
    // 混合 flow 必须按 attention priority 展示: waiting_user → processing-adjacent
    // (downloading / awaiting_sync) → failed → done. 不得把所有 ingest
    // 固定排在所有 download-only 之前.
    //
    // 关键: download-only-1 (downloading) 的 updated_at 比 ingest waiting 更
    // 新, 但按 attention priority 它属于 p2 排 ingest waiting (p1) 之后.
    const mixedFlows = [
      // attention 1 (waiting_user)
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
        created_at: '2026-05-01T00:00:00+08:00',
        updated_at: '2026-04-01T00:00:00+08:00',
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
      // attention 2 downloading download-only (updated_at 较新)
      {
        id: 'download:dl-only-1',
        flow_type: 'download_only' as const,
        route_target: 'download_detail' as const,
        ingest_task_id: null,
        download_task_id: 'dl-only-1',
        total_status: 'downloading',
        title: '下载中任务',
        year: null,
        media_type: null,
        can_confirm: false,
        file_format: null,
        source_path: '/data/downloads/下载中.mkv',
        created_at: '2026-05-03T00:00:00+08:00',
        updated_at: '2026-05-09T00:00:00+08:00',
        status_summary: {
          status: 'downloading',
          current_step: null,
          failure_reason: null,
          confidence: null,
          confidence_level: 'unknown' as const,
          latest_message: 'downloading',
        },
        download_task: {
          id: 'dl-only-1',
          title: '下载中任务',
          source: 'prowlarr',
          qb_hash: null,
          save_path: '/data/downloads/下载中.mkv',
          content_path: null,
          progress: 0.3,
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
          updated_at: '2026-05-09T00:00:00+08:00',
        },
        agent_status_summary: null,
      },
      // attention 2 awaiting_sync download-only (updated_at 较老)
      {
        id: 'download:dl-only-2',
        flow_type: 'download_only' as const,
        route_target: 'download_detail' as const,
        ingest_task_id: null,
        download_task_id: 'dl-only-2',
        total_status: 'awaiting_sync',
        title: '等待同步任务',
        year: null,
        media_type: null,
        can_confirm: false,
        file_format: null,
        source_path: '/data/downloads/等待.mkv',
        created_at: '2026-05-04T00:00:00+08:00',
        updated_at: '2026-05-07T00:00:00+08:00',
        status_summary: {
          status: 'awaiting_sync',
          current_step: null,
          failure_reason: null,
          confidence: null,
          confidence_level: 'unknown' as const,
          latest_message: 'awaiting_sync',
        },
        download_task: {
          id: 'dl-only-2',
          title: '等待同步任务',
          source: 'prowlarr',
          qb_hash: null,
          save_path: '/data/downloads/等待.mkv',
          content_path: null,
          progress: 0,
          download_speed_bytes_per_second: null,
          upload_speed_bytes_per_second: null,
          seeders: 0,
          leechers: 0,
          connections: null,
          qb_state: null,
          status: 'awaiting_sync',
          error_message: null,
          ingest_task_id: null,
          created_at: '2026-05-04T00:00:00+08:00',
          updated_at: '2026-05-07T00:00:00+08:00',
        },
        agent_status_summary: null,
      },
      // attention 4 (library_import_complete)
      {
        id: 'ingest:task-lic-1',
        flow_type: 'external_import' as const,
        route_target: 'task_detail' as const,
        ingest_task_id: 'task-lic-1',
        download_task_id: null,
        total_status: 'library_import_complete',
        title: '已入库任务',
        year: 2020,
        media_type: 'movie' as const,
        can_confirm: false,
        file_format: null,
        source_path: '/data/downloads/已入库.mkv',
        created_at: '2026-05-02T00:00:00+08:00',
        updated_at: '2026-05-08T00:00:00+08:00',
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

    const customService: DashboardService = {
      async listFlows() {
        return {
          status: 'success' as const,
          data: { items: mixedFlows },
          messages: [],
          meta: { page: 1, page_size: mixedFlows.length, total: mixedFlows.length, filters: { filter: 'all' } },
        }
      },
      async getBackgroundStatus() {
        return {
          status: 'success' as const,
          data: {
            enabled: true,
            state: 'idle' as const,
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

    renderDashboard(customService)

    await waitFor(() => {
      expect(screen.getByText('最近流程')).toBeInTheDocument()
    })

    // 提取最近流程区块的渲染顺序
    const flowItems = document.querySelectorAll('.divide-y > div')
    const flowTitles = Array.from(flowItems).map((el) => {
      const titleEl = el.querySelector('p.text-sm.font-medium')
      return titleEl?.textContent?.trim() ?? ''
    })

    // 预期顺序 (统一 attention priority):
    // 1. 待确认任务 (ingest, p1)
    // 2. 下载中任务 (download-only, p2 downloading, updated_at 较新)
    // 3. 等待同步任务 (download-only, p2 awaiting_sync, updated_at 较老)
    // 4. 已入库任务 (ingest, p4)
    const titleCore = flowTitles.map((t) => t.replace(/(等待用户处理|下载中|等待下载器同步|已入库)$/, ''))
    expect(titleCore.slice(0, 4)).toEqual([
      '待确认任务',
      '下载中任务',
      '等待同步任务',
      '已入库任务',
    ])
  })

  it('stat cards use meta.total, not the limited recent flows page', async () => {
    // 统一流程列表契约 收口: 首页 4 个统计卡取自后端
    // filter totals, 不得从最近流程 page_size=5 的 items 本地统计.
    // 构造远超 page_size 的全局 total, 验证统计卡正确显示, 不被
    // 5 条最近流程低估.
    const FIXED_TOTALS = {
      waiting_user: 200,
      processing: 50,
      library_import_complete: 30,
      failed: 15,
    }

    // 最近流程返回 5 条全是 processing, 但 waiting/processing/
    // completed/failed 各 filter 的 total 是上面 4 个大数. 这模拟
    // 真实场景: 全局 200 条 waiting, 但最近 5 条都是 processing.
    const recentItems = Array.from({ length: 5 }, (_, i) => ({
      id: `ingest:proc-${i}`,
      flow_type: 'external_import' as const,
      route_target: 'task_detail' as const,
      ingest_task_id: `proc-${i}`,
      download_task_id: null,
      total_status: 'processing',
      title: `处理中 ${i}`,
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

    const totalsService: DashboardService = {
      async listFlows(params: { filter?: string; page?: number; page_size?: number } = {}) {
        const filter = (params.filter ?? 'all') as 'all' | keyof typeof FIXED_TOTALS
        const total = filter === 'all' ? 295 : FIXED_TOTALS[filter]
        // 最近流程用 page_size=5; filter totals 用 page_size=1.
        const page = params.page ?? 1
        const pageSize = params.page_size ?? 5
        return {
          status: 'success' as const,
          data: { items: pageSize <= 5 ? recentItems.slice(0, pageSize) : [] },
          messages: [],
          meta: { page, page_size: pageSize, total, filters: { filter } },
        }
      },
      async getBackgroundStatus() {
        return {
          status: 'success' as const,
          data: {
            enabled: true,
            state: 'idle' as const,
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

    renderDashboard(totalsService)

    // 4 个 StatCard 必须在 Dashboard 渲染. 等待最近流程 query 完成
    // (loading 解锁) 后断言.
    await waitFor(() => {
      expect(screen.getByText('最近流程')).toBeInTheDocument()
    })

    // StatCard 的 count 在 <p class="text-2xl font-bold"> 标签内.
    // 期待顺序 (按 DashboardPage 渲染顺序): pending / processing /
    // completed / failed → 200, 50, 30, 15.
    // 关键: 不是 "5, 5, 0, 0" 这种从 5 条 processing items 误算的结果.
    const allCountEls = document.querySelectorAll('p.text-2xl.font-bold')
    const counts = Array.from(allCountEls).map((el) => el.textContent?.trim())
    expect(counts).toEqual(['200', '50', '30', '15'])
  })

  it('filter total errors show — instead of 0 when recent flows succeed', async () => {
    // 统一流程列表契约 收口: 与 TaskListPage 对齐, 4
    // 个 filter total queries 失败且无 stale data 时, StatCard 显示
    // "—" 而非 0. 关键: 最近流程 (page_size=5) 必须成功, 否则整个页
    // 面进入 ErrorState, StatCard 不渲染, 守卫就失效了.
    const erroringService: DashboardService = {
      async listFlows(params: { filter?: string; page?: number; page_size?: number } = {}) {
        // 判定: page_size=1 + page=1 是 filter total query, throw.
        // 其它 (page_size=5 是 recentFlowsQuery) 成功, 返回一条 flow.
        const isFilterTotal = params.page_size === 1 && params.page === 1
        if (isFilterTotal) {
          throw new Error('mock stats unavailable')
        }
        return {
          status: 'success' as const,
          data: {
            items: [
              {
                id: 'ingest:recent-1',
                flow_type: 'external_import' as const,
                route_target: 'task_detail' as const,
                ingest_task_id: 'recent-1',
                download_task_id: null,
                total_status: 'processing',
                title: '最近流程 1',
                year: 2020,
                media_type: 'movie' as const,
                can_confirm: false,
                file_format: null,
                source_path: '/data/downloads/recent-1.mkv',
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
              },
            ],
          },
          messages: [],
          meta: { page: 1, page_size: 5, total: 1, filters: { filter: 'all' } },
        }
      },
      async getBackgroundStatus() {
        return {
          status: 'success' as const,
          data: {
            enabled: true,
            state: 'idle' as const,
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

    renderDashboard(erroringService)

    // 等最近流程 query 完成 + 4 个 filter total 都已失败. StatCard
    // 必须全部渲染, 而不是整个页面进入 ErrorState.
    await waitFor(() => {
      expect(screen.getByText('最近流程')).toBeInTheDocument()
    })

    // 4 个 filter total query 都失败, StatCard 必须显示 "—", 不是 0.
    // 关键断言: 验证 4 个值全是 "—", 不是 "0".
    await waitFor(() => {
      const allCountEls = document.querySelectorAll('p.text-2xl.font-bold')
      const counts = Array.from(allCountEls).map((el) => el.textContent?.trim())
      expect(counts).toEqual(['—', '—', '—', '—'])
    })
  })

  it('filter total queries with stale data keep showing the old value on error', async () => {
    // 边界: 4 个 filter total query 第一次成功 (有 stale data 42), 第
    // 二次重试时失败. StatCard 必须保留第一次的 stale value 42, 不
    // 显示 "—" 也不重置成 0. 与 TaskListPage 守卫一致.
    let callCount = 0
    const flakyService: DashboardService = {
      async listFlows(params: { filter?: string; page?: number; page_size?: number } = {}) {
        const isFilterTotal = params.page_size === 1 && params.page === 1
        callCount += 1
        if (isFilterTotal) {
          // 前 5 次成功 (1 recent + 4 filter totals), 之后 throw.
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
        // recent flows 直接成功.
        return {
          status: 'success' as const,
          data: { items: [] },
          messages: [],
          meta: { page: 1, page_size: 5, total: 0, filters: { filter: 'all' } },
        }
      },
      async getBackgroundStatus() {
        return {
          status: 'success' as const,
          data: {
            enabled: true,
            state: 'idle' as const,
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

    renderDashboard(flakyService)

    // 第一次成功后, 4 个 StatCard 都应显示 42 (mock 返回的 total).
    await waitFor(() => {
      const allCountEls = document.querySelectorAll('p.text-2xl.font-bold')
      const counts = Array.from(allCountEls).map((el) => el.textContent?.trim())
      expect(counts).toEqual(['42', '42', '42', '42'])
    })
  })
})
