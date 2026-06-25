import { describe, expect, it, vi, afterEach } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { I18nextProvider } from 'react-i18next'

import i18n from '@/i18n'

// ---------------------------------------------------------------------------
// 通用 select_metadata_candidate 决策卡的渲染测试.
// 验证:
// - 候选的 title / year / confidence / media_type / provider / overview 显示.
// - 不展示原始 JSON payload (candidate_id 等稳定引用由后端消费).
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

// vi.resetModules() + vi.doMock() 模式下, 测试间 DOM 清理有时被
// 漏掉; 显式 cleanup 避免上个用例的 `decision-options-scroll`
// 残留到下个用例让 getByTestId 命中多个元素.
afterEach(() => {
  cleanup()
})

function makeDecision(decision_type: string, options: any[]) {
  return {
    id: 'dr-1',
    run_id: 'run-1',
    task_id: 'task-1',
    decision_type,
    status: 'pending',
    question: '请选择正确的元数据候选。',
    free_text_allowed: false,
    options,
    payload: {},
  }
}

describe('DecisionReplyCard — select_metadata_candidate decision type', () => {
  it('movie 候选展示 title / year / confidence / source, 不显示原始 JSON', async () => {
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
                  makeDecision('select_metadata_candidate', [
                    {
                      id: 'candidate_aaaa',
                      label: 'Example Movie',
                      description: 'movie · confidence=0.70 · source=tmdb',
                      payload: {
                        candidate_id: 'aaaa',
                        provider: 'tmdb',
                        provider_id: 'tmdb:1',
                        media_type: 'movie',
                        title: 'Example Movie',
                        year: 2026,
                        confidence: 0.7,
                        overview: 'A 2026 film.',
                      },
                    },
                    {
                      id: 'candidate_bbbb',
                      label: 'Example Movie 2',
                      description: 'movie · confidence=0.65 · source=tmdb',
                      payload: {
                        candidate_id: 'bbbb',
                        provider: 'tmdb',
                        provider_id: 'tmdb:2',
                        media_type: 'movie',
                        title: 'Example Movie 2',
                        year: 2026,
                        confidence: 0.65,
                        overview: 'Another film.',
                      },
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

    // 标题 (label) 显示
    expect(screen.getByText('Example Movie')).toBeInTheDocument()
    expect(screen.getByText('Example Movie 2')).toBeInTheDocument()
    // 置信度显示 (i18n 当前为 zh, 显示 "置信度 0.70")
    expect(screen.getByText('置信度 0.70')).toBeInTheDocument()
    expect(screen.getByText('置信度 0.65')).toBeInTheDocument()
    // overview 摘要显示
    expect(screen.getByText('A 2026 film.')).toBeInTheDocument()
    expect(screen.getByText('Another film.')).toBeInTheDocument()
    // 不显示原始 JSON 字段
    expect(document.body.textContent).not.toContain('"candidate_id"')
    expect(document.body.textContent).not.toContain('"provider_id"')
    expect(document.body.textContent).not.toContain('"tmdb:1"')
  })

  it('show 候选同样走通用候选卡, 不需要单独的 card 组件', async () => {
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
                  makeDecision('select_metadata_candidate', [
                    {
                      id: 'candidate_cccc',
                      label: 'Example Show',
                      description: 'show · confidence=0.60',
                      payload: {
                        candidate_id: 'cccc',
                        provider: 'tmdb',
                        provider_id: 'tmdb:show-1',
                        media_type: 'show',
                        title: 'Example Show',
                        year: 2024,
                        confidence: 0.6,
                        overview: 'A 2024 show.',
                      },
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
        taskId="task-2"
        agentStatus={{
          run_status: 'waiting_user',
          latest_run_id: null,
          pending_decision_count: 1,
          latest_message_summary: null,
        }}
        service={mockService}
      />,
    )

    // 标题和置信度都展示 (i18n 当前为 zh)
    expect(screen.getByText('Example Show')).toBeInTheDocument()
    expect(screen.getByText('置信度 0.60')).toBeInTheDocument()
    // overview 展示
    expect(screen.getByText('A 2024 show.')).toBeInTheDocument()
    // 类型标签包含 show (类型 show)
    expect(screen.getByText('类型 show')).toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// MP-Lab-02-Matrix-1999-Dominant 现场: 候选很多时 (e.g. Titanic 拉 20
// 条候选), 整张决策卡不能撑破右侧聊天面板. 候选容器自身滚动, 标题 /
// 提交按钮 / ack 状态留在滚动区外不被吞.
// ---------------------------------------------------------------------------

describe('DecisionReplyCard — 候选过多时滚动隔离', () => {
  it('多个候选渲染时, 候选容器具备 max-height + overflow-y-auto', async () => {
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
                  makeDecision('select_metadata_candidate', Array.from({ length: 10 }, (_, i) => ({
                    id: `candidate_${i}`,
                    label: `Movie ${i}`,
                    description: `movie · confidence=${(0.9 - i * 0.05).toFixed(2)}`,
                    payload: {
                      candidate_id: `cand-${i}`,
                      provider: 'tmdb',
                      provider_id: `tmdb:${i}`,
                      media_type: 'movie',
                      title: `Movie ${i}`,
                      year: 2026,
                      confidence: 0.9 - i * 0.05,
                      overview: `Overview for movie ${i}.`,
                    },
                  }))),
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
        taskId="task-scroll"
        agentStatus={{
          run_status: 'waiting_user',
          latest_run_id: null,
          pending_decision_count: 1,
          latest_message_summary: null,
        }}
        service={mockService}
      />,
    )

    const scrollContainer = screen.getByTestId('decision-options-scroll')
    expect(scrollContainer).toBeInTheDocument()
    // 候选列表自身滚动, 不让整个 AgentPanel 被候选无限撑高
    expect(scrollContainer.className).toMatch(/max-h-/)
    expect(scrollContainer.className).toMatch(/overflow-y-auto/)
  })

  it('提交按钮不被吞入滚动区, 候选很多时仍能显示', async () => {
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
                  makeDecision('select_metadata_candidate', Array.from({ length: 10 }, (_, i) => ({
                    id: `candidate_${i}`,
                    label: `Movie ${i}`,
                    description: `movie · confidence=${(0.9 - i * 0.05).toFixed(2)}`,
                    payload: {
                      candidate_id: `cand-${i}`,
                      provider: 'tmdb',
                      provider_id: `tmdb:${i}`,
                      media_type: 'movie',
                      title: `Movie ${i}`,
                      year: 2026,
                      confidence: 0.9 - i * 0.05,
                      overview: `Overview for movie ${i}.`,
                    },
                  }))),
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
        taskId="task-scroll-submit"
        agentStatus={{
          run_status: 'waiting_user',
          latest_run_id: null,
          pending_decision_count: 1,
          latest_message_summary: null,
        }}
        service={mockService}
      />,
    )

    const scrollContainer = screen.getByTestId('decision-options-scroll')
    // 提交按钮 i18n: zh="回复" / en="Reply"
    const submitButton = screen.getByRole('button', { name: /回复|Reply/i })
    expect(submitButton).toBeInTheDocument()
    // 关键: 提交按钮在滚动区外, 候选很多时仍可见可点
    expect(scrollContainer.contains(submitButton)).toBe(false)
  })
})
