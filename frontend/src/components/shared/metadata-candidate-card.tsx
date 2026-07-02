import { useTranslation } from 'react-i18next'
import { ConfidenceBadge } from '@/components/app/shared-ui'

/**
 * 候选卡片共享接口 — 确认页和资源发现页共用。
 * 只定义信息结构，动作按钮由各页面通过 children 定制。
 */
export interface MetadataCardCandidate {
  title: string
  original_title?: string | null
  year?: number | null
  provider: string
  provider_id: string
  poster_url?: string | null
  confidence?: number | null
  overview?: string | null
  media_type?: string | null
  match_reason?: string | null
}

export interface MetadataCandidateCardProps {
  candidate: MetadataCardCandidate
  /** 'compact' 用于列表，'medium' 用于资源发现识别面板，'full' 用于确认页 */
  variant?: 'compact' | 'medium' | 'full'
  /** 是否选中高亮 */
  selected?: boolean
  onClick?: () => void
  /** 额外信息项（仅在 full 模式下显示） */
  extraItems?: Array<{ label: string; value: string }>
  /** 按钮/操作区域 */
  children?: React.ReactNode
}

export function MetadataCandidateCard({
  candidate,
  variant = 'compact',
  selected = false,
  onClick,
  extraItems,
  children,
}: MetadataCandidateCardProps) {
  const { t } = useTranslation()
  const c = candidate
  const confidenceLevel =
    c.confidence != null && c.confidence >= 0.8
      ? 'high'
      : c.confidence != null && c.confidence >= 0.5
        ? 'medium'
        : 'low'

  if (variant === 'compact') {
    return (
      <div
        onClick={onClick}
        className={`flex min-w-0 items-center gap-2 overflow-hidden rounded border px-3 py-2 text-sm cursor-pointer transition-colors ${
          selected
            ? 'border-green-400 bg-green-50 dark:bg-green-900/20'
            : 'border-border hover:border-primary/30'
        }`}
      >
        {c.poster_url && (
          <img
            src={c.poster_url}
            alt=""
            className="w-10 h-14 rounded object-cover shrink-0"
            loading="lazy"
          />
        )}
        <div className="flex-1 min-w-0">
          <div className="font-medium truncate" title={c.title}>{c.title}</div>
          {c.original_title && c.original_title !== c.title && (
            <div className="text-xs text-muted-foreground truncate" title={c.original_title}>{c.original_title}</div>
          )}
          <div className="text-xs text-muted-foreground">
            {c.year && `${c.year} · `}
            {c.provider}
            {c.confidence != null && (
              <> · 置信度: {(c.confidence * 100).toFixed(0)}%</>
            )}
          </div>
          {c.match_reason && (
            <div className="text-[11px] text-muted-foreground/70 truncate">
              {c.match_reason}
            </div>
          )}
        </div>
        {children}
      </div>
    )
  }

  if (variant === 'medium') {
    return (
      <div
        onClick={onClick}
        className={`min-w-0 overflow-hidden rounded-lg border p-4 cursor-pointer transition-colors ${
          selected
            ? 'border-green-400 bg-green-50 dark:bg-green-900/20'
            : 'border-border hover:border-primary/30'
        }`}
      >
        <div className="flex gap-4">
          {c.poster_url && (
            <img
              src={c.poster_url}
              alt=""
              className="w-20 h-28 rounded object-cover shrink-0"
              loading="lazy"
            />
          )}
          <div className="grid flex-1 gap-1.5 min-w-0 content-start">
            {/* 第 1 行：标题 */}
            <div className="font-semibold text-base text-surface-foreground truncate" title={c.title}>
              {c.title}
            </div>
            {/* 第 2 行：原名（始终占位，无原名时显示低调占位符） */}
            <div className="text-sm text-muted-foreground truncate" title={c.original_title ?? undefined}>
              {c.original_title && c.original_title !== c.title
                ? c.original_title
                : '\u200B'}
            </div>
            {/* 第 3 行：年份 · provider · 置信度 · 匹配原因（同行） */}
            <div className="text-sm text-muted-foreground flex flex-wrap items-center gap-x-2 gap-y-0.5">
              {c.year && <span>{c.year}</span>}
              <span>{c.provider}</span>
              {c.confidence != null && (
                <ConfidenceBadge level={confidenceLevel} value={c.confidence} />
              )}
              {c.match_reason && (
                <span className="text-muted-foreground/70 truncate">
                  {c.match_reason}
                </span>
              )}
            </div>
            {/* 第 4 行：简介（始终单行，无简介时占位） */}
            <p className="text-xs text-muted-foreground/60 truncate leading-5">
              {c.overview || t('taskWorkspace.noOverview')}
            </p>
          </div>
        </div>
        {children}
      </div>
    )
  }

  // full variant — 复刻确认页候选卡片骨架
  return (
    <article className="grid min-w-0 gap-4 overflow-hidden rounded-lg border border-border bg-surface p-4">
      <div className="flex gap-4">
        {c.poster_url && (
          <img
            src={c.poster_url}
            alt=""
            className="w-16 h-24 rounded object-cover shrink-0"
            loading="lazy"
          />
        )}

        <div className="grid min-w-0 flex-1 gap-3">
          <div className="grid gap-1">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="min-w-0 max-w-full truncate text-lg font-medium text-surface-foreground" title={c.title}>
                {c.title}
              </h3>
              <ConfidenceBadge level={confidenceLevel} value={c.confidence} />
            </div>
            {c.original_title ? (
              <p className="truncate text-sm text-muted-foreground" title={c.original_title}>
                {c.original_title}
              </p>
            ) : null}
          </div>

          <dl className="grid gap-2 md:grid-cols-2">
            <DetailItem
              label={t('taskWorkspace.year')}
              value={c.year == null ? t('taskWorkspace.unknown') : String(c.year)}
            />
            <DetailItem label={t('taskWorkspace.mediaType')} value={c.media_type ?? t('taskWorkspace.unknown')} />
            <DetailItem label={`${c.provider} ID`} value={c.provider_id} />
            <DetailItem
              label={t('taskWorkspace.matchReason')}
              value={c.match_reason ?? t('taskWorkspace.unknown')}
            />
            {extraItems?.map((item) => (
              <DetailItem key={item.label} label={item.label} value={item.value} />
            ))}
          </dl>
        </div>
      </div>

      {c.overview ? (
        <div className="grid gap-1">
          <span className="text-xs text-muted-foreground">{t('taskWorkspace.overview')}</span>
          <p className="text-sm leading-6 text-surface-foreground">
            {c.overview}
          </p>
        </div>
      ) : null}

      {children}
    </article>
  )
}

function DetailItem({
  label,
  value,
}: {
  label: string
  value: string
}) {
  return (
    <div className="grid gap-0.5">
      <dt className="text-xs text-muted-foreground truncate">{label}</dt>
      <dd className="text-sm text-surface-foreground truncate">{value || '—'}</dd>
    </div>
  )
}
