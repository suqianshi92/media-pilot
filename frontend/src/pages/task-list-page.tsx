import { useEffect, useMemo, useState } from 'react'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import i18n from '@/i18n'
import { Link, useSearchParams } from 'react-router-dom'
import { createColumnHelper, type ColumnDef } from '@tanstack/react-table'

import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  EmptyState,
  ErrorState,
  SkeletonBlock,
} from '@/components/app/shared-ui'
import { AgentRunStatusBadge } from '@/components/app/agent-run-status-badge'
import { getMediaTypeLabel, getTaskStepLabel, isDownloadActive, getTotalStatusLabel, getStatusColorClass, isNonTerminalStatus } from '@/components/app/task-labels'
import { useToast } from '@/components/shared/toast'
import { createTaskService, type TaskService } from '@/services/task-service'
import type { FlowFilter } from '@/services/api-client'
import type { FlowSummary } from '@/types/task'
import { DataTable } from '@/components/layout/data-table'
import { PageHeader, PageToolbar } from '@/components/layout/page-skeleton'
import { MoreHorizontal, Pause, Play, RotateCw, Trash2 } from 'lucide-react'

const IS_MOCK = import.meta.env.VITE_API_MODE === 'mock'
const defaultTaskService = createTaskService()
export type TaskListService = Pick<TaskService, 'listFlows' | 'tick' | 'retryDownloadSync' | 'deleteDownload' | 'deleteTask' | 'pauseDownload' | 'resumeDownload'>

// 任务列表分页状态由 React state 维护并透传给 /flows 后端.
// 任何"假装本地分页是全局分页"的写法都禁止出现.
const DEFAULT_PAGE_SIZE = 10
const PAGE_SIZE_OPTIONS = [10, 20, 50, 100]
const TASK_LIST_COLUMN_CLASSES: Record<string, string> = {
  total_status: 'w-[120px]',
  title: 'w-[360px]',
  agent: 'w-[130px]',
  file_format: 'w-[90px]',
  media_type: 'w-[90px]',
  source: 'w-[100px]',
  download: 'w-[140px]',
  updated_at: 'w-[110px]',
  actions: 'w-[160px]',
}

const filterKeys: Array<{ value: FlowFilter; key: string }> = [
  { value: 'all', key: 'taskList.filter_all' },
  { value: 'waiting_user', key: 'taskList.filter_pending' },
  { value: 'processing', key: 'taskList.filter_processing' },
  { value: 'library_import_complete', key: 'taskList.filter_completed' },
  { value: 'failed', key: 'taskList.filter_failed' },
]


function isDownloadOnlyFlow(f: FlowSummary) {
  return f.route_target === 'download_detail'
}

function compactPath(path: string) {
  const normalized = path.split('/').filter(Boolean)
  if (normalized.length <= 2) return path
  const prefix = normalized.slice(0, 2).join('/')
  const suffix = normalized.slice(-1)[0]
  return `/${prefix}/.../${suffix}`
}

function formatTaskTitle(task: FlowSummary) {
  if (!task.title) return i18n.t('taskList.untitledTask', '待识别任务')
  return task.year ? `${task.title} (${task.year})` : task.title
}

function formatTime(iso: string) {
  try {
    const normalized = /[+\-Z]/i.test(iso.slice(-6)) ? iso : iso + 'Z'
    const d = new Date(normalized)
    if (isNaN(d.getTime())) return iso.slice(0, 16)
    const locale = i18n.language === 'en' ? 'en-US' : 'zh-CN'
    const parts = new Intl.DateTimeFormat(locale, {
      timeZone: 'Asia/Shanghai',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).formatToParts(d)
    const get = (type: string) => parts.find((p) => p.type === type)?.value ?? ''
    return `${get('month')}/${get('day')} ${get('hour')}:${get('minute')}`
  } catch {
    return iso.slice(0, 16)
  }
}

function shouldAutoAdvance(flow: FlowSummary) {
  return isNonTerminalStatus(flow.total_status)
}

// route_target → 详情页 URL; 实际 ID 来自 ingest_task_id / download_task_id.
function flowDetailHref(flow: FlowSummary) {
  if (flow.route_target === 'task_detail' && flow.ingest_task_id) {
    return `/tasks/${flow.ingest_task_id}`
  }
  if (flow.route_target === 'download_detail' && flow.download_task_id) {
    return `/downloads/${flow.download_task_id}`
  }
  return '#'
}

function getFlowSourceLabel(flowType: FlowSummary['flow_type'], t: (key: string) => string) {
  if (flowType === 'managed_download') return t('taskList.systemDownload')
  if (flowType === 'download_only') return t('taskList.downloadOnly')
  return t('taskList.externalImport')
}

// ── 表格列定义 ──

const columnHelper = createColumnHelper<FlowSummary>()

const taskColumns = (t: (key: string) => string): ColumnDef<FlowSummary, any>[] => [
  columnHelper.accessor('total_status', {
    header: t('taskList.status'),
    cell: (info) => <TotalStatusBadge status={info.getValue()} />,
  }),
  columnHelper.accessor((row) => row, {
    id: 'title',
    header: t('taskList.title_col'),
    cell: (info) => {
      const f = info.getValue()
      const titleText = formatTaskTitle(f)
      return (
        <div className="min-w-0">
          <Link
            to={flowDetailHref(f)}
            className="font-medium text-surface-foreground hover:text-primary transition-colors block truncate"
            title={titleText}
          >
            {titleText}
          </Link>
          <p className="text-xs text-muted-foreground mt-0.5 truncate" title={f.source_path ?? ''}>
            {f.source_path ? compactPath(f.source_path) : '—'}
          </p>
        </div>
      )
    },
  }),
  columnHelper.accessor((row) => row.agent_status_summary, {
    id: 'agent',
    header: t('taskList.agentColumn'),
    cell: (info) => {
      const ag = info.getValue()
      if (!ag || ag.run_status === 'none') {
        return <span className="text-muted-foreground text-xs">—</span>
      }
      const labels: Record<string, string> = {
        active: t('agent.processing'),
        waiting_user: t('agent.waitingUser'),
        completed: t('agent.completed'),
        failed: t('agent.failed'),
      }
      // 用 shared AgentRunStatusBadge 保证颜色和详情页 StatusBadge
      // 三处一致 — active 蓝, waiting_user 琥珀, failed 玫红, completed 翡翠.
      return (
        <AgentRunStatusBadge
          runStatus={ag.run_status}
          label={labels[ag.run_status] ?? ag.run_status}
        />
      )
    },
  }),
  columnHelper.accessor('file_format', {
    header: t('taskList.fileFormat'),
    cell: (info) => <span className="text-muted-foreground">{info.getValue() ?? '—'}</span>,
  }),
  columnHelper.accessor('media_type', {
    header: t('taskList.contentType'),
    cell: (info) => (
      <span className="text-muted-foreground">{getMediaTypeLabel(info.getValue())}</span>
    ),
  }),
  columnHelper.accessor((row) => row.flow_type, {
    id: 'source',
    header: t('taskList.source'),
    cell: (info) => (
      <span className="text-muted-foreground text-xs">
        {getFlowSourceLabel(info.getValue(), t)}
      </span>
    ),
  }),
  columnHelper.accessor((row) => row.download_task?.status ?? null, {
    id: 'download',
    header: t('taskList.downloadStatus'),
    cell: (info) => {
      const dl = info.row.original.download_task
      if (!dl) return <span className="text-muted-foreground">—</span>

      if (isDownloadActive(dl.status)) {
        const pct = Math.round((dl.progress ?? 0) * 100)
        return (
          <div className="flex items-center gap-2 min-w-[100px]">
            <div className="h-1.5 flex-1 rounded bg-muted">
              <div
                className="h-1.5 rounded bg-blue-500 transition-all"
                style={{ width: `${pct}%` }}
              />
            </div>
            <span className="text-xs text-muted-foreground tabular-nums">{pct}%</span>
          </div>
        )
      }

      if (dl.status === 'failed' || dl.status === 'sync_failed') {
        return <span className="text-xs text-muted-foreground">{t('taskList.downloadFailed')}</span>
      }

      if (dl.status === 'paused') {
        return <span className="text-xs text-yellow-600">{t('taskList.paused')}</span>
      }

      return <span className="text-xs text-muted-foreground">{t('taskList.downloaded')}</span>
    },
  }),
  columnHelper.accessor('updated_at', {
    header: t('taskList.updatedAt'),
    cell: (info) => <span className="text-xs text-muted-foreground">{formatTime(info.getValue())}</span>,
  }),
  columnHelper.display({
    id: 'actions',
    header: t('taskList.actions'),
    cell: (info) => {
      const flow = info.row.original
      const isDownloadOnly = isDownloadOnlyFlow(flow)
      const dl = flow.download_task
      const canRetry = isDownloadOnly && (dl?.status === 'sync_failed' || dl?.status === 'failed')
      const isPublished = flow.status_summary?.status === 'library_import_complete' || flow.status_summary?.status === 'completed'
      const canDelete = isDownloadOnly || (!isPublished && !isDownloadOnly)
      const canPause = isDownloadOnly && dl != null && isDownloadActive(dl.status)
      const canResume = isDownloadOnly && dl?.status === 'paused'
      const meta = info.table.options.meta as Record<string, unknown> | undefined
      const onDeleteRequest = meta?.onDeleteRequest as ((f: FlowSummary) => void) | undefined
      const onPauseResume = meta?.onPauseResume as ((downloadId: string, action: 'pause' | 'resume') => void) | undefined
      const onRetrySync = meta?.onRetrySync as ((downloadId: string) => void) | undefined
      return (
        <div className="flex items-center gap-1.5">
          <Button asChild variant="secondary" size="sm">
            <Link to={flowDetailHref(flow)}>{t('common.detail')}</Link>
          </Button>
          {canPause && (
            <Button size="sm" variant="ghost" onClick={() => onPauseResume?.(dl!.id, 'pause')}>
              <Pause className="h-4 w-4" />
            </Button>
          )}
          {canResume && (
            <Button size="sm" variant="ghost" onClick={() => onPauseResume?.(dl!.id, 'resume')}>
              <Play className="h-4 w-4" />
            </Button>
          )}
          {isDownloadOnly && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button size="sm" variant="ghost">
                  <MoreHorizontal className="h-4 w-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                {canRetry && (
                  <DropdownMenuItem onClick={() => onRetrySync?.(dl!.id)}>
                    <RotateCw className="mr-2 h-4 w-4" />
                    {t('taskList.retrySync')}
                  </DropdownMenuItem>
                )}
                {canDelete && (
                  <DropdownMenuItem
                    onClick={() => onDeleteRequest?.(flow)}
                    className="text-destructive focus:text-destructive"
                  >
                    <Trash2 className="mr-2 h-4 w-4" />
                    {t('common.delete')}
                  </DropdownMenuItem>
                )}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
          {!isDownloadOnly && canDelete && (
            <Button
              size="sm"
              variant="secondary"
              className="text-red-600 hover:text-red-700 hover:bg-red-50 dark:hover:bg-red-950"
              onClick={() => onDeleteRequest?.(flow)}
            >
              {t('common.delete')}
            </Button>
          )}
        </div>
      )
    },
  }),
]

// ── 表格内状态徽章 ──

function TotalStatusBadge({ status }: { status: string }) {
  return (
    <span className={`inline-flex items-center rounded px-2 py-0.5 text-xs font-medium whitespace-nowrap ${getStatusColorClass(status)}`}>
      {getTotalStatusLabel(status)}
    </span>
  )
}

// ── 窄屏流程卡片（表格的 mobile fallback） ──

function FlowCard({ flow, onDeleteRequest, onPauseResume, onRetrySync }: { flow: FlowSummary; onDeleteRequest?: (f: FlowSummary) => void; onPauseResume?: (downloadId: string, action: 'pause' | 'resume') => void; onRetrySync?: (downloadId: string) => void }) {
  const { t } = useTranslation()
  const isDownloadOnly = isDownloadOnlyFlow(flow)
  const hasDownload = flow.flow_type === 'managed_download' && flow.download_task != null
  const dl = flow.download_task
  const _DOWNLOAD_PHASE_STATUSES = new Set([
    'submitting', 'submitted', 'downloading', 'awaiting_sync', 'completed', 'completed_pending_ingest', 'failed', 'sync_failed', 'paused',
  ])
  const isDownloadPhase = hasDownload && _DOWNLOAD_PHASE_STATUSES.has(flow.total_status)
  const isPublished = flow.status_summary?.status === 'library_import_complete' || flow.status_summary?.status === 'completed'
  const canDelete = isDownloadOnly || (!isPublished && !isDownloadOnly)
  const canPause = isDownloadOnly && dl != null && isDownloadActive(dl.status)
  const canResume = isDownloadOnly && dl?.status === 'paused'
  const canRetry = isDownloadOnly && (dl?.status === 'sync_failed' || dl?.status === 'failed')

  return (
    <article className="grid gap-3 rounded-lg border border-border bg-surface p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="grid min-w-0 gap-1">
          <TotalStatusBadge status={flow.total_status} />
          <h2 className="text-sm font-medium text-surface-foreground">{formatTaskTitle(flow)}</h2>
          <span className="font-mono text-xs text-muted-foreground truncate" title={flow.source_path ?? ''}>
            {flow.source_path ? compactPath(flow.source_path) : '—'}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-1">
          <Button asChild variant="secondary" size="sm">
            <Link to={flowDetailHref(flow)}>{t('common.detail')}</Link>
          </Button>
          {canPause && (
            <Button size="sm" variant="ghost" onClick={() => onPauseResume?.(dl!.id, 'pause')}>
              <Pause className="h-4 w-4" />
            </Button>
          )}
          {canResume && (
            <Button size="sm" variant="ghost" onClick={() => onPauseResume?.(dl!.id, 'resume')}>
              <Play className="h-4 w-4" />
            </Button>
          )}
          {isDownloadOnly && (canRetry || canDelete) && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button size="sm" variant="ghost" aria-label={t('taskList.moreActions')}>
                  <MoreHorizontal className="h-4 w-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                {canRetry && (
                  <DropdownMenuItem onClick={() => onRetrySync?.(dl!.id)}>
                    <RotateCw className="mr-2 h-4 w-4" />
                    {t('taskList.retrySync')}
                  </DropdownMenuItem>
                )}
                {canDelete && (
                  <DropdownMenuItem onClick={() => onDeleteRequest?.(flow)} className="text-destructive focus:text-destructive">
                    <Trash2 className="mr-2 h-4 w-4" />
                    {t('common.delete')}
                  </DropdownMenuItem>
                )}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
          {!isDownloadOnly && canDelete && (
            <Button
              size="sm"
              variant="secondary"
              className="text-red-600 hover:text-red-700 hover:bg-red-50 dark:hover:bg-red-950"
              onClick={() => onDeleteRequest?.(flow)}
            >
              {t('common.delete')}
            </Button>
          )}
        </div>
      </div>

      {hasDownload && isDownloadPhase && flow.download_task!.progress > 0 && (
        <div className="flex items-center gap-2">
          <div className="h-1.5 flex-1 rounded bg-muted">
            <div
              className="h-1.5 rounded bg-blue-500 transition-all"
              style={{ width: `${Math.min(flow.download_task!.progress * 100, 100)}%` }}
            />
          </div>
          <span className="text-xs text-muted-foreground">
            {(flow.download_task!.progress * 100).toFixed(0)}%
          </span>
        </div>
      )}

      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
        <div><dt className="text-muted-foreground">{t('taskList.fileFormat')}</dt><dd>{flow.file_format ?? '—'}</dd></div>
        <div><dt className="text-muted-foreground">{t('taskList.contentType')}</dt><dd>{getMediaTypeLabel(flow.media_type)}</dd></div>
        <div><dt className="text-muted-foreground">{t('taskList.status')}</dt><dd>{getTaskStepLabel(flow.status_summary?.current_step ?? null)}</dd></div>
        <div><dt className="text-muted-foreground">{t('taskList.source')}</dt><dd>{getFlowSourceLabel(flow.flow_type, t)}</dd></div>
      </dl>
    </article>
  )
}

// ── 主组件 ──

// ── 删除目标 ──

interface DeleteTarget {
  flow: FlowSummary
  isDownloadOnly: boolean
}

// ── 主组件 ──

export function TaskListPage({
  service = defaultTaskService,
}: {
  service?: TaskListService
}) {
  const { t } = useTranslation()
  const [searchParams, setSearchParams] = useSearchParams()
  const [deleteTarget, setDeleteTarget] = useState<DeleteTarget | null>(null)
  const [currentPage, setCurrentPage] = useState(1)
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE)
  const [requestedPage, setRequestedPage] = useState(1)
  const [requestedPageSize, setRequestedPageSize] = useState(DEFAULT_PAGE_SIZE)
  const urlFilter = searchParams.get('filter')
  const activeFilter: FlowFilter = (urlFilter && filterKeys.some(o => o.value === urlFilter))
    ? urlFilter as FlowFilter
    : 'all'

  const setActiveFilter = (filter: FlowFilter) => {
    if (filter === 'all') {
      setSearchParams({}, { replace: true })
    } else {
      setSearchParams({ filter }, { replace: true })
    }
    // 切换 filter 必须回到第 1 页, 旧 page 状态对新的 filter 无意义.
    setCurrentPage(1)
    setRequestedPage(1)
  }

  const flowsQuery = useQuery({
    queryKey: ['flows', activeFilter, requestedPage, requestedPageSize],
    queryFn: () => service.listFlows({
      filter: activeFilter,
      page: requestedPage,
      page_size: requestedPageSize,
    }),
    placeholderData: keepPreviousData,
  })

  // 4 个 StatCard 统计的是全局各 filter 的 total, 不得随当前页 items
  // 变化. 复用 listFlows 接口, page=1 page_size=1 拉到 meta.total 即
  // 可. 4 个 filter 各一个 useQuery, 互相独立缓存.
  const waitingUserTotalQuery = useQuery({
    queryKey: ['flows', 'filterTotal', 'waiting_user'] as const,
    queryFn: () => service.listFlows({ filter: 'waiting_user', page: 1, page_size: 1 }),
  })
  const processingTotalQuery = useQuery({
    queryKey: ['flows', 'filterTotal', 'processing'] as const,
    queryFn: () => service.listFlows({ filter: 'processing', page: 1, page_size: 1 }),
  })
  const completedTotalQuery = useQuery({
    queryKey: ['flows', 'filterTotal', 'library_import_complete'] as const,
    queryFn: () => service.listFlows({ filter: 'library_import_complete', page: 1, page_size: 1 }),
  })
  const failedTotalQuery = useQuery({
    queryKey: ['flows', 'filterTotal', 'failed'] as const,
    queryFn: () => service.listFlows({ filter: 'failed', page: 1, page_size: 1 }),
  })

  const queryClient = useQueryClient()
  const { showToast } = useToast()
  const isPageTransition = currentPage !== requestedPage || pageSize !== requestedPageSize

  useEffect(() => {
    if (!isPageTransition || !flowsQuery.isSuccess || flowsQuery.isPlaceholderData) return
    setCurrentPage(requestedPage)
    setPageSize(requestedPageSize)
  }, [
    flowsQuery.isPlaceholderData,
    flowsQuery.isSuccess,
    isPageTransition,
    requestedPage,
    requestedPageSize,
  ])

  useEffect(() => {
    if (!isPageTransition || !flowsQuery.isError) return
    setRequestedPage(currentPage)
    setRequestedPageSize(pageSize)
    showToast(t('taskList.pageLoadFailed'), 'error')
  }, [
    currentPage,
    flowsQuery.isError,
    isPageTransition,
    pageSize,
    showToast,
    t,
  ])

  const deleteDownloadMutation = useMutation({
    mutationFn: (downloadId: string) => service.deleteDownload(downloadId),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ['flows'] })
      setDeleteTarget(null)
      if (res.data.qb_error) {
        showToast(t('taskList.deleteQbError'), 'info')
      } else {
        showToast(t('taskList.deleteSuccess'))
      }
    },
    onError: () => {
      showToast(t('taskList.deleteFailed'), 'error')
    },
  })

  const deleteTaskMutation = useMutation({
    mutationFn: (taskId: string) => service.deleteTask(taskId),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ['flows'] })
      setDeleteTarget(null)
      if (res.data.qb_error) {
        showToast(t('taskList.deleteQbError'), 'info')
      } else {
        showToast(t('taskList.deleteSuccess'))
      }
    },
    onError: () => {
      showToast(t('taskList.deleteFailed'), 'error')
    },
  })

  const pauseMutation = useMutation({
    mutationFn: (downloadId: string) => service.pauseDownload(downloadId).then(() => {}),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['flows'] })
    },
  })

  const resumeMutation = useMutation({
    mutationFn: (downloadId: string) => service.resumeDownload(downloadId).then(() => {}),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['flows'] })
    },
  })

  const retrySyncMutation = useMutation({
    mutationFn: (downloadId: string) => service.retryDownloadSync(downloadId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['flows'] })
    },
  })

  const handlePauseResume = (downloadId: string, action: 'pause' | 'resume') => {
    if (action === 'pause') pauseMutation.mutate(downloadId)
    else resumeMutation.mutate(downloadId)
  }

  const allItems = useMemo((): FlowSummary[] => flowsQuery.data?.data.items ?? [], [flowsQuery.data])
  // 页面 total 走 /flows meta.total, 不得用 items.length.
  const totalCount = flowsQuery.data?.meta.total ?? 0

  // 自动轮询: ingest flow 走 mock tick 推进状态; download-only flow
  // 没有任何客户端副作用, 但必须触发列表 + 4 个 filter total 一起刷新.
  // 任意非终态都算 advancing. 注意必须 invalidate ['flows'] 整体
  // queryKey, 不能只 flowsQuery.refetch() — StatCard 用的是 4 个独立
  // filter total queries, 不失效它们的话统计数据会 stale.
  useEffect(() => {
    const advancingIngest = allItems.filter(
      (f) => !isDownloadOnlyFlow(f) && shouldAutoAdvance(f),
    )
    const advancingDownload = allItems.filter(
      (f) => isDownloadOnlyFlow(f) && shouldAutoAdvance(f),
    )
    if (advancingIngest.length === 0 && advancingDownload.length === 0) return

    const timer = window.setInterval(() => {
      if (IS_MOCK) {
        // mock service 才有 tick 副作用; 真实 API tick 是 no-op
        void Promise.all(advancingIngest.map((f) => {
          if (!f.ingest_task_id) return Promise.resolve()
          return service.tick(f.ingest_task_id)
        })).then(() => {
          void queryClient.invalidateQueries({ queryKey: ['flows'] })
        })
      } else {
        void queryClient.invalidateQueries({ queryKey: ['flows'] })
      }
    }, 3000)

    return () => window.clearInterval(timer)
  }, [queryClient, service, allItems])

  if (flowsQuery.isLoading) {
    return (
      <div>
        <PageHeader title={t('taskList.title')} description={t('taskList.description')} />
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4 mb-6">
          {Array.from({ length: 4 }).map((_, i) => (
            <SkeletonBlock key={i} className="h-24 rounded-lg" />
          ))}
        </div>
        <SkeletonBlock className="h-64 rounded-lg" />
      </div>
    )
  }

  if (flowsQuery.isError && !flowsQuery.data) {
    return (
      <div>
        <PageHeader title={t('taskList.title')} description={t('taskList.description')} />
        <ErrorState
          title={t('common.error')}
          description={flowsQuery.error instanceof Error ? flowsQuery.error.message : t('common.pleaseRetry')}
          action={
            <Button onClick={() => void flowsQuery.refetch()} variant="secondary" size="sm">
              {t('common.retry')}
            </Button>
          }
        />
      </div>
    )
  }

  return (
    <div>
      <PageHeader
        title={t('taskList.title')}
        description={t('taskList.description')}
        actions={
          <Button
            onClick={() => void queryClient.invalidateQueries({ queryKey: ['flows'] })}
            variant="secondary"
            size="sm"
          >
            {t('taskList.manualRefresh')}
          </Button>
        }
      />

      <div className="grid gap-3 grid-cols-2 xl:grid-cols-4 mb-6">
        <StatCard
          label={t('dashboard.pendingConfirm')}
          value={waitingUserTotalQuery.data?.meta.total ?? null}
          isError={waitingUserTotalQuery.isError}
        />
        <StatCard
          label={t('dashboard.processing')}
          value={processingTotalQuery.data?.meta.total ?? null}
          isError={processingTotalQuery.isError}
        />
        <StatCard
          label={t('dashboard.completed')}
          value={completedTotalQuery.data?.meta.total ?? null}
          isError={completedTotalQuery.isError}
        />
        <StatCard
          label={t('dashboard.failed')}
          value={failedTotalQuery.data?.meta.total ?? null}
          isError={failedTotalQuery.isError}
        />
      </div>

      <PageToolbar>
        {filterKeys.map((option) => (
          <Button
            key={option.value}
            onClick={() => setActiveFilter(option.value)}
            size="sm"
            variant={activeFilter === option.value ? 'default' : 'secondary'}
          >
            {t(option.key)}
          </Button>
        ))}
      </PageToolbar>

      {allItems.length === 0 ? (
        <EmptyState
          title={t('taskList.noTasks')}
          description={t('taskList.noTasksDesc')}
        />
      ) : (
        <DataTable
          columns={taskColumns(t)}
          data={allItems}
          disablePagination
          columnClassNames={TASK_LIST_COLUMN_CLASSES}
          tableClassName="min-w-[1200px] table-fixed"
          serverPagination={{
            page: currentPage,
            pageSize,
            total: totalCount,
            pageSizeOptions: PAGE_SIZE_OPTIONS,
            pending: isPageTransition,
            onPageChange: (page) => setRequestedPage(page),
            onPageSizeChange: (nextPageSize) => {
              setRequestedPageSize(nextPageSize)
              setRequestedPage(1)
            },
          }}
          renderMobileCard={(flow) => (
            <FlowCard
              flow={flow}
              onDeleteRequest={(item) => setDeleteTarget({
                flow: item,
                isDownloadOnly: isDownloadOnlyFlow(item),
              })}
              onPauseResume={handlePauseResume}
              onRetrySync={(downloadId: string) => retrySyncMutation.mutate(downloadId)}
            />
          )}
          tableMeta={{
            onDeleteRequest: (item: FlowSummary) => setDeleteTarget({
              flow: item,
              isDownloadOnly: isDownloadOnlyFlow(item),
            }),
            onPauseResume: handlePauseResume,
            onRetrySync: (downloadId: string) => retrySyncMutation.mutate(downloadId),
          }}
        />
      )}

      <ConfirmDialog
        open={deleteTarget !== null}
        title={t('taskList.deleteConfirmTitle')}
        description={
          deleteTarget?.isDownloadOnly
            ? t('taskList.deleteDownloadDesc')
            : t('taskList.deleteTaskDesc')
        }
        confirmLabel={t('common.delete')}
        variant="destructive"
        loading={deleteDownloadMutation.isPending || deleteTaskMutation.isPending}
        onConfirm={() => {
          if (!deleteTarget) return
          if (deleteTarget.isDownloadOnly) {
            const dlId = deleteTarget.flow.download_task_id
            if (!dlId) return
            deleteDownloadMutation.mutate(dlId)
          } else {
            const tId = deleteTarget.flow.ingest_task_id
            if (!tId) return
            deleteTaskMutation.mutate(tId)
          }
        }}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  )
}

function StatCard({ label, value, isError }: { label: string; value: number | null; isError?: boolean }) {
  // error 且无 stale 数据时, 显示 "—" 而不是把 0 误读成"该 filter 没
  // 任何 flow". 已有 stale 数据时仍用旧值 (invalidate 后会立刻 refetch).
  return (
    <div className="grid gap-1 rounded-lg border border-border bg-surface p-4">
      <span className="text-sm text-muted-foreground">{label}</span>
      <strong className="text-2xl font-semibold text-surface-foreground">
        {isError && value === null ? '—' : value ?? 0}
      </strong>
    </div>
  )
}
