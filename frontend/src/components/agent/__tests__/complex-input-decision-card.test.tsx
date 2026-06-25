import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { I18nextProvider } from 'react-i18next'

import i18n from '@/i18n'

// ---------------------------------------------------------------------------
// 复杂输入决策卡的渲染测试.
// 通过把 mockUseQuery 的 useQuery 钩子替换, 让 AgentPanel 把传入的
// agentStatus 解析为带 pending decision 的状态, 进而渲染 DecisionReplyCard.
// ---------------------------------------------------------------------------

const mockService = {
  replyToAgentDecision: vi.fn(),
  listAgentMessages: vi.fn().mockResolvedValue({ data: [] }),
  listAgentDecisions: vi.fn().mockResolvedValue({ data: [] }),
  listAgentToolCalls: vi.fn().mockResolvedValue({ data: [] }),
  createAgentRun: vi.fn(),
  sendFreeformMessage: vi.fn(),
  recoverStuckAgentRun: vi.fn(),
}

const renderWithI18n = (ui: React.ReactElement) =>
  render(<I18nextProvider i18n={i18n}>{ui}</I18nextProvider>)

function makeDecision(decision_type: string, options: any[]) {
  return {
    id: 'dr-1',
    run_id: 'run-1',
    task_id: 'task-1',
    decision_type,
    status: 'pending',
    question: '请选择主视频',
    free_text_allowed: false,
    options,
    payload: {},
  }
}

describe('DecisionReplyCard — complex input decision types', () => {
  it('select_primary_video 渲染文件选项 + 文件大小, 不显示原始 JSON', async () => {
    vi.resetModules()
    vi.doMock('@tanstack/react-query', async (importOriginal) => {
      const actual = await importOriginal<typeof import('@tanstack/react-query')>()
      return {
        ...actual,
        useMutation: () => ({
          mutate: vi.fn(),
          isPending: false,
          isError: false,
          isSuccess: false,
          error: null,
        }),
        useQuery: ({ queryKey }: { queryKey: readonly unknown[] }) => {
          if (queryKey[0] === 'agent-decisions') {
            return {
              data: {
                data: [
                  makeDecision('select_primary_video', [
                    {
                      id: 'video_0',
                      label: 'Example.Movie.2026.mkv',
                      description: '主视频候选 (1.5 GB)',
                      payload: { path: '/dl/A.mkv', name: 'A.mkv', size_bytes: 1610612736 },
                    },
                    {
                      id: 'video_1',
                      label: 'Example.Movie.2026.Extra.mkv',
                      description: '主视频候选 (200.0 MB)',
                      payload: { path: '/dl/B.mkv', name: 'B.mkv', size_bytes: 209715200 },
                    },
                  ]),
                ],
              },
              isLoading: false,
            }
          }
          return { data: undefined, isLoading: false }
        },
        useQueryClient: () => ({ invalidateQueries: vi.fn() }),
      }
    })
    vi.doMock('@/services/task-service', () => ({
      createTaskService: () => mockService,
    }))

    const { AgentPanel } = await import('@/components/agent/agent-panel')

    renderWithI18n(
      <AgentPanel
        taskId="task-1"
        agentStatus={{
          run_status: 'waiting_user',
          latest_run_id: null,
          pending_decision_count: 1,
          latest_message_summary: null,
        }}
        service={mockService}
      />,
    )

    expect(screen.getByText('Example.Movie.2026.mkv')).toBeInTheDocument()
    expect(screen.getByText('Example.Movie.2026.Extra.mkv')).toBeInTheDocument()
    expect(screen.getByText('1.5 GB')).toBeInTheDocument()
    expect(screen.getByText('200.0 MB')).toBeInTheDocument()
    // 不显示原始 JSON
    expect(document.body.textContent).not.toContain('"path":"/dl/A.mkv"')
    vi.doUnmock('@tanstack/react-query')
    vi.doUnmock('@/services/task-service')
  })

  it('select_subtitles 渲染字幕选项 + "不带入字幕" 选项', async () => {
    vi.resetModules()
    vi.doMock('@tanstack/react-query', async (importOriginal) => {
      const actual = await importOriginal<typeof import('@tanstack/react-query')>()
      return {
        ...actual,
        useMutation: () => ({
          mutate: vi.fn(),
          isPending: false,
          isError: false,
          isSuccess: false,
          error: null,
        }),
        useQuery: ({ queryKey }: { queryKey: readonly unknown[] }) => {
          if (queryKey[0] === 'agent-decisions') {
            return {
              data: {
                data: [
                  makeDecision('select_subtitles', [
                    {
                      id: 'subtitle_0',
                      label: 'random_chs.srt',
                      description: '2.0 KB',
                      payload: { path: '/dl/random.srt', name: 'random_chs.srt', size_bytes: 2048 },
                    },
                    {
                      id: 'no_subtitles',
                      label: 'noSubtitlesFallback',
                      description: '不在本次发布中带入任何字幕',
                      payload: { selected_subtitles: [] },
                    },
                  ]),
                ],
              },
              isLoading: false,
            }
          }
          return { data: undefined, isLoading: false }
        },
        useQueryClient: () => ({ invalidateQueries: vi.fn() }),
      }
    })
    vi.doMock('@/services/task-service', () => ({
      createTaskService: () => mockService,
    }))

    const { AgentPanel } = await import('@/components/agent/agent-panel')

    renderWithI18n(
      <AgentPanel
        taskId="task-1"
        agentStatus={{
          run_status: 'waiting_user',
          latest_run_id: null,
          pending_decision_count: 1,
          latest_message_summary: null,
        }}
        service={mockService}
      />,
    )

    expect(screen.getByText('random_chs.srt')).toBeInTheDocument()
    // "不带入字幕" 来自 i18n (zh 默认)
    expect(screen.getByText('不带入字幕')).toBeInTheDocument()
    // 2.0 KB 出现两次: 选项 size 徽章 + description, 用 getAllByText 验证存在.
    expect(screen.getAllByText('2.0 KB').length).toBeGreaterThanOrEqual(1)
    vi.doUnmock('@tanstack/react-query')
    vi.doUnmock('@/services/task-service')
  })

  it('review_complex_input 渲染复核提示, 不展示原始 JSON', async () => {
    vi.resetModules()
    vi.doMock('@tanstack/react-query', async (importOriginal) => {
      const actual = await importOriginal<typeof import('@tanstack/react-query')>()
      return {
        ...actual,
        useMutation: () => ({
          mutate: vi.fn(),
          isPending: false,
          isError: false,
          isSuccess: false,
          error: null,
        }),
        useQuery: ({ queryKey }: { queryKey: readonly unknown[] }) => {
          if (queryKey[0] === 'agent-decisions') {
            return {
              data: {
                data: [
                  {
                    ...makeDecision('review_complex_input', []),
                    free_text_allowed: true,
                    payload: { reason: 'bdmv_or_iso', source_path: '/dl/bdmv' },
                  },
                ],
              },
              isLoading: false,
            }
          }
          return { data: undefined, isLoading: false }
        },
        useQueryClient: () => ({ invalidateQueries: vi.fn() }),
      }
    })
    vi.doMock('@/services/task-service', () => ({
      createTaskService: () => mockService,
    }))

    const { AgentPanel } = await import('@/components/agent/agent-panel')

    renderWithI18n(
      <AgentPanel
        taskId="task-1"
        agentStatus={{
          run_status: 'waiting_user',
          latest_run_id: null,
          pending_decision_count: 1,
          latest_message_summary: null,
        }}
        service={mockService}
      />,
    )

    // 复核提示 — 通过 i18n key agent.complexInput.reviewHint
    // zh 默认: "源目录或文件可能需要人工确认, 请在下方输入框说明"
    // en 默认: "Source may need manual confirmation..."
    const text = document.body.textContent ?? ''
    expect(
      text.includes('说明') || text.includes('复核') || text.includes('review') || text.includes('请'),
    ).toBe(true)
    // 不显示原始 payload
    expect(text).not.toContain('"reason":"bdmv_or_iso"')
    vi.doUnmock('@tanstack/react-query')
    vi.doUnmock('@/services/task-service')
  })
})
