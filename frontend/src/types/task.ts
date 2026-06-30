export type TaskStatus =
  | 'discovered'
  | 'waiting_stable'
  | 'created'
  | 'workspace_imported'
  | 'ai_parsed'
  | 'candidates_ready'
  | 'queued'
  | 'processing'
  | 'agent_running'
  | 'waiting_user'
  | 'agent_failed'
  | 'library_import_complete'
  | 'completed'
  | 'failed'
  | 'deleted'

export type TaskStep =
  | 'download_scan'
  | 'workspace_import'
  | 'workspace_imported'
  | 'media_source_selection'
  | 'raw_metadata_search'
  | 'llm_keyword_cleanup'
  | 'select_media_source'
  | 'generate_search_keyword'
  | 'search_metadata'
  | 'ai_parse'
  | 'metadata_detail'
  | 'jellyfin_movie_writer'
  | 'write_metadata_assets'
  | 'copy_to_staging'
  | 'publish_to_library'
  | 'move_to_library'
  | 'library_import_complete'
  | 'delete_task_input'
  | 'post_revoke_reingest'
  | 'post_revoke_decision'
  | 'post_revoke_decided'
  | 'target_conflict'
  | 'target_conflict_decided'
  | 'agent_start'
  | 'agent_running'
  | 'user_replied'
  | 'completed'
  | 'agent_failed'
  | 'max_tool_failures'
  | 'max_steps_exceeded'
  | 'llm_error'
  | 'config_error'
  | 'agent_interrupted'
  | 'select_metadata_candidate'
  | 'manual_select'
  | 'manual_selection_blocked'
  | 'source_cleanup_decision'
  | 'source_cleanup_decided'
  | 'source_cleanup_kept'
  | 'source_cleanup_trashed'
  | 'source_cleanup_trash_refused'
  | 'source_cleanup_trash_failed'


export type MediaType = 'movie' | 'show' | 'unknown'

export type ConfidenceLevel = 'high' | 'medium' | 'low' | 'unknown'


export interface DownloadTaskSummary {
  id: string
  title: string
  source: string
  qb_hash: string | null
  save_path: string
  content_path: string | null
  progress: number
  download_speed_bytes_per_second: number | null
  upload_speed_bytes_per_second: number | null
  seeders: number
  leechers: number
  connections: number | null
  qb_state: string | null
  status: string
  error_message: string | null
  ingest_task_id: string | null
  created_at: string
  updated_at: string
}

export interface DownloadDetail {
  id: string
  title: string
  source: string
  qb_hash: string | null
  save_path: string
  content_path: string | null
  progress: number
  download_speed_bytes_per_second: number | null
  upload_speed_bytes_per_second: number | null
  seeders: number
  leechers: number
  connections: number | null
  qb_state: string | null
  status: string
  error_message: string | null
  ingest_task_id: string | null
  preselected_metadata_profile: string | null
  preselected_metadata_provider: string | null
  preselected_metadata_external_id: string | null
  created_at: string
  updated_at: string
}

export interface TaskStatusSummary {
  status: TaskStatus
  // current_step 在 DB 是 String(128), 业务侧可能写入动态 / 临时 marker
  // (如 runner 的 `step_N` / 自由对话的 previous_step / 复杂输入决策的
  // decision_type), 稳定子集见 TaskStep union; 这里用 string 接收
  // 以免新加 marker 让前端按 enum 严格判定失败.
  current_step: string | null
  failure_reason: string | null
  confidence: number | null
  confidence_level: ConfidenceLevel
  latest_message: string | null
}

// 后端 FlowStatusSummary 表达 ingest status + download status 的并集.
// 下载状态 (downloading / paused / submitted / awaiting_sync / sync_failed)
// 不在 TaskStatus Literal 内, 这里用 string 接收.
export type FlowStatus = string
export interface FlowStatusSummary {
  status: FlowStatus
  current_step: string | null
  failure_reason: string | null
  confidence: number | null
  confidence_level: ConfidenceLevel
  latest_message: string | null
}

export type FlowType = 'managed_download' | 'external_import' | 'download_only'

export type RouteTarget = 'task_detail' | 'download_detail'

export type AgentRunStatus = 'none' | 'active' | 'waiting_user' | 'completed' | 'failed'

export interface AgentStatusSummary {
  run_status: AgentRunStatus
  latest_run_id: string | null
  pending_decision_count: number
  latest_message_summary: string | null
}

export type MetadataStatus = 'unknown' | 'complete' | 'none'

export interface TaskSummary {
  id: string
  source_path: string
  title: string | null
  year: number | null
  media_type: MediaType | null
  can_confirm: boolean
  flow_type: FlowType
  total_status: string
  metadata_status?: MetadataStatus
  file_format: string | null
  created_at: string
  updated_at: string
  status_summary: TaskStatusSummary
  download_task: DownloadTaskSummary | null
  agent_status_summary: AgentStatusSummary | null
}

// 媒体获取流程列表 read-model. 后端聚合 IngestTask + optional DownloadTask
// 以及 download-only DownloadTask. 字段与 TaskSummary 平行, 列表页直接消费.
// id 使用前缀稳定 (ingest:<id> / download:<id>), 避免与上游 IngestTask.id
// 或 DownloadTask.id 碰撞. ingest_task_id / download_task_id / route_target
// 让前端不必解析 id 即可决定详情跳转.
export interface FlowSummary {
  id: string
  flow_type: FlowType
  route_target: RouteTarget
  ingest_task_id: string | null
  download_task_id: string | null
  total_status: string
  metadata_status?: MetadataStatus
  title: string | null
  year: number | null
  media_type: MediaType | null
  can_confirm: boolean
  file_format: string | null
  source_path: string | null
  created_at: string
  updated_at: string
  status_summary: FlowStatusSummary | null
  agent_status_summary: AgentStatusSummary | null
  download_task: DownloadTaskSummary | null
}

export interface OpenAIToolCall {
  id: string
  type: 'function'
  function: {
    name: string
    arguments: string
  }
}

export interface AgentMessageDto {
  id: string
  run_id: string
  role: string
  content: string | null
  tool_calls: OpenAIToolCall[] | null
  tool_call_id: string | null
  tool_name: string | null
  created_at: string | null
}

export interface AgentDecisionDto {
  id: string
  run_id: string
  task_id: string
  decision_type: string
  question: string
  options: Record<string, unknown>[]
  free_text_allowed: boolean
  payload?: Record<string, unknown> | null
  status: string
  created_at: string | null
}

export interface AgentToolCallDto {
  id: string
  run_id: string
  message_id: string | null
  tool_call_id: string | null
  tool_name: string
  status: string
  input: Record<string, unknown>
  output: Record<string, unknown> | null
  error_message: string | null
  duration_ms: number | null
  created_at: string | null
}

export interface AgentRunResult {
  run_id: string
  status: string
  message_count: number
  tool_call_count: number
  error_message: string | null
}

export interface MediaSourceCandidateFile {
  path: string
  name: string
  size_bytes: number | null
  reason: string
}

export interface MediaSourceSelectionDto {
  input_path: string
  selected_path: string | null
  confidence: number | null
  reason: string | null
  bdmv_detected: boolean
  stream_file_count: number | null
  candidate_files: MediaSourceCandidateFile[]
  excluded_files: MediaSourceCandidateFile[]
}

export interface SearchKeywordDto {
  keyword: string
  source: 'rule' | 'llm' | 'manual'
  confidence: number | null
  reason: string | null
  rule_keyword: string | null
  explanation: string | null
  quality_tokens: string[]
  tokens_removed: string[]
}

export interface MetadataCandidateDto {
  provider: string
  provider_id: string
  title: string
  original_title: string | null
  year: number | null
  media_type: MediaType
  overview: string | null
  poster_url: string | null
  confidence: number | null
  match_reason: string | null
  risk_flags: string[]
  payload: Record<string, unknown>
}

export interface MetadataPersonDto {
  provider_id: string | null
  name: string
  role: string | null
  profile_url: string | null
  image_url: string | null
}

export interface MetadataDetailDto {
  provider: string
  provider_id: string
  media_type: MediaType
  title: string | null
  original_title: string | null
  year: number | null
  overview: string | null
  release_date: string | null
  runtime_minutes: number | null
  rating: number | null
  tmdb_id: string | null
  imdb_id: string | null
  genres: string[]
  countries: string[]
  studios: string[]
  directors: MetadataPersonDto[]
  actors: MetadataPersonDto[]
  poster_url: string | null
  fanart_url: string | null
  clearlogo_url: string | null
}

export interface WritePlanDto {
  target_dir: string
  target_file: string | null
  nfo_path: string | null
  poster_path: string | null
  fanart_path: string | null
  clearlogo_path: string | null
  conflict_status: string | null
  conflict_reason: string | null
}

export interface WriteResultDto {
  status: 'succeeded' | 'warning' | 'failed' | 'target_conflict'
  failure_reason: string | null
  warnings: string[]
  written_paths: string[]
}

export interface FileAssetDto {
  role: string
  path: string
  size_bytes: number | null
}

export interface ProviderCallDto {
  adapter_name: string
  action: string
  status: 'succeeded' | 'failed'
  error_message: string | null
  created_at: string
}

export interface OperationRecordDto {
  operation_type: string
  permission_level: string
  source_path: string | null
  target_path: string | null
  status: string
  details: Record<string, unknown>
  created_at: string
}

export interface AuditLogDto {
  actor: string
  action: string
  object_type: string
  object_id: string | null
  created_at: string
  context: Record<string, unknown>
}


export interface TimelineEventDto {
  key: string
  title: string
  detail: string | null
  created_at: string
  tone: 'default' | 'success' | 'warning' | 'error'
}

export interface EpisodeMappingDto {
  file_path: string
  season: number
  episode: number
  source: string
}

export interface TaskDetailDto {
  task: TaskSummary
  source_selection: MediaSourceSelectionDto | null
  search_keyword: SearchKeywordDto | null
  selected_candidate: MetadataCandidateDto | null
  metadata_detail: MetadataDetailDto | null
  write_plan: WritePlanDto | null
  write_result: WriteResultDto | null
  file_assets: FileAssetDto[]
  provider_calls: ProviderCallDto[]
  operation_records: OperationRecordDto[]
  audit_logs: AuditLogDto[]
  timeline: TimelineEventDto[]
  episode_mappings: EpisodeMappingDto[]
}

// ── 撤销发布 ──

export interface RevokePublishCheckDto {
  allowed: boolean
  publish_dir: string | null
  source_file_exists: boolean
  is_complex_structure: boolean
  outcome_description: string
}

export interface RevokePublishResultDto {
  status: 'waiting_user' | 'deleted'
  outcome: string
  decision_id?: string
}

// ── 人工辅助检索 ──

export interface ManualSelectRequest {
  provider: string
  provider_id: string
  title: string
  year?: number | null
  original_title?: string | null
  media_type?: string
}

export interface ManualSelectResponse {
  status: 'published' | 'waiting_user' | 'saved' | 'agent_failed'
  summary: string
  candidate_id?: string | null
  decision_id?: string | null
  blocking_reasons?: string[]
}

// ── 删除任务输入 ──

export interface DeleteInputPreview {
  allowed: boolean
  target_path: string | null
  path_type: string | null
  outcome_description: string
}

// ---- 手动重搜 ----

export type ResearchScope = 'all' | 'tmdb_movie' | 'tmdb_show' | 'tpdb_adult_movie'

export type ProfileSearchStatus = 'succeeded' | 'failed' | 'skipped'

export interface ProfileSearchStatusDto {
  profile: string
  label: string
  provider: string
  status: ProfileSearchStatus
  candidate_count: number
  error_message: string | null
}

export interface SearchSummaryDto {
  keyword: string
  scope: ResearchScope
  searched_profiles: ProfileSearchStatusDto[]
  total_candidates: number
  kept_existing_candidates: boolean
}

export interface ResearchKeywordRequest {
  keyword: string
  scope: ResearchScope
}

export interface ResearchResponseData {
  candidates: MetadataCandidateDto[]
  search_summary: SearchSummaryDto
}
