import * as TooltipPrimitive from '@radix-ui/react-tooltip'
import type { LucideIcon } from 'lucide-react'
import { AlertCircle, AlertTriangle, Bot, CheckCircle2, CircleDot, Inbox, Trash2 } from 'lucide-react'
import { forwardRef, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import type { ConfidenceLevel, TaskStatus } from '@/types/task'
import { getStatusLabel } from '@/components/app/task-labels'

const statusIconMap: Record<TaskStatus, LucideIcon> = {
  discovered: CircleDot,
  waiting_stable: AlertCircle,
  created: CircleDot,
  workspace_imported: CircleDot,
  ai_parsed: CircleDot,
  candidates_ready: CircleDot,
  queued: CircleDot,
  processing: CircleDot,
  agent_running: Bot,
  waiting_user: AlertTriangle,
  agent_failed: AlertCircle,
  library_import_complete: CheckCircle2,
  completed: CheckCircle2,
  failed: AlertCircle,
  deleted: Trash2,
}

const statusColorMap: Record<TaskStatus, string> = {
  discovered: 'border-slate-400/30 bg-slate-500/10 text-slate-200',
  waiting_stable: 'border-amber-400/50 bg-amber-500/15 text-amber-200',
  created: 'border-slate-400/30 bg-slate-500/10 text-slate-200',
  workspace_imported: 'border-slate-400/30 bg-slate-500/10 text-slate-200',
  ai_parsed: 'border-slate-400/30 bg-slate-500/10 text-slate-200',
  candidates_ready: 'border-sky-400/45 bg-sky-500/15 text-sky-200',
  queued: 'border-cyan-400/45 bg-cyan-500/15 text-cyan-200',
  processing: 'border-blue-400/45 bg-blue-500/15 text-blue-200',
  agent_running: 'border-blue-400/45 bg-blue-500/15 text-blue-200',
  waiting_user: 'border-amber-400/60 bg-amber-500/20 text-amber-100',
  agent_failed: 'border-rose-400/55 bg-rose-500/20 text-rose-100',
  library_import_complete: 'border-emerald-400/45 bg-emerald-500/15 text-emerald-200',
  completed: 'border-emerald-400/45 bg-emerald-500/15 text-emerald-200',
  failed: 'border-rose-400/55 bg-rose-500/20 text-rose-100',
  deleted: 'border-slate-400/40 bg-slate-500/15 text-slate-200',
}

const confidenceConfig: Record<ConfidenceLevel, { className: string }> = {
  high: {
    className: 'border-success/30 bg-success/10 text-success',
  },
  medium: {
    className: 'border-warning/30 bg-warning/10 text-warning',
  },
  low: {
    className: 'border-destructive/30 bg-destructive/10 text-destructive',
  },
  unknown: {
    className: 'border-border bg-surface text-muted-foreground',
  },
}

interface PageShellProps {
  title: string
  description?: string
  actions?: ReactNode
  aside?: ReactNode
  children?: ReactNode
}

export function PageShell({
  title,
  description,
  actions,
  aside,
  children,
}: PageShellProps) {
  return (
    <section className="grid gap-6 overflow-x-hidden">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="grid min-w-0 max-w-5xl gap-1">
          <h1 className="text-3xl font-semibold tracking-normal text-balance">{title}</h1>
          {description ? (
            <p className="text-sm leading-6 text-muted-foreground">{description}</p>
          ) : null}
        </div>
        {actions ? (
          <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>
        ) : null}
      </div>

      {aside ? <div className="grid gap-3">{aside}</div> : null}

      <div className="grid gap-4">{children}</div>
    </section>
  )
}

export function StatusBadge({ status }: { status: TaskStatus }) {
  const { t } = useTranslation()
  const Icon = statusIconMap[status]
  const colorClass = statusColorMap[status]

  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium',
        colorClass,
      )}
    >
      <Icon className="h-3.5 w-3.5" />
      {t(`status.${status}`, getStatusLabel(status))}
    </span>
  )
}

export function ConfidenceBadge({
  level,
  value,
}: {
  level: ConfidenceLevel
  value?: number | null
}) {
  const { t } = useTranslation()
  const config = confidenceConfig[level]
  const label = t(`taskLabel.confidence.${level}`, level)

  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-medium',
        config.className,
      )}
    >
      {value == null ? label : `${label} ${value.toFixed(2)}`}
    </span>
  )
}

interface CalloutProps {
  title: string
  description: string
  icon?: LucideIcon
}

export function RiskCallout({
  title,
  description,
  icon: Icon = AlertTriangle,
}: CalloutProps) {
  return (
    <div className="flex items-start gap-3 rounded-lg border border-warning/30 bg-warning/10 p-4 text-warning">
      <Icon className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="grid gap-1">
        <strong className="text-sm font-medium">{title}</strong>
        <p className="text-sm leading-6 text-foreground">{description}</p>
      </div>
    </div>
  )
}

type InlineMessageVariant = 'success' | 'warning' | 'error'

const inlineMessageConfig: Record<InlineMessageVariant, {
  icon: LucideIcon
  className: string
  iconClassName: string
  titleClassName: string
  descClassName: string
}> = {
  success: {
    icon: CheckCircle2,
    className: 'border-l-4 border-l-success border-success/30 bg-success/10',
    iconClassName: 'text-success',
    titleClassName: 'text-success',
    descClassName: 'text-success/80',
  },
  warning: {
    icon: AlertTriangle,
    className: 'border-l-4 border-l-warning border-warning/40 bg-warning/15',
    iconClassName: 'text-warning',
    titleClassName: 'text-warning',
    descClassName: 'text-warning/80',
  },
  error: {
    icon: AlertTriangle,
    className: 'border-l-4 border-l-destructive border-destructive/40 bg-destructive/15',
    iconClassName: 'text-destructive',
    titleClassName: 'text-destructive',
    descClassName: 'text-destructive/80',
  },
}

interface InlineMessageProps {
  variant: InlineMessageVariant
  title: string
  description?: string
}

export function InlineMessage({ variant, title, description }: InlineMessageProps) {
  const config = inlineMessageConfig[variant]
  const Icon = config.icon

  return (
    <div className={cn('flex items-start gap-3 rounded-lg border p-4', config.className)}>
      <Icon className={cn('mt-0.5 h-4 w-4 shrink-0', config.iconClassName)} />
      <div className="grid gap-1 min-w-0">
        <strong className={cn('text-sm font-medium', config.titleClassName)}>{title}</strong>
        {description ? (
          <p className={cn('text-sm leading-6', config.descClassName)}>{description}</p>
        ) : null}
      </div>
    </div>
  )
}

export function MessageCallout({
  title,
  description,
  icon: Icon = AlertCircle,
}: CalloutProps) {
  return (
    <div className="flex items-start gap-3 rounded-lg border border-border bg-surface p-4">
      <Icon className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
      <div className="grid gap-1">
        <strong className="text-sm font-medium text-surface-foreground">{title}</strong>
        <p className="text-sm leading-6 text-muted-foreground">{description}</p>
      </div>
    </div>
  )
}

interface EmptyStateProps {
  title: string
  description: string
  icon?: LucideIcon
  action?: ReactNode
}

export function EmptyState({
  title,
  description,
  icon: Icon = Inbox,
  action,
}: EmptyStateProps) {
  return (
    <div className="grid justify-items-start gap-4 rounded-lg border border-dashed border-border bg-surface p-6">
      <span className="flex h-10 w-10 items-center justify-center rounded-full bg-primary/10 text-primary">
        <Icon className="h-5 w-5" />
      </span>
      <div className="grid gap-1">
        <h2 className="text-lg font-medium text-surface-foreground">{title}</h2>
        <p className="max-w-2xl text-sm leading-6 text-muted-foreground">{description}</p>
      </div>
      {action ? <div>{action}</div> : null}
    </div>
  )
}

export function ErrorState({
  title,
  description,
  icon: Icon = AlertCircle,
  action,
}: EmptyStateProps) {
  return (
    <div className="grid justify-items-start gap-4 rounded-lg border border-destructive/30 bg-destructive/5 p-6">
      <span className="flex h-10 w-10 items-center justify-center rounded-full bg-destructive/10 text-destructive">
        <Icon className="h-5 w-5" />
      </span>
      <div className="grid gap-1">
        <h2 className="text-lg font-medium text-surface-foreground">{title}</h2>
        <p className="max-w-2xl text-sm leading-6 text-muted-foreground">{description}</p>
      </div>
      {action ? <div>{action}</div> : null}
    </div>
  )
}

export function SkeletonBlock({ className }: { className?: string }) {
  return (
    <div
      className={cn('animate-pulse rounded-md bg-muted/80', className)}
      data-testid="skeleton-block"
    />
  )
}

export const IconButton = forwardRef<
  HTMLButtonElement,
  {
    label: string
    icon: LucideIcon
    className?: string
  }
>(({ label, icon: Icon, className }, ref) => {
  return (
    <Button
      aria-label={label}
      className={className}
      ref={ref}
      size="icon"
      type="button"
      variant="secondary"
    >
      <Icon className="h-4 w-4" />
    </Button>
  )
})

IconButton.displayName = 'IconButton'

export const TooltipProvider = TooltipPrimitive.Provider
export const Tooltip = TooltipPrimitive.Root
export const TooltipTrigger = TooltipPrimitive.Trigger

export function TooltipContent({
  className,
  sideOffset = 8,
  children,
}: TooltipPrimitive.TooltipContentProps) {
  return (
    <TooltipPrimitive.Portal>
      <TooltipPrimitive.Content
        className={cn(
          'z-50 overflow-hidden rounded-md border border-border bg-surface px-3 py-1.5 text-xs text-surface-foreground shadow-md',
          className,
        )}
        sideOffset={sideOffset}
      >
        {children}
      </TooltipPrimitive.Content>
    </TooltipPrimitive.Portal>
  )
}
