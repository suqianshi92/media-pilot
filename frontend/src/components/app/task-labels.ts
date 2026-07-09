import i18n from '@/i18n'
import type {
  MediaType,
  SearchKeywordDto,
  TaskStatus,
  TaskStep,
  TimelineEventDto,
  WriteResultDto,
} from '@/types/task'

// ── i18n resolver ──

function tLabel(ns: string, key: string, fallback: string): string {
  const result = i18n.t(`taskLabel.${ns}.${key}`, '')
  return result || fallback
}

function tWorkspace(key: string, fallback: string): string {
  const result = i18n.t(`taskWorkspace.${key}`, '')
  return result || fallback
}

// ── fallback maps (used when i18n key is missing) ──

const taskStatusLabelFallback: Record<TaskStatus, string> = {
  discovered: '已发现',
  waiting_stable: '等待稳定',
  created: '已创建',
  workspace_imported: '工作区已准备',
  ai_parsed: '已完成解析',
  candidates_ready: '候选已准备',
  queued: '已入队',
  processing: '处理中',
  agent_running: 'Agent 处理中',
  waiting_user: '等待用户回复',
  agent_failed: 'Agent 失败',
  library_import_complete: '已入库',
  completed: '已完成',
  failed: '失败',
  deleted: '已删除',
}

const taskStepLabelFallback: Record<TaskStep, string> = {
  download_scan: '下载扫描',
  workspace_import: '准备工作区',
  workspace_imported: '工作区已准备',
  media_source_selection: '媒体源预判',
  raw_metadata_search: '原始关键词检索',
  llm_keyword_cleanup: 'LLM 清洗关键词',
  select_media_source: '媒体源选择',
  generate_search_keyword: '生成搜索关键词',
  search_metadata: '检索元数据',
  ai_parse: '文件名解析',
  metadata_detail: '获取元数据详情',
  jellyfin_movie_writer: '写入 Jellyfin 产物',
  write_metadata_assets: '写入元数据产物',
  copy_to_staging: '复制到暂存区',
  publish_to_library: '发布到媒体库',
  move_to_library: '移动到媒体库',
  library_import_complete: '媒体入库完成',
  delete_task_input: '删除任务输入',
  // Agent 阶段
  agent_start: 'Agent 启动',
  agent_running: 'Agent 处理中',
  user_replied: '已收到用户回复',
  completed: '已完成',
  // 失败 / 异常步骤
  agent_failed: 'Agent 失败',
  max_tool_failures: '工具连续失败',
  max_steps_exceeded: '超过最大步数',
  llm_error: 'LLM 调用错误',
  config_error: '配置错误',
  agent_interrupted: 'Agent 中断',
  // 人工决策步骤
  select_metadata_candidate: '选择元数据候选',
  target_conflict: '目标冲突',
  target_conflict_decided: '目标冲突已决策',
  manual_select: '人工辅助检索',
  manual_selection_blocked: '人工检索阻塞',
  // 撤回发布步骤
  post_revoke_decision: '撤回后处理决策',
  post_revoke_decided: '撤回决策已处理',
  post_revoke_reingest: '撤回后重新入库',
  // 源文件清理步骤
  source_cleanup_decision: '源文件清理决策',
  source_cleanup_decided: '源文件清理已决策',
  source_cleanup_kept: '源文件保留',
  source_cleanup_trashed: '源文件移入回收区',
  source_cleanup_trash_refused: '源文件回收被拒',
  source_cleanup_trash_failed: '源文件清理失败',
}

const mediaTypeLabelFallback: Record<MediaType, string> = {
  movie: '电影',
  show: '剧集',
  unknown: '未知',
}

const keywordSourceLabelFallback: Record<SearchKeywordDto['source'], string> = {
  rule: '规则清洗',
  llm: 'LLM 生成',
  manual: '人工输入',
}

const keywordReasonLabelFallback: Record<string, string> = {
  filename_rule_cleanup: '文件名规则清洗',
  operator_search_override: '人工修改关键词后重新搜索',
  llm_recovered_title_from_context: 'LLM 根据上下文恢复标题',
  rule_only: '仅规则提取',
  manual_edit: '人工编辑',
}

const sourceSelectionReasonLabelFallback: Record<string, string> = {
  single_video_file: '单个视频文件',
  single_primary_video: '目录中单个主视频',
  largest_video_file: '目录中最大视频文件',
  multiple_similar_videos: '存在多个相似视频',
  no_supported_video: '未找到支持的视频文件',
  bdmv_detected: '检测到 BDMV 目录',
  auto_bdmv_movie_dir: '自动识别 BDMV 电影目录',
}

const writeResultStatusLabelFallback: Record<WriteResultDto['status'], string> = {
  succeeded: '成功',
  warning: '警告',
  failed: '失败',
  target_conflict: '目标冲突',
}

const timelineToneLabelFallback: Record<TimelineEventDto['tone'], string> = {
  default: '一般',
  success: '成功',
  warning: '警告',
  error: '异常',
}

const matchReasonLabelFallback: Record<string, string> = {
  title_exact: '标题精确匹配',
  title_similar: '标题近似匹配',
  year_unknown: '年份未知',
  year_conflict: '年份冲突',
}

export const riskFlagLabelMapFallback: Record<string, string> = {
  year_conflict: '年份冲突',
  title_conflict: '标题可能不匹配',
  low_confidence: '置信度偏低',
  missing_poster: '缺少海报',
  multiple_candidates: '存在多个候选',
}

// ── public getters ──

export function getStatusLabel(status: TaskStatus) {
  return tLabel('status', status, taskStatusLabelFallback[status] ?? status)
}

export function getTaskStepLabel(step: string | null | undefined) {
  if (!step) return i18n.t('common.empty') || '未知'
  return tLabel('step', step, taskStepLabelFallback[step as TaskStep] ?? step)
}

export function getMediaTypeLabel(mediaType: MediaType | null | undefined) {
  if (!mediaType) return i18n.t('common.empty') || '未知'
  return tLabel('mediaType', mediaType, mediaTypeLabelFallback[mediaType] ?? mediaType)
}

export function getKeywordSourceLabel(source: SearchKeywordDto['source'] | null | undefined) {
  if (!source) return i18n.t('common.empty') || '未知'
  return tLabel('keywordSource', source, keywordSourceLabelFallback[source] ?? source)
}

export function getKeywordReasonLabel(reason: string | null | undefined) {
  if (!reason) return i18n.t('common.empty') || '未知'
  return tLabel('keywordReason', reason, keywordReasonLabelFallback[reason] ?? reason)
}

export function getSourceSelectionReasonLabel(reason: string | null | undefined) {
  if (!reason) return i18n.t('common.empty') || '未知'
  return tLabel('sourceSelectionReason', reason, sourceSelectionReasonLabelFallback[reason] ?? reason)
}

export function getWriteResultStatusLabel(status: WriteResultDto['status'] | null | undefined) {
  if (!status) return i18n.t('common.empty') || '未知'
  return tLabel('writeResultStatus', status, writeResultStatusLabelFallback[status] ?? status)
}

export function getTimelineToneLabel(tone: TimelineEventDto['tone']) {
  return tLabel('timelineTone', tone, timelineToneLabelFallback[tone] ?? tone)
}

export function getMatchReasonLabel(reason: string | null | undefined) {
  if (!reason) return i18n.t('common.empty') || '未知'
  return reason
    .split(',')
    .map((token) => {
      const label = tLabel('matchReason', token, matchReasonLabelFallback[token] ?? '')
      if (label) return label
      if (token.startsWith('rank_')) return `第 ${token.replace('rank_', '')} 位`
      return token
    })
    .join('，')
}

export function getRiskFlagLabel(flag: string) {
  return tLabel('riskFlag', flag, riskFlagLabelMapFallback[flag] ?? flag)
}

// ── 下载状态 ──

export const DOWNLOAD_ACTIVE_STATUSES = new Set([
  'submitting',
  'submitted',
  'downloading',
  'awaiting_sync',
])

const downloadStatusLabelFallback: Record<string, string> = {
  submitting: '下载中',
  submitted: '下载中',
  downloading: '下载中',
  awaiting_sync: '等待下载器同步',
  completed: '等待转入入库',
  completed_pending_ingest: '等待转入入库',
  failed: '下载失败',
  sync_failed: '下载失败',
  paused: '已暂停',
}

export function isDownloadActive(status: string | null | undefined): boolean {
  return status != null && DOWNLOAD_ACTIVE_STATUSES.has(status)
}

// ── 下载来源 ──

const downloadSourceLabelFallback: Record<string, string> = {
  manual_upload: '手动上传',
  prowlarr: '资源搜索',
}

export function getDownloadSourceLabel(source: string): string {
  return tLabel('downloadSource', source, downloadSourceLabelFallback[source] ?? source)
}

export function getDownloadStatusLabel(status: string): string {
  return tLabel('downloadStatus', status, downloadStatusLabelFallback[status] ?? status)
}

// ── 统一状态徽章颜色与分组 ──

const STATUS_COLOR_ACTIVE = new Set([
  'submitting', 'submitted', 'downloading', 'processing', 'queued',
])

const STATUS_COLOR_WARNING = new Set([
  'awaiting_sync', 'waiting_stable', 'waiting_user',
  'completed', 'completed_pending_ingest', 'paused',
])

const STATUS_COLOR_SUCCESS = new Set([
  'library_import_complete',
])

const STATUS_COLOR_ERROR = new Set([
  'failed', 'sync_failed', 'agent_failed',
])

export function getStatusColorClass(status: string): string {
  // agent_running 必须与详情页 AgentRunStatusBadge(active) / agentRunStatusColorMap.active
  // 字面量一致, 共享同一蓝色调色板.
  if (status === 'agent_running') return 'border-blue-400/45 bg-blue-500/15 text-blue-200'
  if (STATUS_COLOR_ACTIVE.has(status)) return 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300'
  if (STATUS_COLOR_WARNING.has(status)) return 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-300'
  if (STATUS_COLOR_SUCCESS.has(status)) return 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300'
  if (STATUS_COLOR_ERROR.has(status)) return 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300'
  return 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300'
}

export function getTotalStatusLabel(status: string): string {
  // task statuses take priority over download statuses
  // (e.g. completed is both a task status and a download status with different meanings)
  if (taskStatusLabelFallback[status as TaskStatus] !== undefined) return getStatusLabel(status as TaskStatus)
  if (downloadStatusLabelFallback[status] !== undefined) return getDownloadStatusLabel(status)
  return status
}

/** 非终态：仍可能自动推进的状态 */
export const NON_TERMINAL_STATUSES = new Set([
  'submitting', 'submitted', 'downloading', 'awaiting_sync',
  'completed_pending_ingest',
  'discovered', 'waiting_stable', 'created',
  'workspace_imported', 'ai_parsed', 'candidates_ready',
  'queued', 'processing', 'agent_running',
])

export function isNonTerminalStatus(status: string): boolean {
  return NON_TERMINAL_STATUSES.has(status)
}

// ── provider / profile labels ──

const providerLabelFallback: Record<string, string> = {
  tmdb: 'TMDB',
  tpdb: 'TPDB',
  fake_metadata: '测试数据',
}

export function getProviderLabel(provider: string) {
  return tWorkspace(`provider.${provider}`, providerLabelFallback[provider] ?? provider)
}

const profileLabelFallback: Record<string, string> = {
  tmdb_movie: 'TMDB 电影',
  tmdb_show: 'TMDB 剧集',
  tpdb_adult_movie: 'TPDB 成人影片',
}

export function getProfileLabel(profile: string) {
  return tLabel('profile', profile, profileLabelFallback[profile] ?? profile)
}

export const tpdbFieldLabelMap: Record<string, string> = {
  identifier: '番号',
  identifier_type: '番号类型',
  normalized_code: '标准番号',
  raw_code: '原始番号',
  studio: '厂牌/片商',
  label: '厂牌',
  serial: '番号',
  sorttitle: '排序标题',
}
