import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import i18n from '@/i18n'
import { ListTodo, AlertTriangle, CheckCircle2, Search, CircleDot } from 'lucide-react'
import { createTaskService, type TaskService } from '@/services/task-service'
import { PageHeader } from '@/components/layout/page-skeleton'
import { Button } from '@/components/ui/button'
import { ErrorState, SkeletonBlock } from '@/components/app/shared-ui'
import { BackgroundAgentCard } from '@/components/app/background-agent-card'
import { getMediaTypeLabel, getTotalStatusLabel, getStatusColorClass } from '@/components/app/task-labels'
import type { FlowSummary } from '@/types/task'

const defaultService = createTaskService()
export type DashboardService = Pick<TaskService, 'listFlows' | 'getBackgroundStatus'>

function formatTime(iso: string) {
  try { return new Date(iso).toLocaleDateString(i18n.language === 'en' ? 'en-US' : 'zh-CN') } catch { return iso.slice(0, 10) }
}

function flowDetailHref(flow: FlowSummary) {
  if (flow.route_target === 'task_detail' && flow.ingest_task_id) {
    return `/tasks/${flow.ingest_task_id}`
  }
  if (flow.route_target === 'download_detail' && flow.download_task_id) {
    return `/downloads/${flow.download_task_id}`
  }
  return '#'
}

export function DashboardPage({ service, showAdminStatus = true }: { service?: DashboardService; showAdminStatus?: boolean }) {
  const svc = service ?? defaultService
  const navigate = useNavigate()
  const { t } = useTranslation()

  // 4 个统计卡取自后端 filter totals, 不再从当前页 items 本地计算.
  // 最近流程只需后端按 attention priority 排好的前 5 条, page_size=5.
  // 5 个 useQuery 互不依赖, 各自缓存.
  const recentFlowsQuery = useQuery({
    queryKey: ['dashboard', 'flows', 'recent'] as const,
    queryFn: () => svc.listFlows({ page_size: 5 }),
  })
  const waitingUserTotalQuery = useQuery({
    queryKey: ['dashboard', 'flows', 'filterTotal', 'waiting_user'] as const,
    queryFn: () => svc.listFlows({ filter: 'waiting_user', page: 1, page_size: 1 }),
  })
  const processingTotalQuery = useQuery({
    queryKey: ['dashboard', 'flows', 'filterTotal', 'processing'] as const,
    queryFn: () => svc.listFlows({ filter: 'processing', page: 1, page_size: 1 }),
  })
  const completedTotalQuery = useQuery({
    queryKey: ['dashboard', 'flows', 'filterTotal', 'library_import_complete'] as const,
    queryFn: () => svc.listFlows({ filter: 'library_import_complete', page: 1, page_size: 1 }),
  })
  const failedTotalQuery = useQuery({
    queryKey: ['dashboard', 'flows', 'filterTotal', 'failed'] as const,
    queryFn: () => svc.listFlows({ filter: 'failed', page: 1, page_size: 1 }),
  })

  const recentFlows = recentFlowsQuery.data?.data.items ?? []

  if (recentFlowsQuery.isLoading) {
    return (
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {[1, 2, 3, 4].map((i) => (
          <SkeletonBlock key={i} className="h-24 rounded-lg" />
        ))}
      </div>
    )
  }

  if (recentFlowsQuery.isError) {
    const errorMsg = recentFlowsQuery.error instanceof Error
      ? recentFlowsQuery.error.message
      : t('common.pleaseRetry')
    return (
      <div>
        <PageHeader title={t('dashboard.title')} description={t('dashboard.description')} />
        <ErrorState
          title={t('common.error')}
          description={errorMsg}
          action={
            <Button
              onClick={() => {
                void recentFlowsQuery.refetch()
              }}
              variant="secondary"
              size="sm"
            >
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
        title={t('dashboard.title')}
        description={t('dashboard.description')}
        actions={
          <Button onClick={() => navigate('/discovery')} size="sm">
            <Search className="h-4 w-4 mr-1" />
            {t('dashboard.resourceSearch')}
          </Button>
        }
      />

      {showAdminStatus && <BackgroundAgentCard service={svc} />}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <StatCard
          label={t('dashboard.pendingConfirm')}
          count={waitingUserTotalQuery.data?.meta.total ?? null}
          isError={waitingUserTotalQuery.isError}
          icon={<AlertTriangle className="h-5 w-5" />}
          className="border-yellow-200 bg-yellow-50 dark:border-yellow-800 dark:bg-yellow-950/30"
          onClick={() => navigate('/tasks?filter=waiting_user')}
        />
        <StatCard
          label={t('dashboard.processing')}
          count={processingTotalQuery.data?.meta.total ?? null}
          isError={processingTotalQuery.isError}
          icon={<CircleDot className="h-5 w-5" />}
          className="border-blue-200 bg-blue-50 dark:border-blue-800 dark:bg-blue-950/30"
          onClick={() => navigate('/tasks?filter=processing')}
        />
        <StatCard
          label={t('dashboard.completed')}
          count={completedTotalQuery.data?.meta.total ?? null}
          isError={completedTotalQuery.isError}
          icon={<CheckCircle2 className="h-5 w-5" />}
          className="border-green-200 bg-green-50 dark:border-green-800 dark:bg-green-950/30"
          onClick={() => navigate('/tasks?filter=library_import_complete')}
        />
        <StatCard
          label={t('dashboard.failed')}
          count={failedTotalQuery.data?.meta.total ?? null}
          isError={failedTotalQuery.isError}
          icon={<AlertTriangle className="h-5 w-5" />}
          className="border-red-200 bg-red-50 dark:border-red-800 dark:bg-red-950/30"
          onClick={() => navigate('/tasks?filter=failed')}
        />
      </div>

      <div className="rounded-lg border border-border bg-surface p-4">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-surface-foreground">{t('dashboard.recentFlows')}</h2>
          <Button variant="ghost" size="sm" onClick={() => navigate('/tasks')}>
            <ListTodo className="h-4 w-4 mr-1" />
            {t('dashboard.viewAll')}
          </Button>
        </div>
        {recentFlows.length === 0 ? (
          <p className="text-sm text-muted-foreground py-6 text-center">{t('common.empty')}</p>
        ) : (
          <div className="divide-y divide-border">
            {recentFlows.map((f) => (
              <div
                key={f.id}
                className="flex items-center py-2 px-2 -mx-2 rounded cursor-pointer hover:bg-muted/50"
                onClick={() => {
                  navigate(flowDetailHref(f))
                }}
              >
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium truncate">
                    {f.title || f.source_path?.split('/').pop() || f.id.slice(0, 8)}
                    <span className={`inline-block ml-2 align-middle text-xs px-2 py-0.5 rounded-full font-medium ${getStatusColorClass(f.total_status)}`}>
                      {getTotalStatusLabel(f.total_status)}
                    </span>
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {f.file_format && `${f.file_format} · `}
                    {getMediaTypeLabel(f.media_type)}
                    {f.updated_at && ` · ${formatTime(f.updated_at)}`}
                  </p>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function StatCard({
  label, count, icon, className = '', onClick, isError,
}: {
  label: string; count: number | null; icon: React.ReactNode; className?: string; onClick?: () => void; isError?: boolean
}) {
  // 边界: filter total query 失败且无 stale data 时, 显示 "—"
  // 而不是把 0 误读成"该 filter 没任何 flow". 有 stale data 时
  // count 仍带值, 保留旧值, 等下次 invalidate 后 refetch.
  const display = isError && count === null ? '—' : count ?? 0
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-3 rounded-lg border p-4 text-left transition-colors hover:opacity-80 ${className}`}
    >
      <div className="text-current opacity-70">{icon}</div>
      <div>
        <p className="text-2xl font-bold">{display}</p>
        <p className="text-xs text-muted-foreground">{label}</p>
      </div>
    </button>
  )
}
