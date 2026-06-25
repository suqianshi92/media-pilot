// ---------------------------------------------------------------------------
// agent-retry-button-state.test.tsx
//
// 覆盖 fix-agent-retry-button-ui-state-semantics 的新 capability:
//   - agent-retry-button-state: AgentPanel 重试按钮的 UI 状态机契约
//     · retrySubmitting (POST 生命周期) 与 agentRunning (server 状态) 解耦
//     · POST 成功 → 立即 refetch 4 个 key + scrollToBottom
//     · POST 失败 → 按钮恢复可点击 + useToast 弹错误
//     · 不回归 freeform/decision submitted ack 现有行为
//
// 测试要求: 用真实 React Query (useMutation/useQueryClient), 不替成 mock,
// 这样能观察到真实的 isPending 状态机变化; 真实 ToastProvider 验证 toast
// 真的渲染到 DOM.
// ---------------------------------------------------------------------------

import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { I18nextProvider } from 'react-i18next'
import i18n from '@/i18n'

import { ToastProvider } from '@/components/shared/toast'
import { AgentPanel, type AgentService } from '@/components/agent/agent-panel'

// ---------------------------------------------------------------------------
// 滚动几何 polyfill: jsdom 没有 scrollHeight / clientHeight / scrollTop 默认
// getter, 也没有 Element.scrollTo. 用 Object.defineProperty 暴露可写属性.
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
  const calls: Array<{ top: number; behavior?: 'auto' | 'smooth' }> = []
  ;(HTMLElement.prototype as unknown as { __scrollToCalls?: typeof calls }).__scrollToCalls = calls
  HTMLElement.prototype.scrollTo = function (this: HTMLElement & { __st?: number }, arg: number | { top: number; behavior?: 'auto' | 'smooth' }) {
    const opts = typeof arg === 'number' ? { top: arg } : arg
    calls.push({ top: opts.top, behavior: opts.behavior })
    this.__st = opts.top
  } as HTMLElement['scrollTo']
  return calls
}

function scrollToCallsFor(el: HTMLElement) {
  return (el as unknown as { __scrollToCalls?: Array<{ top: number; behavior?: 'auto' | 'smooth' }> }).__scrollToCalls ?? []
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
    replyToAgentDecision: vi.fn().mockResolvedValue(_envelope({ run_id: 'r1' })),
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

function makeFailedStatus() {
  return {
    run_status: 'failed' as const,
    latest_run_id: 'run-old-failed',
    pending_decision_count: 0,
    latest_message_summary: null,
  }
}

function makeActiveStatus() {
  return {
    run_status: 'active' as const,
    latest_run_id: 'run-new',
    pending_decision_count: 0,
    latest_message_summary: null,
  }
}

/**
 * 渲染失败状态下的 AgentPanel, 注入自定义 service mock. 失败状态下
 * run_status='failed' 才会显示重试按钮.
 */
function renderFailedPanel(
  service: AgentServiceMock,
  queryClient: QueryClient,
  taskStatus: string = 'agent_failed',
) {
  return render(
    <QueryClientProvider client={queryClient}>
      <I18nWrap>
        <ToastProvider>
          <AgentPanel
            taskId="task-1"
            agentStatus={makeFailedStatus()}
            taskStatus={taskStatus}
            service={service}
          />
        </ToastProvider>
      </I18nWrap>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  polyfillScrollMeasurement()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

// ---------------------------------------------------------------------------
// 1. retry POST 成功路径
// ---------------------------------------------------------------------------

describe('AgentPanel retry 按钮: POST 成功路径', () => {
  it('点击重试 → POST resolve 后, 按钮 loading 立即结束, 不延展到 run 全流程', async () => {
    // 关键契约: retrySubmitting 仅覆盖 POST 生命周期. POST resolve 那一刻
    // 按钮 MUST 立即恢复可点击, 即便后续 agentStatus.run_status 仍是 'failed'
    // (父组件 taskDetailQuery 还没 refetch) 也不延展.
    const user = userEvent.setup()
    let resolveCreate!: (v: ReturnType<typeof _envelope<{
      run_id: string; status: string; message_count: number; tool_call_count: number; error_message: null
    }>>) => void
    const createAgentRun = vi.fn(() => new Promise<ReturnType<typeof _envelope<{
      run_id: string; status: string; message_count: number; tool_call_count: number; error_message: null
    }>>>((res) => {
      resolveCreate = res
    })) as unknown as AgentService['createAgentRun']
    const svc = makeServiceMock({ createAgentRun })
    const queryClient = makeQueryClient()
    renderFailedPanel(svc, queryClient)

    // 按钮出现
    const retryBtn = await screen.findByRole('button', { name: /重试/ })
    expect(retryBtn).not.toBeDisabled()

    // 点击 → POST 进入 pending → 按钮 disabled + spinner 文案
    await user.click(retryBtn)
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /重试中/ })).toBeInTheDocument()
    })
    expect(screen.getByRole('button', { name: /重试中/ })).toBeDisabled()

    // resolve POST → 按钮 loading 必须立即结束
    await act(async () => {
      resolveCreate(_envelope({ run_id: 'run-new', status: 'active', message_count: 0, tool_call_count: 0, error_message: null }))
    })

    // 关键断言: 按钮回到 "重试" 状态 (not "重试中")
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /重试中/ })).toBeNull()
    })
    const restoredBtn = screen.getByRole('button', { name: /重试/ })
    expect(restoredBtn).not.toBeDisabled()
  })

  it('POST resolve 后, 4 个 refetchQueries 被实际调用 (task-detail / agent-messages / agent-decisions / agent-tool-calls)', async () => {
    // 关键契约: POST 成功后 MUST 立即 refetch 4 个 key (而非仅 invalidate).
    // 修复前只用 invalidateQueries, 实际请求要等下次自然 refetch 才触发, UI
    // 卡死. 修复后用 refetchQueries, 立即触发, 让父组件 taskDetailQuery
    // 把新 agentStatus 透传过来.
    const user = userEvent.setup()
    const createAgentRun = vi.fn().mockResolvedValue({
      data: { run_id: 'run-new', status: 'active', message_count: 0, tool_call_count: 0, error_message: null },
    })
    const svc = makeServiceMock({ createAgentRun })
    const queryClient = makeQueryClient()
    const refetchSpy = vi.spyOn(queryClient, 'refetchQueries')
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')

    renderFailedPanel(svc, queryClient)

    const retryBtn = await screen.findByRole('button', { name: /重试/ })
    await user.click(retryBtn)

    // POST resolve 后, 必须出现 refetchQueries 调用 (4 个 key)
    await waitFor(() => {
      expect(refetchSpy).toHaveBeenCalled()
    })
    const refetchedKeys = refetchSpy.mock.calls.map((c) => c[0]?.queryKey).filter(Boolean)
    expect(refetchedKeys).toEqual(
      expect.arrayContaining([
        ['task-detail', 'task-1'],
        ['agent-messages', 'task-1'],
        ['agent-decisions', 'task-1'],
        ['agent-tool-calls', 'task-1'],
      ]),
    )
    // 修复前只用 invalidateQueries, 修复后 MUST 改用 refetchQueries;
    // 严禁"refetch + invalidate 重复触发"或"仅 invalidate".
    expect(invalidateSpy).not.toHaveBeenCalled()
  })

  it('POST resolve 后, 聊天框触发 scrollToBottom (强制滚到底, 不论 follow 状态)', async () => {
    // 关键契约: POST 成功后 MUST 显式调 scrollToBottom('auto') 一次,
    // 把新 run 启动后的初始 message / spinner 滚入视野. 即便用户上翻
    // 离开底部, retry 是用户主动行为, 视作"主动发送"语义.
    const user = userEvent.setup()
    const createAgentRun = vi.fn().mockResolvedValue({
      data: { run_id: 'run-new', status: 'active', message_count: 0, tool_call_count: 0, error_message: null },
    })
    const svc = makeServiceMock({ createAgentRun })
    const queryClient = makeQueryClient()
    renderFailedPanel(svc, queryClient)

    // 等 messagesQuery 解析完, scroll 容器出现
    const el = (await screen.findByTestId('agent-messages-scroll')) as HTMLElement
    // 模拟视口在中间位置 (follow=false), scrollHeight=1000, scrollTop=200,
    // clientHeight=400 → 距底部 400 > 80 → follow=false.
    ;(el as unknown as { __sh: number }).__sh = 1000
    ;(el as unknown as { __ch: number }).__ch = 400
    ;(el as unknown as { __st: number }).__st = 200
    act(() => { el.dispatchEvent(new Event('scroll')) })
    scrollToCallsFor(el).length = 0

    const retryBtn = await screen.findByRole('button', { name: /重试/ })
    await user.click(retryBtn)

    // POST resolve 后, 必有 scrollTo({ top: 1000, behavior: 'auto' })
    await waitFor(() => {
      const calls = scrollToCallsFor(el)
      expect(calls).toContainEqual({ top: 1000, behavior: 'auto' })
    })
  })

  it('POST resolve 后, 父组件 props 更新到 active, 顶部 / 底部 "Agent 正在处理中" 提示出现', async () => {
    // 关键契约: agentRunning 状态来自 server-side props 更新, 不来自
    // retrySubmitting. 父组件透传 run_status='active' 后, 顶部 spinner +
    // 底部 processingWait 都必须出现.
    const user = userEvent.setup()
    const createAgentRun = vi.fn().mockResolvedValue({
      data: { run_id: 'run-new', status: 'active', message_count: 0, tool_call_count: 0, error_message: null },
    })
    const svc = makeServiceMock({ createAgentRun })
    const queryClient = makeQueryClient()
    const { rerender } = render(
      <QueryClientProvider client={queryClient}>
        <I18nWrap>
          <ToastProvider>
            <AgentPanel
              taskId="task-1"
              agentStatus={makeFailedStatus()}
              taskStatus="agent_failed"
              service={svc}
            />
          </ToastProvider>
        </I18nWrap>
      </QueryClientProvider>,
    )

    const retryBtn = await screen.findByRole('button', { name: /重试/ })
    await user.click(retryBtn)
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /重试中/ })).toBeNull()
    })

    // 模拟父组件透传新 agentStatus (taskDetailQuery refetch 完成)
    rerender(
      <QueryClientProvider client={queryClient}>
        <I18nWrap>
          <ToastProvider>
            <AgentPanel
              taskId="task-1"
              agentStatus={makeActiveStatus()}
              taskStatus="agent_running"
              service={svc}
            />
          </ToastProvider>
        </I18nWrap>
      </QueryClientProvider>,
    )

    // 关键断言: 顶部 spinner + 底部 processingWait 都出现
    expect(await screen.findByText('Agent 正在处理中，请稍候...')).toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// 2. retry POST 失败路径
// ---------------------------------------------------------------------------

describe('AgentPanel retry 按钮: POST 失败路径', () => {
  it('POST reject → 按钮 disabled 立即恢复 false, 红色 error 条显示', async () => {
    // 关键契约: 失败时按钮 MUST 立即恢复可点击, 红色 error 条保留,
    // 用户可以再次点击重试.
    const user = userEvent.setup()
    const createAgentRun = vi.fn().mockRejectedValue(new Error('run_busy'))
    const svc = makeServiceMock({ createAgentRun })
    const queryClient = makeQueryClient()
    renderFailedPanel(svc, queryClient)

    const retryBtn = await screen.findByRole('button', { name: /重试/ })
    await user.click(retryBtn)

    // 等 reject resolve 后, 按钮恢复 "重试" 文案
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /重试中/ })).toBeNull()
    })
    const restoredBtn = screen.getByRole('button', { name: /重试/ })
    expect(restoredBtn).not.toBeDisabled()

    // 红色 error 条出现 (现有 retryMutation.isError 渲染逻辑保留)
    expect(await screen.findByText('run_busy')).toBeInTheDocument()
  })

  it('POST reject → useToast 弹错误 toast (双通道错误反馈)', async () => {
    // 关键契约: 失败时除了组件内红色 error 条, 还必须用 useToast 弹全局
    // 错误提示, 与站内其它 mutation 失败行为对齐.
    const user = userEvent.setup()
    const createAgentRun = vi.fn().mockRejectedValue(new Error('db_locked'))
    const svc = makeServiceMock({ createAgentRun })
    const queryClient = makeQueryClient()
    renderFailedPanel(svc, queryClient)

    const retryBtn = await screen.findByRole('button', { name: /重试/ })
    await user.click(retryBtn)

    // toast 出现 (zh 默认 agent.retryFailed = "重试失败")
    expect(await screen.findByText('重试失败')).toBeInTheDocument()
    void user
  })

  it('失败后用户可立即再次点击重试', async () => {
    // 关键契约: 失败不得永久禁用按钮, 用户可再次点.
    const user = userEvent.setup()
    const createAgentRun = vi.fn()
      .mockRejectedValueOnce(new Error('transient'))
      .mockResolvedValueOnce({
        data: { run_id: 'run-new', status: 'active', message_count: 0, tool_call_count: 0, error_message: null },
      })
    const svc = makeServiceMock({ createAgentRun })
    const queryClient = makeQueryClient()
    renderFailedPanel(svc, queryClient)

    // 第一次重试 → 失败
    const retryBtn = await screen.findByRole('button', { name: /重试/ })
    await user.click(retryBtn)
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /重试中/ })).toBeNull()
    })
    expect(createAgentRun).toHaveBeenCalledTimes(1)

    // 第二次重试 → 成功
    const retryBtn2 = screen.getByRole('button', { name: /重试/ })
    expect(retryBtn2).not.toBeDisabled()
    await user.click(retryBtn2)
    await waitFor(() => {
      expect(createAgentRun).toHaveBeenCalledTimes(2)
    })
  })
})

// ---------------------------------------------------------------------------
// 3. 不回归: freeform / decision submitted ack 现有行为
// ---------------------------------------------------------------------------

describe('AgentPanel retry 修改: 不回归 freeform / decision submitted ack', () => {
  it('run_status=completed 下, FreeformInput 正常渲染, 不被 retry 改造误显示', async () => {
    // 关键契约: 既有 FreeformInput 入口 (run_status !== 'failed' && !== 'active' &&
    // 无 pendingDecision) MUST NOT 被本次 retry 修改影响 — 仍渲染 FreeformInput,
    // 不渲染 retry 按钮. 详细 streaming 端到端在 agent-panel-scroll-follow.test.tsx
    // 里有专门覆盖, 这里只验证 retry 修改没破坏 freeform 入口的可见性.
    const svc = makeServiceMock()
    const queryClient = makeQueryClient()
    render(
      <QueryClientProvider client={queryClient}>
        <I18nWrap>
          <ToastProvider>
            <AgentPanel
              taskId="task-1"
              agentStatus={{
                run_status: 'completed' as const,
                latest_run_id: 'run-1',
                pending_decision_count: 0,
                latest_message_summary: null,
              }}
              service={svc}
            />
          </ToastProvider>
        </I18nWrap>
      </QueryClientProvider>,
    )

    // FreeformInput 渲染 (等 query resolve)
    expect(await screen.findByRole('textbox')).toBeInTheDocument()
    // 关键断言: 不显示 retry 按钮 (run_status !== 'failed' 时 retry 区块不渲染)
    expect(screen.queryByRole('button', { name: /重试/ })).toBeNull()
  })

  it('decision reply 提交后 ack 立即出现, 不等 refetch (existing polish fix 不回归)', async () => {
    // 关键契约: DecisionReplyCard.onSubmitted 同步设置 ack state, 不等 refetch.
    // 本次 retry 修改不应影响这条路径. 详细 ack 行为在 agent-panel-scroll-follow
    // 测试里有专门覆盖, 这里只验证"retry 修改后, run_status=waiting_user 状态
    // 下 FreeformInput 仍存在, 不会因 retry 改造误显示 retry 按钮".
    const svc = makeServiceMock()
    const queryClient = makeQueryClient()

    render(
      <QueryClientProvider client={queryClient}>
        <I18nWrap>
          <ToastProvider>
            <AgentPanel
              taskId="task-1"
              agentStatus={{
                run_status: 'waiting_user' as const,
                latest_run_id: 'run-1',
                pending_decision_count: 1,
                latest_message_summary: null,
              }}
              service={svc}
            />
          </ToastProvider>
        </I18nWrap>
      </QueryClientProvider>,
    )

    // run_status='waiting_user' + 无 pendingDecision → FreeformInput 渲染
    expect(await screen.findByRole('textbox')).toBeInTheDocument()
    // 关键断言: run_status !== 'failed' → retry 区块不渲染
    expect(screen.queryByRole('button', { name: /重试/ })).toBeNull()
  })
})
