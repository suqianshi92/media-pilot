/**
 * 真实 API client — 调用后端 /api/v1 端点
 *
 * 与 mock service 接口一致，通过 VITE_API_BASE_URL 配置后端地址。
 */

import type { ApiEnvelope, ApiListEnvelope } from '@/types/api'
import type {
  AgentDecisionDto,
  AgentMessageDto,
  AgentRunResult,
  AgentToolCallDto,
  DeleteInputPreview,
  DownloadDetail,
  DownloadTaskSummary,
  FlowSummary,
  ManualSelectResponse,
  ResearchResponseData,
  RevokePublishCheckDto,
  RevokePublishResultDto,
  TaskDetailDto,
  TaskStatusSummary,
  TaskSummary,
} from '@/types/task'
import { createSettingsService } from '@/services/settings-service'

export type TaskFilter = TaskSummary['status_summary']['status'] | 'all'

// /api/v1/flows 的 filter 参数与后端 VALID_FILTERS 保持一致.
export type FlowFilter = 'all' | 'waiting_user' | 'processing' | 'library_import_complete' | 'failed' | 'no_metadata'

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''

/** 通用错误类型 — 在 Error 上挂 ``code`` / ``status`` / ``retryable``,
 * 让调用方 (mutations 的 onError) 可以区分 db_locked / invalid_video_source
 * / movie_write_failed 等场景, 决定是否自动重试 vs 提示用户操作. */
export class ApiError extends Error {
  code?: string
  status?: number
  retryable: boolean
  details?: unknown
  constructor(
    message: string,
    opts: {
      code?: string
      status?: number
      retryable?: boolean
      details?: unknown
    } = {},
  ) {
    super(message)
    this.name = 'ApiError'
    this.code = opts.code
    this.status = opts.status
    this.retryable = Boolean(opts.retryable)
    this.details = opts.details
  }
}

async function _envelopeToError(
  body: any,
  fallbackStatus: number,
): Promise<ApiError> {
  const msg = body?.messages?.[0]
  return new ApiError(
    msg?.text ?? 'unknown error',
    {
      code: msg?.code,
      retryable: Boolean(body?.meta?.retryable),
      details: msg?.details,
      status: fallbackStatus,
    },
  )
}

async function apiGet<T>(path: string, params?: Record<string, string>): Promise<T> {
  const url = new URL(`${BASE_URL}/api/v1${path}`, window.location.origin)
  if (params) {
    Object.entries(params).forEach(([k, v]) => {
      if (v) url.searchParams.set(k, v)
    })
  }
  const resp = await fetch(url.toString())
  if (!resp.ok) {
    // 5xx / 4xx — 尝试解析 ApiEnvelope, 失败则用 HTTP 状态码兜底.
    let body: any = null
    try {
      body = await resp.json()
    } catch {
      throw new ApiError(`HTTP ${resp.status}`, { status: resp.status })
    }
    throw await _envelopeToError(body, resp.status)
  }
  const body = await resp.json()
  if (body?.status === 'error') {
    throw await _envelopeToError(body, resp.status)
  }
  return body as T
}

async function apiPost<T>(path: string, data: unknown): Promise<T> {
  const url = `${BASE_URL}/api/v1${path}`
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!resp.ok) {
    let body: any = null
    try {
      body = await resp.json()
    } catch {
      throw new ApiError(`HTTP ${resp.status}`, { status: resp.status })
    }
    throw await _envelopeToError(body, resp.status)
  }
  const body = await resp.json()
  if (body?.status === 'error') {
    throw await _envelopeToError(body, resp.status)
  }
  return body as T
}

export function createApiTaskService() {
  return {
    reset() {
      // no-op: real API doesn't need reset
    },


    async listDownloads(
      params: { page?: number; page_size?: number } = {},
    ): Promise<ApiListEnvelope<DownloadTaskSummary>> {
      const query: Record<string, string> = {}
      if (params.page) query.page = String(params.page)
      if (params.page_size) query.page_size = String(params.page_size)
      return apiGet<ApiListEnvelope<DownloadTaskSummary>>('/downloads', query)
    },

    async listTasks(
      params: { status?: TaskFilter; page?: number; page_size?: number } = {},
    ): Promise<ApiListEnvelope<TaskSummary>> {
      const query: Record<string, string> = {}
      if (params.status && params.status !== 'all') {
        query.status = params.status
      }
      if (params.page) query.page = String(params.page)
      if (params.page_size) query.page_size = String(params.page_size)
      return apiGet<ApiListEnvelope<TaskSummary>>('/tasks', query)
    },

    async listFlows(
      params: { filter?: FlowFilter; page?: number; page_size?: number } = {},
    ): Promise<ApiListEnvelope<FlowSummary>> {
      const query: Record<string, string> = {}
      if (params.filter && params.filter !== 'all') {
        query.filter = params.filter
      }
      if (params.page) query.page = String(params.page)
      if (params.page_size) query.page_size = String(params.page_size)
      return apiGet<ApiListEnvelope<FlowSummary>>('/flows', query)
    },

    async getTaskDetail(taskId: string): Promise<ApiEnvelope<TaskDetailDto>> {
      return apiGet<ApiEnvelope<TaskDetailDto>>(`/tasks/${taskId}`)
    },

    async getTaskStatus(taskId: string): Promise<ApiEnvelope<TaskStatusSummary>> {
      return apiGet<ApiEnvelope<TaskStatusSummary>>(`/tasks/${taskId}/status`)
    },

    async researchCandidates(
      taskId: string,
      keyword: string,
      scope: string = 'all',
    ): Promise<ApiEnvelope<ResearchResponseData>> {
      return apiPost<ApiEnvelope<ResearchResponseData>>(
        `/tasks/${taskId}/research`,
        { keyword, scope },
      )
    },

    // 注: 旧 confirmCandidate /tasks/{id}/confirmation 端点已下线；
    // 候选选择改走 manualSelect + AgentDecisionRequest 路径。


    async getProfileOptions(): Promise<Array<{ value: string; label: string; disabled?: boolean; reason?: string }>> {
      try {
        const settingsSvc = createSettingsService()
        const response = await settingsSvc.getSettings()
        const profiles = response.data.available_profiles

        const options: Array<{ value: string; label: string; disabled?: boolean; reason?: string }> = [
          { value: 'all', label: '全部启用来源' },
        ]

        for (const p of profiles) {
          if (p.enabled && p.supported) {
            options.push({ value: p.value, label: p.label, disabled: false })
          } else if (!p.supported) {
            options.push({ value: p.value, label: p.label, disabled: true, reason: '未配置 API Key' })
          } else {
            options.push({ value: p.value, label: p.label, disabled: true, reason: '未启用' })
          }
        }

        return options
      } catch {
        // 设置加载失败时，只提供 all
        return [{ value: 'all', label: '全部启用来源' }]
      }
    },

    /** 轮询单个任务状态 — 用于处理中任务自动刷新 */
    async tick(taskId: string): Promise<{ task: TaskSummary } | null> {
      void taskId
      // 真实 API 通过 getTaskStatus 或列表 refetch 刷新，tick 仅占位
      return null
    },

    // ── 后台 Agent 状态 ──

    async getBackgroundStatus(): Promise<ApiEnvelope<import('@/types/agent-background').AgentBackgroundStatusData>> {
      return apiGet<ApiEnvelope<import('@/types/agent-background').AgentBackgroundStatusData>>('/agent-background/status')
    },

    // ── 资源发现 ──

    async searchResources(inputText: string, searchType: string = 'all', skipIntent: boolean = false): Promise<ApiEnvelope<import('@/types/discovery').ResourceSearchData>> {
      return apiPost('/resource-discovery/search', { input_text: inputText, search_type: searchType, skip_intent: skipIntent })
    },

    async submitDownload(params: {
      candidate_token: string
      title: string
      source: string
      indexer: string
      preselected_profile?: string | null
      preselected_provider?: string | null
      preselected_external_id?: string | null
    }): Promise<ApiEnvelope<{ title: string; info_hash?: string }>> {
      return apiPost('/resource-discovery/download', params)
    },

    // ── 撤销发布 ──

    async getRevokePublishCheck(taskId: string): Promise<ApiEnvelope<RevokePublishCheckDto>> {
      return apiGet<ApiEnvelope<RevokePublishCheckDto>>(`/tasks/${taskId}/revoke-publish`)
    },

    async executeRevokePublish(taskId: string): Promise<ApiEnvelope<RevokePublishResultDto>> {
      return apiPost<ApiEnvelope<RevokePublishResultDto>>(`/tasks/${taskId}/revoke-publish`, {})
    },

    // ── 下载重试 ──

    async retryDownloadSync(downloadId: string): Promise<ApiEnvelope<{ synced: number; failed: number; skipped: number }>> {
      return apiPost<ApiEnvelope<{ synced: number; failed: number; skipped: number }>>(`/downloads/${downloadId}/retry-sync`, {})
    },

    // ── 下载详情与控制 ──

    async getDownloadDetail(downloadId: string): Promise<ApiEnvelope<DownloadDetail>> {
      return apiGet<ApiEnvelope<DownloadDetail>>(`/downloads/${downloadId}`)
    },

    async pauseDownload(downloadId: string): Promise<ApiEnvelope<{ download_id: string; status: string }>> {
      return apiPost<ApiEnvelope<{ download_id: string; status: string }>>(`/downloads/${downloadId}/pause`, {})
    },

    async resumeDownload(downloadId: string): Promise<ApiEnvelope<{ download_id: string; status: string }>> {
      return apiPost<ApiEnvelope<{ download_id: string; status: string }>>(`/downloads/${downloadId}/resume`, {})
    },

    async refreshDownload(downloadId: string): Promise<ApiEnvelope<{ synced: number; failed: number; skipped: number }>> {
      return apiPost<ApiEnvelope<{ synced: number; failed: number; skipped: number }>>(`/downloads/${downloadId}/refresh`, {})
    },

    // ── 删除 ──

    async deleteDownload(downloadId: string): Promise<ApiEnvelope<{ task_id: string; deleted: boolean; qb_deleted: boolean | null; qb_error: string | null; files_cleaned: string[] }>> {
      return apiPost<ApiEnvelope<{ task_id: string; deleted: boolean; qb_deleted: boolean | null; qb_error: string | null; files_cleaned: string[] }>>(`/downloads/${downloadId}/delete`, {})
    },

    async deleteTask(taskId: string): Promise<ApiEnvelope<{ task_id: string; deleted: boolean; qb_deleted: boolean | null; qb_error: string | null; files_cleaned: string[] }>> {
      return apiPost<ApiEnvelope<{ task_id: string; deleted: boolean; qb_deleted: boolean | null; qb_error: string | null; files_cleaned: string[] }>>(`/tasks/${taskId}/delete`, {})
    },

    // ── Agent ──

    async listAgentMessages(taskId: string): Promise<ApiEnvelope<AgentMessageDto[]>> {
      return apiGet<ApiEnvelope<AgentMessageDto[]>>(`/tasks/${taskId}/agent-messages`)
    },

    async listAgentDecisions(taskId: string): Promise<ApiEnvelope<AgentDecisionDto[]>> {
      return apiGet<ApiEnvelope<AgentDecisionDto[]>>(`/tasks/${taskId}/agent-decisions`)
    },

    async replyToAgentDecision(
      decisionId: string,
      optionId?: string,
      freeText?: string,
      decidedBy?: string,
    ): Promise<ApiEnvelope<AgentRunResult>> {
      const body: Record<string, unknown> = { decided_by: decidedBy ?? 'user' }
      if (optionId) body.option_id = optionId
      if (freeText) body.free_text = freeText
      return apiPost<ApiEnvelope<AgentRunResult>>(`/agent-decisions/${decisionId}/reply`, body)
    },

    async listAgentToolCalls(taskId: string): Promise<ApiEnvelope<AgentToolCallDto[]>> {
      return apiGet<ApiEnvelope<AgentToolCallDto[]>>(`/tasks/${taskId}/agent-tool-calls`)
    },

    async createAgentRun(taskId: string): Promise<ApiEnvelope<AgentRunResult>> {
      return apiPost<ApiEnvelope<AgentRunResult>>(`/tasks/${taskId}/agent-runs`, {})
    },

    /**
     * 卡住 Agent 恢复入口 — 与普通 createAgentRun (重试) 严格区分.
     *
     * 用法: 仅在 taskStatus==='agent_running' + run_status==='active' + 无
     * pending decision 时由 AgentPanel 触发. 后端校验链失败 (waiting_user
     * / pending / no active run / 终态 / task 不存在 / db_locked) 抛 ApiError,
     * 状态码透传自后端 JSONResponse (404/409).
     */
    async recoverStuckAgentRun(taskId: string): Promise<ApiEnvelope<{ run_id: string; status: string }>> {
      return apiPost<ApiEnvelope<{ run_id: string; status: string }>>(
        `/tasks/${taskId}/agent-runs/recover-stuck`,
        {},
      )
    },

    async sendFreeformMessage(
      taskId: string, message: string,
    ): Promise<ApiEnvelope<AgentRunResult>> {
      return apiPost<ApiEnvelope<AgentRunResult>>(
        `/tasks/${taskId}/agent-runs`, { message },
      )
    },

    // ── 人工辅助检索 ──

    async manualSelect(taskId: string, params: {
      provider: string
      provider_id: string
      title: string
      year?: number | null
      original_title?: string | null
      media_type?: string
    }): Promise<ApiEnvelope<ManualSelectResponse>> {
      return apiPost<ApiEnvelope<ManualSelectResponse>>(`/tasks/${taskId}/manual-select`, params)
    },

    async publishWithoutMetadata(taskId: string, libraryTarget: 'movie' | 'adult'): Promise<ApiEnvelope<{
      status: string
      metadata_status: 'unknown' | 'complete' | 'none'
      final_target_dir?: string | null
      final_target_file?: string | null
      cleanup_decision_requested?: boolean
      decision_id?: string | null
    }>> {
      return apiPost<ApiEnvelope<{
        status: string
        metadata_status: 'unknown' | 'complete' | 'none'
        final_target_dir?: string | null
        final_target_file?: string | null
        cleanup_decision_requested?: boolean
        decision_id?: string | null
      }>>(`/tasks/${taskId}/publish-without-metadata`, {
        confirmed: true,
        library_target: libraryTarget,
      })
    },

    // ── 删除任务输入 ──

    async getDeleteInputPreview(taskId: string): Promise<ApiEnvelope<DeleteInputPreview>> {
      return apiGet<ApiEnvelope<DeleteInputPreview>>(`/tasks/${taskId}/delete-input/preview`)
    },

    async executeDeleteInput(taskId: string): Promise<ApiEnvelope<{ status: string; outcome: string }>> {
      return apiPost<ApiEnvelope<{ status: string; outcome: string }>>(`/tasks/${taskId}/delete-input`, { confirmed: true })
    },
  }
}
