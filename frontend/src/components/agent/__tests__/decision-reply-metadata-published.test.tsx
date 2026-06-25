// ---------------------------------------------------------------------------
// decision-reply-metadata-published.test.tsx
//
// 覆盖 fix-decision-reply-metadata-published-ui-sync 的新 capability:
//   - select-metadata-candidate-publish-sync
//
// 测试要求: 用真实 React Query (useMutation/useQueryClient) + 真实
// ToastProvider, 验证:
//   - replyToAgentDecision resolve data.status="metadata_published" 时:
//     · 4 个详情页 query (task-detail / agent-decisions / agent-messages
//       / agent-tool-calls) 走 refetchQueries (不是 invalidate)
//     · 列表 query (flows / tasks) 走 invalidateQueries
//     · useToast 弹 success toast "<title> 入库成功", level=success
//     · 不弹 error toast
//   - 其它 success status (target_conflict_overwritten / completed 等)
//     不弹入库成功 toast, 但仍 refetch
//   - title fallback: task 详情无 title 且 source_path 为空, toast 用
//     "任务 入库成功"
// ---------------------------------------------------------------------------

import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider, type QueryClient as QueryClientType } from '@tanstack/react-query'
import { I18nextProvider } from 'react-i18next'
import i18n from '@/i18n'

import { ToastProvider } from '@/components/shared/toast'
import { AgentPanel, type AgentService } from '@/components/agent/agent-panel'

// ---------------------------------------------------------------------------
// 滚动几何 polyfill (jsdom 没有 scrollHeight / scrollTop 默认 getter, 也
// 没有 Element.scrollTo).
// ---------------------------------------------------------------------------

function polyfillScrollMeasurement() {
  const defaults = { scrollHeight: 0, scrollTop: 0, clientHeight: 0 }
  Object.defineProperty(HTMLElement.prototype, 'scrollHeight', {
    configurable: true,
    get(this: HTMLElement) { return (this as HTMLElement & { __sh?: number }).__sh ?? defaults.scrollHeight },
    set(this: HTMLElement & { __sh?: number }, v: number) { this.__sh = v },
  })
  Object.defineProperty(HTMLElement.prototype, 'scrollTop', {
    configurable: true,
    get(this: HTMLElement) { return (this as HTMLElement & { __st?: number }).__st ?? defaults.scrollTop },
    set(this: HTMLElement & { __st?: number }, v: number) { this.__st = v },
  })
  Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
    configurable: true,
    get(this: HTMLElement) { return (this as HTMLElement & { __ch?: number }).__ch ?? defaults.clientHeight },
    set(this: HTMLElement & { __ch?: number }, v: number) { this.__ch = v },
  })
  HTMLElement.prototype.scrollTo = function (this: HTMLElement & { __st?: number }, arg: number | { top: number; behavior?: 'auto' | 'smooth' }) {
    const opts = typeof arg === 'number' ? { top: arg } : arg
    ;(this as HTMLElement & { __st?: number }).__st = opts.top
  } as HTMLElement['scrollTo']
}

const I18nWrap: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <I18nextProvider i18n={i18n}>{children}</I18nextProvider>
)

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
}

type AgentServiceMock = Pick<AgentService, 'listAgentMessages' | 'listAgentDecisions' | 'listAgentToolCalls' | 'replyToAgentDecision' | 'createAgentRun' | 'sendFreeformMessage' | 'recoverStuckAgentRun'>

function _envelope<T>(data: T) {
  return { status: 'success' as const, messages: [], meta: {}, data }
}

function makeServiceMock(overrides: Partial<AgentServiceMock> = {}): AgentService {
  return {
    listAgentMessages: vi.fn().mockResolvedValue(_envelope([])),
    listAgentDecisions: vi.fn().mockResolvedValue(_envelope([])),
    listAgentToolCalls: vi.fn().mockResolvedValue(_envelope([])),
    replyToAgentDecision: vi.fn().mockResolvedValue(_envelope({ run_id: 'r1', status: 'completed' })),
    createAgentRun: vi.fn().mockResolvedValue(_envelope({
      run_id: 'run-new', status: 'active', message_count: 0, tool_call_count: 0, error_message: null,
    })),
    sendFreeformMessage: vi.fn().mockResolvedValue(_envelope({
      run_id: 'run-freeform', status: 'completed', message_count: 0, tool_call_count: 0, error_message: null,
    })),
    recoverStuckAgentRun: vi.fn().mockResolvedValue(_envelope({
      run_id: 'run-recover', status: 'active',
    })),
    ...overrides,
  } as unknown as AgentService
}

function makePendingDecision(taskId: string) {
  return {
    id: 'dr-pending',
    run_id: 'run-pending',
    task_id: taskId,
    decision_type: 'select_metadata_candidate',
    status: 'pending',
    question: '请选择正确的元数据候选。',
    free_text_allowed: false,
    options: [
      {
        id: 'candidate_a',
        label: '天气之子 (2019)',
        description: 'movie · confidence=0.85 · source=tmdb',
        payload: {
          candidate_id: 'a',
          provider: 'tmdb',
          provider_id: 'tmdb:568160',
          media_type: 'movie',
          title: '天气之子',
          year: 2019,
          confidence: 0.85,
        },
      },
    ],
    payload: {},
  }
}

function renderPanel(
  service: AgentServiceMock,
  queryClient: QueryClientType,
  options: { taskId?: string; pendingDecision?: ReturnType<typeof makePendingDecision> | null } = {},
) {
  const { taskId = 'task-1', pendingDecision = makePendingDecision('task-1') } = options
  // 如果传 null 表示"无 pending decision"; 否则注入
  const decisionsMock = pendingDecision
    ? _envelope([pendingDecision])
    : _envelope([])

  service.listAgentDecisions = vi.fn().mockResolvedValue(decisionsMock)

  return render(
    <QueryClientProvider client={queryClient}>
      <I18nWrap>
        <ToastProvider>
          <AgentPanel
            taskId={taskId}
            agentStatus={{
              run_status: 'waiting_user' as const,
              latest_run_id: 'run-pending',
              pending_decision_count: pendingDecision ? 1 : 0,
              latest_message_summary: null,
            }}
            service={service}
          />
        </ToastProvider>
      </I18nWrap>
    </QueryClientProvider>,
  )
}

/**
 * 选中第一个候选 (默认第一个), 然后点 "回复" 提交按钮. 决策卡的提交
 * 按钮初始 disabled, 必须先选中候选才能 submit.
 */
async function selectCandidateAndSubmit(user: ReturnType<typeof userEvent.setup>, candidateLabel = /天气之子/) {
  const candidateBtn = await screen.findByRole('button', { name: candidateLabel })
  await user.click(candidateBtn)
  const submitBtn = await screen.findByRole('button', { name: /回复|Reply/i })
  await user.click(submitBtn)
}

beforeEach(() => {
  polyfillScrollMeasurement()
  // 重置 i18n 默认语言为 zh, 避免测试间状态污染
  i18n.changeLanguage('zh')
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

// ---------------------------------------------------------------------------
// 1. metadata_published 成功路径
// ---------------------------------------------------------------------------

describe('DecisionReplyCard metadata_published 成功路径', () => {
  it('replyToAgentDecision resolve status=metadata_published → 4 个 refetchQueries 立即触发 + 2 个 invalidateQueries 异步', async () => {
    // 关键契约: select_metadata_candidate 用户选候选后, 后端 fetch +
    // publish 成功, 必须 (a) refetch 4 个详情页 key (同步拉取, 不等
    // 自然 invalidate), (b) invalidate 列表 key (flows / tasks).
    // 旧实现 4 个全用 invalidate, 详情页用户会看到 "点了好像没反应".
    const user = userEvent.setup()
    const replyToAgentDecision = vi.fn().mockResolvedValue(_envelope({
      run_id: 'run-pending',
      status: 'metadata_published',
      message_count: 0,
      tool_call_count: 0,
      error_message: null,
    }))
    const svc = makeServiceMock({ replyToAgentDecision })
    const queryClient = makeQueryClient()
    const refetchSpy = vi.spyOn(queryClient, 'refetchQueries')
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')

    renderPanel(svc, queryClient)

    await selectCandidateAndSubmit(user)

    // 4 个详情页 key 走 refetchQueries
    await waitFor(() => {
      expect(refetchSpy).toHaveBeenCalled()
    })
    const refetchedKeys = refetchSpy.mock.calls.map((c) => c[0]?.queryKey).filter(Boolean)
    expect(refetchedKeys).toEqual(
      expect.arrayContaining([
        ['task-detail', 'task-1'],
        ['agent-decisions', 'task-1'],
        ['agent-messages', 'task-1'],
        ['agent-tool-calls', 'task-1'],
      ]),
    )

    // 列表 key 走 invalidateQueries
    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalled()
    })
    const invalidatedKeys = invalidateSpy.mock.calls.map((c) => c[0]?.queryKey).filter(Boolean)
    expect(invalidatedKeys).toEqual(
      expect.arrayContaining([
        ['flows'],
        ['tasks'],
      ]),
    )
  })

  it('metadata_published 成功 → useToast 弹 success toast "<title> 入库成功"', async () => {
    // 关键契约: 成功路径必须弹 i18n toast, 内容 "<title> 入库成功".
    // title 优先用 task 详情缓存里的 title 字段; 旧实现因为后端 envelope
    // 是 error, 走 onError 弹红 toast, 用户看到 "Agent run uuid:
    // metadata_published" 这种内部文案.
    const user = userEvent.setup()
    const replyToAgentDecision = vi.fn().mockResolvedValue(_envelope({
      run_id: 'run-pending',
      status: 'metadata_published',
      message_count: 0,
      tool_call_count: 0,
      error_message: null,
    }))
    const svc = makeServiceMock({ replyToAgentDecision })
    const queryClient = makeQueryClient()

    // 预置 task-detail 缓存, 包含 title
    queryClient.setQueryData(['task-detail', 'task-1'], {
      status: 'success' as const,
      messages: [],
      meta: {},
      data: {
        task: {
          id: 'task-1',
          title: '天气之子 (2019)',
          source_path: '/data/downloads/天气之子.mkv',
          status_summary: { status: 'waiting_user' },
        },
      },
    })

    renderPanel(svc, queryClient)

    await selectCandidateAndSubmit(user)

    // success toast "天气之子 (2019) 入库成功" 出现
    expect(await screen.findByText('天气之子 (2019) 入库成功')).toBeInTheDocument()

    // 不弹 error toast
    expect(screen.queryByText(/Agent run/)).not.toBeInTheDocument()
  })

  it('task 详情无 title 但有 source_path, toast 用 source_path 的 basename', async () => {
    // 边界: task.title 缺失但 source_path 有值, 用 .mkv 文件名.
    const user = userEvent.setup()
    const replyToAgentDecision = vi.fn().mockResolvedValue(_envelope({
      run_id: 'run-pending',
      status: 'metadata_published',
      message_count: 0,
      tool_call_count: 0,
      error_message: null,
    }))
    const svc = makeServiceMock({ replyToAgentDecision })
    const queryClient = makeQueryClient()

    // 预置 task-detail 缓存, title 缺失但 source_path 有
    queryClient.setQueryData(['task-detail', 'task-1'], {
      status: 'success' as const,
      messages: [],
      meta: {},
      data: {
        task: {
          id: 'task-1',
          title: '',
          source_path: '/data/downloads/铃芽之旅.mkv',
          status_summary: { status: 'waiting_user' },
        },
      },
    })

    renderPanel(svc, queryClient)

    await selectCandidateAndSubmit(user)

    // toast 用 basename "铃芽之旅.mkv"
    expect(await screen.findByText('铃芽之旅.mkv 入库成功')).toBeInTheDocument()
  })

  it('title 和 source_path 都缺失, toast fallback 到 "任务 入库成功"', async () => {
    // 边界: task 详情缓存完全没 title / source_path. 极少见, 但 fallback
    // 必须存在, 不得弹空 title.
    const user = userEvent.setup()
    const replyToAgentDecision = vi.fn().mockResolvedValue(_envelope({
      run_id: 'run-pending',
      status: 'metadata_published',
      message_count: 0,
      tool_call_count: 0,
      error_message: null,
    }))
    const svc = makeServiceMock({ replyToAgentDecision })
    const queryClient = makeQueryClient()
    // 不预置 task-detail 缓存

    renderPanel(svc, queryClient)

    await selectCandidateAndSubmit(user)

    // fallback toast "任务 入库成功" 出现
    expect(await screen.findByText('任务 入库成功')).toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// 2. 非 metadata_published 状态不弹入库成功 toast
// ---------------------------------------------------------------------------

describe('DecisionReplyCard 其它 success status: 不弹入库成功 toast', () => {
  it('status=target_conflict_overwritten → 仍 refetch, 但不弹入库成功 toast', async () => {
    // 关键: target_conflict 决策回复成功后是 overwrite_target / cancel,
    // 没有"入库成功"语义. 必须 gate 在 data.status === "metadata_published".
    const user = userEvent.setup()
    const replyToAgentDecision = vi.fn().mockResolvedValue(_envelope({
      run_id: 'run-pending',
      status: 'target_conflict_overwritten',
      message_count: 0,
      tool_call_count: 0,
      error_message: null,
    }))
    const svc = makeServiceMock({ replyToAgentDecision })
    const queryClient = makeQueryClient()
    queryClient.setQueryData(['task-detail', 'task-1'], {
      status: 'success' as const, messages: [], meta: {},
      data: { task: { id: 'task-1', title: '天气之子', source_path: '/dl/天气之子.mkv' } },
    })
    const refetchSpy = vi.spyOn(queryClient, 'refetchQueries')

    renderPanel(svc, queryClient)

    await selectCandidateAndSubmit(user)

    // refetch 仍触发
    await waitFor(() => {
      expect(refetchSpy).toHaveBeenCalled()
    })
    // 但不弹入库成功 toast
    expect(screen.queryByText(/入库成功/)).not.toBeInTheDocument()
    // 也不弹 error toast
    expect(screen.queryByText(/Agent run/)).not.toBeInTheDocument()
  })

  it('status=completed → 仍 refetch, 但不弹入库成功 toast', async () => {
    // mock service 默认 status=completed, 即便 refetch 跑了, 也不应当
    // 误触发入库成功 toast. 这是 decision 续跑 Agent 路径, 不是入库完成.
    const user = userEvent.setup()
    const replyToAgentDecision = vi.fn().mockResolvedValue(_envelope({
      run_id: 'run-pending',
      status: 'completed',
      message_count: 1,
      tool_call_count: 0,
      error_message: null,
    }))
    const svc = makeServiceMock({ replyToAgentDecision })
    const queryClient = makeQueryClient()
    queryClient.setQueryData(['task-detail', 'task-1'], {
      status: 'success' as const, messages: [], meta: {},
      data: { task: { id: 'task-1', title: '天气之子', source_path: '/dl/天气之子.mkv' } },
    })
    const refetchSpy = vi.spyOn(queryClient, 'refetchQueries')

    renderPanel(svc, queryClient)

    await selectCandidateAndSubmit(user)

    await waitFor(() => {
      expect(refetchSpy).toHaveBeenCalled()
    })
    expect(screen.queryByText(/入库成功/)).not.toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// 3. error 路径: 不弹入库成功 toast, 弹 error toast, ack 回退
// ---------------------------------------------------------------------------

describe('DecisionReplyCard 错误路径: 不弹入库成功 toast', () => {
  it('replyToAgentDecision reject → 弹 error toast, 不弹入库成功 toast', async () => {
    // 后端 envelope=error (旧实现) 或 throw 错误. 失败路径继续走
    // onError 弹红 toast, 不弹入库成功 toast.
    const user = userEvent.setup()
    const replyToAgentDecision = vi.fn().mockRejectedValue(new Error('decision_reply_failed'))
    const svc = makeServiceMock({ replyToAgentDecision })
    const queryClient = makeQueryClient()

    renderPanel(svc, queryClient)

    await selectCandidateAndSubmit(user)

    // error toast 出现 (站内错误用 i18n agent.replyFailed, 但 mockRejectedValue
    // 会带上原始 message; DecisionReplyCard 也会展示同一条内联错误. 至少要
    // 出现一次即可.)
    expect(await screen.findAllByText('decision_reply_failed')).not.toHaveLength(0)
    // 不弹入库成功 toast
    expect(screen.queryByText(/入库成功/)).not.toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// 4. metadata_published toast 必须用 refetched 新 title, 不能用旧 basename
// ---------------------------------------------------------------------------

describe('DecisionReplyCard metadata_published toast: 必须等 task-detail refetch 完成', () => {
  it('task-detail 缓存初始 title 为空, refetch 后写入最终 title → toast 用新 title 而非旧 source_path basename', async () => {
    // 关键契约: 用户提交 select_metadata_candidate 决策时, task.title 还没
    // 写入 (publish 工具本次确认后才写). 旧实现 onSuccess 立即 getQueryData,
    // 读到的是 stale title (= "" 或老 source_path basename), 用户看到的
    // toast 用的不是真正的元数据 title. 修复后 onSuccess 是 async, 必须
    // await refetchQueries(['task-detail', taskId]) 完成后再 getQueryData.
    //
    // 测试场景:
    //   - 缓存初值: title='', source_path='/dl/原始文件名.mkv' (旧值, 用户提交前)
    //   - refetch 后: title='天气之子 (2019)', source_path 保持
    //   - 期望 toast: "天气之子 (2019) 入库成功"
    //   - 不期望: 旧 basename "原始文件名.mkv 入库成功"
    const user = userEvent.setup()
    const replyToAgentDecision = vi.fn().mockResolvedValue(_envelope({
      run_id: 'run-pending',
      status: 'metadata_published',
      message_count: 0,
      tool_call_count: 0,
      error_message: null,
    }))
    const svc = makeServiceMock({ replyToAgentDecision })
    const queryClient = makeQueryClient()

    // 注册 task-detail 的 queryFn, refetch 时调它, 返回新数据 (title 写入,
    // status 推到 library_import_complete). 模拟 "publish 工具成功后,
    // task 详情被后端更新". 必须先 setQueryDefaults, 再 setQueryData —
    // setQueryData 创建 query 时才会应用 defaults (拿到 queryFn).
    queryClient.setQueryDefaults(['task-detail'], {
      queryFn: async () => ({
        status: 'success' as const,
        messages: [],
        meta: {},
        data: {
          task: {
            id: 'task-1',
            title: '天气之子 (2019)',
            source_path: '/dl/原始文件名.mkv',
            status_summary: { status: 'library_import_complete' },
          },
        },
      }),
    })

    // 预置 task-detail 缓存: 旧 title (空) + 旧 source_path. 模拟"用户
    // 提交决策前, 后端 task.title 还没写入"的状态.
    queryClient.setQueryData(['task-detail', 'task-1'], {
      status: 'success' as const,
      messages: [],
      meta: {},
      data: {
        task: {
          id: 'task-1',
          title: '',
          source_path: '/dl/原始文件名.mkv',
          status_summary: { status: 'waiting_user' },
        },
      },
    })

    renderPanel(svc, queryClient)

    await selectCandidateAndSubmit(user)

    // 关键断言: toast 用新 title "天气之子 (2019)", 不得用旧 basename
    // "原始文件名.mkv".
    expect(await screen.findByText('天气之子 (2019) 入库成功')).toBeInTheDocument()
    expect(screen.queryByText('原始文件名.mkv 入库成功')).not.toBeInTheDocument()
  })

  it('task-detail refetch 失败 → 退而求其次用 refetch 前缓存, toast 不阻断', async () => {
    // 边界: refetch 抛错 (网络断开 / 后端 5xx). 旧实现 (sync getQueryData)
    // 也能弹 toast (用旧缓存). 新实现 await 后 try/catch, 必须也弹
    // toast — 不能让用户看到"入库成功"被吞掉.
    const user = userEvent.setup()
    const replyToAgentDecision = vi.fn().mockResolvedValue(_envelope({
      run_id: 'run-pending',
      status: 'metadata_published',
      message_count: 0,
      tool_call_count: 0,
      error_message: null,
    }))
    const svc = makeServiceMock({ replyToAgentDecision })
    const queryClient = makeQueryClient()

    // 注册 task-detail queryFn, refetch 时永远 reject. 先 setQueryDefaults
    // 再 setQueryData (同上一个 test 解释).
    queryClient.setQueryDefaults(['task-detail'], {
      queryFn: async () => {
        throw new Error('network down')
      },
    })

    // 预置 task-detail 缓存: 只有 source_path, 无 title
    queryClient.setQueryData(['task-detail', 'task-1'], {
      status: 'success' as const,
      messages: [],
      meta: {},
      data: {
        task: {
          id: 'task-1',
          title: '',
          source_path: '/dl/原始文件名.mkv',
          status_summary: { status: 'waiting_user' },
        },
      },
    })

    renderPanel(svc, queryClient)

    await selectCandidateAndSubmit(user)

    // refetch 失败, 退到旧缓存: 用 source_path basename "原始文件名.mkv"
    // 弹 toast. 即便数据不完美, 也得给用户成功反馈.
    expect(await screen.findByText('原始文件名.mkv 入库成功')).toBeInTheDocument()
  })
})
