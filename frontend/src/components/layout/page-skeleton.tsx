import type { ReactNode } from 'react'

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string
  description?: string
  actions?: ReactNode
}) {
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
      <div>
        <h1 className="text-xl font-semibold text-surface-foreground">{title}</h1>
        {description && (
          <p className="mt-1 text-sm text-muted-foreground">{description}</p>
        )}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  )
}

export function PageToolbar({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <div className={`mb-4 flex flex-wrap items-center gap-3 ${className}`}>
      {children}
    </div>
  )
}

export function ContentFrame({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-lg border border-border bg-surface p-4 ${className}`}>
      {children}
    </div>
  )
}
