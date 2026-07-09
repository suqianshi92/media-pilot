import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'
import { Bot, ChevronDown, ChevronUp, Loader2, RefreshCw, Send } from 'lucide-react'

import { SkeletonBlock } from '@/components/app/shared-ui'
import { useToast } from '@/components/shared/toast'
import { createTaskService } from '@/services/task-service'
import type { AgentDecisionDto, AgentMessageDto, AgentStatusSummary, AgentToolCallDto, OpenAIToolCall, TaskDetailDto } from '@/types/task'
import type { ApiEnvelope } from '@/types/api'
import { MarkdownView } from '@/components/agent/markdown-view'

// task-detail query 缓存值类型 — 与 services/api-client.getTaskDetail 一致.
type TaskDetailEnvelope = ApiEnvelope<TaskDetailDto>

const defaultTaskService = createTaskService()

/**
 * 聊天滚动容器的"接近底部"自动跟随 hook.
 *
 * 用法:
 *   const { follow, scrollToBottom, onContentChange } = useFollowScroll(scrollRef)
 *   - `follow`: 当前视口是否紧贴底部(距底部 ≤ 80px).
 *   - `scrollToBottom(behavior)`: 强制滚到底, 不论 follow 状态.
 *     用于初次挂载 / 切 taskId / 用户主动发送 / 用户提交 decision.
 *   - `onContentChange()`: 仅在 follow === true 时温和滚到底; 用户上翻
 *     离开底部后, 该回调变成 no-op, 保留用户阅读位置.
 *
 * 引用稳定性: scrollToBottom / onContentChange 都用 useCallback 锁住引用.
 * onContentChange 内部通过 followRef 读最新 follow state (避免在闭包里
 * 捕获陈旧 follow). 调用方把 onContentChange 放进 useEffect 依赖, 必须
 * 保持稳定, 否则 AgentPanel 在普通重渲染时 useEffect 会反复执行并把用户
 * 拉回底部 (违反"用户上翻后不被拉回"目标).
 *
 * 阈值常量 80px 是聊天类 UI 的常见做法 (ChatGPT / Slack / Lark 50~120px
 * 区间). 前端是 Vite SPA, 没有 SSR, 这里直接用 useLayoutEffect 读
 * scrollHeight 不会有"还没 layout" 的窗口期; 若将来切到 SSR, 需 fallback
 * 到 useEffect + 二次 mount scroll.
 */
const FOLLOW_THRESHOLD_PX = 80

export function useFollowScroll(scrollRef: RefObject<HTMLElement>) {
  const [follow, setFollow] = useState(true)

  const scrollToBottom = useCallback((behavior: 'auto' | 'smooth' = 'auto') => {
    const el = scrollRef.current
    if (!el) return
    el.scrollTo({ top: el.scrollHeight, behavior })
  }, [scrollRef])

  // followRef 镜像 follow state, 让 onContentChange 闭包始终读最新值;
  // useLayoutEffect 保证 followRef 在 AgentPanel 的 useEffect 跑之前同步,
  // 避免读到陈旧值. 即便 AgentPanel 重排 hook 顺序, useLayoutEffect 一定
  // 早于 useEffect, 这条不变式仍然成立.
  const followRef = useRef(follow)
  useLayoutEffect(() => {
    followRef.current = follow
  }, [follow])

  const onContentChange = useCallback(() => {
    if (!followRef.current) return
    scrollToBottom('smooth')
  }, [scrollToBottom])

  useLayoutEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const onScroll = () => {
      const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
      setFollow(distanceFromBottom <= FOLLOW_THRESHOLD_PX)
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [scrollRef])

  return { follow, scrollToBottom, onContentChange }
}

export type AgentService = Pick<
  ReturnType<typeof createTaskService>,
  'listAgentMessages' | 'listAgentDecisions' | 'replyToAgentDecision' | 'listAgentToolCalls' | 'createAgentRun' | 'sendFreeformMessage' | 'recoverStuckAgentRun'
>

export interface AgentPanelProps {
  taskId: string
  agentStatus: AgentStatusSummary | null
  taskStatus?: string
  service?: AgentService
}

const roleLabelMap: Record<string, string> = {
  user: 'agent.roleUser',
  assistant: 'agent.roleAssistant',
  tool: 'agent.roleTool',
}

function formatTime(iso: string | null) {
  if (!iso) return ''
  try {
    const d = new Date(iso.endsWith('Z') || /[+\-]\d{2}:\d{2}$/.test(iso) ? iso : iso + 'Z')
    if (isNaN(d.getTime())) return iso.slice(11, 16)
    return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
  } catch {
    return iso.slice(11, 16)
  }
}

function formatDuration(ms: number | null) {
  if (ms == null) return ''
  if (ms < 1000) return `${ms} ms`
  return `${(ms / 1000).toFixed(1)} s`
}

type ToolCallDisplayStatus = 'success' | 'failed' | 'running' | 'pending' | 'unknown'

/**
 * 工具调用展示状态 — 多来源判定, 防止 UI 把中间态误标为失败:
 * - API 已归一化为 "succeeded" / "failed" (见 /agent-tool-calls),
 *   但 DB 历史可能含 "completed" (runner 新写入).
 * - 某些工具的 wire output 用 {status: "success" | "failure"} 描述,
 *   也作为兜底.
 */
function getToolCallDisplayStatus(toolDetail: AgentToolCallDto | undefined): ToolCallDisplayStatus {
  if (!toolDetail) return 'unknown'
  const wireStatus = (toolDetail.status || '').toLowerCase()
  if (wireStatus === 'succeeded' || wireStatus === 'completed') return 'success'
  if (wireStatus === 'failed' || wireStatus === 'failure' || wireStatus === 'error') return 'failed'
  const output = toolDetail.output
  if (output && typeof output === 'object') {
    const dataStatus = (output as Record<string, unknown>).status
    if (typeof dataStatus === 'string' && dataStatus.toLowerCase() === 'success') {
      return 'success'
    }
    if (typeof dataStatus === 'string' && ['failure', 'failed', 'error'].includes(dataStatus.toLowerCase())) {
      return 'failed'
    }
  }
  if (wireStatus === 'running') return 'running'
  if (wireStatus === 'pending' || wireStatus === 'skipped') return 'pending'
  return 'unknown'
}

function getToolCallStatusClass(status: ToolCallDisplayStatus): string {
  if (status === 'success') return 'text-green-500'
  if (status === 'failed') return 'text-red-500'
  if (status === 'running') return 'text-blue-500'
  return 'text-muted-foreground'
}

function getToolCallStatusLabel(t: TFunction, status: ToolCallDisplayStatus): string {
  if (status === 'success') return t('agent.toolSuccess')
  if (status === 'failed') return t('agent.toolFailed')
  if (status === 'running') return t('agent.toolRunning')
  if (status === 'pending') return t('agent.toolPending')
  return t('agent.toolUnknown')
}

const toolLabelKeyMap: Record<string, string> = {
  get_task_context: 'agent.toolLabels.getTaskContext',
  scan_task_files: 'agent.toolLabels.scanTaskFiles',
  get_current_metadata: 'agent.toolLabels.getCurrentMetadata',
  search_metadata: 'agent.toolLabels.searchMetadata',
  get_metadata_candidates: 'agent.toolLabels.getMetadataCandidates',
  get_auto_ingest_eligibility: 'agent.toolLabels.getAutoIngestEligibility',
  prepare_complex_input_decision: 'agent.toolLabels.prepareComplexInputDecision',
  prepare_show_structure: 'agent.toolLabels.prepareShowStructure',
  prepare_select_metadata_candidate_decision: 'agent.toolLabels.prepareSelectMetadataCandidateDecision',
  request_user_decision: 'agent.toolLabels.requestUserDecision',
  persist_metadata_selection: 'agent.toolLabels.persistMetadataSelection',
  fetch_and_save_metadata_detail: 'agent.toolLabels.fetchAndSaveMetadataDetail',
  publish_movie_to_library: 'agent.toolLabels.publishMovieToLibrary',
  publish_show_to_library: 'agent.toolLabels.publishShowToLibrary',
  publish_without_metadata: 'agent.toolLabels.publishWithoutMetadata',
  draft_metadata_replacement: 'agent.toolLabels.draftMetadataReplacement',
  draft_publish_plan: 'agent.toolLabels.draftPublishPlan',
  handle_source_cleanup: 'agent.toolLabels.handleSourceCleanup',
  revoke_publish: 'agent.toolLabels.revokePublish',
}

function getToolLabel(t: TFunction, toolName: string): string {
  return t(toolLabelKeyMap[toolName] ?? 'agent.toolLabels.internalStep')
}

export function summarizeSourceCleanup(output: unknown, t: TFunction): string {
  if (!output || typeof output !== 'object') return ''
  // 真实 ToolResult wire shape: output = {status, summary, data: {action, ...}}
  // 优先读 data.*, 兼容旧版直接平铺 (action 在根级) 的 wire shape.
  const o = output as Record<string, unknown>
  const data = (o.data && typeof o.data === 'object'
    ? (o.data as Record<string, unknown>)
    : null) ?? o
  const action = data.action
  if (action === 'kept') return t('agent.toolSummary.sourceCleanupKept')
  if (action === 'trashed') {
    // 故意不暴露 trash_target / 任何内部路径, 只显示短文案
    return t('agent.toolSummary.sourceCleanupTrashed')
  }
  if (action === 'trash_failed') {
    const reason = typeof data.reason === 'string' ? data.reason : ''
    return reason
      ? t('agent.toolSummary.sourceCleanupFailedWithReason', { reason })
      : t('agent.toolSummary.sourceCleanupFailed')
  }
  if (data.decision_type === 'source_cleanup_action') {
    return t('agent.toolSummary.sourceCleanupDecisionRequested')
  }
  if (action === 'preflight_refused') {
    const reason = typeof data.reason === 'string' ? data.reason : ''
    return reason
      ? t('agent.toolSummary.sourceCleanupPreflightRefusedWithReason', { reason })
      : t('agent.toolSummary.sourceCleanupPreflightRefused')
  }
  return ''
}

export function summarizeSearchMetadata(output: unknown, t: TFunction): string {
  if (!output || typeof output !== 'object') return ''
  // 真实 ToolResult wire shape: output = {status, summary, data: {candidates, ...}}
  // 优先读 data.candidates, fallback 到顶层 output.candidates, 最后 fallback 到 summary.
  const o = output as Record<string, unknown>
  const data = o.data && typeof o.data === 'object'
    ? (o.data as Record<string, unknown>)
    : null
  const candidates =
    (data && Array.isArray(data.candidates) ? (data.candidates as unknown[]) : null) ??
    (Array.isArray(o.candidates) ? (o.candidates as unknown[]) : null)
  if (candidates) {
    return t('agent.toolSummary.candidateCount', { count: candidates.length })
  }
  // fallback: 用 server-side summary (e.g. "Found 3 candidates for 'X' on tmdb")
  if (typeof o.summary === 'string' && o.summary) {
    return o.summary
  }
  return ''
}

export function summarizePersistMetadataSelection(output: unknown, input: unknown, t: TFunction): string {
  if (!output || typeof output !== 'object') return ''
  // 真实 wire shape: output.data.title; fallback: output.title (legacy),
  // 再 fallback: input.title, 最后 generic.
  const o = output as Record<string, unknown>
  const data = o.data && typeof o.data === 'object'
    ? (o.data as Record<string, unknown>)
    : null
  const fromData = data && typeof data.title === 'string' ? data.title : ''
  const fromOutput = !fromData && typeof o.title === 'string' ? o.title : ''
  const fromInput =
    !fromData && !fromOutput
    && typeof input === 'object' && input
    && typeof (input as Record<string, unknown>).title === 'string'
      ? ((input as Record<string, unknown>).title as string)
      : ''
  const title = fromData || fromOutput || fromInput
  if (title) return t('agent.toolSummary.metadataSelectedWithTitle', { title })
  return t('agent.toolSummary.metadataSelected')
}

export function summarizeToolOutput(toolName: string, output: unknown, input: unknown, t: TFunction): string {
  switch (toolName) {
    case 'handle_source_cleanup':
      return summarizeSourceCleanup(output, t)
    case 'search_metadata':
      return summarizeSearchMetadata(output, t)
    case 'persist_metadata_selection':
      return summarizePersistMetadataSelection(output, input, t)
    case 'get_task_context':
      return t('agent.toolSummary.getTaskContext')
    case 'scan_task_files':
      return t('agent.toolSummary.scanTaskFiles')
    case 'get_current_metadata':
      return t('agent.toolSummary.getCurrentMetadata')
    case 'get_metadata_candidates':
      return t('agent.toolSummary.getMetadataCandidates')
    case 'get_auto_ingest_eligibility':
      return t('agent.toolSummary.getAutoIngestEligibility')
    case 'prepare_complex_input_decision':
      return t('agent.toolSummary.prepareComplexInputDecision')
    case 'prepare_show_structure':
      return t('agent.toolSummary.prepareShowStructure')
    case 'prepare_select_metadata_candidate_decision':
      return t('agent.toolSummary.prepareSelectMetadataCandidateDecision')
    case 'request_user_decision':
      return t('agent.toolSummary.requestUserDecision')
    case 'fetch_and_save_metadata_detail':
      return t('agent.toolSummary.fetchAndSaveMetadataDetail')
    case 'publish_movie_to_library':
      return t('agent.toolSummary.publishMovieToLibrary')
    case 'publish_show_to_library':
      return t('agent.toolSummary.publishShowToLibrary')
    case 'publish_without_metadata':
      return t('agent.toolSummary.publishWithoutMetadata')
    case 'draft_metadata_replacement':
      return t('agent.toolSummary.draftMetadataReplacement')
    case 'draft_publish_plan':
      return t('agent.toolSummary.draftPublishPlan')
    case 'revoke_publish':
      return t('agent.toolSummary.revokePublish')
    default:
      return ''
  }
}

export function ToolCallBlock({
  toolCall,
  toolDetail,
  expanded,
  onToggle,
}: {
  toolCall: OpenAIToolCall
  toolDetail?: AgentToolCallDto
  expanded: boolean
  onToggle: () => void
}) {
  const { t } = useTranslation()
  const funcName = toolCall.function?.name ?? 'unknown'
  const toolLabel = getToolLabel(t, funcName)
  const collapsedSummary = toolDetail
    ? summarizeToolOutput(funcName, toolDetail.output, toolDetail.input, t)
    : ''
  const displayStatus = getToolCallDisplayStatus(toolDetail)

  return (
    <div className="mt-2 rounded border border-border/70 bg-background">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs hover:bg-muted/50"
      >
        <span className="text-muted-foreground">{t('agent.toolCallPrefix')}</span>
        <span className="font-medium text-surface-foreground">{toolLabel}</span>
        {!expanded && collapsedSummary ? (
          <span
            data-testid={`tool-call-summary-${funcName}`}
            className="text-xs text-muted-foreground truncate max-w-[60%]"
          >
            · {collapsedSummary}
          </span>
        ) : null}
        {toolDetail ? (
          <span
            data-testid={`tool-call-status-${funcName}`}
            className={`ml-auto text-xs ${getToolCallStatusClass(displayStatus)}`}
          >
            {getToolCallStatusLabel(t, displayStatus)}
          </span>
        ) : null}
        {expanded ? <ChevronUp className="h-3 w-3 text-muted-foreground shrink-0" /> : <ChevronDown className="h-3 w-3 text-muted-foreground shrink-0" />}
      </button>
      {expanded && toolDetail ? (
        <div className="border-t border-border/70 px-3 py-2 space-y-2 text-xs">
          <div>
            <span className="font-medium text-muted-foreground">{t('agent.toolCallFunction')}</span>
            <code className="ml-2 font-mono text-surface-foreground">{funcName}</code>
          </div>
          {toolDetail.input && Object.keys(toolDetail.input).length > 0 ? (
            <div>
              <span className="font-medium text-muted-foreground">{t('agent.toolCallInput')}</span>
              <pre className="mt-1 max-h-32 overflow-auto rounded bg-muted/50 p-2 font-mono text-xs text-surface-foreground whitespace-pre-wrap">
                {JSON.stringify(toolDetail.input, null, 2)}
              </pre>
            </div>
          ) : null}
          {funcName === 'handle_source_cleanup' && toolDetail.output ? (
            <p className="text-surface-foreground">
              {summarizeSourceCleanup(toolDetail.output, t)}
            </p>
          ) : null}
          {toolDetail.output ? (
            <div>
              <span className="font-medium text-muted-foreground">{t('agent.toolCallOutput')}</span>
              <pre className="mt-1 max-h-32 overflow-auto rounded bg-muted/50 p-2 font-mono text-xs text-surface-foreground whitespace-pre-wrap">
                {JSON.stringify(toolDetail.output, null, 2)}
              </pre>
            </div>
          ) : null}
          {toolDetail.error_message ? (
            <div>
              <span className="font-medium text-red-500">{t('agent.toolCallError')}</span>
              <p className="mt-1 text-red-400">{toolDetail.error_message}</p>
            </div>
          ) : null}
          {toolDetail.duration_ms != null ? (
            <p className="text-muted-foreground">{t('agent.toolCallDuration')}: {formatDuration(toolDetail.duration_ms)}</p>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

export function MessageBubble({
  msg,
  toolDetails,
}: {
  msg: AgentMessageDto
  toolDetails: AgentToolCallDto[]
}) {
  const { t } = useTranslation()
  const [expandedIndex, setExpandedIndex] = useState<number>(-1)
  const roleLabel = t(roleLabelMap[msg.role] ?? 'agent.roleUnknown')
  const timeStr = formatTime(msg.created_at)

  const isAssistant = msg.role === 'assistant'
  const isSystemAction = msg.content?.startsWith('[SystemAction]') ?? false
  const displayContent = isSystemAction ? msg.content!.replace('[SystemAction] ', '') : msg.content
  const toolCalls = msg.tool_calls

  if (msg.role === 'tool') {
    return null
  }

  const bubbleClass = isSystemAction
    ? 'border-amber-500/30 bg-amber-500/5'
    : isAssistant
      ? 'border-blue-500/20 bg-blue-500/5'
      : 'border-border bg-background'

  return (
    <div className={`rounded-md border px-3 py-2 text-sm ${bubbleClass}`}>
      <div className="flex items-center gap-2">
        {isSystemAction ? (
          <span className="text-xs font-medium text-amber-600">{t('agent.systemAction')}</span>
        ) : (
          <span className="text-xs font-medium text-muted-foreground">{roleLabel}</span>
        )}
        {timeStr ? <span className="text-xs text-muted-foreground/60">{timeStr}</span> : null}
      </div>
      {displayContent ? (
        isAssistant ? (
          <div className="mt-1" data-testid="assistant-markdown">
            <MarkdownView content={displayContent} />
          </div>
        ) : (
          <p className="mt-1 whitespace-pre-wrap text-surface-foreground">{displayContent}</p>
        )
      ) : null}
      {toolCalls && toolCalls.length > 0
        ? toolCalls.map((tc, idx) => {
            const toolDetail = toolDetails.find((d) => {
              if (d.tool_call_id && tc.id) return d.tool_call_id === tc.id
              return d.message_id === msg.id && d.tool_name === tc.function?.name
            })
            return (
              <ToolCallBlock
                key={tc.id ?? idx}
                toolCall={tc}
                toolDetail={toolDetail}
                expanded={expandedIndex === idx}
                onToggle={() => setExpandedIndex(expandedIndex === idx ? -1 : idx)}
              />
            )
          })
        : null}
    </div>
  )
}

// Reusable scroll wrapper for long decision option lists.
// select_metadata_candidate 拉 20 候选时, 整张决策卡不能撑破右侧
// 聊天面板 (MP-Lab-02-Matrix-1999-Dominant 现场). 候选列表自身滚动
// (max-h-96 ≈ 3 candidate cards, overflow-y-auto), 标题 / 提交按钮 /
// ack 状态保留在滚动区外, 不被吞. select_primary_video /
// select_subtitles / source_cleanup_action / 通用 fallback 共用本容器
// 避免各自重复 max-h 设置. 桌面右侧面板占 ~40vh (max-h-96 = 24rem ≈
// 384px, 1080p 视口下接近 40vh), 移动端也不会撑破页面.
function ScrollableDecisionOptions({ children }: { children: React.ReactNode }) {
  return (
    <div
      data-testid="decision-options-scroll"
      className="max-h-96 overflow-y-auto pr-1"
    >
      {children}
    </div>
  )
}

function ComplexInputFileOptions({
  options,
  decisionType,
  selectedOption,
  onSelect,
  disabled,
}: {
  options: Record<string, unknown>[]
  decisionType: 'select_primary_video' | 'select_subtitles'
  selectedOption: string | null
  onSelect: (optId: string) => void
  disabled: boolean
}) {
  const { t } = useTranslation()
  return (
    <div className="space-y-2">
      {options.map((opt, i) => {
        const optId = typeof opt.id === 'string' ? opt.id : String(opt.id ?? i)
        const isNoSubtitles = decisionType === 'select_subtitles' && optId === 'no_subtitles'
        const optLabel = isNoSubtitles
          ? t('agent.complexInput.noSubtitles')
          : (typeof opt.label === 'string' ? opt.label : '')
        const optDescription = typeof opt.description === 'string' ? opt.description : ''
        const optPayload = (opt.payload && typeof opt.payload === 'object') ? opt.payload as Record<string, unknown> : {}
        const optSize = typeof optPayload.size_bytes === 'number' ? optPayload.size_bytes : null
        return (
          <button
            key={optId}
            type="button"
            disabled={disabled}
            onClick={() => onSelect(optId)}
            className={`w-full rounded-md border px-3 py-2.5 text-left text-sm transition-colors ${
              selectedOption === optId
                ? 'border-primary bg-primary/10 text-surface-foreground'
                : 'border-border bg-background text-surface-foreground hover:bg-muted/50'
            } disabled:opacity-50`}
          >
            <div className="flex items-baseline justify-between gap-2">
              <span className="font-medium break-all">{optLabel}</span>
              {optSize != null ? (
                <span className="shrink-0 text-xs text-muted-foreground">
                  {formatFileSize(optSize)}
                </span>
              ) : null}
            </div>
            {optDescription ? (
              <p className="mt-1 text-xs text-muted-foreground">{optDescription}</p>
            ) : null}
          </button>
        )
      })}
    </div>
  )
}

function SelectMetadataCandidateOptions({
  options,
  selectedOption,
  onSelect,
  disabled,
}: {
  options: Record<string, unknown>[]
  selectedOption: string | null
  onSelect: (optId: string) => void
  disabled: boolean
}) {
  const { t } = useTranslation()
  return (
    <div className="space-y-2">
      {options.map((opt, i) => {
        const optId = typeof opt.id === 'string' ? opt.id : String(opt.id ?? i)
        const optLabel = typeof opt.label === 'string' ? opt.label : ''
        const optDescription = typeof opt.description === 'string' ? opt.description : ''
        const optPayload = (opt.payload && typeof opt.payload === 'object') ? opt.payload as Record<string, unknown> : {}
        const mediaType = typeof optPayload.media_type === 'string' ? optPayload.media_type : ''
        const provider = typeof optPayload.provider === 'string' ? optPayload.provider : ''
        const confidence =
          typeof optPayload.confidence === 'number' ? optPayload.confidence : null
        const overview = typeof optPayload.overview === 'string' ? optPayload.overview : ''
        const year = typeof optPayload.year === 'number' ? optPayload.year : null
        return (
          <button
            key={optId}
            type="button"
            disabled={disabled}
            onClick={() => onSelect(optId)}
            className={`w-full rounded-md border px-3 py-2.5 text-left text-sm transition-colors ${
              selectedOption === optId
                ? 'border-primary bg-primary/10 text-surface-foreground'
                : 'border-border bg-background text-surface-foreground hover:bg-muted/50'
            } disabled:opacity-50`}
          >
            <div className="flex items-baseline justify-between gap-2">
              <span className="font-medium break-all">
                {optLabel}
                {year != null ? <span className="ml-1 text-xs text-muted-foreground">({year})</span> : null}
              </span>
              {confidence != null ? (
                <span className="shrink-0 text-xs text-muted-foreground">
                  {t('agent.candidate.confidence', { value: confidence.toFixed(2) })}
                </span>
              ) : null}
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
              {mediaType ? <span>{t('agent.candidate.mediaType', { value: mediaType })}</span> : null}
              {provider ? <span>· {t('agent.candidate.provider', { value: provider })}</span> : null}
            </div>
            {overview ? (
              <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{overview}</p>
            ) : null}
            {optDescription ? (
              <p className="mt-1 text-xs text-muted-foreground">{optDescription}</p>
            ) : null}
          </button>
        )
      })}
    </div>
  )
}

function formatFileSize(sizeBytes: number): string {
  if (sizeBytes < 1024) return `${sizeBytes} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let value = sizeBytes / 1024
  let unitIndex = 0
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024
    unitIndex += 1
  }
  return `${value.toFixed(1)} ${units[unitIndex]}`
}

function DecisionReplyCard({
  decision,
  taskId,
  service,
  onSubmitted,
}: {
  decision: AgentDecisionDto
  taskId: string
  service: AgentService
  /**
   * 提交按钮被点击后立即触发(早于 mutate). AgentPanel 用来强制滚到底,
   * 无需等 refetch 把 decision 标为 decided. 错误回退 (error 状态退出 ack
   * 回到表单) 与本回调正交, 不受 onSubmitted 影响.
   */
  onSubmitted?: () => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { showToast } = useToast()
  const [selectedOption, setSelectedOption] = useState<string | null>(null)
  const [freeText, setFreeText] = useState('')
  // 乐观 ack: 用户点提交后立即切到 "已提交, Agent 正在继续处理", 整段
  // 选项 / 文本框 / 按钮全部不渲染, 避免误以为"没反应"再点一次.
  // onError 退出 ack 回到可重试状态; onSuccess 不重置, 等 refetch 把
  // decision 标为 decided 后整张卡自然卸载.
  const [submitted, setSubmitted] = useState(false)

  const replyMutation = useMutation({
    mutationFn: () =>
      service.replyToAgentDecision(
        decision.id,
        selectedOption ?? undefined,
        freeText.trim() || undefined,
      ),
    onSuccess: async (data) => {
      // 详情页 query 立即 refetch: 用户正在看, 状态标签 / Agent 面板右上
      // / pending decision 卸载必须与 toast 同步出现. 旧实现只用
      // invalidateQueries, 异步 refetch, 用户会看到 "点了好像没反应"
      // 几秒后才更新. 列表 query 仍用 invalidate, 详情页不在时不强求
      // 立即刷新.
      //
      // task-detail MUST await — metadata_published 时 toast 的 title
      // 从 ['task-detail', taskId] 缓存读, 任务 title 是本次确认后才写入
      // 的, 不 await 会读到旧 title / source_path basename.
      const taskDetailPromise = queryClient.refetchQueries({
        queryKey: ['task-detail', taskId],
      })
      void queryClient.refetchQueries({ queryKey: ['agent-decisions', taskId] })
      void queryClient.refetchQueries({ queryKey: ['agent-messages', taskId] })
      void queryClient.refetchQueries({ queryKey: ['agent-tool-calls', taskId] })
      void queryClient.invalidateQueries({ queryKey: ['flows'] })
      void queryClient.invalidateQueries({ queryKey: ['tasks'] })

      // 仅当 select_metadata_candidate 用户选候选并入库成功时弹入库成功
      // toast. 其它 decision success (target_conflict_overwritten /
      // target_conflict_pending / source_cleanup_kept /
      // manual_selection_cancelled / completed) 不弹入库成功 toast,
      // 走原行为 (无 toast, ack 视图等 decision refetch 后自然卸载).
      // data 是 ApiEnvelope<AgentRunResult>, data.data.status 是后端
      // decision_reply 返回的 result.status.
      const resultStatus = data?.data?.status
      if (resultStatus === 'metadata_published') {
        // 等 task-detail refetch 完成, 再从缓存读最新 title. 旧实现
        // 立即 getQueryData 读到的是 stale title (入库前 task.title
        // 还是 "" / 老的 source_path basename), 用户看到 toast 用旧
        // 文件名.
        let cached: TaskDetailEnvelope | undefined
        try {
          await taskDetailPromise
          cached = queryClient.getQueryData<TaskDetailEnvelope>(['task-detail', taskId])
        } catch {
          // refetch 失败 (网络 / 后端 5xx). 退而求其次用旧缓存, 让
          // toast 至少能弹出来, 不阻断成功反馈.
          cached = queryClient.getQueryData<TaskDetailEnvelope>(['task-detail', taskId])
        }
        const task = cached?.data?.task
        const titleFromCache = task?.title?.trim() || ''
        const titleFromPath = (task?.source_path ?? '').split('/').filter(Boolean).pop() || ''
        const title = titleFromCache || titleFromPath || t('agent.taskTitleFallback')
        showToast(t('agent.metadataPublished', { title }), 'success')
      }
    },
    onError: (err) => {
      // ApiError 透传自后端 v1.py 的 JSONResponse 409/422.
      // 用户必须看到错误 (而不是"加载一会什么也没发生"),
      // db_locked / invalid_video_source / movie_write_failed 这类
      // 可恢复错误会带 retryable=true, 给出明确提示并提示重试.
      const apiErr = err as Error & {
        code?: string
        status?: number
        retryable?: boolean
      }
      const message = apiErr.message || t('agent.replyFailed')
      showToast(message, 'error')
      // 退出 ack 状态, 让用户重新点提交; 选项 / 文本框 / 提交按钮重新出现.
      setSubmitted(false)
      // 让决策 / 消息 / 任务详情刷新 — 决策可能已被 server 标 decided,
      // 也可能保持 pending (e.g. overwrite 失败). 都拉一次最新.
      queryClient.invalidateQueries({ queryKey: ['agent-decisions', taskId] })
      queryClient.invalidateQueries({ queryKey: ['task-detail', taskId] })
    },
  })

  const canSubmit = (selectedOption != null || (decision.free_text_allowed && freeText.trim())) && !replyMutation.isPending
  // ack 视图: 提交后(成功 pending 或 success, 但 error 状态已回退)展示
  // 轻量"已提交"文案 + 小 spinner. 任何 error 状态都退出 ack 回到表单.
  const showAck = submitted && !replyMutation.isError

  const handleSubmitClick = () => {
    if (!canSubmit) return
    setSubmitted(true)
    onSubmitted?.()
    replyMutation.mutate()
  }

  if (showAck) {
    return (
      <div className="space-y-2 p-4" data-testid="decision-reply-ack">
        <p className="text-xs font-medium text-amber-600">{t('agent.pendingDecision')}</p>
        <p className="mt-1 text-sm font-medium text-surface-foreground">{decision.question}</p>
        <div className="mt-3 flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          <span data-testid="decision-reply-ack-text">{t('agent.replySubmitted')}</span>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-3 p-4">
      <div>
        <p className="text-xs font-medium text-amber-600">{t('agent.pendingDecision')}</p>
        <p className="mt-1 text-sm font-medium text-surface-foreground">{decision.question}</p>
      </div>

      {decision.decision_type === 'target_conflict' && decision.payload ? (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-xs">
          <div className="font-medium text-amber-700">{t('agent.targetConflict.targetDir')}</div>
          <div className="font-mono break-all text-amber-900">
            {String(decision.payload.final_target_dir ?? '')}
          </div>
          <div className="mt-1 font-medium text-amber-700">{t('agent.targetConflict.targetFile')}</div>
          <div className="font-mono break-all text-amber-900">
            {String(decision.payload.final_target_file ?? '')}
          </div>
          {decision.payload.conflict ? (
            <div className="mt-1 text-amber-700">
              {t('agent.targetConflict.reason')}: {String(decision.payload.conflict)}
            </div>
          ) : null}
        </div>
      ) : null}

      {decision.decision_type === 'select_primary_video' && decision.options.length > 0 ? (
        <ScrollableDecisionOptions>
          <ComplexInputFileOptions
            options={decision.options}
            decisionType="select_primary_video"
            selectedOption={selectedOption}
            onSelect={(optId) => {
              setSelectedOption(selectedOption === optId ? null : optId)
              setFreeText('')
            }}
            disabled={replyMutation.isPending}
          />
        </ScrollableDecisionOptions>
      ) : null}

      {decision.decision_type === 'select_subtitles' && decision.options.length > 0 ? (
        <ScrollableDecisionOptions>
          <ComplexInputFileOptions
            options={decision.options}
            decisionType="select_subtitles"
            selectedOption={selectedOption}
            onSelect={(optId) => {
              setSelectedOption(selectedOption === optId ? null : optId)
              setFreeText('')
            }}
            disabled={replyMutation.isPending}
          />
        </ScrollableDecisionOptions>
      ) : null}

      {decision.decision_type === 'review_complex_input' ? (
        <p className="text-xs text-muted-foreground">
          {t('agent.complexInput.reviewHint')}
        </p>
      ) : null}

      {decision.decision_type === 'select_metadata_candidate' && decision.options.length > 0 ? (
        <ScrollableDecisionOptions>
          <SelectMetadataCandidateOptions
            options={decision.options}
            selectedOption={selectedOption}
            onSelect={(optId) => {
              setSelectedOption(selectedOption === optId ? null : optId)
              setFreeText('')
            }}
            disabled={replyMutation.isPending}
          />
        </ScrollableDecisionOptions>
      ) : null}

      {decision.options.length > 0 &&
      decision.decision_type !== 'select_primary_video' &&
      decision.decision_type !== 'select_subtitles' &&
      decision.decision_type !== 'select_metadata_candidate' ? (
        <ScrollableDecisionOptions>
          <div className="space-y-2">
          {decision.options.map((opt, i) => {
            const optId = typeof opt.id === 'string' ? opt.id : String(opt.id ?? i)
            const optLabel =
              decision.decision_type === 'source_cleanup_action'
                ? t(`agent.sourceCleanupAction.${optId}`, {
                    defaultValue: typeof opt.label === 'string' ? opt.label : JSON.stringify(opt),
                  })
                : typeof opt.label === 'string'
                ? opt.label
                : JSON.stringify(opt)
            const optDesc =
              decision.decision_type === 'source_cleanup_action'
                ? t(`agent.sourceCleanupAction.${optId}Desc`, { defaultValue: '' })
                : typeof opt.description === 'string'
                ? opt.description
                : undefined
            return (
              <button
                key={optId}
                type="button"
                disabled={replyMutation.isPending}
                onClick={() => {
	                  setSelectedOption(selectedOption === optId ? null : optId);
	                  setFreeText('');
	                }}
                className={`w-full rounded-md border px-3 py-2.5 text-left text-sm transition-colors ${
                  selectedOption === optId
                    ? 'border-primary bg-primary/10 text-surface-foreground'
                    : 'border-border bg-background text-surface-foreground hover:bg-muted/50'
                } disabled:opacity-50`}
              >
                <span className="font-medium">{optLabel}</span>
                {optDesc ? <span className="ml-2 text-xs text-muted-foreground">{optDesc}</span> : null}
              </button>
            )
          })}
          </div>
        </ScrollableDecisionOptions>
      ) : null}

      {decision.free_text_allowed ? (
        <textarea
          className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-surface-foreground outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-primary disabled:opacity-50"
          rows={2}
          placeholder={t('agent.replyFreeText')}
          value={freeText}
          onChange={(e) => {
                    setFreeText(e.target.value)
                    setSelectedOption(null)
                  }}
          disabled={replyMutation.isPending}
        />
      ) : null}

      {replyMutation.isError ? (
        <p className="text-xs text-red-500" data-testid="decision-reply-error">
          {replyMutation.error instanceof Error ? replyMutation.error.message : t('agent.replyFailed')}
        </p>
      ) : null}

      {replyMutation.isSuccess ? (
        <p className="text-xs text-green-500">{t('agent.replySuccess')}</p>
      ) : null}

      <div className="flex justify-end">
        <button
          type="button"
          disabled={!canSubmit}
          onClick={handleSubmitClick}
          className="inline-flex items-center gap-1.5 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {replyMutation.isPending ? (
            <>
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              {t('agent.replying')}
            </>
          ) : (
            <>
              <Send className="h-3.5 w-3.5" />
              {t('agent.reply')}
            </>
          )}
        </button>
      </div>
    </div>
  )
}

function FreeformInput({
  taskId,
  isDisabled,
  onSent,
  onStreamingUpdate,
  onMessagesRefresh,
}: {
  taskId: string
  service: AgentService
  isDisabled: boolean
  /** 用户成功发送一条自由消息时触发 — AgentPanel 用来强制滚到底 */
  onSent?: () => void
  /** streaming 文本 / tool_call 状态变化时触发 — AgentPanel 用来"接近底部则跟随" */
  onStreamingUpdate?: () => void
  onMessagesRefresh: () => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [inputText, setInputText] = useState('')
  const [streamingText, setStreamingText] = useState('')
  const [streamingTool, setStreamingTool] = useState<string | null>(null)
  const [isStreaming, setIsStreaming] = useState(false)
  const [streamError, setStreamError] = useState<string | null>(null)

  const sendMutation = useMutation({
    mutationFn: async (message: string) => {
      const baseUrl = import.meta.env.VITE_API_BASE_URL || ''
      const streamUrl = `${baseUrl}/api/v1/tasks/${taskId}/agent-runs/stream`

      setStreamingText('')
      setStreamingTool(null)
      setStreamError(null)
      setIsStreaming(true)

      let response: Response
      try {
        response = await fetch(streamUrl, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message }),
        })
      } catch {
        // Network error before any run was created — no fallback POST
        setIsStreaming(false)
        throw new Error(t('agent.streamConnectFailed'))
      }

      if (!response.ok) {
        setIsStreaming(false)
        const errorText = await response.text()
        throw new Error(errorText || t('agent.sendFailed'))
      }

      const reader = response.body?.getReader()
      if (!reader) {
        setIsStreaming(false)
        throw new Error('No response body')
      }

      const decoder = new TextDecoder()
      let buffer = ''

      try {
        while (true) {
          const { done, value } = await reader.read()
          if (done) {
            // Process any remaining buffer
            if (buffer.trim()) {
              const parsed = _parseSSEBlock(buffer)
              if (parsed) {
                _applySSEEvent(parsed.event, parsed.data)
              }
            }
            break
          }

          buffer += decoder.decode(value, { stream: true })

          // Split into complete SSE event blocks (separated by \n\n)
          while (true) {
            const idx = buffer.indexOf('\n\n')
            if (idx === -1) break
            const block = buffer.slice(0, idx)
            buffer = buffer.slice(idx + 2)
            const parsed = _parseSSEBlock(block)
            if (parsed) {
              _applySSEEvent(parsed.event, parsed.data)
            }
          }
        }
      } catch {
        // Stream disconnected mid-read — run continues on backend, refresh to poll
        setIsStreaming(false)
        setStreamingText('')
        setStreamingTool(null)
        onMessagesRefresh()
        return { status: 'stream_disconnected' }
      }

      setIsStreaming(false)
      setStreamingText('')
      setStreamingTool(null)
      return { status: 'success', data: { status: 'completed' } }
    },
    onSuccess: () => {
      setInputText('')
      queryClient.invalidateQueries({ queryKey: ['agent-messages', taskId] })
      queryClient.invalidateQueries({ queryKey: ['agent-decisions', taskId] })
      queryClient.invalidateQueries({ queryKey: ['agent-tool-calls', taskId] })
      queryClient.invalidateQueries({ queryKey: ['task-detail', taskId] })
      onMessagesRefresh()
    },
    onError: () => {
      setIsStreaming(false)
      setStreamingText('')
      setStreamingTool(null)
    },
  })

  // ── streaming 滚动触发 ──
  // 不在 _applySSEEvent 同步分支里调 onStreamingUpdate, 而是 useEffect 监听
  // streamingText / streamingTool 实际 state 变化, 在 DOM commit 之后 (rAF)
  // 才触发回调. 修复前的 BUG: setStreamingText 同步分支立即调 onStreamingUpdate,
  // 此时 DOM 还没 commit, scrollToBottom 读到的还是旧 scrollHeight.
  //
  // callback 用 useRef 镜像: useEffect 依赖里不直接放 onStreamingUpdate
  // (parent 每次 render 都可能给一个新闭包), 这样 effect 不会因 callback
  // 引用变化而重复触发, 行为更稳定.
  const onStreamingUpdateRef = useRef(onStreamingUpdate)
  useEffect(() => {
    onStreamingUpdateRef.current = onStreamingUpdate
  }, [onStreamingUpdate])
  useEffect(() => {
    if (streamingText === '' && streamingTool === null) return
    const id = requestAnimationFrame(() => onStreamingUpdateRef.current?.())
    return () => cancelAnimationFrame(id)
  }, [streamingText, streamingTool])

  function _applySSEEvent(event: string, data: Record<string, unknown>) {
    switch (event) {
      case 'assistant_delta':
        if (typeof data.delta === 'string') {
          setStreamingText((prev) => prev + data.delta)
        }
        break
      case 'tool_call_started':
        if (typeof data.tool_name === 'string') {
          setStreamingTool(data.tool_name)
        }
        break
      case 'tool_call_finished':
        setStreamingTool(null)
        break
      case 'error':
        if (typeof data.error === 'string') {
          setStreamError(data.error)
        }
        break
      // user_message, assistant_message, decision_created, run_finished:
      // no immediate UI update needed — refetch handles them
    }
  }

  const handleSubmit = () => {
    const trimmed = inputText.trim()
    if (!trimmed || isDisabled || sendMutation.isPending || isStreaming) return
    onSent?.()
    sendMutation.mutate(trimmed)
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  return (
    <div className="p-3 space-y-2">
      {/* streaming tool status */}
      {streamingTool ? (
        <div className="flex items-center gap-1.5 px-1 text-xs text-blue-500">
          <Loader2 className="h-3 w-3 animate-spin" />
          <span>{t('agent.callingTool')}: {streamingTool}</span>
        </div>
      ) : null}

      {/* live assistant text */}
      {streamingText ? (
        <div className="max-h-24 overflow-y-auto rounded-md border border-border bg-muted/30 p-2 text-xs text-surface-foreground whitespace-pre-wrap">
          {streamingText}
          {isStreaming ? <span className="inline-block w-1.5 h-4 bg-blue-500 animate-pulse ml-0.5 align-middle" /> : null}
        </div>
      ) : null}

      {/* error display */}
      {streamError ? (
        <p className="text-xs text-red-500 px-1">{streamError}</p>
      ) : null}
      {sendMutation.isError && !streamError ? (
        <p className="text-xs text-red-500 px-1">
          {sendMutation.error instanceof Error ? sendMutation.error.message : t('agent.sendFailed')}
        </p>
      ) : null}

      {/* input area */}
      <div className="flex items-end gap-2">
        <textarea
          className="flex-1 resize-none rounded-md border border-border bg-background px-3 py-2 text-sm text-surface-foreground focus:outline-none focus:ring-1 focus:ring-primary disabled:cursor-not-allowed disabled:opacity-50"
          rows={2}
          value={inputText}
          onChange={(e) => setInputText(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={isDisabled || sendMutation.isPending || isStreaming}
        />
        <button
          type="button"
          disabled={!inputText.trim() || isDisabled || sendMutation.isPending || isStreaming}
          onClick={handleSubmit}
          className="inline-flex items-center gap-1 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50 shrink-0"
        >
          {sendMutation.isPending || isStreaming ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Send className="h-4 w-4" />
          )}
        </button>
      </div>
      <p className="text-xs text-muted-foreground px-1">
        {t('agent.freeformHint')}
      </p>
    </div>
  )
}


/** Parse a single SSE event block (lines terminated by \n\n) into {event, data}. */
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


export function AgentPanel({
  taskId,
  agentStatus,
  taskStatus,
  service = defaultTaskService,
}: AgentPanelProps) {
  const { t } = useTranslation()
  const toast = useToast()

  const hasAgent = agentStatus != null && agentStatus.run_status !== 'none'
  const isActive = agentStatus?.run_status === 'active'
  const taskDeleted = taskStatus === 'deleted'
  const pollMs = isActive ? 5000 : false

  const messagesQuery = useQuery({
    queryKey: ['agent-messages', taskId],
    queryFn: () => service.listAgentMessages(taskId),
    enabled: hasAgent,
    refetchInterval: pollMs,
  })

  const decisionsQuery = useQuery({
    queryKey: ['agent-decisions', taskId],
    queryFn: () => service.listAgentDecisions(taskId),
    enabled: hasAgent,
    refetchInterval: pollMs,
  })

  const toolCallsQuery = useQuery({
    queryKey: ['agent-tool-calls', taskId],
    queryFn: () => service.listAgentToolCalls(taskId),
    enabled: hasAgent,
    refetchInterval: pollMs,
  })

  const queryClient = useQueryClient()

  // ── 智能滚动跟随 ──
  // 必须在 retryMutation 之前调用, 因为 retry onSuccess 闭包依赖 scrollToBottom
  // 函数引用. useFollowScroll 内部已用 useCallback 锁住, 重渲染引用稳定.
  const scrollRef = useRef<HTMLDivElement>(null)
  const { scrollToBottom, onContentChange } = useFollowScroll(scrollRef)

  const retryMutation = useMutation({
    mutationFn: () => service.createAgentRun(taskId),
    onSuccess: () => {
      // POST 成功: 立即 refetch 4 个 key, 触发数据更新 (不依赖 invalidate
      // 自然触发 / 不依赖轮询周期). 父组件 `TaskDetailPage` 持有 task-detail
      // query, refetch 后会把新 agentStatus / taskStatus 透传过来, 触发
      // `agentRunning` boolean 切换到 true, 进入正常 active UI.
      // 显式 scrollToBottom 把"新 run 启动后的初始 message / spinner"
      // 滚入视野, 即便用户上翻离开底部 (retry 视作"用户主动行为").
      void queryClient.refetchQueries({ queryKey: ['task-detail', taskId] })
      void queryClient.refetchQueries({ queryKey: ['agent-messages', taskId] })
      void queryClient.refetchQueries({ queryKey: ['agent-decisions', taskId] })
      void queryClient.refetchQueries({ queryKey: ['agent-tool-calls', taskId] })
      scrollToBottom('auto')
    },
    onError: () => {
      // 失败双通道反馈: 组件内红色 error 条由 retryMutation.isError 块保留,
      // 这里补一个全局 toast 通知, 与站内其它 mutation 失败行为对齐.
      toast.showToast(t('agent.retryFailed'), 'error')
    },
  })

  // ── 卡住 Agent 恢复 mutation ──
  // 与 retry 严格区分: 入口不同 (POST /recover-stuck), 语义不同
  // (active run + 无 pending 时才允许; retry 只用于 failed). 失败 / 成功
  // 反馈通道与 retry 对齐, 沿用 4-key refetch 模式.
  const recoverMutation = useMutation({
    mutationFn: () => service.recoverStuckAgentRun(taskId),
    onSuccess: () => {
      void queryClient.refetchQueries({ queryKey: ['task-detail', taskId] })
      void queryClient.refetchQueries({ queryKey: ['agent-messages', taskId] })
      void queryClient.refetchQueries({ queryKey: ['agent-decisions', taskId] })
      void queryClient.refetchQueries({ queryKey: ['agent-tool-calls', taskId] })
      void queryClient.invalidateQueries({ queryKey: ['flows'] })
      void queryClient.invalidateQueries({ queryKey: ['tasks'] })
    },
    onError: () => {
      toast.showToast(t('agent.recoverStuckFailed'), 'error')
    },
  })

  // ── retry / agent running 状态机 ──
  // retrySubmitting: 仅覆盖 retry POST 请求生命周期, 驱动按钮 disabled + spinner.
  // agentRunning:    来自 server-side 任务 / run 状态, 驱动顶部 / 底部 "Agent
  //                  正在处理中" 提示 + FreeformInput disabled. 两者 MUST 严格
  //                  解耦 — retry POST resolve 后 retrySubmitting 立即变 false,
  //                  agentRunning 仍由 props 决定 (可能要等父组件透传新
  //                  agentStatus 才变 true).
  // 后续 PR 不得把两者合并 (e.g. `const isProcessing = retrySubmitting || agentRunning`),
  // 否则会复现"重试按钮 loading 长延展" 的回归 (USBA-089 后置 pipeline 收口).
  const retrySubmitting = retryMutation.isPending
  const agentRunning = isActive || taskStatus === 'agent_running'

  // 切 taskId 时强制滚到底, 丢弃之前任务的滚动位置.
  useEffect(() => {
    scrollToBottom('auto')
  }, [taskId, scrollToBottom])

  // 内容变化(append-only): 仅在 follow=true 时温和跟随, 用户上翻时保留.
  // 依赖用基础类型 (number / string), 避免每次 query 返回新数组导致重复触发.
  // 这里依赖 messagesQuery.data / decisionsQuery.data 读取必须在 isLoading
  // 早退前完成 — 由于 React 渲染是先跑 hooks 再判定条件, 这里是安全的.
  const messages = (messagesQuery.data?.data ?? []).filter((m) => m.role !== 'system')
  const visibleMessages = messages.filter((m) => m.role !== 'tool')
  const toolDetails = toolCallsQuery.data?.data ?? []
  const pendingDecision = (decisionsQuery.data?.data ?? []).find((d) => d.status === 'pending')

  useEffect(() => {
    onContentChange()
  }, [messages.length, pendingDecision?.id, toolDetails.length, onContentChange])

  // 用户主动发送 / 提交: 强制滚到底, 不论 follow 状态.
  // - 自由消息发送: handleFreeformSent (FreeformInput.onSent)
  // - decision 提交: DecisionReplyCard.onSubmitted (handleSubmitClick 同步调, 不等 refetch)
  const handleFreeformSent = useCallback(() => {
    scrollToBottom('smooth')
  }, [scrollToBottom])
  // streaming 触发: 走 onContentChange, 保留 follow 语义 (用户上翻时不强制拉回).
  // 用 useCallback 锁住引用, FreeformInput 的 useEffect 依赖 streamingText
  // / streamingTool, 不依赖 callback 引用, 锁住主要是为了避免父组件重渲染
  // 时给子组件一个新闭包 (调试时容易发现"每次 render 都触发"的疑点).
  const handleStreamingUpdate = useCallback(() => {
    onContentChange()
  }, [onContentChange])
  const handleDecisionSubmitted = useCallback(() => {
    scrollToBottom('smooth')
  }, [scrollToBottom])

  // ── empty state ──
  if (!hasAgent) {
    return (
      <aside className="flex flex-col rounded-lg border border-border bg-surface" style={{ minHeight: 400 }}>
        <div className="flex items-center gap-2 border-b border-border px-4 py-3">
          <Bot className="h-4 w-4 text-muted-foreground" />
          <h2 className="text-sm font-medium text-surface-foreground">{t('agent.messages')}</h2>
        </div>
        <div className="flex flex-1 flex-col items-center justify-center gap-2 p-6 text-center">
          <Bot className="h-8 w-8 text-muted-foreground/50" />
          <p className="text-sm font-medium text-muted-foreground">{t('agent.noAgent')}</p>
          <p className="text-xs text-muted-foreground">{t('agent.noAgentDesc')}</p>
        </div>
      </aside>
    )
  }

  // ── loading ──
  if (messagesQuery.isLoading) {
    return (
      <aside className="flex flex-col rounded-lg border border-border bg-surface" style={{ minHeight: 400 }}>
        <div className="flex items-center gap-2 border-b border-border px-4 py-3">
          <Bot className="h-4 w-4 text-muted-foreground" />
          <h2 className="text-sm font-medium text-surface-foreground">{t('agent.messages')}</h2>
          {isActive ? (
            <Loader2 className="ml-auto h-3.5 w-3.5 animate-spin text-blue-500" />
          ) : null}
        </div>
        <div className="flex-1 space-y-3 p-4">
          <SkeletonBlock className="h-16" />
          <SkeletonBlock className="h-12" />
          <SkeletonBlock className="h-20" />
        </div>
      </aside>
    )
  }

  return (
    <aside className="flex flex-col rounded-lg border border-border bg-surface" style={{ minHeight: 400, maxHeight: 'calc(100vh - 180px)' }}>
      {/* header */}
      <div className="flex shrink-0 items-center gap-2 border-b border-border px-4 py-3">
        <Bot className="h-4 w-4 text-muted-foreground" />
        <h2 className="text-sm font-medium text-surface-foreground">{t('agent.messages')}</h2>
        {agentRunning && !pendingDecision && isActive ? (
          // 卡住 Agent 恢复按钮: 仅在 active + 无 pending decision 时显示.
          // 语义上与 failed 时的 retry 严格区分 — retry 重试失败任务,
          // recover 主动接管疑似卡住的 active run. 同一时刻只渲染一个.
          <div className="ml-auto flex items-center gap-2">
            <span className="inline-flex items-center gap-1 text-xs text-blue-500">
              <Loader2 className="h-3 w-3 animate-spin" />
              {t('agent.processing')}
            </span>
            <button
              type="button"
              data-testid="recover-stuck-agent"
              disabled={recoverMutation.isPending}
              onClick={() => recoverMutation.mutate()}
              className="inline-flex items-center gap-1 rounded-md border border-amber-500/40 bg-background px-2 py-0.5 text-xs font-medium text-amber-700 hover:bg-amber-500/10 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {recoverMutation.isPending ? (
                <>
                  <Loader2 className="h-3 w-3 animate-spin" />
                  {t('agent.recovering')}
                </>
              ) : (
                <>
                  <RefreshCw className="h-3 w-3" />
                  {t('agent.recoverStuckAgent')}
                </>
              )}
            </button>
          </div>
        ) : null}
        {agentRunning && pendingDecision ? (
          <span className="ml-auto inline-flex items-center gap-1 text-xs text-blue-500">
            <Loader2 className="h-3 w-3 animate-spin" />
            {t('agent.processing')}
          </span>
        ) : null}
        {agentStatus?.run_status === 'waiting_user' ? (
          <span className="ml-auto inline-flex items-center gap-1 text-xs font-medium text-amber-500">
            {t('agent.waitingUser')}
          </span>
        ) : null}
        {agentStatus?.run_status === 'completed' ? (
          <span className="ml-auto text-xs text-muted-foreground">{t('agent.completed')}</span>
        ) : null}
        {agentStatus?.run_status === 'failed' ? (
          <div className="ml-auto flex items-center gap-2">
            <span className="text-xs text-red-500">{t('agent.failed')}</span>
            <button
              type="button"
              disabled={retrySubmitting}
              onClick={() => retryMutation.mutate()}
              className="inline-flex items-center gap-1 rounded-md border border-border bg-background px-2 py-0.5 text-xs font-medium text-surface-foreground hover:bg-muted/50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {retrySubmitting ? (
                <>
                  <Loader2 className="h-3 w-3 animate-spin" />
                  {t('agent.retrying')}
                </>
              ) : (
                <>
                  <RefreshCw className="h-3 w-3" />
                  {t('agent.retryAgent')}
                </>
              )}
            </button>
          </div>
        ) : null}
      </div>

      {/* retry error/success feedback */}
      {retryMutation.isError ? (
        <div className="shrink-0 border-b border-border bg-red-500/5 px-4 py-2">
          <p className="text-xs text-red-500">
            {retryMutation.error instanceof Error ? retryMutation.error.message : t('agent.retryFailed')}
          </p>
        </div>
      ) : null}

      {/* recover-stuck error feedback — 组件内错误条, 与 retry 对齐, 让用户
          在面板内直接看到后端拒收原因 (waiting_user / pending / 终态 等). */}
      {recoverMutation.isError ? (
        <div
          className="shrink-0 border-b border-border bg-red-500/5 px-4 py-2"
          data-testid="recover-stuck-error"
        >
          <p className="text-xs text-red-500">
            {recoverMutation.error instanceof Error ? recoverMutation.error.message : t('agent.recoverStuckFailed')}
          </p>
        </div>
      ) : null}

      {/* messages area */}
      <div
        ref={scrollRef}
        data-testid="agent-messages-scroll"
        className="flex-1 overflow-y-auto p-4"
      >
        {visibleMessages.length === 0 ? (
          <p className="text-center text-sm text-muted-foreground py-8">{t('agent.emptyMessages')}</p>
        ) : (
          <div className="space-y-3">
            {visibleMessages.map((msg) => (
              <MessageBubble key={msg.id} msg={msg} toolDetails={toolDetails} />
            ))}
          </div>
        )}
      </div>

      {/* bottom action area */}
      <div className="shrink-0 border-t border-border">
        {pendingDecision ? (
          <DecisionReplyCard
            decision={pendingDecision}
            taskId={taskId}
            service={service}
            onSubmitted={handleDecisionSubmitted}
          />
        ) : taskDeleted ? (
          <div className="p-3 text-center">
            <p className="text-xs text-muted-foreground">{t('agent.deletedTaskNoInput')}</p>
          </div>
        ) : agentRunning ? (
          <div className="p-3 text-center">
            <Loader2 className="mx-auto h-4 w-4 animate-spin text-muted-foreground" />
            <p className="mt-1 text-xs text-muted-foreground">{t('agent.processingWait')}</p>
          </div>
        ) : (
          <FreeformInput
            taskId={taskId}
            service={service}
            isDisabled={agentRunning}
            onSent={handleFreeformSent}
            onStreamingUpdate={handleStreamingUpdate}
            onMessagesRefresh={() => {
              messagesQuery.refetch()
              decisionsQuery.refetch()
              toolCallsQuery.refetch()
            }}
          />
        )}
      </div>
    </aside>
  )
}
