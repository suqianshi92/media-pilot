/**
 * 后台 Agent 状态卡 — 首页靠前展示
 *
 * 边界:
 * - 不暴露 LLM prompt、工具调用原始 JSON、密钥、完整异常堆栈
 * - disabled 时只展示用户可理解的禁用原因列表
 * - 当前 / 等待 / 失败 任务存在时, 提供任务工作台或任务列表筛选入口
 * - 不提供"立即跑一轮"或重试 / 暂停后台线程的写按钮
 */

import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { Bot, AlertCircle, ListChecks, ExternalLink, Clock } from 'lucide-react'

import type { TaskService } from '@/services/task-service'
import type { AgentBackgroundStatusData } from '@/types/agent-background'

export type BackgroundAgentService = Pick<TaskService, 'getBackgroundStatus'>

function stateClass(state: AgentBackgroundStatusData['state']) {
  switch (state) {
    case 'disabled':
      return 'border-muted-foreground/30 bg-muted/30 text-muted-foreground'
    case 'needs_attention':
      return 'border-yellow-200 bg-yellow-50 dark:border-yellow-800 dark:bg-yellow-950/30'
    case 'recently_failed':
      return 'border-red-200 bg-red-50 dark:border-red-800 dark:bg-red-950/30'
    case 'idle':
      return 'border-green-200 bg-green-50 dark:border-green-800 dark:bg-green-950/30'
    case 'processing_task':
    case 'syncing_downloads':
    case 'scanning_watch':
      return 'border-blue-200 bg-blue-50 dark:border-blue-800 dark:bg-blue-950/30'
    default:
      return 'border-border bg-surface'
  }
}

function formatTime(iso: string | null) {
  if (!iso) return '—'
  try { return new Date(iso).toLocaleString() } catch { return iso }
}

export function BackgroundAgentCard({ service }: { service: BackgroundAgentService }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const query = useQuery({
    queryKey: ['agent-background', 'status'],
    queryFn: () => service.getBackgroundStatus(),
    refetchInterval: 5000,
  })

  if (query.isLoading) {
    return (
      <div className="rounded-lg border border-border bg-surface p-4 mb-6">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Bot className="h-4 w-4" />
          <span>{t('dashboard.backgroundAgent.title')}</span>
        </div>
        <p className="mt-2 text-sm text-muted-foreground">…</p>
      </div>
    )
  }

  if (query.isError || !query.data) {
    return (
      <div className="rounded-lg border border-border bg-surface p-4 mb-6">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Bot className="h-4 w-4" />
          <span>{t('dashboard.backgroundAgent.title')}</span>
        </div>
        <p className="mt-2 text-sm text-muted-foreground">{t('common.pleaseRetry')}</p>
      </div>
    )
  }

  const status = query.data.data
  const stateKey = `dashboard.backgroundAgent.states.${status.state}` as const

  return (
    <div
      data-testid="background-agent-card"
      data-state={status.state}
      className={`rounded-lg border p-4 mb-6 ${stateClass(status.state)}`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 text-sm font-semibold">
            <Bot className="h-4 w-4" />
            <span>{t('dashboard.backgroundAgent.title')}</span>
            <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-background/60 border border-current/20">
              {t(stateKey)}
            </span>
          </div>
          <p className="mt-2 text-sm">{status.summary}</p>

          {status.state === 'disabled' && status.disabled_reasons.length > 0 && (
            <div className="mt-2 text-xs">
              <span className="text-muted-foreground">{t('dashboard.backgroundAgent.disabledReasons')}:</span>
              <ul className="mt-1 list-disc list-inside">
                {status.disabled_reasons.map((r) => (
                  <li key={r}><code className="text-xs">{r}</code></li>
                ))}
              </ul>
            </div>
          )}

          <div className="mt-3 flex items-center gap-4 text-xs text-muted-foreground">
            <span className="flex items-center gap-1">
              <ListChecks className="h-3 w-3" />
              {t('dashboard.backgroundAgent.waitingUser')}: {status.waiting_user_count}
            </span>
            <span className="flex items-center gap-1">
              <AlertCircle className="h-3 w-3" />
              {t('dashboard.backgroundAgent.failed')}: {status.agent_failed_count}
            </span>
            <span className="flex items-center gap-1">
              <Clock className="h-3 w-3" />
              {t('dashboard.backgroundAgent.lastRun')}: {formatTime(status.last_run)}
            </span>
          </div>
        </div>

        <div className="flex flex-col items-end gap-2">
          {status.current_task_id && (
            <button
              type="button"
              onClick={() => navigate(`/tasks/${status.current_task_id}`)}
              className="text-xs flex items-center gap-1 text-blue-600 hover:underline"
            >
              {t('dashboard.backgroundAgent.viewCurrent')}
              <ExternalLink className="h-3 w-3" />
            </button>
          )}
          {status.waiting_user_count > 0 && (
            <button
              type="button"
              onClick={() => navigate('/tasks?filter=waiting_user')}
              className="text-xs flex items-center gap-1 text-yellow-700 hover:underline"
            >
              {t('dashboard.backgroundAgent.viewTasks')}
              <ExternalLink className="h-3 w-3" />
            </button>
          )}
          {status.agent_failed_count > 0 && (
            <button
              type="button"
              onClick={() => navigate('/tasks?filter=agent_failed')}
              className="text-xs flex items-center gap-1 text-red-600 hover:underline"
            >
              {t('dashboard.backgroundAgent.viewFailures')}
              <ExternalLink className="h-3 w-3" />
            </button>
          )}
        </div>
      </div>

      {status.history.length > 0 && (
        <div className="mt-3 pt-3 border-t border-current/10">
          <ul className="space-y-1 text-xs">
            {status.history.slice(-5).map((entry, idx) => (
              <li key={`${entry.timestamp}-${idx}`} className="flex items-center gap-2 text-muted-foreground">
                <span className="font-mono text-[10px]">{entry.timestamp.slice(11, 19)}</span>
                <span className="uppercase font-medium">{entry.level}</span>
                <span className="truncate">{entry.summary}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
