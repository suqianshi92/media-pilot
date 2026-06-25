import { beforeEach, describe, expect, it } from 'vitest'

import { createMockTaskService } from './service'

describe('mock task service', () => {
  beforeEach(() => {
    createMockTaskService().reset()
  })

  it('filters tasks by status', async () => {
    const service = createMockTaskService()

    const waitingList = await service.listTasks({ status: 'waiting_user' })

    expect(waitingList.status).toBe('success')
    expect(waitingList.data.items.length).toBeGreaterThan(0)
    expect(
      waitingList.data.items.every(
        (task) => task.status_summary.status === 'waiting_user',
      ),
    ).toBe(true)
  })

  it('returns task details without confirmation_request field', async () => {
    const service = createMockTaskService()

    const bdmvDetail = await service.getTaskDetail('task-bdmv-manual')
    const providerFailureDetail = await service.getTaskDetail('task-provider-failed')

    expect(bdmvDetail.status).toBe('success')
    expect((bdmvDetail.data as unknown as Record<string, unknown>).confirmation_request).toBeUndefined()
    expect(bdmvDetail.data.source_selection?.bdmv_detected).toBe(true)

    expect(providerFailureDetail.status).toBe('success')
    expect((providerFailureDetail.data as unknown as Record<string, unknown>).confirmation_request).toBeUndefined()
  })

  it('listFlows 返回 ingest + download-only 三类 flow, route_target 分流', async () => {
    const service = createMockTaskService()
    const resp = await service.listFlows()
    expect(resp.status).toBe('success')
    const items = resp.data.items
    expect(items.length).toBeGreaterThan(0)
    // 每条 flow 必须有前缀 id
    for (const f of items) {
      expect(f.id.startsWith('ingest:') || f.id.startsWith('download:')).toBe(true)
    }
    // route_target 必须分流
    const ingestFlows = items.filter((f) => f.route_target === 'task_detail')
    const downloadFlows = items.filter((f) => f.route_target === 'download_detail')
    for (const f of ingestFlows) {
      expect(f.ingest_task_id).toBeTruthy()
      expect(f.flow_type === 'managed_download' || f.flow_type === 'external_import').toBe(true)
    }
    for (const f of downloadFlows) {
      expect(f.ingest_task_id).toBeNull()
      expect(f.flow_type).toBe('download_only')
      expect(f.download_task_id).toBeTruthy()
    }
    // linked download 不得重复
    const dlIds = new Set<string>()
    for (const f of items) {
      if (f.download_task_id) {
        expect(dlIds.has(f.download_task_id)).toBe(false)
        dlIds.add(f.download_task_id)
      }
    }
    expect(resp.meta.total).toBe(items.length)
  })

  it('listFlows filter=processing 只返回 processing-adjacent flow', async () => {
    const service = createMockTaskService()
    const resp = await service.listFlows({ filter: 'processing' })
    const allowed = new Set([
      'agent_running', 'processing', 'queued', 'waiting_stable',
      'submitted', 'downloading', 'awaiting_sync', 'paused',
    ])
    for (const f of resp.data.items) {
      expect(allowed.has(f.total_status)).toBe(true)
    }
  })

  it('listFlows filter=waiting_user 只返回 waiting flow', async () => {
    const service = createMockTaskService()
    const resp = await service.listFlows({ filter: 'waiting_user' })
    for (const f of resp.data.items) {
      expect(f.total_status).toBe('waiting_user')
    }
  })

  it('updates search keyword and candidates when re-searching', async () => {
    const service = createMockTaskService()

    const response = await service.researchCandidates('task-no-candidates', '天气之子 2019')

    expect(response.status).toBe('success')
    expect((response.data as unknown as Record<string, unknown>).confirmation_request).toBeUndefined()
    expect(response.data.candidates[0]?.provider_id).toBe('movie:568160')
    expect(response.data.search_summary.keyword).toBe('天气之子 2019')
    expect(response.data.search_summary.total_candidates).toBeGreaterThan(0)
  })

  // 注: 旧 confirmCandidate 通道已下线。新流程下"确认候选"由 Agent
  // 决策面板或 manualSelect 承载，task-low-confidence 的 processing 推进
  // 路径不再由 mock 直接模拟；本测试删除。
})
