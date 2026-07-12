/**
 * api-client 测试 — 重点验证 decision reply envelope 解析合约.
 *
 * 背景 (Issue 2): 后端 reply_to_agent_decision 把 result.status 映射到
 * envelope.status. 旧实现只把 completed / waiting_user 视为 success,
 * 导致 overwrite_target / cancel_publish / source_cleanup_* /
 * manual_selection_cancelled 这些确定性后端成功被 envelope.status
 * = "error" 误标, apiPost 走 ApiError.onError toast, 用户看到
 * "加载一会什么也没发生"反向. 修复后这些 status 都归 success envelope.
 *
 * 这里用 fetch mock 直接验证 createApiTaskService().replyToAgentDecision
 * 不会因为后端返 success + data.status == "target_conflict_overwritten"
 * 而抛 ApiError. 也就是说, agent-panel 的 replyMutation.onError
 * 不会被无谓触发.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, apiFetch, createApiTaskService } from '@/services/api-client'
import { abortAuthenticatedRequests } from '@/services/http-client'

interface MockResponseInit {
  status?: number
  body?: unknown
}

function _mockFetchOnce(init: MockResponseInit = {}): void {
  const status = init.status ?? 200
  const body = init.body ?? {}
  const resp = new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
  vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(resp as unknown as Response)
}

describe('createApiTaskService().replyToAgentDecision envelope contract', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('overwrite_target success: 200 + envelope.status=success + data.status=target_conflict_overwritten must not throw', async () => {
    _mockFetchOnce({
      status: 200,
      body: {
        status: 'success',
        data: {
          run_id: 'run-1',
          status: 'target_conflict_overwritten',
          message_count: 1,
          tool_call_count: 0,
          error_message: null,
        },
        messages: [
          {
            level: 'info',
            code: 'agent_continue_target_conflict_overwritten',
            text: 'Agent run run-1: target_conflict_overwritten',
          },
        ],
        meta: {},
      },
    })

    const svc = createApiTaskService()
    let captured: unknown = null
    let thrown: unknown = null
    try {
      captured = await svc.replyToAgentDecision('decision-1', 'overwrite_target', undefined, 'user')
    } catch (err) {
      thrown = err
    }

    expect(thrown).toBeNull()
    expect(captured).not.toBeNull()
    const envelope = captured as { status: string; data: { status: string } }
    expect(envelope.status).toBe('success')
    expect(envelope.data.status).toBe('target_conflict_overwritten')
  })

  it('adds the CSRF cookie to every state-changing request', async () => {
    document.cookie = 'media_pilot_csrf=csrf-token-123; path=/'
    _mockFetchOnce({
      body: { status: 'success', data: { status: 'completed' }, messages: [], meta: {} },
    })

    await createApiTaskService().replyToAgentDecision(
      'decision-csrf',
      'continue',
      undefined,
      'user',
    )

    const init = vi.mocked(fetch).mock.calls[0][1]
    expect(new Headers(init?.headers).get('X-CSRF-Token')).toBe('csrf-token-123')
    expect(init?.credentials).toBe('same-origin')
  })

  it('broadcasts session expiry on a 401 response', async () => {
    const expired = vi.fn()
    window.addEventListener('media-pilot:unauthorized', expired, { once: true })
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(null, { status: 401 }))

    await apiFetch('/api/v1/tasks')

    expect(expired).toHaveBeenCalledOnce()
  })

  it('aborts requests started by the current browser session on logout', async () => {
    let captured: AbortSignal | null = null
    vi.spyOn(globalThis, 'fetch').mockImplementationOnce((_input, init) => {
      captured = init?.signal as AbortSignal
      return Promise.resolve(new Response('{}', { status: 200 }))
    })
    await apiFetch('/api/v1/tasks')

    abortAuthenticatedRequests()

    expect((captured as unknown as AbortSignal).aborted).toBe(true)
  })

  it('cancel_publish success: data.status=target_conflict_cancelled must not throw', async () => {
    _mockFetchOnce({
      status: 200,
      body: {
        status: 'success',
        data: {
          run_id: 'run-1',
          status: 'target_conflict_cancelled',
          message_count: 1,
          tool_call_count: 0,
          error_message: null,
        },
        messages: [
          {
            level: 'info',
            code: 'agent_continue_target_conflict_cancelled',
            text: 'Agent run run-1: target_conflict_cancelled',
          },
        ],
        meta: {},
      },
    })

    const svc = createApiTaskService()
    const captured = await svc.replyToAgentDecision('decision-2', 'cancel_publish', undefined, 'user')
    const envelope = captured as { status: string; data: { status: string } }
    expect(envelope.status).toBe('success')
    expect(envelope.data.status).toBe('target_conflict_cancelled')
  })

  it('source_cleanup_kept success: data.status=source_cleanup_kept must not throw', async () => {
    _mockFetchOnce({
      status: 200,
      body: {
        status: 'success',
        data: {
          run_id: 'run-1',
          status: 'source_cleanup_kept',
          message_count: 1,
          tool_call_count: 0,
          error_message: null,
        },
        messages: [
          {
            level: 'info',
            code: 'agent_continue_source_cleanup_kept',
            text: 'Agent run run-1: source_cleanup_kept',
          },
        ],
        meta: {},
      },
    })

    const svc = createApiTaskService()
    const captured = await svc.replyToAgentDecision('decision-3', 'keep_input', undefined, 'user')
    const envelope = captured as { status: string; data: { status: string } }
    expect(envelope.status).toBe('success')
    expect(envelope.data.status).toBe('source_cleanup_kept')
  })

  it('manual_selection_cancelled success must not throw', async () => {
    _mockFetchOnce({
      status: 200,
      body: {
        status: 'success',
        data: {
          run_id: 'run-1',
          status: 'manual_selection_cancelled',
          message_count: 1,
          tool_call_count: 0,
          error_message: null,
        },
        messages: [
          {
            level: 'info',
            code: 'agent_continue_manual_selection_cancelled',
            text: 'Agent run run-1: manual_selection_cancelled',
          },
        ],
        meta: {},
      },
    })

    const svc = createApiTaskService()
    const captured = await svc.replyToAgentDecision('decision-4', 'cancel', undefined, 'user')
    const envelope = captured as { status: string; data: { status: string } }
    expect(envelope.status).toBe('success')
    expect(envelope.data.status).toBe('manual_selection_cancelled')
  })

  it('overwrite failure with structured code: 422 + envelope.status=error + code must throw ApiError with retryable', async () => {
    _mockFetchOnce({
      status: 422,
      body: {
        status: 'error',
        data: {},
        messages: [
          {
            level: 'error',
            code: 'no_main_video',
            text: '任务输入目录中没有可识别的主视频文件',
          },
        ],
        meta: { retryable: true },
      },
    })

    const svc = createApiTaskService()
    let thrown: unknown = null
    try {
      await svc.replyToAgentDecision('decision-5', 'overwrite_target', undefined, 'user')
    } catch (err) {
      thrown = err
    }

    expect(thrown).toBeInstanceOf(ApiError)
    const err = thrown as ApiError
    expect(err.code).toBe('no_main_video')
    expect(err.status).toBe(422)
    expect(err.retryable).toBe(true)
    expect(err.message).toContain('任务输入目录中没有可识别的主视频文件')
  })

  it('db_locked: 409 + retryable must throw ApiError (so onError toast fires)', async () => {
    _mockFetchOnce({
      status: 409,
      body: {
        status: 'error',
        data: {},
        messages: [
          {
            level: 'error',
            code: 'db_locked',
            text: '数据库暂时被占用，请稍后重试',
          },
        ],
        meta: { retryable: true },
      },
    })

    const svc = createApiTaskService()
    let thrown: unknown = null
    try {
      await svc.replyToAgentDecision('decision-6', 'cancel_publish', undefined, 'user')
    } catch (err) {
      thrown = err
    }

    expect(thrown).toBeInstanceOf(ApiError)
    const err = thrown as ApiError
    expect(err.code).toBe('db_locked')
    expect(err.status).toBe(409)
    expect(err.retryable).toBe(true)
  })
})
