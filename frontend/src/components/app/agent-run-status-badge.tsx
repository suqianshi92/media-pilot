import { Bot, CheckCircle2, AlertTriangle, AlertCircle, CircleDashed } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { type ReactNode } from 'react'

import { cn } from '@/lib/utils'
import type { AgentRunStatus } from '@/types/task'

/** 跨详情页 / 任务列表 / 首页共享的 Agent 状态颜色映射.

颜色与 ``shared-ui.statusColorMap`` 对齐 (``active`` 与 ``agent_running``
同色), 保证同一语义状态在三个页面渲染同一颜色. 维护: 颜色改这里, 三处
跟着变.
*/
export const agentRunStatusColorMap: Record<AgentRunStatus, string> = {
  active: 'border-blue-400/45 bg-blue-500/15 text-blue-200',
  waiting_user: 'border-amber-400/60 bg-amber-500/20 text-amber-100',
  failed: 'border-rose-400/55 bg-rose-500/20 text-rose-100',
  completed: 'border-emerald-400/45 bg-emerald-500/15 text-emerald-200',
  none: 'text-muted-foreground',
}

const agentRunStatusIconMap: Record<AgentRunStatus, LucideIcon> = {
  active: Bot,
  waiting_user: AlertTriangle,
  failed: AlertCircle,
  completed: CheckCircle2,
  none: CircleDashed,
}

interface AgentRunStatusBadgeProps {
  runStatus: AgentRunStatus
  label?: ReactNode
  className?: string
}

export function AgentRunStatusBadge({
  runStatus,
  label,
  className,
}: AgentRunStatusBadgeProps) {
  const Icon = agentRunStatusIconMap[runStatus]
  const colorClass = agentRunStatusColorMap[runStatus]

  return (
    <span
      data-testid="agent-run-status-badge"
      data-run-status={runStatus}
      className={cn(
        'inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-medium',
        colorClass,
        className,
      )}
    >
      <Icon className="h-3.5 w-3.5" />
      {label ?? runStatus}
    </span>
  )
}
