import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

import i18n from '@/i18n'
import { createMockTaskService } from '@/mocks/service'

import { TaskDetailPage, type TaskDetailService } from './task-detail-page'

function renderTaskDetailPage(taskId: string, service: TaskDetailService = createMockTaskService()) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  })

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/tasks/${taskId}`]}>
        <Routes>
          <Route path="/tasks/:taskId" element={<TaskDetailPage service={service} />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => {
  cleanup()
})

describe('TaskDetailPage', () => {
  it('renders the page title as 任务详情', async () => {
    renderTaskDetailPage('task-completed')

    expect(await screen.findByRole('heading', { level: 1, name: '任务详情' })).toBeInTheDocument()
    expect(screen.getByText('当前任务 ID：task-completed')).toBeInTheDocument()
  })

  it('renders the base information section for a completed task', async () => {
    renderTaskDetailPage('task-completed')

    expect(await screen.findByRole('heading', { level: 2, name: '基础信息' })).toBeInTheDocument()
    expect(screen.getByText('状态')).toBeInTheDocument()
    expect(screen.getByText('源路径')).toBeInTheDocument()
    expect(screen.getByText('当前步骤')).toBeInTheDocument()
    expect(screen.getByText('创建时间')).toBeInTheDocument()
    expect(screen.getByText('更新时间')).toBeInTheDocument()
    expect(screen.getAllByText('/data/downloads/天气之子.mkv').length).toBeGreaterThan(0)
    expect(screen.getAllByText('已入库').length).toBeGreaterThan(0)
    expect(screen.getAllByText('已完成媒体入库').length).toBeGreaterThan(0)
    expect(screen.getAllByText('无').length).toBeGreaterThan(0)
  })

  it('renders the completed-state main media header', async () => {
    renderTaskDetailPage('task-completed')

    const poster = await screen.findByRole('img', { name: '天气之子 poster' })
    const fanart = screen.getByRole('img', { name: '天气之子 fanart' })
    const clearlogo = screen.getByRole('img', { name: '天气之子 clearlogo' })

    expect(poster).toBeInTheDocument()
    expect(poster).toHaveAttribute('src', '/api/v1/tasks/task-completed/assets/poster')
    expect(fanart).toBeInTheDocument()
    expect(fanart).toHaveAttribute('src', '/api/v1/tasks/task-completed/assets/fanart')
    expect(clearlogo).toBeInTheDocument()
    expect(clearlogo).toHaveAttribute('src', '/api/v1/tasks/task-completed/assets/clearlogo')
    expect(screen.getByRole('heading', { level: 2, name: '天气之子' })).toBeInTheDocument()
    expect(screen.getByText('天気の子 · 2019')).toBeInTheDocument()
    expect(screen.getByText('评分 7.9')).toBeInTheDocument()
    expect(screen.getByText('112 分钟')).toBeInTheDocument()
    expect(screen.getByText('动画 / 爱情 / 奇幻')).toBeInTheDocument()
    expect(screen.getByText('日本')).toBeInTheDocument()
  })

  it('renders stable empty states when completed images are missing', async () => {
    renderTaskDetailPage('task-completed-no-images')

    expect(await screen.findByRole('heading', { level: 2, name: '天气之子' })).toBeInTheDocument()
    expect(screen.getByLabelText('暂无背景图')).toBeInTheDocument()
    expect(screen.getByText('暂无海报')).toBeInTheDocument()
    expect(screen.getByText('暂无标题 Logo')).toBeInTheDocument()
    expect(screen.queryByRole('img', { name: '天气之子 poster' })).not.toBeInTheDocument()
    expect(screen.queryByRole('img', { name: '天气之子 fanart' })).not.toBeInTheDocument()
    expect(screen.queryByRole('img', { name: '天气之子 clearlogo' })).not.toBeInTheDocument()
  })

  it('falls back to stable empty states when completed images fail to load', async () => {
    renderTaskDetailPage('task-completed')

    const poster = await screen.findByRole('img', { name: '天气之子 poster' })
    const fanart = screen.getByRole('img', { name: '天气之子 fanart' })
    const clearlogo = screen.getByRole('img', { name: '天气之子 clearlogo' })

    fireEvent.error(poster)
    fireEvent.error(fanart)
    fireEvent.error(clearlogo)

    expect(screen.getByLabelText('暂无背景图')).toBeInTheDocument()
    expect(screen.getByText('暂无海报')).toBeInTheDocument()
    expect(screen.getByText('暂无标题 Logo')).toBeInTheDocument()
    expect(screen.queryByRole('img', { name: '天气之子 poster' })).not.toBeInTheDocument()
    expect(screen.queryByRole('img', { name: '天气之子 fanart' })).not.toBeInTheDocument()
    expect(screen.queryByRole('img', { name: '天气之子 clearlogo' })).not.toBeInTheDocument()
  })

  it('renders the media source selection section for a bdmv task', async () => {
    renderTaskDetailPage('task-bdmv-manual')

    expect(await screen.findByRole('heading', { level: 2, name: '媒体源选择' })).toBeInTheDocument()
    expect(screen.getByText('输入路径')).toBeInTheDocument()
    expect(screen.getByText('选中的主媒体文件')).toBeInTheDocument()
    expect(screen.getByText('选择原因')).toBeInTheDocument()
    expect(screen.getByText('选择置信度')).toBeInTheDocument()
    expect(screen.getByText('/data/workspace/你的名字/BDMV')).toBeInTheDocument()
    expect(screen.getAllByText('尚未选择').length).toBeGreaterThan(0)
    expect(screen.getByText('检测到 BDMV 目录')).toBeInTheDocument()
    expect(screen.getByText('1.00')).toBeInTheDocument()
    expect(screen.getByText('候选文件')).toBeInTheDocument()
    expect(screen.getByText('排除文件')).toBeInTheDocument()
    expect(screen.getByText('BDMV 需要人工处理')).toBeInTheDocument()
  })

  it('renders the completed hero and metadata sections for a completed task', async () => {
    renderTaskDetailPage('task-completed')

    expect(await screen.findByRole('heading', { level: 2, name: '天气之子' })).toBeInTheDocument()
    expect(screen.getByText('天気の子 · 2019')).toBeInTheDocument()
    // External metadata section should show TMDB ID
    expect(screen.getByText('568160')).toBeInTheDocument()
    expect(screen.getByText('外部资料')).toBeInTheDocument()
  })

  it('renders the metadata detail section for a completed task', async () => {
    renderTaskDetailPage('task-completed')

    expect(await screen.findByRole('heading', { level: 2, name: '元数据详情' })).toBeInTheDocument()
    expect(screen.getByText('简介')).toBeInTheDocument()
    expect(screen.getByText('导演')).toBeInTheDocument()
    expect(screen.getByText('主演')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 3, name: '外部资料' })).toBeInTheDocument()
    expect(screen.getByText('离家少年与拥有晴天能力的少女相遇。')).toBeInTheDocument()
    expect(screen.getByText('新海诚')).toBeInTheDocument()
    expect(screen.getByText('醍醐虎汰朗（森岛帆高）')).toBeInTheDocument()
    expect(screen.getByText('568160')).toBeInTheDocument()
    expect(screen.getByText('tt9426210')).toBeInTheDocument()
    expect(screen.getByText('2019-07-19')).toBeInTheDocument()
    expect(screen.getByText('CoMix Wave Films')).toBeInTheDocument()
  })

  it('renders manual metadata research section with keyword input', async () => {
    renderTaskDetailPage('task-multiple-candidates')

    expect(await screen.findByRole('heading', { level: 2, name: '手动检索元数据' })).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: '搜索关键词' })).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: '搜索范围' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重新搜索' })).toBeInTheDocument()
    expect(screen.getByText('可修改关键词或搜索范围后点击重新搜索。')).toBeInTheDocument()
  })

  it('searches manual metadata candidates with keyword and scope', async () => {
    const base = createMockTaskService()
    let args: { taskId: string; keyword: string; scope: string } | null = null

    const customService: TaskDetailService = {
      ...base,
      researchCandidates: async (taskId, keyword, scope) => {
        args = { taskId, keyword, scope: scope ?? 'all' }
        return base.researchCandidates(taskId, keyword, scope ?? 'all')
      },
    }

    renderTaskDetailPage('task-multiple-candidates', customService)

    const keywordInput = await screen.findByRole('textbox', { name: '搜索关键词' })
    const scopeSelect = screen.getByRole('combobox', { name: '搜索范围' })
    const searchButton = screen.getByRole('button', { name: '重新搜索' })

    fireEvent.change(keywordInput, { target: { value: '铃芽之旅' } })
    fireEvent.change(scopeSelect, { target: { value: 'tmdb_show' } })
    fireEvent.click(searchButton)

    await waitFor(() => {
      expect(args).toEqual({ taskId: 'task-multiple-candidates', keyword: '铃芽之旅', scope: 'tmdb_show' })
    })
    expect(await screen.findByText('天气之子')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: '选择此候选并继续' }).length).toBeGreaterThan(0)
  })

  it('calls manualSelect and refreshes task-related queries', async () => {
    const base = createMockTaskService()
    let finishManualSelect: (() => void) | undefined
    const called = {
      getTaskDetail: 0,
      listAgentMessages: 0,
      listAgentDecisions: 0,
      listAgentToolCalls: 0,
      researchCandidates: 0,
      manualSelect: 0,
    }

    const customService: TaskDetailService = {
      ...base,
      getTaskDetail: async (taskId) => {
        called.getTaskDetail += 1
        return base.getTaskDetail(taskId)
      },
      listAgentMessages: async (taskId) => {
        called.listAgentMessages += 1
        return base.listAgentMessages(taskId)
      },
      listAgentDecisions: async (taskId) => {
        called.listAgentDecisions += 1
        return base.listAgentDecisions(taskId)
      },
      listAgentToolCalls: async (taskId) => {
        called.listAgentToolCalls += 1
        return base.listAgentToolCalls(taskId)
      },
      researchCandidates: async (taskId, keyword, scope) => {
        called.researchCandidates += 1
        return base.researchCandidates(taskId, keyword, scope)
      },
      manualSelect: async (taskId, params) => {
        called.manualSelect += 1
        await new Promise<void>((resolve) => {
          finishManualSelect = resolve
        })
        return base.manualSelect(taskId, params)
      },
    }

    renderTaskDetailPage('task-multiple-candidates', customService)

    const keywordInput = await screen.findByRole('textbox', { name: '搜索关键词' })
    fireEvent.change(keywordInput, { target: { value: '天气之子' } })
    fireEvent.click(screen.getByRole('button', { name: '重新搜索' }))

    await waitFor(() => {
      expect(called.researchCandidates).toBe(1)
    })
    expect(await screen.findByText('天气之子')).toBeInTheDocument()

    const baseline = {
      getTaskDetail: called.getTaskDetail,
      listAgentMessages: called.listAgentMessages,
      listAgentDecisions: called.listAgentDecisions,
      listAgentToolCalls: called.listAgentToolCalls,
    }

    fireEvent.click(screen.getAllByRole('button', { name: '选择此候选并继续' })[0])

    await waitFor(() => {
      expect(called.manualSelect).toBe(1)
    })
    expect(screen.getByText('已提交元数据选择')).toBeInTheDocument()
    expect(screen.getByText('正在使用 天气之子 继续处理入库。')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: '处理中…' }).length).toBeGreaterThan(0)
    finishManualSelect?.()
    await waitFor(() => {
      expect(called.getTaskDetail).toBeGreaterThan(baseline.getTaskDetail)
      expect(called.listAgentMessages).toBeGreaterThan(baseline.listAgentMessages)
      expect(called.listAgentDecisions).toBeGreaterThan(baseline.listAgentDecisions)
      expect(called.listAgentToolCalls).toBeGreaterThan(baseline.listAgentToolCalls)
    })
    expect(await screen.findByText('已选择 天气之子 (tmdb)')).toBeInTheDocument()
  })

  it('renders poster images for manual metadata research candidates', async () => {
    const base = createMockTaskService()
    const customService: TaskDetailService = {
      ...base,
      researchCandidates: async () => ({
        status: 'success',
        data: {
          candidates: [
            {
              provider: 'tmdb',
              provider_id: 'movie:poster',
              title: '带封面的候选',
              original_title: 'Poster Candidate',
              year: 2026,
              media_type: 'movie',
              confidence: 0.91,
              match_reason: 'manual_search',
              risk_flags: [],
              payload: {},
              poster_url: 'https://example.test/poster.jpg',
              overview: '候选简介',
            },
          ],
          search_summary: {
            keyword: '带封面的候选',
            scope: 'all',
            total_candidates: 1,
            kept_existing_candidates: false,
            searched_profiles: [],
          },
        },
        messages: [],
        meta: {},
      }),
    }

    const { container } = renderTaskDetailPage('task-multiple-candidates', customService)

    const keywordInput = await screen.findByRole('textbox', { name: '搜索关键词' })
    fireEvent.change(keywordInput, { target: { value: '带封面的候选' } })
    fireEvent.click(screen.getByRole('button', { name: '重新搜索' }))

    expect(await screen.findByText('带封面的候选')).toBeInTheDocument()
    expect(container.querySelector('img[src="https://example.test/poster.jpg"]')).not.toBeNull()
  })

  it('disables manual metadata research inputs when agent is running', async () => {
    const base = createMockTaskService()
    const customService: TaskDetailService = {
      ...base,
      getTaskDetail: async (taskId) => {
        const response = await base.getTaskDetail(taskId)
        if (taskId !== 'task-multiple-candidates') return response
        return {
          ...response,
          data: {
            ...response.data,
            task: {
              ...response.data.task,
              status_summary: {
                ...response.data.task.status_summary,
                status: 'agent_running',
              },
            },
          },
        }
      },
    }

    renderTaskDetailPage('task-multiple-candidates', customService)

    expect(await screen.findByRole('heading', { level: 2, name: '手动检索元数据' })).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: '搜索关键词' })).toBeDisabled()
    expect(screen.getByRole('combobox', { name: '搜索范围' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '重新搜索' })).toBeDisabled()
    expect(screen.getByText('当前状态不支持人工检索')).toBeInTheDocument()
    expect(
      screen.getByText('任务正在 Agent 处理中，不允许手动检索元数据，请等待任务执行完成或使用卡住恢复后重试。'),
    ).toBeInTheDocument()
  })

  it('renders the write result section for a completed task', async () => {
    renderTaskDetailPage('task-completed')

    expect(await screen.findByRole('heading', { level: 2, name: '写入结果' })).toBeInTheDocument()
    expect(screen.getByText('目标目录')).toBeInTheDocument()
    expect(screen.getByText('视频文件')).toBeInTheDocument()
    expect(screen.getAllByText('NFO').length).toBeGreaterThan(0)
    expect(screen.getByText('Poster')).toBeInTheDocument()
    expect(screen.getByText('Fanart')).toBeInTheDocument()
    expect(screen.getByText('Clearlogo')).toBeInTheDocument()
    expect(screen.getByText('写入状态')).toBeInTheDocument()
    expect(screen.getByText('告警')).toBeInTheDocument()
    expect(screen.getAllByText('/data/library/movies/天气之子 (2019)').length).toBeGreaterThan(0)
    expect(screen.getAllByText('/data/library/movies/天气之子 (2019)/天气之子 (2019).mkv').length).toBeGreaterThan(0)
    expect(screen.getAllByText('/data/library/movies/天气之子 (2019)/天气之子 (2019).nfo').length).toBeGreaterThan(0)
    expect(screen.getAllByText('/data/library/movies/天气之子 (2019)/天气之子 (2019)-poster.jpg').length).toBeGreaterThan(0)
    expect(screen.getAllByText('/data/library/movies/天气之子 (2019)/天气之子 (2019)-fanart.jpg').length).toBeGreaterThan(0)
    expect(screen.getAllByText('/data/library/movies/天气之子 (2019)/天气之子 (2019)-clearlogo.png').length).toBeGreaterThan(0)
    expect(screen.getAllByText('成功').length).toBeGreaterThan(0)
    expect(screen.getAllByText('无').length).toBeGreaterThan(0)
  })

  it('renders the file assets section with localized roles and readable sizes', async () => {
    renderTaskDetailPage('task-completed')

    expect(await screen.findByRole('heading', { level: 2, name: '文件资产' })).toBeInTheDocument()
    expect(screen.getByText('最终影片')).toBeInTheDocument()
    expect(screen.getAllByText('NFO').length).toBeGreaterThan(0)
    expect(screen.getByText('海报')).toBeInTheDocument()
    expect(screen.getByText('背景图')).toBeInTheDocument()
    expect(screen.getByText('标题 Logo')).toBeInTheDocument()
    expect(screen.getAllByText('48.4 GB').length).toBeGreaterThan(0)
    expect(screen.getByText('2.0 KB')).toBeInTheDocument()
    expect(screen.getByText('500.0 KB')).toBeInTheDocument()
    expect(screen.getByText('2.0 MB')).toBeInTheDocument()
    expect(screen.getByText('100.0 KB')).toBeInTheDocument()
    expect(screen.getAllByText('/data/library/movies/天气之子 (2019)/天气之子 (2019).mkv').length).toBeGreaterThan(0)
    expect(screen.getAllByText('/data/library/movies/天气之子 (2019)/天气之子 (2019)-poster.jpg').length).toBeGreaterThan(0)
  })

  it('renders the publish information section for a completed task', async () => {
    renderTaskDetailPage('task-completed')

    expect(await screen.findByRole('heading', { level: 2, name: '发布信息' })).toBeInTheDocument()
    expect(screen.getByText('复制方式')).toBeInTheDocument()
    expect(screen.getByText('复制')).toBeInTheDocument()
    expect(screen.getByText('复制耗时')).toBeInTheDocument()
    expect(screen.getByText('18.2 秒')).toBeInTheDocument()
    expect(screen.getByText('复制字节数')).toBeInTheDocument()
    expect(screen.getAllByText('48.4 GB').length).toBeGreaterThan(0)
    expect(screen.getByText('当前发布阶段')).toBeInTheDocument()
    expect(screen.getAllByText('媒体入库完成').length).toBeGreaterThan(0)
    expect(screen.getByText('复制目标')).toBeInTheDocument()
    expect(
      screen.getAllByText(
        '/data/library/movies/.media-pilot-staging/task-completed/天气之子 (2019)/天气之子 (2019).mkv',
      ).length,
    ).toBeGreaterThan(0)
    expect(screen.getByText('最终发布目录')).toBeInTheDocument()
    expect(screen.getAllByText('/data/library/movies/天气之子 (2019)').length).toBeGreaterThan(0)
  })

  it('renders the event timeline for completed task events', async () => {
    renderTaskDetailPage('task-completed')

    expect(await screen.findByRole('heading', { level: 2, name: '事件时间线' })).toBeInTheDocument()
    expect(screen.getByText('提交下载')).toBeInTheDocument()
    expect(screen.getByText('下载完成')).toBeInTheDocument()
    expect(screen.getByText('已确认候选')).toBeInTheDocument()
    expect(screen.getAllByText('已完成媒体入库').length).toBeGreaterThan(0)
    expect(screen.getByText('2026-05-08T09:50:00+08:00')).toBeInTheDocument()
    expect(screen.getByText('2026-05-08T10:00:00+08:00')).toBeInTheDocument()
    expect(screen.getByText('2026-05-08T10:03:30+08:00')).toBeInTheDocument()
    expect(screen.getByText('2026-05-08T10:06:30+08:00')).toBeInTheDocument()
    expect(screen.getAllByText('成功').length).toBeGreaterThan(0)
  })

  it('renders the revoke publish section for a completed task', async () => {
    renderTaskDetailPage('task-completed')

    expect(await screen.findByRole('heading', { level: 2, name: '撤销发布' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '撤销发布' })).toBeInTheDocument()
  })

  it('shows revoke publish info-only message for non-completed tasks', async () => {
    renderTaskDetailPage('task-failed-rollback')

    expect(await screen.findByText('仅已完成入库的任务支持撤销发布。')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '撤销发布' })).not.toBeInTheDocument()
  })
})

// 注: 旧 "TaskDetailPage legacy confirmation read-only" describe 块已删除。
// ConfirmationSection 及其 confirmCandidate 服务方法随 ConfirmationRequest 一起下线；
// 候选选择改走右侧 Agent 面板决策 (DecisionReplyCard) 或 manualSelect 快捷发布。

describe('TaskDetailPage Agent Panel', () => {
  it('shows Agent empty state when task has no agent run', async () => {
    renderTaskDetailPage('task-completed-no-images')

    expect(await screen.findByText('Agent 尚未参与此任务')).toBeInTheDocument()
    expect(screen.getByText('该任务还没有 Agent 活动记录。')).toBeInTheDocument()
  })

  it('does not show generic free-text chat input when no agent run', async () => {
    renderTaskDetailPage('task-completed-no-images')

    await screen.findByText('Agent 尚未参与此任务')
    // no textarea/input for free-form chat
    expect(screen.queryByPlaceholderText('输入补充说明...')).not.toBeInTheDocument()
  })

  it('does not show start-agent button', async () => {
    renderTaskDetailPage('task-multiple-candidates')

    await screen.findByText('Agent 对话')
    expect(screen.queryByRole('button', { name: /启动/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /开始/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /start/i })).not.toBeInTheDocument()
  })

  it('shows Agent messages for a task with agent run', async () => {
    renderTaskDetailPage('task-multiple-candidates')

    expect(await screen.findByText('请分析任务并确认元数据')).toBeInTheDocument()
    expect(screen.getByText('我已检索到以下候选元数据，请确认：')).toBeInTheDocument()
  })

  it('filters out system messages from the display', async () => {
    renderTaskDetailPage('task-multiple-candidates')

    // wait for messages to load
    expect(await screen.findByText('请分析任务并确认元数据')).toBeInTheDocument()
    // verify user/assistant role labels are shown (no system)
    expect(screen.getByText('用户')).toBeInTheDocument()
    expect(screen.getAllByText('Agent').length).toBeGreaterThan(0)
  })

  it('shows tool call block for assistant messages with tool_calls', async () => {
    renderTaskDetailPage('task-multiple-candidates')

    const toolCallLabels = await screen.findAllByText('调用工具')
    expect(toolCallLabels.length).toBeGreaterThanOrEqual(2)
    const toolNames = screen.getAllByText(/search_metadata|搜索元数据/)
    expect(toolNames.length).toBeGreaterThanOrEqual(1)
  })

  it('can expand tool call details', async () => {
    renderTaskDetailPage('task-multiple-candidates')

    const toolCallLabels2 = await screen.findAllByText('调用工具')
    const expandButton = toolCallLabels2[0].closest('button')
    expect(expandButton).toBeInTheDocument()

    fireEvent.click(expandButton!)

    // expanded details should show input/output/duration for the first matching tool
    expect(await screen.findByText('输入')).toBeInTheDocument()
    expect(screen.getByText('输出')).toBeInTheDocument()
    // duration label + value
    expect(screen.getByText(/耗时/)).toBeInTheDocument()
  })

  it('correctly matches same-name tool_calls to distinct details', async () => {
    renderTaskDetailPage('task-multiple-candidates')

    // find all tool call expand buttons (each shows "调用工具" prefix)
    const toolCallButtons = await screen.findAllByText('调用工具')
    // msg-3 has 2 tool_calls (both search_metadata), so we should have at least 2
    expect(toolCallButtons.length).toBeGreaterThanOrEqual(2)

    // expand the first tool call
    fireEvent.click(toolCallButtons[0].closest('button')!)
    expect(await screen.findByText('输入')).toBeInTheDocument()
    // verify first tool call shows keyword "天气之子" in its input
    expect(screen.getByText(/"keyword": "天气之子"/)).toBeInTheDocument()

    // collapse first
    fireEvent.click(toolCallButtons[0].closest('button')!)

    // expand second tool call
    fireEvent.click(toolCallButtons[1].closest('button')!)
    expect(await screen.findByText('输入')).toBeInTheDocument()
    // "天気の子" uniquely identifies tc-4 — proof of correct match
    expect(screen.getByText(/"keyword": "天気の子"/)).toBeInTheDocument()

    // collapse second
    fireEvent.click(toolCallButtons[1].closest('button')!)
  })

  it('shows pending decision in the bottom area', async () => {
    renderTaskDetailPage('task-multiple-candidates')

    expect(await screen.findByText('待决策')).toBeInTheDocument()
    expect(screen.getByText('请选择要使用的元数据候选')).toBeInTheDocument()
  })

  it('renders decision options as clickable buttons', async () => {
    renderTaskDetailPage('task-multiple-candidates')

    expect(await screen.findByText('天气之子 (2019) - 置信度 96%')).toBeInTheDocument()
    expect(screen.getByText('《天气之子》制作纪录片 (2020)')).toBeInTheDocument()
  })

  it('shows free-text input when free_text_allowed is true', async () => {
    renderTaskDetailPage('task-multiple-candidates')

    expect(await screen.findByPlaceholderText('输入补充说明...')).toBeInTheDocument()
  })

  it('disables reply button until an option is selected', async () => {
    renderTaskDetailPage('task-multiple-candidates')

    await screen.findByText('待决策')
    const replyButton = screen.getByRole('button', { name: /回复/ })
    expect(replyButton).toBeDisabled()
  })

  it('enables reply after selecting an option', async () => {
    renderTaskDetailPage('task-multiple-candidates')

    const option = await screen.findByText('天气之子 (2019) - 置信度 96%')
    fireEvent.click(option)

    const replyButton = screen.getByRole('button', { name: /回复/ })
    expect(replyButton).not.toBeDisabled()
  })

  it('shows ack state after replying (polish-agent-panel-scroll-and-reply-ack)', async () => {
    renderTaskDetailPage('task-multiple-candidates')

    const option = await screen.findByText('天气之子 (2019) - 置信度 96%')
    fireEvent.click(option)

    const replyButton = screen.getByRole('button', { name: /回复/ })
    fireEvent.click(replyButton)

    // 新行为: 提交后立即进入 ack 状态, 选项 / 文本框 / 按钮不再渲染;
    // 显示 "已提交, Agent 正在继续处理" (agent.replySubmitted i18n key).
    expect(await screen.findByText('已提交, Agent 正在继续处理')).toBeInTheDocument()
    // 提交按钮消失
    expect(screen.queryByRole('button', { name: /回复/ })).toBeNull()
  })

  it("clears freeText when an option is selected", async () => {
    renderTaskDetailPage("task-multiple-candidates")

    const textarea = await screen.findByPlaceholderText("输入补充说明...")
    fireEvent.change(textarea, { target: { value: "my custom reply" } })
    expect(textarea).toHaveValue("my custom reply")

    const option = screen.getByText("天气之子 (2019) - 置信度 96%")
    fireEvent.click(option)
    expect(textarea).toHaveValue("")
  })

  it("clears selectedOption when freeText is entered", async () => {
    renderTaskDetailPage("task-multiple-candidates")

    const option = await screen.findByText("天气之子 (2019) - 置信度 96%")
    fireEvent.click(option)

    const selectedButton = option.closest("button")
    expect(selectedButton?.className).toContain("bg-primary/10")

    const textarea = screen.getByPlaceholderText("输入补充说明...")
    fireEvent.change(textarea, { target: { value: "custom" } })

    expect(selectedButton?.className).not.toContain("bg-primary/10")
  })

  it('shows no pending decision area when agent has no run', async () => {
    renderTaskDetailPage('task-completed-no-images')

    // empty state shown instead of decision area
    expect(await screen.findByText('Agent 尚未参与此任务')).toBeInTheDocument()
    // no pending decision label
    expect(screen.queryByText('待决策')).not.toBeInTheDocument()
  })

  it('renders the two-pane workspace layout', async () => {
    renderTaskDetailPage('task-completed')

    // left pane: task facts should be visible
    expect(await screen.findByRole('heading', { level: 2, name: '基础信息' })).toBeInTheDocument()
    // right pane: Agent panel should be visible (even as empty state)
    expect(screen.getByText('Agent 对话')).toBeInTheDocument()
  })

  it('shows waiting_user status in the agent panel header', async () => {
    renderTaskDetailPage('task-multiple-candidates')

    expect(await screen.findByText('等待确认')).toBeInTheDocument()
  })

  it('shows completed status in the agent panel header', async () => {
    renderTaskDetailPage('task-completed')

    expect(await screen.findByText('已分析')).toBeInTheDocument()
  })

  it('does not show generic free-text chat input anywhere', async () => {
    renderTaskDetailPage('task-multiple-candidates')

    await screen.findByText('Agent 对话')
    // 等待 DecisionReplyCard 的 free_text textarea 出现（free_text_allowed=true）
    await screen.findByPlaceholderText('输入补充说明...')
    const textareas = screen.getAllByRole('textbox')
    const chatInput = textareas.find(el => el.getAttribute('placeholder')?.includes('发送') || el.getAttribute('placeholder')?.includes('chat') || el.getAttribute('placeholder')?.includes('message'))
    expect(chatInput).toBeUndefined()
  })

  it('uses the injected service for AgentPanel', async () => {
    const base = createMockTaskService()
    let listAgentMessagesCalled = false
    const customService: TaskDetailService = {
      ...base,
      listAgentMessages: async (taskId: string) => {
        listAgentMessagesCalled = true
        return base.listAgentMessages(taskId)
      },
    }
    renderTaskDetailPage('task-multiple-candidates', customService)

    await screen.findByText('请分析任务并确认元数据')
    expect(listAgentMessagesCalled).toBe(true)
  })

  it('shows retry button when agent run has failed', async () => {
    renderTaskDetailPage('task-failed-rollback')

    // Wait for messages to load so status badge and retry button appear
    expect(await screen.findByText('失败')).toBeInTheDocument()
    const retryButton = await screen.findByRole('button', { name: /重试/ })
    expect(retryButton).toBeInTheDocument()
  })

  it('does not show retry button when agent is active', async () => {
    renderTaskDetailPage('task-multiple-candidates')

    // Wait for messages to load so status badges appear
    expect(await screen.findByText('等待确认')).toBeInTheDocument()
    expect(screen.queryByText('重试')).not.toBeInTheDocument()
  })

  it('does not show retry button when agent completed', async () => {
    renderTaskDetailPage('task-completed')

    // Wait for messages to load so status badges appear
    expect(await screen.findByText('已分析')).toBeInTheDocument()
    expect(screen.queryByText('重试')).not.toBeInTheDocument()
  })

  it('does not show retry button when task has no agent run', async () => {
    renderTaskDetailPage('task-completed-no-images')

    await screen.findByText('Agent 尚未参与此任务')
    expect(screen.queryByText('重试')).not.toBeInTheDocument()
  })

  it('calls createAgentRun when retry button is clicked', async () => {
    const base = createMockTaskService()
    let createAgentRunCalled = false
    const customService: TaskDetailService = {
      ...base,
      createAgentRun: async (taskId: string) => {
        createAgentRunCalled = true
        return base.createAgentRun(taskId)
      },
    }
    renderTaskDetailPage('task-failed-rollback', customService)

    // Wait for messages to load so retry button appears
    await screen.findByText('失败')
    const retryButton = await screen.findByRole('button', { name: /重试/ })
    fireEvent.click(retryButton)

    await waitFor(() => {
      expect(createAgentRunCalled).toBe(true)
    })
  })
})

// ---------------------------------------------------------------------------
// AgentPanel recover-stuck button (与 retry 严格区分, 独立 mutation)
// ---------------------------------------------------------------------------

describe('TaskDetailPage AgentPanel recover-stuck button', () => {
  afterEach(() => {
    i18n.changeLanguage('zh')
  })

  it('shows recover-stuck button when run_status=active + no pending decision (task-processing)', async () => {
    // task-processing: run_status='active', pending_decision_count=0,
    // status='processing'. 满足"卡住 Agent"展示条件.
    renderTaskDetailPage('task-processing')

    // 等待 AgentPanel 渲染 (用 recover 按钮作为锚点, '处理中' 文本在
    // status badge 和 agent panel header 都会出现, 不可靠).
    const recoverButton = await screen.findByRole('button', { name: '恢复处理' })
    expect(recoverButton).toBeInTheDocument()
    // 关键: 与 retry 互斥. 同一时刻只有 recover, 不能两个都出现.
    expect(screen.queryByRole('button', { name: /重试 Agent/ })).not.toBeInTheDocument()
  })

  it('hides recover-stuck button when waiting_user + pending decision (task-multiple-candidates)', async () => {
    renderTaskDetailPage('task-multiple-candidates')

    // 等待 AgentPanel 渲染 (waiting_user)
    expect(await screen.findByText('等待确认')).toBeInTheDocument()
    // 关键: 有 pending decision 时必须隐藏恢复按钮 — 防止绕过用户决策.
    expect(screen.queryByRole('button', { name: '恢复处理' })).not.toBeInTheDocument()
  })

  it('hides recover-stuck button when run_status=failed (task-failed-rollback)', async () => {
    // 失败任务应该走 retry, 绝不显示 recover (语义不同).
    renderTaskDetailPage('task-failed-rollback')

    // retry 按钮可能在 BaseInfoSection 出现 '失败' 文字后才渲染, 用 findByRole 等待
    const retryButton = await screen.findByRole('button', { name: /重试/ })
    expect(retryButton).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '恢复处理' })).not.toBeInTheDocument()
  })

  it('hides recover-stuck button when run_status=completed (task-completed)', async () => {
    renderTaskDetailPage('task-completed')

    expect(await screen.findByText('已分析')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '恢复处理' })).not.toBeInTheDocument()
  })

  it('hides recover-stuck button when task has no agent run (task-completed-no-images)', async () => {
    renderTaskDetailPage('task-completed-no-images')

    expect(await screen.findByText('Agent 尚未参与此任务')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '恢复处理' })).not.toBeInTheDocument()
  })

  it('calls recoverStuckAgentRun (NOT createAgentRun) when recover button is clicked', async () => {
    const base = createMockTaskService()
    let recoverStuckAgentRunCalled = false
    let createAgentRunCalled = false
    const customService: TaskDetailService = {
      ...base,
      recoverStuckAgentRun: async (taskId: string) => {
        recoverStuckAgentRunCalled = true
        return base.recoverStuckAgentRun!(taskId)
      },
      createAgentRun: async (taskId: string) => {
        createAgentRunCalled = true
        return base.createAgentRun(taskId)
      },
    }
    renderTaskDetailPage('task-processing', customService)

    // 等待 recover 按钮出现 (active + no pending)
    const recoverButton = await screen.findByRole('button', { name: '恢复处理' })
    fireEvent.click(recoverButton)

    await waitFor(() => {
      expect(recoverStuckAgentRunCalled).toBe(true)
    })
    // 关键: 不能 fallback 到 createAgentRun (普通重试). 这是关键契约
    // 区分点 — 失败重试和卡住恢复必须走不同 mutation.
    expect(createAgentRunCalled).toBe(false)
  })

  it('shows error message (恢复失败) when recoverStuckAgentRun fails', async () => {
    const base = createMockTaskService()
    const customService: TaskDetailService = {
      ...base,
      recoverStuckAgentRun: async () => {
        throw new Error('任务不在卡住状态, 无法恢复')
      },
    }
    renderTaskDetailPage('task-processing', customService)

    const recoverButton = await screen.findByRole('button', { name: '恢复处理' })
    fireEvent.click(recoverButton)

    // 错误反馈: 组件内错误条 (供用户在面板内看到)
    expect(await screen.findByText('任务不在卡住状态, 无法恢复')).toBeInTheDocument()
    // 关键: 失败后按钮恢复可点击 (不卡在 loading)
    expect(screen.getByRole('button', { name: '恢复处理' })).not.toBeDisabled()
  })
})

describe('TaskDetailPage i18n', () => {
  afterEach(() => {
    i18n.changeLanguage('zh')
  })

  it('shows English section titles when language is switched to English', async () => {
    i18n.changeLanguage('en')
    renderTaskDetailPage('task-completed')

    expect(await screen.findByRole('heading', { level: 2, name: 'Basic Info' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'Write Result' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'Publish Info' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'File Assets' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'Event Timeline' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'Revoke Publish' })).toBeInTheDocument()
  })

  it('shows English status label when language is switched to English', async () => {
    i18n.changeLanguage('en')
    renderTaskDetailPage('task-completed')

    await waitFor(() => {
      const completedTexts = screen.getAllByText('Completed')
      expect(completedTexts.length).toBeGreaterThan(0)
    })
  })

  it('shows English media type label in candidate card when language is switched', async () => {
    i18n.changeLanguage('en')
    renderTaskDetailPage('task-multiple-candidates')

    const movieTexts = await screen.findAllByText('Movie')
    expect(movieTexts.length).toBeGreaterThan(0)
  })

  it('shows duration with English unit "s" instead of Chinese "秒"', async () => {
    i18n.changeLanguage('en')
    renderTaskDetailPage('task-completed')

    expect(await screen.findByRole('heading', { level: 2, name: 'Publish Info' })).toBeInTheDocument()
    expect(screen.getByText('18.2 s')).toBeInTheDocument()
    expect(screen.queryByText(/秒/)).not.toBeInTheDocument()
  })

  it('shows English page title when language is switched to English', async () => {
    i18n.changeLanguage('en')
    renderTaskDetailPage('task-completed')

    expect(await screen.findByRole('heading', { level: 1, name: 'Task Detail' })).toBeInTheDocument()
    expect(screen.getByText('Task ID: task-completed')).toBeInTheDocument()
  })
})

describe('TaskDetailPage back-to-tasks button', () => {
  afterEach(() => {
    i18n.changeLanguage('zh')
  })

  it('shows the back-to-tasks button in the loaded state', async () => {
    renderTaskDetailPage('task-completed')

    // 等待 loaded 状态
    expect(await screen.findByRole('heading', { level: 2, name: '基础信息' })).toBeInTheDocument()
    const backButton = screen.getByRole('link', { name: '返回任务列表' })
    expect(backButton).toBeInTheDocument()
    expect(backButton).toHaveAttribute('href', '/tasks')
  })

  it('shows the back-to-tasks button in the error state (task not found)', async () => {
    // 不存在的 taskId → useQuery 失败 → 进入 error 分支
    renderTaskDetailPage('task-not-found-xyz')

    expect(await screen.findByText('任务详情加载失败')).toBeInTheDocument()
    const backButton = screen.getByRole('link', { name: '返回任务列表' })
    expect(backButton).toBeInTheDocument()
    expect(backButton).toHaveAttribute('href', '/tasks')
  })

  it('shows the back-to-tasks button in the loading state', async () => {
    // 注入一个永不 resolve 的 service, 让 query 永远 pending → loading 分支
    const baseService = createMockTaskService()
    const neverResolveService: TaskDetailService = {
      ...baseService,
      getTaskDetail: () => new Promise(() => {}) as ReturnType<TaskDetailService['getTaskDetail']>,
    }
    renderTaskDetailPage('task-completed', neverResolveService)

    // loading 状态显示 SkeletonBlock, 但 back button 仍应在最上层
    // wait for at least one skeleton to appear
    await waitFor(() => {
      expect(document.querySelectorAll('[class*="animate-pulse"]').length).toBeGreaterThan(0)
    })
    const backButton = screen.getByRole('link', { name: '返回任务列表' })
    expect(backButton).toBeInTheDocument()
    expect(backButton).toHaveAttribute('href', '/tasks')
  })

  it('navigates to /tasks when the back button is clicked in the loaded state', async () => {
    // 注意: MemoryRouter 的 initialEntries 是 /tasks/:taskId, 但 Routes 只
    // 注册了 /tasks/:taskId, 没有 /tasks 路由. 这里我们验证 link 的 href
    // 而非真实 navigate 行为 (后者需要 /tasks 路由, 已超出本测试范围).
    renderTaskDetailPage('task-completed')

    expect(await screen.findByRole('heading', { level: 2, name: '基础信息' })).toBeInTheDocument()
    const backButton = screen.getByRole('link', { name: '返回任务列表' })
    expect(backButton).toHaveAttribute('href', '/tasks')
  })

  it('uses English i18n label when language is English', async () => {
    i18n.changeLanguage('en')
    renderTaskDetailPage('task-completed')

    expect(await screen.findByRole('heading', { level: 2, name: 'Basic Info' })).toBeInTheDocument()
    const backButton = screen.getByRole('link', { name: 'Back to tasks' })
    expect(backButton).toBeInTheDocument()
    expect(backButton).toHaveAttribute('href', '/tasks')
  })
})
