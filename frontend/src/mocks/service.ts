import type { ApiEnvelope, ApiListEnvelope } from '@/types/api'
import type { TaskService } from '@/services/task-service'
import type {
  AgentDecisionDto,
  AgentMessageDto,
  AgentRunResult,
  AgentToolCallDto,
  DownloadTaskSummary,
  FlowSummary,
  MetadataCandidateDto,
  ResearchResponseData,
  RevokePublishResultDto,
  TaskDetailDto,
  TaskSummary,
} from '@/types/task'

import { mockDownloadTasks, mockTaskDetails } from './tasks'

export type TaskFilter = TaskSummary['status_summary']['status'] | 'all'
export type FlowFilter = 'all' | 'waiting_user' | 'processing' | 'library_import_complete' | 'failed'

interface ListTaskParams {
  status?: TaskFilter
  page?: number
  page_size?: number
}

interface ListFlowParams {
  filter?: FlowFilter
  page?: number
  page_size?: number
}

// 后端 attention priority 顺序 (与 services/flow_list.py 一致):
// 1: waiting_user, 2: processing-adjacent, 3: failed, 4: done, 5: unknown.
const PRIORITY_1 = new Set(['waiting_user'])
const PRIORITY_2 = new Set([
  'agent_running', 'processing', 'queued', 'waiting_stable',
  'submitted', 'downloading', 'awaiting_sync', 'paused',
])
const PRIORITY_3 = new Set(['agent_failed', 'failed', 'sync_failed'])
const PRIORITY_4 = new Set([
  'library_import_complete', 'completed_pending_ingest', 'completed',
])

function flowPriority(status: string): number {
  if (PRIORITY_1.has(status)) return 1
  if (PRIORITY_2.has(status)) return 2
  if (PRIORITY_3.has(status)) return 3
  if (PRIORITY_4.has(status)) return 4
  return 5
}

function filterStatusSet(name: FlowFilter): Set<string> | null {
  if (name === 'all' || !name) return null
  if (name === 'waiting_user') return PRIORITY_1
  if (name === 'processing') return PRIORITY_2
  if (name === 'library_import_complete') return PRIORITY_4
  if (name === 'failed') return PRIORITY_3
  return null
}

// 把 mockTaskDetails (IngestTask) + mockDownloadTasks (DownloadTask) 合成
// FlowSummary 列表, 覆盖三类 flow (linked ingest / external_import /
// download_only). 仅供测试, 与后端 services/flow_list.py 语义等价.
function buildMockFlows(): FlowSummary[] {
  const flows: FlowSummary[] = []
  // 1) ingest flow (linked or external_import)
  for (const entry of mockTaskDetails) {
    const t = entry.task
    const hasDownload = t.download_task != null
    flows.push({
      id: `ingest:${t.id}`,
      flow_type: hasDownload ? 'managed_download' : 'external_import',
      route_target: 'task_detail',
      ingest_task_id: t.id,
      download_task_id: t.download_task?.id ?? null,
      total_status: t.total_status,
      metadata_status: t.metadata_status ?? 'unknown',
      title: t.title,
      year: t.year,
      media_type: t.media_type,
      can_confirm: t.can_confirm,
      file_format: t.file_format,
      source_path: t.source_path,
      created_at: t.created_at,
      updated_at: t.updated_at,
      status_summary: {
        status: t.status_summary.status,
        current_step: t.status_summary.current_step,
        failure_reason: t.status_summary.failure_reason,
        confidence: t.status_summary.confidence,
        confidence_level: t.status_summary.confidence_level,
        latest_message: t.status_summary.latest_message,
      },
      agent_status_summary: t.agent_status_summary,
      download_task: t.download_task,
    })
  }
  // 2) download-only flow (DownloadTask 没有被 ingest 引用)
  const referenced = new Set<string>(
    flows.map((f) => f.download_task_id).filter((v): v is string => v != null),
  )
  for (const dl of mockDownloadTasks) {
    if (referenced.has(dl.id)) continue
    flows.push({
      id: `download:${dl.id}`,
      flow_type: 'download_only',
      route_target: 'download_detail',
      ingest_task_id: null,
      download_task_id: dl.id,
      total_status: dl.status,
      metadata_status: 'unknown',
      title: dl.title,
      year: null,
      media_type: null,
      can_confirm: false,
      file_format: null,
      source_path: dl.save_path,
      created_at: dl.created_at,
      updated_at: dl.updated_at,
      status_summary: {
        status: dl.status,
        current_step: null,
        failure_reason: dl.error_message,
        confidence: null,
        confidence_level: 'unknown',
        latest_message: dl.error_message ?? dl.qb_state ?? dl.status,
      },
      agent_status_summary: { run_status: 'none', latest_run_id: null, pending_decision_count: 0, latest_message_summary: null },
      download_task: dl,
    })
  }
  return flows
}

interface TickResult {
  task: TaskSummary
}

const searchCandidates: MetadataCandidateDto[] = [
  {
    provider: 'tmdb',
    provider_id: 'movie:568160',
    title: '天气之子',
    original_title: '天気の子',
    year: 2019,
    media_type: 'movie',
    overview: '离家少年与拥有晴天能力的少女相遇。',
    poster_url: 'https://image.tmdb.org/t/p/w342/weathering-with-you.jpg',
    confidence: 0.96,
    match_reason: 'title_exact,rank_1',
    risk_flags: [],
    payload: {},
  },
  {
    provider: 'tmdb',
    provider_id: 'movie:1461942',
    title: '《天气之子》制作纪录片',
    original_title: null,
    year: 2020,
    media_type: 'movie',
    overview: '制作花絮纪录片。',
    poster_url: null,
    confidence: 0.54,
    match_reason: 'title_similar,rank_2',
    risk_flags: ['year_conflict'],
    payload: {},
  },
]

function clone<T>(value: T): T {
  return structuredClone(value)
}

function createSuccessEnvelope<T>(data: T): ApiEnvelope<T> {
  return {
    status: 'success',
    data,
    messages: [],
    meta: {},
  }
}

function updateTaskSummary(task: TaskDetailDto, updater: (taskSummary: TaskSummary) => void) {
  updater(task.task)
  task.task.updated_at = new Date().toISOString()
}

function findTaskOrThrow(tasks: TaskDetailDto[], taskId: string): TaskDetailDto {
  const task = tasks.find((entry) => entry.task.id === taskId)
  if (!task) {
    throw new Error(`Unknown task: ${taskId}`)
  }
  return task
}

function initialState() {
  return clone(mockTaskDetails)
}

let state = initialState()

export function createMockTaskService(): TaskService {
  // mock service 实现是手写 dict, 部分返回类型推断为 Record<string, unknown>;
  // 在不重写全部方法的前提下, 用 unknown 转换以满足 TaskService 契约.
  // 测试时不会触发强类型链路, 仅做组件依赖注入.
  return {
    reset() {
      state = initialState()
    },

    async listDownloads(
      params: { page?: number; page_size?: number } = {},
    ): Promise<ApiListEnvelope<DownloadTaskSummary>> {
      void params
      return {
        status: 'success',
        data: { items: clone(mockDownloadTasks) },
        messages: [],
        meta: { page: 1, page_size: mockDownloadTasks.length, total: mockDownloadTasks.length, filters: {} },
      }
    },

    async listTasks(params: ListTaskParams = {}): Promise<ApiListEnvelope<TaskSummary>> {
      const filter = params.status ?? 'all'
      const items = state
        .map((entry) => entry.task)
        .filter((task) => {
          if (filter === 'all') {
            return true
          }
          return task.status_summary.status === filter
        })

      return {
        status: 'success',
        data: { items: clone(items) },
        messages: [],
        meta: {
          page: 1,
          page_size: items.length,
          total: items.length,
          filters: { status: filter },
        },
      }
    },

    async listFlows(params: ListFlowParams = {}): Promise<ApiListEnvelope<FlowSummary>> {
      const filterName = params.filter ?? 'all'
      const allowed = filterStatusSet(filterName)
      const all = buildMockFlows()
      const filtered = allowed == null
        ? all
        : all.filter((f) => allowed.has(f.total_status))
      // 排序: attention priority asc, 同 priority 内 updated_at desc → created_at desc → id asc
      const sorted = [...filtered].sort((a, b) => {
        const pa = flowPriority(a.total_status)
        const pb = flowPriority(b.total_status)
        if (pa !== pb) return pa - pb
        if (a.updated_at !== b.updated_at) return a.updated_at < b.updated_at ? 1 : -1
        if (a.created_at !== b.created_at) return a.created_at < b.created_at ? 1 : -1
        if (a.id !== b.id) return a.id < b.id ? -1 : 1
        return 0
      })
      const total = sorted.length
      const page = params.page ?? 1
      const pageSize = (params.page_size ?? total) || 1
      const start = (page - 1) * pageSize
      return {
        status: 'success',
        data: { items: clone(sorted.slice(start, start + pageSize)) },
        messages: [],
        meta: { page, page_size: pageSize, total, filters: { filter: filterName } },
      }
    },

    async getTaskDetail(taskId: string): Promise<ApiEnvelope<TaskDetailDto>> {
      return createSuccessEnvelope(clone(findTaskOrThrow(state, taskId)))
    },

    async researchCandidates(
      taskId: string,
      keyword: string,
      scope: string = 'all',
    ): Promise<ApiEnvelope<ResearchResponseData>> {
      const task = findTaskOrThrow(state, taskId)

      const nextKeyword = {
        keyword,
        source: 'manual' as const,
        confidence: 0.91,
        reason: 'manual_override',
        rule_keyword: keyword,
        explanation: '人工修改关键词后重新搜索',
        quality_tokens: [],
        tokens_removed: [],
      }

      let candidates: MetadataCandidateDto[]
      let searchedProfiles: Array<{
        profile: string; label: string; provider: string;
        status: 'succeeded' | 'failed' | 'skipped';
        candidate_count: number; error_message: string | null;
      }>

      if (scope === 'tmdb_movie') {
        candidates = clone(searchCandidates).filter(c => c.provider === 'tmdb')
        searchedProfiles = [{ profile: 'tmdb_movie', label: 'TMDB Movie', provider: 'tmdb',
          status: 'succeeded', candidate_count: candidates.length, error_message: null }]
      } else if (scope === 'tmdb_show') {
        candidates = clone(searchCandidates).filter(c => c.provider === 'tmdb')
        searchedProfiles = [{ profile: 'tmdb_show', label: 'TMDB Show', provider: 'tmdb',
          status: 'succeeded', candidate_count: candidates.length, error_message: null }]
      } else if (scope === 'tpdb_adult_movie') {
        candidates = clone(searchCandidates).filter(c => c.provider === 'tpdb')
        searchedProfiles = [{ profile: 'tpdb_adult_movie', label: 'TPDB JAV', provider: 'tpdb',
          status: 'succeeded', candidate_count: candidates.length, error_message: null }]
      } else {
        candidates = clone(searchCandidates)
        searchedProfiles = [
          { profile: 'tmdb_movie', label: 'TMDB Movie', provider: 'tmdb',
            status: 'succeeded', candidate_count: searchCandidates.filter(c => c.provider === 'tmdb').length,
            error_message: null },
          { profile: 'tpdb_adult_movie', label: 'TPDB JAV', provider: 'tpdb',
            status: 'succeeded', candidate_count: searchCandidates.filter(c => c.provider === 'tpdb').length,
            error_message: null },
        ]
      }

      if (task.search_keyword) {
        task.search_keyword = nextKeyword
      }

      updateTaskSummary(task, (summary) => {
        summary.title = '天气之子'
        summary.year = 2019
        summary.media_type = 'movie'
        summary.can_confirm = true
        summary.status_summary = {
          ...summary.status_summary,
          status: 'waiting_user',
          current_step: 'metadata_detail',
          confidence: 0.91,
          confidence_level: 'high',
          latest_message: '已根据新关键词刷新候选列表',
        }
      })

      return createSuccessEnvelope({
        candidates: clone(candidates),
        search_summary: {
          keyword,
          scope: scope as 'all' | 'tmdb_movie' | 'tmdb_show' | 'tpdb_adult_movie',
          searched_profiles: searchedProfiles,
          total_candidates: candidates.length,
          kept_existing_candidates: false,
        },
      })
    },

    // 注: 旧 confirmCandidate 通道已下线；候选选择改走 manualSelect 或 AgentDecisionReply。

    // In mock mode, TPDB is disabled by default (matching settings dialog mock).
    // The test can override the real API client to simulate different TPDB states.
    async getProfileOptions(): Promise<Array<{ value: string; label: string; disabled?: boolean; reason?: string }>> {
      return [
        { value: 'all', label: '全部启用来源' },
        { value: 'tmdb_movie', label: 'TMDB Movie', disabled: false },
        { value: 'tmdb_show', label: 'TMDB Show', disabled: false },
        { value: 'tpdb_adult_movie', label: 'TPDB JAV', disabled: true, reason: '未配置 TPDB API Key' },
      ]
    },

    async tick(taskId: string): Promise<TickResult | null> {
      const task = findTaskOrThrow(state, taskId)
      const status = task.task.status_summary.status

      if (status === 'queued') {
        updateTaskSummary(task, (summary) => {
          summary.status_summary = {
            ...summary.status_summary,
            status: 'processing',
            current_step: 'metadata_detail',
            latest_message: '后台任务开始获取详情并写入媒体库',
          }
        })
        return { task: clone(task.task) }
      }

      if (status === 'processing') {
        task.write_result = {
          status: 'succeeded',
          failure_reason: null,
          warnings: [],
          written_paths: [
            '/data/library/movies/天气之子 (2019)/天气之子 (2019).mkv',
            '/data/library/movies/天气之子 (2019)/天气之子 (2019).nfo',
          ],
        }
        updateTaskSummary(task, (summary) => {
          summary.status_summary = {
            ...summary.status_summary,
            status: 'library_import_complete',
            current_step: 'library_import_complete',
            latest_message: '后台任务已完成媒体入库',
          }
        })
        return { task: clone(task.task) }
      }

      return null
    },

    // ── 资源发现 ──

    async searchResources(inputText: string, searchType: string = 'all') {
      void inputText
      void searchType
      // 模拟搜索延迟
      await new Promise((r) => setTimeout(r, 300))
      return {
        status: 'success' as const,
        data: {
          candidates: [
            {
              candidate_token: 'mock_token_abc123',
              title: '[TGx] Weathering With You 2019 1080p BluRay x264',
              indexer: 'TorrentGalaxy',
              source: 'prowlarr',
              size_bytes: 2147483648,
              seeders: 42,
              leechers: 3,
              publish_date: '2026-05-01T12:00:00Z',
              download_count: 150,
              category: '',
              match_reason: '',
              downloadable: true,
              relevance_score: 0.85,
              relevance_level: 'high' as const,
              match_reasons: ['匹配片名「天气之子」', '匹配英文名「Weathering」'],
          release_tags: {
            resolutions: ['1080p'],
            sources: ['BluRay'],
            codecs: ['AVC'],
            hdr_tags: [],
            audio_tags: [],
          },
          display_tags: ['1080p', 'BluRay', 'AVC'],
            },
            {
              candidate_token: 'mock_token_def456',
              title: '天气之子 2019 1080p WEB-DL x264',
              indexer: 'Nyaa',
              source: 'prowlarr',
              size_bytes: 1887436800,
              seeders: 15,
              leechers: 2,
              publish_date: '2026-04-28T08:00:00Z',
              download_count: 80,
              category: '',
              match_reason: '',
              downloadable: true,
              relevance_score: 0.65,
              relevance_level: 'high' as const,
              match_reasons: ['匹配片名「天气之子」'],
              release_tags: {
                resolutions: ['1080p'],
                sources: ['WEB-DL'],
                codecs: ['AVC'],
                hdr_tags: [],
                audio_tags: [],
              },
              display_tags: ['1080p', 'WEB-DL', 'AVC'],
            },
          ],
          query_used: '天气之子 1080p',
          search_type: 'movie',
          source: 'prowlarr',
          message: '找到 2 个候选',
          intent: {
            query_text: '天气之子',
            search_type: 'movie',
            title_candidates: ['天气之子'],
            resource_keywords: ['天气之子 1080p', 'Weathering With You 1080p'],
            profile_hint: 'tmdb_movie',
            preferred_title_candidates: ['天气之子'],
            adult_identifier_candidates: [],
            resource_search_keywords: ['Weathering With You 1080p'],
            reason: '用户请求动画电影，分析为普通电影搜索',
            preferred_resolutions: ['1080p'],
            preferred_sources: [],
            preferred_video_codecs: [],
            preferred_hdr_tags: [],
            preferred_audio_tags: [],
          },
        },
        messages: [] as { level: string; code: string; text: string }[],
        meta: {},
      }
    },

    async submitDownload(params: { candidate_token: string; title: string; source: string; indexer: string }) {
      await new Promise((r) => setTimeout(r, 500))
      return {
        status: 'success' as const,
        data: { title: params.title, info_hash: 'abc123def456' },
        messages: [{ level: 'info' as const, code: 'submitted', text: `已提交到 qBittorrent："${params.title}"` }],
        meta: {},
      }
    },

    // ── 撤销发布 ──

    async getRevokePublishCheck(taskId: string) {
      const task = findTaskOrThrow(state, taskId)
      const publishDir = task.write_plan?.target_dir ?? null
      const sourcePath = task.task.source_path

      // 模拟三种场景
      const isComplex = task.source_selection?.bdmv_detected ?? false
      const sourceExists = !sourcePath.includes('已缺失')

      let allowed = true
      let outcomeDescription: string

      if (task.task.status_summary.status !== 'library_import_complete') {
        allowed = false
        outcomeDescription = `任务当前状态为 ${task.task.status_summary.status}，仅已完成入库的任务可撤销发布`
      } else if (isComplex) {
        outcomeDescription = (
          'BDMV / 复杂结构：撤销后将删除发布目录并删除任务关联业务数据，'
          + '当前不支持回到人工确认重处理'
        )
      } else if (!sourceExists) {
        outcomeDescription = '主文件已缺失：撤销后将删除发布目录并删除任务关联业务数据'
      } else {
        outcomeDescription = (
          '主文件仍存在：撤销后将删除发布目录，任务回到人工确认状态，'
          + '可重新检索、选择候选并再次发布'
        )
      }

      return createSuccessEnvelope({
        allowed,
        publish_dir: publishDir,
        source_file_exists: sourceExists,
        is_complex_structure: isComplex,
        outcome_description: outcomeDescription,
      })
    },

    // ── 下载重试 ──

    async retryDownloadSync(downloadId: string): Promise<ApiEnvelope<{ synced: number; failed: number; skipped: number }>> {
      const dl = mockDownloadTasks.find((d) => d.id === downloadId)
      if (!dl || dl.ingest_task_id != null) {
        return createSuccessEnvelope({ synced: 0, failed: 0, skipped: 1 })
      }
      dl.status = 'downloading'
      dl.error_message = null
      dl.progress = 0.01
      return createSuccessEnvelope({ synced: 1, failed: 0, skipped: 0 })
    },

    async pauseDownload(downloadId: string): Promise<ApiEnvelope<{ download_id: string; status: string }>> {
      const dl = mockDownloadTasks.find((d) => d.id === downloadId)
      if (dl) dl.status = 'paused'
      return createSuccessEnvelope({ download_id: downloadId, status: 'paused' })
    },

    async resumeDownload(downloadId: string): Promise<ApiEnvelope<{ download_id: string; status: string }>> {
      const dl = mockDownloadTasks.find((d) => d.id === downloadId)
      if (dl) dl.status = 'awaiting_sync'
      return createSuccessEnvelope({ download_id: downloadId, status: 'awaiting_sync' })
    },

    async refreshDownload(downloadId: string): Promise<ApiEnvelope<{ synced: number; failed: number; skipped: number }>> {
      const dl = mockDownloadTasks.find((d) => d.id === downloadId)
      if (dl) dl.progress = Math.min(dl.progress + 0.05, 1.0)
      return createSuccessEnvelope({ synced: 1, failed: 0, skipped: 0 })
    },

    async getDownloadDetail(downloadId: string): Promise<ApiEnvelope<Record<string, unknown>>> {
      const dl = mockDownloadTasks.find((d) => d.id === downloadId)
      if (!dl) throw new Error('下载任务不存在')
      return createSuccessEnvelope({
        id: dl.id,
        title: dl.title,
        source: dl.source,
        qb_hash: dl.qb_hash,
        save_path: dl.save_path,
        content_path: dl.content_path,
        progress: dl.progress,
        download_speed_bytes_per_second: dl.download_speed_bytes_per_second,
        upload_speed_bytes_per_second: dl.upload_speed_bytes_per_second,
        seeders: dl.seeders,
        leechers: dl.leechers,
        connections: dl.connections,
        qb_state: dl.qb_state,
        status: dl.status,
        error_message: dl.error_message,
        ingest_task_id: dl.ingest_task_id,
        preselected_metadata_profile: null,
        preselected_metadata_provider: null,
        preselected_metadata_external_id: null,
        created_at: dl.created_at,
        updated_at: dl.updated_at,
      })
    },

    async executeRevokePublish(taskId: string): Promise<ApiEnvelope<RevokePublishResultDto>> {
      // Re-check preconditions
      const checkResult = await (this as unknown as { getRevokePublishCheck: (id: string) => Promise<ApiEnvelope<unknown>> }).getRevokePublishCheck(taskId)
      const check = (checkResult as ApiEnvelope<Record<string, unknown>>).data as Record<string, unknown>
      if (!check.allowed) {
        throw new Error(check.outcome_description as string)
      }

      const isComplex = check.is_complex_structure as boolean
      const sourceExists = check.source_file_exists as boolean

      if (isComplex || !sourceExists) {
        // Delete task from mock state
        state = state.filter((entry) => entry.task.id !== taskId)
        return createSuccessEnvelope<RevokePublishResultDto>({
          status: 'deleted',
          outcome: '已删除发布目录与任务业务数据',
          decision_id: undefined,
        })
      }

      // Back to waiting_user with Agent decision
      const task = findTaskOrThrow(state, taskId)
      updateTaskSummary(task, (summary) => {
        summary.status_summary = {
          ...summary.status_summary,
          status: 'waiting_user',
          current_step: 'post_revoke_reingest' as import('@/types/task').TaskStep,
          failure_reason: 'revoke_publish_source_available',
          latest_message: '发布已撤销，等待用户选择后续操作',
        }
        summary.can_confirm = false
        summary.agent_status_summary = {
          run_status: 'waiting_user',
          latest_run_id: 'run-mock-post-revoke',
          pending_decision_count: 1,
          latest_message_summary: '请选择撤回后操作',
        }
      })

      return createSuccessEnvelope<RevokePublishResultDto>({
        status: 'waiting_user',
        outcome: '发布目录已删除，任务等待用户选择后续操作',
        decision_id: 'decision-mock-post-revoke',
      })
    },

    // ── 删除 ──

    async deleteDownload(downloadId: string): Promise<ApiEnvelope<{ task_id: string; deleted: boolean; qb_deleted: boolean | null; qb_error: string | null; files_cleaned: string[] }>> {
      const idx = mockDownloadTasks.findIndex((d) => d.id === downloadId)
      if (idx === -1) {
        throw new Error('下载任务不存在')
      }
      mockDownloadTasks.splice(idx, 1)
      return createSuccessEnvelope({
        task_id: downloadId,
        deleted: true,
        qb_deleted: true,
        qb_error: null,
        files_cleaned: ['/data/downloads/test-file.mkv'],
      })
    },

    async deleteTask(taskId: string): Promise<ApiEnvelope<{ task_id: string; deleted: boolean; qb_deleted: boolean | null; qb_error: string | null; files_cleaned: string[] }>> {
      const task = findTaskOrThrow(state, taskId)
      if (task.task.status_summary.status === 'library_import_complete') {
        throw new Error('已发布完成的任务不允许删除，请先撤销发布')
      }
      state = state.filter((entry) => entry.task.id !== taskId)
      return createSuccessEnvelope({
        task_id: taskId,
        deleted: true,
        qb_deleted: null,
        qb_error: null,
        files_cleaned: ['/data/downloads/test-source.mkv'],
      })
    },

    // ── Agent ──

    async listAgentMessages(taskId: string): Promise<ApiEnvelope<AgentMessageDto[]>> {
      const messagesByTask: Record<string, AgentMessageDto[]> = {
        'task-multiple-candidates': [
          {
            id: 'msg-1',
            run_id: 'run-mock-1',
            role: 'user',
            content: '请分析任务并确认元数据',
            tool_calls: null,
            tool_call_id: null,
            tool_name: null,
            created_at: '2026-05-08T10:00:00+08:00',
          },
          {
            id: 'msg-2',
            run_id: 'run-mock-1',
            role: 'assistant',
            content: '我已检索到以下候选元数据，请确认：',
            tool_calls: null,
            tool_call_id: null,
            tool_name: null,
            created_at: '2026-05-08T10:00:05+08:00',
          },
          {
            id: 'msg-3',
            run_id: 'run-mock-1',
            role: 'assistant',
            content: null,
            tool_calls: [
              {
                id: 'call_1',
                type: 'function',
                function: { name: 'search_metadata', arguments: '{"keyword":"天气之子"}' },
              },
              {
                id: 'call_2',
                type: 'function',
                function: { name: 'search_metadata', arguments: '{"keyword":"天気の子"}' },
              },
            ],
            tool_call_id: null,
            tool_name: null,
            created_at: '2026-05-08T10:00:03+08:00',
          },
          {
            id: 'msg-4',
            run_id: 'run-mock-1',
            role: 'tool',
            content: '{"candidates": [{"title": "天气之子", "year": 2019}]}',
            tool_calls: null,
            tool_call_id: 'call_1',
            tool_name: 'search_metadata',
            created_at: '2026-05-08T10:00:04+08:00',
          },
          {
            id: 'msg-5',
            run_id: 'run-mock-1',
            role: 'tool',
            content: '{"candidates": [{"title": "天気の子", "year": 2019}]}',
            tool_calls: null,
            tool_call_id: 'call_2',
            tool_name: 'search_metadata',
            created_at: '2026-05-08T10:00:04+08:00',
          },
        ],
        'task-completed': [
          {
            id: 'msg-comp-1',
            run_id: 'run-mock-3',
            role: 'user',
            content: '请分析任务并确认元数据',
            tool_calls: null,
            tool_call_id: null,
            tool_name: null,
            created_at: '2026-05-08T09:50:00+08:00',
          },
          {
            id: 'msg-comp-2',
            run_id: 'run-mock-3',
            role: 'assistant',
            content: '已完成元数据确认，元数据已写入媒体库。',
            tool_calls: null,
            tool_call_id: null,
            tool_name: null,
            created_at: '2026-05-08T09:52:00+08:00',
          },
        ],
        'task-failed-rollback': [
          {
            id: 'msg-fail-1',
            run_id: 'run-mock-4',
            role: 'user',
            content: '请分析任务并确认元数据',
            tool_calls: null,
            tool_call_id: null,
            tool_name: null,
            created_at: '2026-05-08T09:30:00+08:00',
          },
          {
            id: 'msg-fail-2',
            run_id: 'run-mock-4',
            role: 'assistant',
            content: '海报下载失败，需要人工处理。',
            tool_calls: null,
            tool_call_id: null,
            tool_name: null,
            created_at: '2026-05-08T09:31:00+08:00',
          },
        ],
      }
      return createSuccessEnvelope(messagesByTask[taskId] ?? [])
    },

    async listAgentDecisions(taskId: string): Promise<ApiEnvelope<AgentDecisionDto[]>> {
      const pendingDecisions: Record<string, AgentDecisionDto[]> = {
        'task-multiple-candidates': [
          {
            id: 'decision-mock-1',
            run_id: 'run-mock-1',
            task_id: taskId,
            decision_type: 'metadata_confirmation',
            question: '请选择要使用的元数据候选',
            options: [
              { id: 'opt1', label: '天气之子 (2019) - 置信度 96%', description: 'TMDB movie:568160' },
              { id: 'opt2', label: '《天气之子》制作纪录片 (2020)', description: 'TMDB movie:1461942' },
            ],
            free_text_allowed: true,
            status: 'pending',
            created_at: '2026-05-08T10:00:06+08:00',
          },
          {
            id: 'decision-mock-target-conflict',
            run_id: 'run-mock-1',
            task_id: taskId,
            decision_type: 'target_conflict',
            question: '目标 /data/library/movies/Weathering With You (2019)/Weathering.With.You.2019.mkv 已被占用（同名文件已存在）。请选择处理方式。',
            options: [
              {
                id: 'overwrite_target',
                label: '覆盖发布目标',
                description: '由系统后端基于现有发布计划直接覆盖，不调用 LLM。',
              },
              {
                id: 'cancel_publish',
                label: '取消本次发布',
                description: '任务进入失败态，等待用户后续处理。',
              },
            ],
            free_text_allowed: false,
            payload: {
              final_target_dir: '/data/library/movies/Weathering With You (2019)',
              final_target_file: '/data/library/movies/Weathering With You (2019)/Weathering.With.You.2019.mkv',
              conflict: 'target_file_already_exists',
            },
            status: 'pending',
            created_at: '2026-05-08T10:01:12+08:00',
          },
        ],
      }
      return createSuccessEnvelope(pendingDecisions[taskId] ?? [])
    },

    async replyToAgentDecision(
      decisionId: string,
      optionId?: string,
      freeText?: string,
      decidedBy?: string,
    ): Promise<ApiEnvelope<AgentRunResult>> {
      void decisionId; void optionId; void freeText; void decidedBy
      return createSuccessEnvelope({
        run_id: 'run-mock-1',
        status: 'completed',
        message_count: 6,
        tool_call_count: 3,
        error_message: null,
      })
    },

    async listAgentToolCalls(taskId: string): Promise<ApiEnvelope<AgentToolCallDto[]>> {
      const toolCallsByTask: Record<string, AgentToolCallDto[]> = {
        'task-multiple-candidates': [
          {
            id: 'tc-1',
            run_id: 'run-mock-1',
            message_id: 'msg-3',
            tool_call_id: 'call_3',
            tool_name: 'get_task_context',
            status: 'succeeded',
            input: { task_id: taskId },
            output: { source_path: '/data/downloads/天气之子.mkv', media_type: 'movie' },
            error_message: null,
            duration_ms: 45,
            created_at: '2026-05-08T10:00:01+08:00',
          },
          {
            id: 'tc-2',
            run_id: 'run-mock-1',
            message_id: 'msg-3',
            tool_call_id: 'call_1',
            tool_name: 'search_metadata',
            status: 'succeeded',
            input: { keyword: '天气之子', provider: 'tmdb' },
            output: { candidates: [{ title: '天气之子', year: 2019 }], count: 3 },
            error_message: null,
            duration_ms: 320,
            created_at: '2026-05-08T10:00:04+08:00',
          },
          {
            id: 'tc-3',
            run_id: 'run-mock-1',
            message_id: null,
            tool_call_id: null,
            tool_name: 'scan_task_files',
            status: 'failed',
            input: { task_id: taskId },
            output: null,
            error_message: 'Directory not found',
            duration_ms: 12,
            created_at: '2026-05-08T10:00:02+08:00',
          },
          {
            id: 'tc-4',
            run_id: 'run-mock-1',
            message_id: 'msg-3',
            tool_call_id: 'call_2',
            tool_name: 'search_metadata',
            status: 'succeeded',
            input: { keyword: '天気の子', provider: 'tmdb' },
            output: { candidates: [{ title: '天気の子', year: 2019 }], count: 2 },
            error_message: null,
            duration_ms: 290,
            created_at: '2026-05-08T10:00:04+08:00',
          },
        ],
        'task-completed': [
          {
            id: 'tc-comp-1',
            run_id: 'run-mock-3',
            message_id: 'msg-comp-2',
            tool_call_id: 'call_comp_1',
            tool_name: 'search_metadata',
            status: 'succeeded',
            input: { keyword: '天气之子', provider: 'tmdb' },
            output: { title: '天气之子', year: 2019 },
            error_message: null,
            duration_ms: 280,
            created_at: '2026-05-08T09:51:00+08:00',
          },
        ],
        'task-failed-rollback': [
          {
            id: 'tc-fail-1',
            run_id: 'run-mock-4',
            message_id: 'msg-fail-2',
            tool_call_id: 'call_fail_1',
            tool_name: 'download_poster',
            status: 'failed',
            input: { url: 'https://image.tmdb.org/t/p/original/xxx.jpg' },
            output: null,
            error_message: 'HTTP 404',
            duration_ms: 1200,
            created_at: '2026-05-08T09:31:00+08:00',
          },
        ],
      }
      return createSuccessEnvelope(toolCallsByTask[taskId] ?? [])
    },

    async createAgentRun(taskId: string): Promise<ApiEnvelope<AgentRunResult>> {
      void taskId
      return createSuccessEnvelope({
        run_id: 'run-mock-new',
        status: 'completed',
        message_count: 4,
        tool_call_count: 2,
        error_message: null,
      })
    },

    async sendFreeformMessage(
      taskId: string, _message: string,
    ): Promise<ApiEnvelope<AgentRunResult>> {
      void taskId
      return createSuccessEnvelope({
        run_id: 'run-mock-freeform',
        status: 'completed',
        message_count: 3,
        tool_call_count: 0,
        error_message: null,
      })
    },

    /**
     * 卡住 Agent 恢复 — mock 实现. 真实场景下后端会校验 task.status==
     * 'agent_running' + 存在 active run + 无 pending decision, 失败抛 409;
     * 成功标旧 run failed 并启动新 run.
     *
     * Mock 走 happy path: 始终返新 run_id + status='active', 让
     * AgentPanel 走"POST 成功 → refetch 4 个 key"路径即可. 错误场景
     * 由真实 api-client 测试覆盖 (api-client-recover-stuck.test.ts),
     * mock 只覆盖组件依赖注入.
     */
    async recoverStuckAgentRun(taskId: string): Promise<ApiEnvelope<{ run_id: string; status: string }>> {
      void taskId
      return createSuccessEnvelope({
        run_id: 'run-mock-recover',
        status: 'active',
      })
    },

    // ── 人工辅助检索 ──

    async manualSelect(taskId: string, params: {
      provider: string
      provider_id: string
      title: string
      year?: number | null
      original_title?: string | null
      media_type?: string
    }): Promise<ApiEnvelope<import('@/types/task').ManualSelectResponse>> {
      void params
      const task = findTaskOrThrow(state, taskId)
      updateTaskSummary(task, (summary) => {
        summary.title = params.title
        summary.year = params.year ?? summary.year
        summary.media_type = (params.media_type as import('@/types/task').MediaType) ?? summary.media_type
        summary.can_confirm = false
        summary.status_summary = {
          ...summary.status_summary,
          status: 'library_import_complete',
          current_step: 'library_import_complete',
          latest_message: `已选择 ${params.title} (${params.provider})`,
        }
      })
      return createSuccessEnvelope({
        status: 'published',
        summary: `已选择 ${params.title} 并完成快捷发布`,
        candidate_id: 'mock-candidate-manual',
        decision_id: null,
        blocking_reasons: [],
      })
    },

    async publishWithoutMetadata(taskId: string) {
      const task = findTaskOrThrow(state, taskId)
      updateTaskSummary(task, (summary) => {
        summary.metadata_status = 'none'
        summary.status_summary = {
          ...summary.status_summary,
          status: 'library_import_complete',
          current_step: 'library_import_complete',
          latest_message: '已按无元数据方式入库',
        }
      })
      return createSuccessEnvelope({
        status: 'published',
        metadata_status: 'none' as const,
        final_target_dir: '/data/library/mock-no-metadata',
        final_target_file: '/data/library/mock-no-metadata/mock.mkv',
        cleanup_decision_requested: false,
        decision_id: null,
      })
    },

    // ── 删除任务输入 ──

    async getDeleteInputPreview(taskId: string): Promise<ApiEnvelope<import('@/types/task').DeleteInputPreview>> {
      const task = findTaskOrThrow(state, taskId)
      return createSuccessEnvelope({
        allowed: true,
        target_path: task.task.source_path,
        path_type: 'file',
        outcome_description: `将删除文件 ${task.task.source_path}`,
      })
    },

    async executeDeleteInput(taskId: string): Promise<ApiEnvelope<{ status: string; outcome: string }>> {
      state = state.filter((entry) => entry.task.id !== taskId)
      return createSuccessEnvelope({
        status: 'deleted',
        outcome: '已删除任务输入并清理任务数据',
      })
    },

    async getBackgroundStatus(): Promise<ApiEnvelope<import('@/types/agent-background').AgentBackgroundStatusData>> {
      return createSuccessEnvelope({
        enabled: true,
        state: 'idle',
        summary: 'mock 后台 Agent 空闲',
        disabled_reasons: [],
        waiting_user_count: 0,
        agent_failed_count: 0,
        last_run: null,
        history: [],
        current_task_id: null,
        current_download_id: null,
      })
    },
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  } as unknown as TaskService
}
