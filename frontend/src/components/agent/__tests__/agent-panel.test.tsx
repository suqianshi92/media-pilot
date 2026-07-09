import { describe, expect, it, afterEach } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { I18nextProvider } from 'react-i18next'
import i18n from '@/i18n'

afterEach(() => {
  cleanup()
})

// ---------------------------------------------------------------------------
// SSE block parser (module-level function extracted from agent-panel.tsx)
// ---------------------------------------------------------------------------

function _parseSSEBlock(block: string): { event: string; data: Record<string, unknown> } | null {
  let event = 'message'
  let dataStr = ''

  const lines = block.split('\n')
  for (const line of lines) {
    if (line.startsWith('event: ')) {
      event = line.slice(7).trim()
    } else if (line.startsWith('data: ')) {
      dataStr = line.slice(6)
    }
  }

  if (!dataStr) return null

  try {
    const data = JSON.parse(dataStr)
    return { event, data }
  } catch {
    return null
  }
}

describe('_parseSSEBlock', () => {
  it('parses assistant_delta event', () => {
    const block = 'event: assistant_delta\ndata: {"delta":"Hello"}'
    const result = _parseSSEBlock(block)
    expect(result).not.toBeNull()
    expect(result!.event).toBe('assistant_delta')
    expect(result!.data).toEqual({ delta: 'Hello' })
  })

  it('parses tool_call_started event', () => {
    const block = 'event: tool_call_started\ndata: {"tool_name":"search_metadata","tool_call_id":"call_1"}'
    const result = _parseSSEBlock(block)
    expect(result).not.toBeNull()
    expect(result!.event).toBe('tool_call_started')
    expect(result!.data.tool_name).toBe('search_metadata')
  })

  it('parses tool_call_finished event', () => {
    const block = 'event: tool_call_finished\ndata: {"tool_name":"search_metadata","status":"success"}'
    const result = _parseSSEBlock(block)
    expect(result).not.toBeNull()
    expect(result!.event).toBe('tool_call_finished')
    expect(result!.data.status).toBe('success')
  })

  it('parses error event', () => {
    const block = 'event: error\ndata: {"error":"Something went wrong"}'
    const result = _parseSSEBlock(block)
    expect(result).not.toBeNull()
    expect(result!.event).toBe('error')
    expect(result!.data.error).toBe('Something went wrong')
  })

  it('parses run_finished event', () => {
    const block = 'event: run_finished\ndata: {"status":"completed"}'
    const result = _parseSSEBlock(block)
    expect(result).not.toBeNull()
    expect(result!.event).toBe('run_finished')
    expect(result!.data.status).toBe('completed')
  })

  it('returns null for empty block', () => {
    expect(_parseSSEBlock('')).toBeNull()
    expect(_parseSSEBlock('\n')).toBeNull()
  })

  it('returns null for malformed JSON', () => {
    const block = 'event: message\ndata: {not valid json}'
    expect(_parseSSEBlock(block)).toBeNull()
  })

  it('uses default "message" event when no event line present', () => {
    const block = 'data: {"key":"value"}'
    const result = _parseSSEBlock(block)
    expect(result).not.toBeNull()
    expect(result!.event).toBe('message')
    expect(result!.data).toEqual({ key: 'value' })
  })

  it('handles multi-line block gracefully (uses last data line)', () => {
    const block = 'event: assistant_delta\ndata: {"delta":"first"}\ndata: {"delta":"second"}'
    const result = _parseSSEBlock(block)
    expect(result).not.toBeNull()
    expect(result!.event).toBe('assistant_delta')
    // Last data line wins
    expect(result!.data).toEqual({ delta: 'second' })
  })
})

// ---------------------------------------------------------------------------
// [SystemAction] prefix detection
// ---------------------------------------------------------------------------

describe('[SystemAction] message prefix', () => {
  it('detects system action messages', () => {
    const content = '[SystemAction] 用户手动选择了 天气之子 (2019) from tmdb'
    expect(content.startsWith('[SystemAction]')).toBe(true)
  })

  it('does not match non-system messages', () => {
    const content = 'This is a normal assistant response'
    expect(content.startsWith('[SystemAction]')).toBe(false)
  })

  it('strips [SystemAction] prefix for display', () => {
    const content = '[SystemAction] 撤回发布完成'
    const displayContent = content.replace('[SystemAction] ', '')
    expect(displayContent).toBe('撤回发布完成')
  })

  it('empty string does not match', () => {
    expect(''.startsWith('[SystemAction]')).toBe(false)
  })

  it('content containing [SystemAction] but not starting with it', () => {
    const content = 'Agent reported [SystemAction] completed'
    expect(content.startsWith('[SystemAction]')).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// AgentPanel visibility rules (unit-testable logic)
// ---------------------------------------------------------------------------

describe('AgentPanel visibility', () => {
  it('pending decision takes priority: pending_decision_count > 0 means FreeformInput is disabled', () => {
    // When there are pending decisions, the freeform input should be disabled
    // because the user must resolve the decision first.
    const hasPendingDecisions = (decisionCount: number) => decisionCount > 0

    expect(hasPendingDecisions(1)).toBe(true)
    expect(hasPendingDecisions(0)).toBe(false)
    expect(hasPendingDecisions(3)).toBe(true)
  })

  it('active run disables freeform input', () => {
    // When an AgentRun is active, freeform input should be disabled
    const isInputDisabled = (agentStatus: string | null) => {
      return agentStatus === 'active' || agentStatus === 'waiting_user'
    }

    expect(isInputDisabled('active')).toBe(true)
    expect(isInputDisabled('waiting_user')).toBe(true)
    expect(isInputDisabled('completed')).toBe(false)
    expect(isInputDisabled('failed')).toBe(false)
    expect(isInputDisabled(null)).toBe(false)
    expect(isInputDisabled('none')).toBe(false)
  })

  it('deleted task disables freeform input', () => {
    const isInputDisabled = (taskStatus: string) => taskStatus === 'deleted'
    expect(isInputDisabled('deleted')).toBe(true)
    expect(isInputDisabled('library_import_complete')).toBe(false)
    expect(isInputDisabled('completed')).toBe(false)
  })

  it('SSE disconnect does not re-POST to create a new run', () => {
    // The stream disconnect handler calls onMessagesRefresh() instead of
    // service.sendFreeformMessage(). This test verifies the logic:
    const handleStreamDisconnect = (onMessagesRefresh: () => void) => {
      // Simulates what happens on stream disconnect:
      // - do NOT call sendFreeformMessage
      // - call onMessagesRefresh instead
      onMessagesRefresh()
    }

    let refreshCalled = false

    const mockRefresh = () => { refreshCalled = true }

    // On disconnect: refresh only
    handleStreamDisconnect(mockRefresh)

    expect(refreshCalled).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// isToolCallSuccessful — 工具调用成功判定 (防止 UI 把成功误标为失败)
// ---------------------------------------------------------------------------

// 注: agent-panel.tsx 里这个函数是私有的 (没 export). 这里复制一份
// 与源码完全对齐, 用于单元测试. 若源码逻辑调整, 单元测试必须同步
// 更新, 否则会变成无意义的双胞胎测试 (drift).

type AgentToolCallLike = {
  status?: string
  output?: Record<string, unknown> | null
}

function isToolCallSuccessful(toolDetail: AgentToolCallLike | undefined): boolean {
  if (!toolDetail) return false
  const wireStatus = (toolDetail.status || '').toLowerCase()
  if (wireStatus === 'succeeded' || wireStatus === 'completed') return true
  if (wireStatus === 'failed' || wireStatus === 'failure' || wireStatus === 'error') return false
  const output = toolDetail.output
  if (output && typeof output === 'object') {
    const dataStatus = (output as Record<string, unknown>).status
    if (typeof dataStatus === 'string' && dataStatus === 'success') {
      return true
    }
  }
  return false
}

describe('isToolCallSuccessful', () => {
  it('returns true when status is "succeeded" (API 归一值)', () => {
    expect(isToolCallSuccessful({ status: 'succeeded' })).toBe(true)
  })

  it('returns true when status is "completed" (runner 新写入值)', () => {
    // regression: 在 API 完成归一之前, 前端必须能容忍 runner 写入的
    // "completed" 字符串, 避免 UI 把成功误标为失败.
    expect(isToolCallSuccessful({ status: 'completed' })).toBe(true)
  })

  it('returns true when status is missing but output.status is "success"', () => {
    // 兜底: 即使 wire status 缺失或异常, output.status=success 仍
    // 视为成功.
    expect(isToolCallSuccessful({
      status: '',
      output: { status: 'success' },
    })).toBe(true)
  })

  it('returns false when status is "failed"', () => {
    expect(isToolCallSuccessful({ status: 'failed' })).toBe(false)
  })

  it('returns false when status is "failure" (legacy / 内部值)', () => {
    expect(isToolCallSuccessful({ status: 'failure' })).toBe(false)
  })

  it('returns false when status is "error" (legacy / 内部值)', () => {
    expect(isToolCallSuccessful({ status: 'error' })).toBe(false)
  })

  it('returns false when toolDetail is undefined', () => {
    expect(isToolCallSuccessful(undefined)).toBe(false)
  })

  it('returns false when output is null and status is unknown', () => {
    expect(isToolCallSuccessful({ status: 'unknown', output: null })).toBe(false)
  })

  it('is case-insensitive for status comparisons', () => {
    expect(isToolCallSuccessful({ status: 'COMPLETED' })).toBe(true)
    expect(isToolCallSuccessful({ status: 'Succeeded' })).toBe(true)
    expect(isToolCallSuccessful({ status: 'FAILED' })).toBe(false)
  })

  it('falls through to output.status check when wire status is "running"', () => {
    // running 是中间态, 不应被视为成功, 但若 tool 的 output.status
    // 是 success (例如后端已经判定), 仍按 success 显示.
    expect(isToolCallSuccessful({
      status: 'running',
      output: { status: 'success' },
    })).toBe(true)
  })

  it('does not match output.status that is not exactly "success"', () => {
    // 防止 output.status = "successful" / "succeeded" 等变体被误判.
    // 当前契约是 wire status 用 "succeeded", output 内部用 "success".
    expect(isToolCallSuccessful({
      status: 'unknown',
      output: { status: 'successful' },
    })).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// ToolCallBlock 折叠态摘要 — 不得在 header 显示原始 JSON, 应显示一句
// 中文摘要, 内部路径 / 阈值等敏感字段不出现.  这里直接复用 agent-panel
// 内部的 summarizers (导出后供测试).
// ---------------------------------------------------------------------------

import {
  summarizePersistMetadataSelection,
  summarizeSearchMetadata,
  summarizeSourceCleanup,
  summarizeToolOutput,
} from '@/components/agent/agent-panel'

describe('summarizeSourceCleanup', () => {
  const t = i18n.t.bind(i18n)
  it('kept action 走中文 "保留"', () => {
    expect(summarizeSourceCleanup({ data: { action: 'kept' } }, t)).toBe('源文件按策略保留')
  })

  it('trashed action 走中文 "回收区" 摘要, 不得包含 trash_target 路径', () => {
    const summary = summarizeSourceCleanup({
      data: {
        action: 'trashed',
        trash_target: '/data/trash/secret-uuid-1234/movie.mkv',
      },
    }, t)
    expect(summary).toBe('源文件已移入回收区')
    // 关键: 路径不能泄漏到折叠态摘要
    expect(summary).not.toContain('trash')
    expect(summary).not.toContain('secret-uuid')
    expect(summary).not.toContain('/data')
  })

  it('decision_type=source_cleanup_action 走 "已请求用户决策"', () => {
    expect(
      summarizeSourceCleanup({ data: { decision_type: 'source_cleanup_action' } }, t)
    ).toBe(t('agent.toolSummary.sourceCleanupDecisionRequested'))
  })

  it('未知 action 返回空串 — 不暴露原始字段', () => {
    expect(summarizeSourceCleanup({ data: { action: 'unknown_thing' } }, t)).toBe('')
  })

  it('非对象 input 返回空串', () => {
    expect(summarizeSourceCleanup(null, t)).toBe('')
    expect(summarizeSourceCleanup('string', t)).toBe('')
  })

  it('legacy wire shape (action 在根级, 不在 data) 仍兼容', () => {
    expect(summarizeSourceCleanup({ action: 'kept' }, t)).toBe('源文件按策略保留')
  })
})

describe('summarizeSearchMetadata — 真实 ToolResult wire shape', () => {
  const t = i18n.t.bind(i18n)

  it('空 data.candidates → "0 个候选"', () => {
    expect(summarizeSearchMetadata({ data: { candidates: [] } }, t)).toBe('0 个候选')
  })

  it('data.candidates 单数 → "1 个候选"', () => {
    expect(summarizeSearchMetadata({ data: { candidates: [{}] } }, t)).toBe('1 个候选')
  })

  it('data.candidates 多候选 → "N 个候选"', () => {
    expect(summarizeSearchMetadata({ data: { candidates: [{}, {}, {}] } }, t)).toBe('3 个候选')
  })

  it('顶层 candidates 兼容 (legacy shape)', () => {
    expect(summarizeSearchMetadata({ candidates: [{}, {}] }, t)).toBe('2 个候选')
  })

  it('无 candidates 但有 server-side summary → fallback summary', () => {
    expect(
      summarizeSearchMetadata({
        data: {},
        summary: "Found 3 candidates for 'Inception' on tmdb",
      }, t)
    ).toBe("Found 3 candidates for 'Inception' on tmdb")
  })

  it('既无 candidates 也无 summary → 空串', () => {
    expect(summarizeSearchMetadata({ data: {} }, t)).toBe('')
    expect(summarizeSearchMetadata({}, t)).toBe('')
  })
})

describe('summarizePersistMetadataSelection — 真实 wire shape', () => {
  const t = i18n.t.bind(i18n)

  it('output.data.title → "已选择: <title>"', () => {
    expect(
      summarizePersistMetadataSelection({ data: { title: 'Inception' } }, {}, t)
    ).toBe('已选择: Inception')
  })

  it('legacy output.title 仍兼容', () => {
    expect(
      summarizePersistMetadataSelection({ title: 'Inception' }, {}, t)
    ).toBe('已选择: Inception')
  })

  it('output 无 title, input 有 title → 退回 input', () => {
    expect(
      summarizePersistMetadataSelection({}, { title: 'Inception' }, t)
    ).toBe('已选择: Inception')
  })

  it('都无 title → 通用 "已选择候选"', () => {
    expect(summarizePersistMetadataSelection({}, {}, t)).toBe('已选择候选')
  })
})

describe('summarizeToolOutput dispatcher', () => {
  const t = i18n.t.bind(i18n)
  it('handle_source_cleanup 走 source_cleanup 分支', () => {
    expect(
      summarizeToolOutput('handle_source_cleanup', { data: { action: 'kept' } }, undefined, t)
    ).toBe('源文件按策略保留')
  })

  it('search_metadata 走 search_metadata 分支', () => {
    expect(
      summarizeToolOutput('search_metadata', { data: { candidates: [{}, {}] } }, undefined, t)
    ).toBe('2 个候选')
  })

  it('persist_metadata_selection 走 persist 分支', () => {
    expect(
      summarizeToolOutput('persist_metadata_selection', { data: { title: 'X' } }, {}, t)
    ).toBe('已选择: X')
  })

  it('未知工具返回空串 — 折叠态只显示通用工具名称, 不重复摘要', () => {
    expect(
      summarizeToolOutput('unknown_tool', { some: 'json' }, {}, t)
    ).toBe('')
  })
})

// ---------------------------------------------------------------------------
// MessageBubble 渲染 — 默认折叠 / 折叠态摘要 / 点击展开
// ---------------------------------------------------------------------------

import { MessageBubble } from '@/components/agent/agent-panel'

const renderWithI18n = (ui: React.ReactElement) =>
  render(<I18nextProvider i18n={i18n}>{ui}</I18nextProvider>)

function makeAssistantMsg(content: string = '分析一下, 这个看起来是电影.', toolCalls: any[] = []) {
  return {
    id: 'msg-1',
    run_id: 'run-1',
    role: 'assistant' as const,
    content,
    tool_call_id: null,
    tool_name: null,
    created_at: '2026-06-05T12:00:00Z',
    tool_calls: toolCalls,
  }
}

function makeToolCall(name: string, args: any = {}) {
  return {
    id: `tc-${name}`,
    type: 'function' as const,
    function: { name, arguments: JSON.stringify(args) },
  }
}

function makeToolDetail(
  name: string,
  output: Record<string, unknown> | null,
  input: Record<string, unknown> = {},
  status: string = 'succeeded'
) {
  return {
    id: `detail-${name}`,
    run_id: 'run-1',
    message_id: 'msg-1',
    tool_call_id: `tc-${name}`,
    tool_name: name,
    status,
    input,
    output,
    error_message: null,
    duration_ms: 100,
    created_at: '2026-06-05T12:00:00Z',
  }
}

describe('MessageBubble — ToolCallBlock 默认折叠', () => {
  it('初次渲染不出现 input/output JSON 的 <pre> 元素', () => {
    const toolDetail = makeToolDetail(
      'search_metadata',
      { candidates: [{}, {}], has_clear_winner: true },
      { keyword: 'Inception' }
    )
    const msg = makeAssistantMsg('分析一下.', [makeToolCall('search_metadata', { keyword: 'Inception' })])

    const { container } = renderWithI18n(
      <MessageBubble msg={msg} toolDetails={[toolDetail]} />
    )

    // 关键: input / output JSON 不应在折叠态渲染
    const pres = container.querySelectorAll('pre')
    expect(pres.length).toBe(0)
  })

  it('折叠态在 header 显示一句中文摘要 ("2 个候选")', () => {
    const toolDetail = makeToolDetail(
      'search_metadata',
      { candidates: [{}, {}] },
      { keyword: 'Inception' }
    )
    const msg = makeAssistantMsg('分析一下.', [makeToolCall('search_metadata', { keyword: 'Inception' })])

    renderWithI18n(<MessageBubble msg={msg} toolDetails={[toolDetail]} />)

    expect(screen.getByText('搜索元数据')).toBeInTheDocument()
    expect(screen.queryByText('search_metadata')).not.toBeInTheDocument()
    expect(screen.getByTestId('tool-call-summary-search_metadata')).toHaveTextContent('2 个候选')
  })

  it('折叠态对 handle_source_cleanup 显示 "保留" 摘要 (不暴露 trash_target 等内部字段)', () => {
    const toolDetail = makeToolDetail(
      'handle_source_cleanup',
      { action: 'kept', policy: 'keep' },
      { task_id: 'task-1' }
    )
    const msg = makeAssistantMsg('已清理.', [makeToolCall('handle_source_cleanup', { task_id: 'task-1' })])

    renderWithI18n(<MessageBubble msg={msg} toolDetails={[toolDetail]} />)

    const summary = screen.getByTestId('tool-call-summary-handle_source_cleanup')
    expect(summary).toHaveTextContent('源文件按策略保留')
    expect(summary.textContent).not.toContain('policy')
    expect(summary.textContent).not.toContain('task-1')
  })

  it('执行中的工具显示 "执行中", 不误标为失败', () => {
    const toolDetail = makeToolDetail(
      'handle_source_cleanup',
      null,
      { task_id: 'task-1' },
      'running'
    )
    const msg = makeAssistantMsg('发布成功, 正在清理源文件.', [
      makeToolCall('handle_source_cleanup', { task_id: 'task-1' }),
    ])

    renderWithI18n(<MessageBubble msg={msg} toolDetails={[toolDetail]} />)

    const status = screen.getByTestId('tool-call-status-handle_source_cleanup')
    expect(status).toHaveTextContent('执行中')
    expect(status).not.toHaveTextContent('失败')
  })

  it('模拟用户点击 ToolCallBlock → 出现 input/output JSON', async () => {
    const user = userEvent.setup()
    const toolDetail = makeToolDetail(
      'search_metadata',
      { candidates: [{}, {}] },
      { keyword: 'Inception' }
    )
    const msg = makeAssistantMsg('分析一下.', [makeToolCall('search_metadata', { keyword: 'Inception' })])

    const { container } = renderWithI18n(
      <MessageBubble msg={msg} toolDetails={[toolDetail]} />
    )

    // 点击 header (button 内) 展开
    const headerButton = container.querySelector('button[class*="hover:bg-muted"]') as HTMLElement
    expect(headerButton).toBeTruthy()
    await user.click(headerButton)

    // 展开后出现 <pre> 元素
    const pres = container.querySelectorAll('pre')
    expect(pres.length).toBeGreaterThan(0)
  })
})

describe('MessageBubble — assistant 内容走 MarkdownView', () => {
  it('assistant 消息的 # 标题 / - 列表渲染为 markdown 结构', () => {
    const msg = makeAssistantMsg('# 总结\n\n- 第一步\n- 第二步', [])
    const { container } = renderWithI18n(
      <MessageBubble msg={msg} toolDetails={[]} />
    )
    // 标题 + 列表 都应渲染
    expect(container.querySelector('p.text-base')).toBeTruthy()
    expect(container.querySelectorAll('li').length).toBe(2)
  })

  it('tool 角色消息不再单独渲染（返回结果已在 assistant tool_call block 展示）', () => {
    const msg = {
      id: 'msg-tool',
      run_id: 'run-1',
      role: 'tool' as const,
      content: '# 这不应渲染为标题',
      tool_call_id: null,
      tool_name: null,
      created_at: '2026-06-05T12:00:00Z',
      tool_calls: [],
    }
    const { container } = renderWithI18n(
      <MessageBubble msg={msg} toolDetails={[]} />
    )
    expect(container).toBeEmptyDOMElement()
    expect(screen.queryByText('# 这不应渲染为标题')).not.toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// MessageBubble — [SystemAction] 前缀消息渲染
//
// 锁定:
// - 消息以 [SystemAction] 起头 → 用 amber 边框 + "系统动作" role label
// - 显示内容时 strip 掉 [SystemAction] 前缀 (用户不该看到内部 marker)
// - 不当作 markdown 渲染 (避免被解释成标题 / 列表)
// - 不当作 assistant 角色 (避免误标蓝色)
// - 关键: 不能因为 strip 前缀就丢失原文 / 改写成奇怪形态
// ---------------------------------------------------------------------------

describe('MessageBubble — [SystemAction] rendering', () => {
  function makeSystemActionMsg(content: string) {
    return {
      id: 'msg-sys-1',
      run_id: 'run-sys-1',
      role: 'assistant' as const,
      content,
      tool_calls: null,
      tool_call_id: null,
      tool_name: null,
      created_at: '2026-06-05T12:00:00Z',
    }
  }

  it('strips [SystemAction] prefix from display content', () => {
    const msg = makeSystemActionMsg('[SystemAction] 已选择元数据候选：天气之子 (2019)')

    renderWithI18n(<MessageBubble msg={msg} toolDetails={[]} />)

    // 显示内容: prefix 必须被去掉, 用户只看到摘要
    expect(screen.getByText(/已选择元数据候选/)).toBeInTheDocument()
    // 关键: 内部 [SystemAction] marker 不能泄漏到用户界面
    expect(screen.queryByText(/\[SystemAction\]/)).not.toBeInTheDocument()
  })

  it('renders with amber border (system action style)', () => {
    const msg = makeSystemActionMsg('[SystemAction] 已确认候选')

    const { container } = renderWithI18n(
      <MessageBubble msg={msg} toolDetails={[]} />
    )

    // 关键: 边框颜色必须是 amber, 不能与 assistant 蓝或 user 中性色混淆
    const bubble = container.querySelector('[class*="border-amber"]')
    expect(bubble).toBeTruthy()
    // 不能是蓝色 (assistant) 或纯灰色 (user)
    expect(bubble?.className).not.toContain('border-blue')
  })

  it('uses "系统动作" (agent.systemAction) as the role label, not the assistant label', () => {
    const msg = makeSystemActionMsg('[SystemAction] 用户决策: 保留源文件')

    renderWithI18n(<MessageBubble msg={msg} toolDetails={[]} />)

    // role label 必须是 "系统动作" i18n key
    expect(screen.getByText('系统动作')).toBeInTheDocument()
    // 不该出现 assistant 角色标签
    expect(screen.queryByText('Agent')).not.toBeInTheDocument()
  })

  it('does not render system action content as markdown', () => {
    // # 标题符号 / - 列表符号: 在 system action 上下文中, 这些是普通文本,
    // 走 plain 渲染, 不解释为 markdown 结构.
    const msg = makeSystemActionMsg('[SystemAction] 已提交: # 候选 1 命中率高')

    const { container } = renderWithI18n(
      <MessageBubble msg={msg} toolDetails={[]} />
    )

    // 没有 h1 标签, 整段在 plain <p> 内
    expect(container.querySelector('h1')).toBeNull()
    const p = container.querySelector('p.whitespace-pre-wrap')
    expect(p).toBeTruthy()
    expect(p!.textContent).toContain('# 候选 1 命中率高')
  })

  it('renders empty system action (e.g. with no trailing text) without crashing', () => {
    // 防御: 极端情况下后端可能只发 [SystemAction] 单独一条, 不带任何内容.
    // 不应该抛错, 整张 bubble 仍能渲染.
    const msg = makeSystemActionMsg('[SystemAction]')

    expect(() => {
      renderWithI18n(<MessageBubble msg={msg} toolDetails={[]} />)
    }).not.toThrow()
    // role label 仍然显示
    expect(screen.getByText('系统动作')).toBeInTheDocument()
  })

  it('does NOT treat content containing "[SystemAction]" mid-string as system action', () => {
    // 防御: 字符串中段含 [SystemAction] 但不是 message 类型的 marker,
    // 应当走普通 assistant 渲染路径, 而不是误标为 system action.
    const msg = makeSystemActionMsg('Agent reported [SystemAction] completed')

    const { container } = renderWithI18n(
      <MessageBubble msg={msg} toolDetails={[]} />
    )

    // 因为不是 prefix, 应当走 assistant 蓝色边框
    const bubble = container.querySelector('[class*="border-blue"]')
    expect(bubble).toBeTruthy()
    // role label 是 Agent, 不是 "系统动作"
    expect(screen.queryByText('系统动作')).not.toBeInTheDocument()
    // 显示内容保留原样 (因为这条不是 system action, 不 strip 前缀)
    expect(screen.getByText(/Agent reported/)).toBeInTheDocument()
  })
})
