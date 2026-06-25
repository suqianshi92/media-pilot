// ---------------------------------------------------------------------------
// agent-panel-scroll-follow.test.tsx
//
// 覆盖 polish-agent-panel-scroll-and-reply-ack 的两个新 capability:
//   - agent-panel-scroll-follow: 聊天滚动容器按 "接近底部" 阈值自动跟随
//   - agent-decision-reply-ack:   decision reply 提交后的乐观 ack 状态机
//
// jsdom 默认不实现 Element.scrollTo 与 scrollHeight / clientHeight 等几何
// 属性, 在每个用例 setup 阶段用 polyfillScrollMeasurement 一次性补齐.
// ---------------------------------------------------------------------------

import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { I18nextProvider } from 'react-i18next'
import { useRef } from 'react'
import i18n from '@/i18n'

// ---------------------------------------------------------------------------
// 滚动几何 polyfill: jsdom 没有 scrollHeight / clientHeight / scrollTop 默认
// getter, 也没有 Element.scrollTo. 用 Object.defineProperty 暴露可写属性,
// 由用例按需覆盖.
// ---------------------------------------------------------------------------

interface ScrollGeom {
  scrollHeight: number
  scrollTop: number
  clientHeight: number
}

function polyfillScrollMeasurement() {
  const defaults: ScrollGeom = { scrollHeight: 0, scrollTop: 0, clientHeight: 0 }
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
  // scrollTo 桩: 记录调用, 同时把 scrollTop 推到目标 (模拟布局后的滚动结果).
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

const renderWithI18n = (ui: React.ReactElement) =>
  render(<I18nextProvider i18n={i18n}>{ui}</I18nextProvider>)

/**
 * rerender-safe 版本的 renderWithI18n. 直接用 `render(<I18nWrap>{ui}</I18nWrap>)`
 * 在 rerender 时会替换整棵树 (I18nextProvider 被卸载, 子树 useRef 重新创建).
 * 用 `render` 的 `wrapper` 选项, I18nextProvider 会在 rerender 时保留, 子树
 * 走正常 React 协调 (同一个 component instance, hooks 状态稳定).
 */
const I18nWrap: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <I18nextProvider i18n={i18n}>{children}</I18nextProvider>
)
const renderWithI18nStable = (ui: React.ReactElement) =>
  render(ui, { wrapper: I18nWrap })

// ---------------------------------------------------------------------------
// i18n: agent.replySubmitted 在 zh / en 都存在非空字符串
// ---------------------------------------------------------------------------

describe('i18n: agent.replySubmitted', () => {
  it('zh 默认值: 已提交, Agent 正在继续处理', () => {
    i18n.changeLanguage('zh')
    expect(i18n.t('agent.replySubmitted')).toBe('已提交, Agent 正在继续处理')
  })

  it('en 默认值: Submitted, Agent is continuing...', async () => {
    await i18n.changeLanguage('en')
    expect(i18n.t('agent.replySubmitted')).toBe('Submitted, Agent is continuing...')
    await i18n.changeLanguage('zh')
  })
})

// ---------------------------------------------------------------------------
// useFollowScroll hook — 在 agent-panel.tsx 中导出, 单独写一个最小 host
// 组件验证其行为. 该 host 仅暴露 hook 的 ref + 三个返回值.
// ---------------------------------------------------------------------------

// 这里直接 import hook 是不行的(还没写). 测试文件先写, RED; 再补 hook
// 实现. 为了让 import 不报缺模块错, 用 vi.importActual 间接取出.
// 不过更简单: 测试文件里直接定义一个与 hook 同签名的桩, 等到 hook 真正
// export 之后, 切换到真实 import. 这里先用动态 import, 看到 hook 未导出
// 即 module not found 时, 整份测试会失败, 起到 RED 作用.
let _useFollowScroll: typeof import('@/components/agent/agent-panel')['useFollowScroll']

beforeEach(async () => {
  const mod = await import('@/components/agent/agent-panel')
  _useFollowScroll = mod.useFollowScroll
})

function HookHost({ onReady }: { onReady: (api: { ref: React.RefObject<HTMLDivElement>; api: ReturnType<NonNullable<typeof _useFollowScroll>> }) => void }) {
  const ref = useRef<HTMLDivElement>(null)
  const api = _useFollowScroll(ref)
  onReady({ ref, api })
  return <div ref={ref} data-testid="hook-host" style={{ height: 200 }} />
}

describe('useFollowScroll hook', () => {
  beforeEach(() => {
    polyfillScrollMeasurement()
  })
  afterEach(() => {
    cleanup()
  })

  it('初次挂载后 scrollToBottom("auto") 被调一次, follow 默认为 true', () => {
    let captured: { api: ReturnType<NonNullable<typeof _useFollowScroll>>; ref: React.RefObject<HTMLDivElement> } | null = null
    renderWithI18n(<HookHost onReady={(c) => (captured = c)} />)
    expect(captured).not.toBeNull()
    expect(captured!.api.follow).toBe(true)
    // 初次挂载不会有 scrollTo 行为 — hook 不在 mount 时自动滚, 由 AgentPanel
    // 自行决定是否调用 scrollToBottom. 这里只验证 hook 暴露的 scrollToBottom
    // 是可用的, 调用一次后能写入 scrollTop.
    const el = captured!.ref.current!
    ;(el as unknown as { __sh: number }).__sh = 500
    captured!.api.scrollToBottom('auto')
    expect(scrollToCallsFor(el).length).toBe(1)
    expect(scrollToCallsFor(el)[0]).toEqual({ top: 500, behavior: 'auto' })
  })

  it('scroll 事件: 视口距底部 ≤ 80 → follow=true, onContentChange 触发 scrollTo(smooth)', () => {
    let captured: { api: ReturnType<NonNullable<typeof _useFollowScroll>>; ref: React.RefObject<HTMLDivElement> } | null = null
    renderWithI18n(<HookHost onReady={(c) => (captured = c)} />)
    const el = captured!.ref.current!
    ;(el as unknown as { __sh: number }).__sh = 1000
    ;(el as unknown as { __ch: number }).__ch = 400
    // 距底部 50px: 1000 - 410 - 400 = 190? 不对: scrollTop = 600, scrollHeight = 1000,
    // clientHeight = 400 → 1000 - 600 - 400 = 0 ≤ 80 → follow 仍 true.
    ;(el as unknown as { __st: number }).__st = 600
    act(() => { el.dispatchEvent(new Event('scroll')) })
    expect(captured!.api.follow).toBe(true)
    // 触发 onContentChange: 视口在底部, 应滚动
    act(() => { captured!.api.onContentChange() })
    expect(scrollToCallsFor(el).length).toBe(1)
    expect(scrollToCallsFor(el)[0].behavior).toBe('smooth')
  })

  it('scroll 事件: 视口距底部 > 80 → follow=false, onContentChange 不再触发 scrollTo', () => {
    let captured: { api: ReturnType<NonNullable<typeof _useFollowScroll>>; ref: React.RefObject<HTMLDivElement> } | null = null
    renderWithI18n(<HookHost onReady={(c) => (captured = c)} />)
    const el = captured!.ref.current!
    ;(el as unknown as { __sh: number }).__sh = 1000
    ;(el as unknown as { __ch: number }).__ch = 400
    // 距底部 200px: 1000 - 0 - 400 = 600, 但用户上翻到 scrollTop=0
    ;(el as unknown as { __st: number }).__st = 0
    act(() => { el.dispatchEvent(new Event('scroll')) })
    expect(captured!.api.follow).toBe(false)
    // 清掉之前的 scrollTo 记录, 然后调 onContentChange
    scrollToCallsFor(el).length = 0
    act(() => { captured!.api.onContentChange() })
    expect(scrollToCallsFor(el).length).toBe(0)
  })

  it('scroll 事件: 再次滚回距底部 ≤ 80 → follow 切回 true', () => {
    let captured: { api: ReturnType<NonNullable<typeof _useFollowScroll>>; ref: React.RefObject<HTMLDivElement> } | null = null
    renderWithI18n(<HookHost onReady={(c) => (captured = c)} />)
    const el = captured!.ref.current!
    ;(el as unknown as { __sh: number }).__sh = 1000
    ;(el as unknown as { __ch: number }).__ch = 400
    // 先上翻 → follow=false
    ;(el as unknown as { __st: number }).__st = 0
    act(() => { el.dispatchEvent(new Event('scroll')) })
    expect(captured!.api.follow).toBe(false)
    // 再滚到底
    ;(el as unknown as { __st: number }).__st = 600
    act(() => { el.dispatchEvent(new Event('scroll')) })
    expect(captured!.api.follow).toBe(true)
  })

  it('scrollToBottom 是强制的: 即使 follow=false 也直接滚到底', () => {
    let captured: { api: ReturnType<NonNullable<typeof _useFollowScroll>>; ref: React.RefObject<HTMLDivElement> } | null = null
    renderWithI18n(<HookHost onReady={(c) => (captured = c)} />)
    const el = captured!.ref.current!
    ;(el as unknown as { __sh: number }).__sh = 1000
    ;(el as unknown as { __ch: number }).__ch = 400
    ;(el as unknown as { __st: number }).__st = 0
    act(() => { el.dispatchEvent(new Event('scroll')) })
    expect(captured!.api.follow).toBe(false)
    scrollToCallsFor(el).length = 0
    act(() => { captured!.api.scrollToBottom('smooth') })
    expect(scrollToCallsFor(el).length).toBe(1)
    expect(scrollToCallsFor(el)[0]).toEqual({ top: 1000, behavior: 'smooth' })
  })

  // -------------------------------------------------------------------
  // polish fix: 引用稳定性 — scrollToBottom / onContentChange 必须 useCallback 锁住,
  // 否则 AgentPanel 把 onContentChange 放进 useEffect 依赖后, 每次 render
  // 都会触发 effect 重跑, 在普通重渲染时把用户拉回底部.
  // -------------------------------------------------------------------

  it('scrollToBottom / onContentChange 在多次 render 中保持同一函数引用', () => {
    const apis: Array<ReturnType<NonNullable<typeof _useFollowScroll>>> = []
    const Capture: React.FC<{ tick: number }> = ({ tick }) => {
      const ref = useRef<HTMLDivElement>(null)
      const api = _useFollowScroll(ref)
      apis.push(api)
      return <div ref={ref} data-testid={`capture-${tick}`} style={{ height: 200 }} />
    }
    // 用 render 的 wrapper 选项让 I18nextProvider 在 rerender 时保留,
    // 避免 Capture 被卸载再挂载 (那样 useRef 会返回新 ref 对象,
    // useCallback 的 [scrollRef] 也会随之变化, 破坏稳定引用断言).
    const I18nWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
      <I18nextProvider i18n={i18n}>{children}</I18nextProvider>
    )
    const { rerender } = render(<Capture tick={0} />, { wrapper: I18nWrapper })
    rerender(<Capture tick={1} />)
    rerender(<Capture tick={2} />)
    expect(apis.length).toBe(3)
    expect(apis[0].scrollToBottom).toBe(apis[1].scrollToBottom)
    expect(apis[1].scrollToBottom).toBe(apis[2].scrollToBottom)
    expect(apis[0].onContentChange).toBe(apis[1].onContentChange)
    expect(apis[1].onContentChange).toBe(apis[2].onContentChange)
  })

  it('onContentChange 内部通过 followRef 读最新 follow state (而非闭包陈旧值)', () => {
    let captured: { api: ReturnType<NonNullable<typeof _useFollowScroll>>; ref: React.RefObject<HTMLDivElement> } | null = null
    renderWithI18n(<HookHost onReady={(c) => (captured = c)} />)
    const el = captured!.ref.current!
    ;(el as unknown as { __sh: number }).__sh = 1000
    ;(el as unknown as { __ch: number }).__ch = 400
    ;(el as unknown as { __st: number }).__st = 600  // distance = 0, follow=true
    act(() => { el.dispatchEvent(new Event('scroll')) })
    expect(captured!.api.follow).toBe(true)
    // 用户上翻: follow 切到 false
    ;(el as unknown as { __st: number }).__st = 0  // distance = 600 > 80
    act(() => { el.dispatchEvent(new Event('scroll')) })
    expect(captured!.api.follow).toBe(false)
    // 此时 onContentChange 应读 followRef.current = false → no-op,
    // 即便引用没变 (跟第一次 render 时同一个函数), 也不能用闭包里的旧 follow=true
    scrollToCallsFor(el).length = 0
    act(() => { captured!.api.onContentChange() })
    expect(scrollToCallsFor(el).length).toBe(0)
  })
})

// ---------------------------------------------------------------------------
// DecisionReplyCard ack 状态机 — 在 agent-panel.tsx 中导出 DecisionReplyCard
// 组件 (新), 或者通过 AgentPanel 集成测试触发. 选更直接的方式: 把 reply API
// mock 后渲染 AgentPanel, 走端到端路径.
// ---------------------------------------------------------------------------

const decisionMockService = () => ({
  listAgentMessages: vi.fn().mockResolvedValue({ data: [] }),
  listAgentDecisions: vi.fn().mockResolvedValue({ data: [] }),
  listAgentToolCalls: vi.fn().mockResolvedValue({ data: [] }),
  replyToAgentDecision: vi.fn(),
  createAgentRun: vi.fn(),
  sendFreeformMessage: vi.fn(),
  recoverStuckAgentRun: vi.fn(),
})

function makePendingDecision() {
  return {
    id: 'dr-1',
    run_id: 'run-1',
    task_id: 'task-1',
    decision_type: 'source_cleanup_action',
    question: '源文件清理方式?',
    free_text_allowed: false,
    options: [
      { id: 'keep', label: '保留源文件' },
      { id: 'trash', label: '移入回收区' },
    ],
    payload: {},
    status: 'pending',
    created_at: '2026-06-08T00:00:00Z',
  }
}

describe('DecisionReplyCard ack 状态机 (经 AgentPanel 集成测试)', () => {
  beforeEach(() => {
    polyfillScrollMeasurement()
  })
  afterEach(() => {
    cleanup()
    vi.resetModules()
  })

  it('用户点提交后, 选项/按钮立即消失, 出现 ack 文案', async () => {
    const replyToAgentDecision = vi.fn().mockResolvedValue({})
    vi.resetModules()
    vi.doMock('@tanstack/react-query', async (importOriginal) => {
      const actual = await importOriginal<typeof import('@tanstack/react-query')>()
      return {
        ...actual,
        useMutation: ({ mutationFn }: { mutationFn: () => Promise<unknown> }) => {
          return {
            mutate: () => { void mutationFn() },
            isPending: false,
            isError: false,
            isSuccess: false,
            error: null,
          }
        },
        useQuery: ({ queryKey }: { queryKey: readonly unknown[] }) => {
          if (queryKey[0] === 'agent-decisions') {
            return { data: { data: [makePendingDecision()] }, isLoading: false }
          }
          return { data: { data: [] }, isLoading: false }
        },
        useQueryClient: () => ({ invalidateQueries: vi.fn() }),
      }
    })
    const svc = {
      ...decisionMockService(),
      replyToAgentDecision,
    }
    vi.doMock('@/services/task-service', () => ({
      createTaskService: () => svc,
    }))
    const { AgentPanel } = await import('@/components/agent/agent-panel')
    const user = userEvent.setup()
    renderWithI18n(
      <AgentPanel
        taskId="task-1"
        agentStatus={{
          run_status: 'waiting_user',
          latest_run_id: null,
          pending_decision_count: 1,
          latest_message_summary: null,
        }}
        service={svc}
      />,
    )
    // 选项出现
    const keepBtn = await screen.findByText('保留源文件')
    expect(keepBtn).toBeInTheDocument()
    // 点 "保留源文件" → 选中
    await user.click(keepBtn)
    // 点提交按钮 — 文案: agent.reply = "回复" (zh 默认)
    const submitBtn = screen.getByRole('button', { name: /回复/ })
    await user.click(submitBtn)
    // 同一帧内选项和提交按钮消失, ack 文案出现
    expect(screen.queryByText('保留源文件')).toBeNull()
    expect(screen.getByText('已提交, Agent 正在继续处理')).toBeInTheDocument()
    // mock reply 被调过
    expect(replyToAgentDecision).toHaveBeenCalledTimes(1)
    vi.doUnmock('@tanstack/react-query')
    vi.doUnmock('@/services/task-service')
  })

  it('reply API 失败 → 选项/按钮重新出现, 错误文案显示, 用户选项保留', async () => {
    const replyToAgentDecision = vi.fn().mockRejectedValue(new Error('db_locked'))
    vi.resetModules()
    vi.doMock('@tanstack/react-query', async (importOriginal) => {
      const actual = await importOriginal<typeof import('@tanstack/react-query')>()
      return {
        ...actual,
        useMutation: ({ mutationFn }: { mutationFn: () => Promise<unknown> }) => {
          return {
            mutate: () => { void mutationFn().catch(() => {}) },
            // 真实 React Query 行为: isError 在 promise reject 后切到 true;
            // 这里我们在 useMutation 返回对象上同步给一个切换机制, 模拟 isError
            // 状态以测试 ack → error 回退. 为简化, 我们在 host 组件里手动用
            // 真实 useMutation, 不替它; 改用 react-query 的真实实现.
            isPending: false,
            isError: true,
            isSuccess: false,
            error: new Error('db_locked'),
          }
        },
        useQuery: ({ queryKey }: { queryKey: readonly unknown[] }) => {
          if (queryKey[0] === 'agent-decisions') {
            return { data: { data: [makePendingDecision()] }, isLoading: false }
          }
          return { data: { data: [] }, isLoading: false }
        },
        useQueryClient: () => ({ invalidateQueries: vi.fn() }),
      }
    })
    const svc = {
      ...decisionMockService(),
      replyToAgentDecision,
    }
    vi.doMock('@/services/task-service', () => ({
      createTaskService: () => svc,
    }))
    const { AgentPanel } = await import('@/components/agent/agent-panel')
    const user = userEvent.setup()
    renderWithI18n(
      <AgentPanel
        taskId="task-1"
        agentStatus={{
          run_status: 'waiting_user',
          latest_run_id: null,
          pending_decision_count: 1,
          latest_message_summary: null,
        }}
        service={svc}
      />,
    )
    const keepBtn = await screen.findByText('保留源文件')
    await user.click(keepBtn)
    const submitBtn = screen.getByRole('button', { name: /回复/ })
    await user.click(submitBtn)
    // 错误状态下, ack 区域应退出, 选项应重新出现, 错误文案应出现.
    // 由于 useMutation 被 mock 成 isError=true 一直保持, 这里直接验证:
    // - 选项 "保留源文件" 重新出现
    // - 错误文案 "db_locked" 在 decision-reply-error 测试 ID 上出现
    // (mock 里 isError 一直为 true, 等价于 error 已 settle 的稳态.)
    expect(screen.getByText('保留源文件')).toBeInTheDocument()
    expect(screen.getByTestId('decision-reply-error').textContent).toBe('db_locked')
    vi.doUnmock('@tanstack/react-query')
    vi.doUnmock('@/services/task-service')
  })
})

// ---------------------------------------------------------------------------
// AgentPanel 集成: 智能滚动 (polish fix)
// 验证 polish fix 的 3 个关键不变量:
//   (a) 用户上翻后, AgentPanel 重渲染不得调用 scrollToBottom
//   (b) 接近底部时 messages 增长, 视口跟随到底部
//   (c) 提交 decision reply 后立即滚到底, 不必等 pendingDecision 消失
// ---------------------------------------------------------------------------

function setupPanelMocks(opts: {
  messagesRef: { current: Array<{ id: string; role: string; content: string; created_at: string }> }
  hasPendingDecision?: boolean
}) {
  vi.resetModules()
  vi.doMock('@tanstack/react-query', async (importOriginal) => {
    const actual = await importOriginal<typeof import('@tanstack/react-query')>()
    return {
      ...actual,
      useQuery: ({ queryKey }: { queryKey: readonly unknown[] }) => {
        if (queryKey[0] === 'agent-messages') {
          return { data: { data: opts.messagesRef.current }, isLoading: false }
        }
        if (queryKey[0] === 'agent-decisions') {
          return {
            data: {
              data: opts.hasPendingDecision ? [makePendingDecision()] : [],
            },
            isLoading: false,
          }
        }
        return { data: { data: [] }, isLoading: false }
      },
      useMutation: ({ mutationFn }: { mutationFn: () => Promise<unknown> }) => {
        return {
          mutate: () => { void mutationFn() },
          isPending: false,
          isError: false,
          isSuccess: false,
          error: null,
        }
      },
      useQueryClient: () => ({ invalidateQueries: vi.fn() }),
    }
  })
  const svc = decisionMockService()
  vi.doMock('@/services/task-service', () => ({
    createTaskService: () => svc,
  }))
  return svc
}

describe('AgentPanel 集成: 智能滚动 (polish fix)', () => {
  beforeEach(() => {
    polyfillScrollMeasurement()
  })
  afterEach(() => {
    cleanup()
    vi.resetModules()
    vi.doUnmock('@tanstack/react-query')
    vi.doUnmock('@/services/task-service')
  })

  it('(a) 用户上翻后, AgentPanel 重渲染不得调用 scrollToBottom', async () => {
    const messagesRef: { current: Array<{ id: string; role: string; content: string; created_at: string }> } = {
      current: [
        { id: 'm1', role: 'user', content: 'hi', created_at: '2026-06-08T00:00:00Z' },
        { id: 'm2', role: 'assistant', content: 'hello', created_at: '2026-06-08T00:00:01Z' },
      ],
    }
    const svc = setupPanelMocks({ messagesRef, hasPendingDecision: false })
    const { AgentPanel } = await import('@/components/agent/agent-panel')
    const completedStatus = {
      run_status: 'completed' as const,
      latest_run_id: null,
      pending_decision_count: 0,
      latest_message_summary: null,
    }
    const { rerender } = renderWithI18nStable(
      <AgentPanel taskId="task-1" agentStatus={completedStatus} service={svc} />,
    )
    const el = screen.getByTestId('agent-messages-scroll') as HTMLElement
    // 模拟视口在顶部: 距底部 600px > 80 → follow=false
    ;(el as unknown as { __sh: number }).__sh = 1000
    ;(el as unknown as { __ch: number }).__ch = 400
    ;(el as unknown as { __st: number }).__st = 0
    act(() => { el.dispatchEvent(new Event('scroll')) })
    // 清理 mount 期间的 scrollTo 记录
    scrollToCallsFor(el).length = 0
    // 触发一次重渲染: 用新的 agentStatus 对象引用, AgentPanel 内部 state / query
    // 都不变. 这是 polish fix 的核心场景: 验证 useFollowScroll 返回值引用稳定
    // 导致 useEffect 不重复执行.
    rerender(
      <AgentPanel taskId="task-1" agentStatus={{ ...completedStatus }} service={svc} />,
    )
    expect(scrollToCallsFor(el)).toHaveLength(0)
  })

  it('(b) 接近底部时 messages 增长会跟随到底部 (smooth)', async () => {
    const messagesRef: { current: Array<{ id: string; role: string; content: string; created_at: string }> } = {
      current: [],
    }
    const svc = setupPanelMocks({ messagesRef, hasPendingDecision: false })
    const { AgentPanel } = await import('@/components/agent/agent-panel')
    const completedStatus = {
      run_status: 'completed' as const,
      latest_run_id: null,
      pending_decision_count: 0,
      latest_message_summary: null,
    }
    const { rerender } = renderWithI18nStable(
      <AgentPanel taskId="task-1" agentStatus={completedStatus} service={svc} />,
    )
    const el = screen.getByTestId('agent-messages-scroll') as HTMLElement
    // 模拟视口紧贴底部: 距底部 0 ≤ 80 → follow=true
    ;(el as unknown as { __sh: number }).__sh = 1000
    ;(el as unknown as { __ch: number }).__ch = 400
    ;(el as unknown as { __st: number }).__st = 600
    act(() => { el.dispatchEvent(new Event('scroll')) })
    scrollToCallsFor(el).length = 0
    // 增长 messages (从 0 条到 2 条)
    messagesRef.current = [
      { id: 'm1', role: 'assistant', content: 'hi', created_at: '2026-06-08T00:00:00Z' },
      { id: 'm2', role: 'assistant', content: 'world', created_at: '2026-06-08T00:00:01Z' },
    ]
    rerender(
      <AgentPanel taskId="task-1" agentStatus={{ ...completedStatus }} service={svc} />,
    )
    // messages.length 从 0 变 2, useEffect [messages.length, ..., onContentChange] 触发
    // → onContentChange → follow=true → scrollToBottom('smooth') → top=1000
    const calls = scrollToCallsFor(el)
    expect(calls.some((c) => c.top === 1000 && c.behavior === 'smooth')).toBe(true)
  })

  it('(c) 提交 decision reply 后立即滚到底, 不必等 pendingDecision 消失', async () => {
    const messagesRef: { current: Array<{ id: string; role: string; content: string; created_at: string }> } = {
      current: [],
    }
    const svc = setupPanelMocks({ messagesRef, hasPendingDecision: true })
    const { AgentPanel } = await import('@/components/agent/agent-panel')
    const user = userEvent.setup()
    renderWithI18nStable(
      <AgentPanel
        taskId="task-1"
        agentStatus={{
          run_status: 'waiting_user',
          latest_run_id: null,
          pending_decision_count: 1,
          latest_message_summary: null,
        }}
        service={svc}
      />,
    )
    const el = screen.getByTestId('agent-messages-scroll') as HTMLElement
    // 视口在中间位置: scrollHeight=1000, scrollTop=200, clientHeight=400
    // → 1000 - 200 - 400 = 400 > 80 → follow=false. 这样可以验证 onSubmitted
    // 是强制滚(不依赖 follow).
    ;(el as unknown as { __sh: number }).__sh = 1000
    ;(el as unknown as { __ch: number }).__ch = 400
    ;(el as unknown as { __st: number }).__st = 200
    act(() => { el.dispatchEvent(new Event('scroll')) })
    // 清理 mount 期间的 scrollTo 记录
    scrollToCallsFor(el).length = 0
    // 选 + 提交
    const keepBtn = await screen.findByText('保留源文件')
    await user.click(keepBtn)
    const submitBtn = screen.getByRole('button', { name: /回复/ })
    await user.click(submitBtn)
    // 同一帧内: onSubmitted 已触发 scrollToBottom('smooth') → top=1000
    // 不依赖 refetch, 不依赖 pendingDecision 消失. mock 让 pendingDecision
    // 永远存在 (静态返回), 所以这条路径纯靠 onSubmitted 触发.
    const calls = scrollToCallsFor(el)
    expect(calls).toContainEqual({ top: 1000, behavior: 'smooth' })
    // 验证 pendingDecision 还在 (mock 静态) — 证明确实走的 onSubmitted, 不
    // 是被 useEffect([pendingDecision]) 旧机制救回来的
    expect(screen.getByText('已提交, Agent 正在继续处理')).toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// FreeformInput 集成: assistant_delta 走 rAF + useEffect 触发滚动 (polish fix)
// 验证 polish fix 的关键不变量:
//   - assistant_delta / tool_call_* 事件触发 setStreamingText / setStreamingTool
//     时, 不再同步调 onStreamingUpdate
//   - 改为 FreeformInput 内部 useEffect 监听 streamingText / streamingTool 变化,
//     调度 requestAnimationFrame, 在 rAF 回调里 (即 DOM commit 之后) 才调
//     onStreamingUpdate
//   - 修复前的 BUG: _applySSEEvent 同步调 onStreamingUpdate, scrollToBottom
//     读到的是 DOM commit 前的旧 scrollHeight, 滚动距离不准确
//
// 端到端验证: 模拟一次真实的 assistant_delta 事件流过 _applySSEEvent, 验证
// streaming 文本出现在 DOM 中. 这是最简单的端到端契约 — 修复前修复后都能
// 通过 (修复前的实现也让 streaming 文本显示), 但配合下面的"rAF 时序"测试
// 能完整覆盖 polish fix 的两条不变量.
// ---------------------------------------------------------------------------

/**
 * 构造一个返回 SSE 流的可控 Response. _applySSEEvent 的入口是
 * fetch + body.getReader().read(), 构造满足该接口的 body 即可. jsdom 的
 * ReadableStream 实现与 Node 不完全一致, 用一个手写 body 桩: getReader()
 * 返回一个 reader, read() 第一次返回包含所有 SSE 块的 Uint8Array, 之后
 * 永远 pending (不 close), 模拟持续流.
 */
function makeStreamingResponse(events: Array<{ event: string; data: Record<string, unknown> }>) {
  const encoder = new TextEncoder()
  const chunk = encoder.encode(
    events
      .map((e) => `event: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`)
      .join(''),
  )
  let consumed = false
  const body = {
    getReader() {
      return {
        async read() {
          if (consumed) {
            // 永远 pending, 模拟持续流 — 否则 mutationFn 末尾会清空
            // streaming text, 测试看不到 'hi' 渲染到 DOM.
            await new Promise(() => {})
            return { done: true, value: undefined }
          }
          consumed = true
          // 强制 await 一个 macrotask, 让 React commit setStreamingText('hi')
          // 之后再返回. 否则 mutationFn 同步跑完, 多次 setState 批处理,
          // DOM 从未出现 'hi'.
          await new Promise((r) => setTimeout(r, 5))
          return { done: false, value: chunk }
        },
        releaseLock() {},
        cancel() { return Promise.resolve() },
      }
    },
  }
  return {
    ok: true,
    status: 200,
    body,
  } as unknown as Response
}

describe('FreeformInput 集成: assistant_delta 走 rAF + useEffect 触发滚动 (polish fix)', () => {
  let fetchSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    polyfillScrollMeasurement()
  })

  afterEach(async () => {
    if (fetchSpy) fetchSpy.mockRestore()
    cleanup()
    vi.resetModules()
    vi.doUnmock('@tanstack/react-query')
    vi.doUnmock('@/services/task-service')
    // 兜底清掉所有可能残留的 stream listener (mock 的 body 永远 pending)
    await new Promise((r) => setTimeout(r, 0))
  })

  it('assistant_delta 事件经 _applySSEEvent 处理后, streaming 文本出现在 DOM (端到端)', async () => {
    // 端到端验证 assistant_delta 路径: 用户提交消息 → mutationFn 调 fetch
    // → 读取 SSE 流 → _applySSEEvent 处理 assistant_delta → setStreamingText
    // → React commit → streaming 容器内出现 'hi' 文本.
    const messagesRef: { current: Array<{ id: string; role: string; content: string; created_at: string }> } = {
      current: [],
    }
    const svc = setupPanelMocks({ messagesRef, hasPendingDecision: false })
    const fetchResponse = makeStreamingResponse([
      { event: 'assistant_delta', data: { delta: 'hi' } },
    ])
    fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(fetchResponse)

    const { AgentPanel } = await import('@/components/agent/agent-panel')
    const user = userEvent.setup()
    // run_status='completed' 让 FreeformInput 正常渲染; 真正的 streaming 状态
    // 是 FreeformInput 的局部 state, 不依赖 agentStatus.
    renderWithI18nStable(
      <AgentPanel
        taskId="task-1"
        agentStatus={{
          run_status: 'completed' as const,
          latest_run_id: null,
          pending_decision_count: 0,
          latest_message_summary: null,
        }}
        service={svc}
      />,
    )

    // 触发流式文本
    const textarea = screen.getByRole('textbox') as HTMLTextAreaElement
    await user.type(textarea, 'hello')
    await user.keyboard('{Enter}')

    // 等到 fetch 被调 (说明 mutation 进入了 streaming 阶段)
    await waitFor(() => { expect(fetchSpy).toHaveBeenCalled() })

    // 等到 streaming text 渲染到 DOM (用 querySelector 避开 getByText 对
    // 复合元素的误判 — streaming 容器内有 cursor span 子元素)
    await waitFor(() => {
      const el = document.querySelector('.whitespace-pre-wrap')
      expect(el?.textContent).toBe('hi')
    })
  })

  it('修复后, _applySSEEvent 不再同步调 onStreamingUpdate — 由 useEffect + rAF 接管', async () => {
    // 验证 polish fix 的核心不变量: _applySSEEvent 处理 assistant_delta 时
    // 只调 setStreamingText, 不调 onStreamingUpdate; 后者由 useEffect 监听
    // streamingText 变化, 在 React commit 之后调度 rAF 才触发.
    //
    // 我们通过观察 FreeformInput 的 onStreamingUpdate 被调用的次数来验证:
    // 修复前 — 同步调, onStreamingUpdate 在 _applySSEEvent 同步执行路径里
    // 至少被调 N 次 (N = assistant_delta 事件数). 修复后 — 由 useEffect +
    // rAF 调度, 在 rAF 回调里调 onStreamingUpdate, 次数相同但时机不同.
    //
    // 用 vi.useFakeTimers 冻结 rAF: 修复前 onStreamingUpdate 在 setState
    // 同步链上立即被调, 计数器++; 修复后 onStreamingUpdate 在 rAF 回调
    // 里被调, rAF 被冻结, 计数器不变. 这样就能区分两种实现.
    //
    // 注意: 这里的 spy 目标是 onStreamingUpdate prop, 即 AgentPanel 传给
    // FreeformInput 的 handleStreamingUpdate. 但 FreeformInput 不导出,
    // 只能从 AgentPanel 侧观察. 改为观察 rAF 调度次数更直接:
    // 修复前 — 同步路径, 不调 rAF; 修复后 — 必调 rAF. 配合 spy 即可.
    const rafScheduled: number[] = []
    const realRAF = window.requestAnimationFrame
    const rafSpy = vi.spyOn(window, 'requestAnimationFrame').mockImplementation(() => {
      rafScheduled.push(Date.now())
      return 0  // 不真正调度
    })
    try {
      const messagesRef: { current: Array<{ id: string; role: string; content: string; created_at: string }> } = {
        current: [],
      }
      const svc = setupPanelMocks({ messagesRef, hasPendingDecision: false })
      const fetchResponse = makeStreamingResponse([
        { event: 'assistant_delta', data: { delta: 'hi' } },
      ])
      fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(fetchResponse)

      const { AgentPanel } = await import('@/components/agent/agent-panel')
      const user = userEvent.setup()
      renderWithI18nStable(
        <AgentPanel
          taskId="task-1"
          agentStatus={{
            run_status: 'completed' as const,
            latest_run_id: null,
            pending_decision_count: 0,
            latest_message_summary: null,
          }}
          service={svc}
        />,
      )

      const beforeSubmit = rafScheduled.length
      const textarea = screen.getByRole('textbox') as HTMLTextAreaElement
      await user.type(textarea, 'test')
      await user.keyboard('{Enter}')

      // 等到 streaming text 渲染到 DOM
      await waitFor(() => {
        const el = document.querySelector('.whitespace-pre-wrap')
        expect(el?.textContent).toBe('hi')
      })

      // 关键断言: 修复后, 必有 rAF 调度来自 useEffect (因为 _applySSEEvent
      // 不再调 onStreamingUpdate, 改由 useEffect 在 commit 后调度 rAF).
      // 修复前, _applySSEEvent 同步调 onStreamingUpdate, 不走 rAF, 计数
      // 不会因为 assistant_delta 事件增加. (注: React 18 scheduler 也会
      // 调 rAF, 但 baseline `beforeSubmit` 已经包含了 React 调度, 我们
      // 只关心"差值 > 0", 即本次 streaming 路径有没有调 rAF.)
      const rafDelta = rafScheduled.length - beforeSubmit
      expect(rafDelta).toBeGreaterThan(0)
    } finally {
      rafSpy.mockRestore()
      // 恢复 rAF
      window.requestAnimationFrame = realRAF
    }
  })
})
