/**
 * api-client.recoverStuckAgentRun — 卡住 Agent 恢复服务方法测试.
 *
 * 锁定 contract:
 * - POST /api/v1/tasks/{taskId}/agent-runs/recover-stuck
 * - 成功: 200 + envelope.status=success + data={run_id, status:"active"}
 * - 失败: 409 (waiting_user / pending decision / no active run / 终态) /
 *   404 (task not found) → 抛 ApiError, code 来自 messages[0].code
 * - 与 createAgentRun (普通重试) 走不同 endpoint, 不共用 mutation
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, createApiTaskService } from '@/services/api-client'

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

describe('createApiTaskService().recoverStuckAgentRun', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('成功 ack: 200 + envelope.status=success + data.status=active', async () => {
    _mockFetchOnce({
      status: 200,
      body: {
        status: 'success',
        data: {
          run_id: 'run-recover-1',
          status: 'active',
        },
        messages: [],
        meta: {},
      },
    })

    const svc = createApiTaskService()
    const captured = await svc.recoverStuckAgentRun('task-stuck')

    expect(captured.status).toBe('success')
    expect(captured.data.run_id).toBe('run-recover-1')
    expect(captured.data.status).toBe('active')

    // endpoint 校验: 必须是 POST /api/v1/tasks/{taskId}/agent-runs/recover-stuck
    const fetchMock = vi.mocked(globalThis.fetch)
    const calledUrl = (fetchMock.mock.calls[0]?.[0] as string) ?? ''
    expect(calledUrl).toContain('/api/v1/tasks/task-stuck/agent-runs/recover-stuck')
    // 必须用 POST (恢复是 mutation, 不是 GET 查询)
    expect(fetchMock.mock.calls[0]?.[1]?.method).toBe('POST')
  })

  it('pending decision: 409 + envelope.status=error → 抛 ApiError (code=pending_decision)', async () => {
    _mockFetchOnce({
      status: 409,
      body: {
        status: 'error',
        data: {},
        messages: [
          {
            level: 'error',
            code: 'pending_decision',
            text: 'Task has pending decisions; resolve them before recovering',
          },
        ],
        meta: { retryable: false },
      },
    })

    const svc = createApiTaskService()
    let thrown: unknown = null
    try {
      await svc.recoverStuckAgentRun('task-stuck-pending')
    } catch (err) {
      thrown = err
    }

    expect(thrown).toBeInstanceOf(ApiError)
    const err = thrown as ApiError
    expect(err.status).toBe(409)
    expect(err.retryable).toBe(false)
    expect(err.code).toBe('pending_decision')
  })

  it('task not found: 404 → 抛 ApiError', async () => {
    _mockFetchOnce({
      status: 404,
      body: {
        status: 'error',
        data: {},
        messages: [
          {
            level: 'error',
            code: 'task_not_found',
            text: 'Task task-not-found not found',
          },
        ],
        meta: { retryable: false },
      },
    })

    const svc = createApiTaskService()
    let thrown: unknown = null
    try {
      await svc.recoverStuckAgentRun('task-not-found')
    } catch (err) {
      thrown = err
    }

    expect(thrown).toBeInstanceOf(ApiError)
    const err = thrown as ApiError
    expect(err.status).toBe(404)
  })

  it('db_locked: 409 + retryable=true → 抛 ApiError with retryable', async () => {
    _mockFetchOnce({
      status: 409,
      body: {
        status: 'error',
        data: {},
        messages: [
          {
            level: 'error',
            code: 'db_locked',
            text: 'db_locked: OperationalError',
          },
        ],
        meta: { retryable: true },
      },
    })

    const svc = createApiTaskService()
    let thrown: unknown = null
    try {
      await svc.recoverStuckAgentRun('task-stuck')
    } catch (err) {
      thrown = err
    }

    expect(thrown).toBeInstanceOf(ApiError)
    const err = thrown as ApiError
    expect(err.status).toBe(409)
    expect(err.retryable).toBe(true)
    expect(err.code).toBe('db_locked')
  })

  it('endpoint 与普通 createAgentRun (POST /agent-runs) 严格不同, 不共用 mutation', async () => {
    // 验证契约: recover-stuck 是独立 endpoint, 不能让 AgentPanel 误用
    // createAgentRun 当作"恢复" 入口 (避免回归到旧 issue:
    // agent_failed 重试 + agent_running 卡住恢复混成一个 mutation).
    _mockFetchOnce({
      status: 200,
      body: {
        status: 'success',
        data: { run_id: 'run-x', status: 'active' },
        messages: [],
        meta: {},
      },
    })

    const svc = createApiTaskService()
    await svc.recoverStuckAgentRun('task-1')

    const fetchMock = vi.mocked(globalThis.fetch)
    const calledUrl = (fetchMock.mock.calls[0]?.[0] as string) ?? ''
    // 绝不允许 fallback 到 /agent-runs (普通重试 endpoint)
    expect(calledUrl).not.toMatch(/\/agent-runs$/)
    // 必须是 /recover-stuck 结尾
    expect(calledUrl).toMatch(/\/agent-runs\/recover-stuck$/)
  })
})
