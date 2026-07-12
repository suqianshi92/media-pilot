import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useNavigate, useParams } from 'react-router-dom'
import { MoreHorizontal, Pause, Play, RefreshCw, RotateCw, Trash2 } from 'lucide-react'

import {
  ErrorState,
  PageShell,
  SkeletonBlock,
} from '@/components/app/shared-ui'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { getDownloadSourceLabel, getDownloadStatusLabel, isDownloadActive } from '@/components/app/task-labels'
import { createApiTaskService } from '@/services/api-client'
import type { DownloadDetail } from '@/types/task'

const api = createApiTaskService()

function formatSpeed(bytesPerSecond: number | null): string {
  if (bytesPerSecond == null || bytesPerSecond === 0) return '—'
  if (bytesPerSecond < 1024) return `${bytesPerSecond} B/s`
  const kb = bytesPerSecond / 1024
  if (kb < 1024) return `${kb.toFixed(1)} KB/s`
  return `${(kb / 1024).toFixed(1)} MB/s`
}

function formatPercent(progress: number): string {
  return `${(progress * 100).toFixed(1)}%`
}

function formatDateTime(value: string) {
  return value.replace('T', ' ').replace('+08:00', ' UTC+08:00').replace('Z', ' UTC')
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline gap-2">
      <dt className="w-24 shrink-0 text-xs text-muted-foreground">{label}</dt>
      <dd className="break-all text-sm text-surface-foreground">{value}</dd>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-border bg-surface p-4">
      <h2 className="mb-3 text-sm font-semibold text-surface-foreground">{title}</h2>
      {children}
    </section>
  )
}

export function DownloadDetailPage({ showOwner = false }: { showOwner?: boolean }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { downloadId } = useParams<{ downloadId: string }>()
  const [deleteConfirm, setDeleteConfirm] = useState(false)

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['download-detail', downloadId],
    queryFn: () => api.getDownloadDetail(downloadId!),
    enabled: !!downloadId,
    refetchInterval: 5000,
  })

  const pauseMutation = useMutation({
    mutationFn: () => api.pauseDownload(downloadId!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['download-detail', downloadId] })
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
    },
  })

  const resumeMutation = useMutation({
    mutationFn: () => api.resumeDownload(downloadId!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['download-detail', downloadId] })
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
    },
  })

  const refreshMutation = useMutation({
    mutationFn: () => api.refreshDownload(downloadId!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['download-detail', downloadId] })
    },
  })

  const retryMutation = useMutation({
    mutationFn: () => api.retryDownloadSync(downloadId!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['download-detail', downloadId] })
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: () => api.deleteDownload(downloadId!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
      navigate('/tasks', { replace: true })
    },
  })

  if (isLoading) {
    return (
      <PageShell title={t('downloadDetail.title')} description={t('downloadDetail.description')}>
        <div className="grid gap-4">
          <SkeletonBlock className="h-48" />
          <SkeletonBlock className="h-32" />
        </div>
      </PageShell>
    )
  }

  if (isError || !data) {
    return (
      <PageShell title={t('downloadDetail.title')} description={t('downloadDetail.description')}>
        <ErrorState
          title={t('downloadDetail.loadFailed')}
          description={t('common.pleaseRetry')}
          action={<Button size="sm" variant="secondary" onClick={() => refetch()}>{t('common.retry')}</Button>}
        />
      </PageShell>
    )
  }

  const dl: DownloadDetail = data.data as unknown as DownloadDetail
  const active = isDownloadActive(dl.status)
  const isPaused = dl.status === 'paused'

  const hasPreselected = !!(
    dl.preselected_metadata_profile ||
    dl.preselected_metadata_provider ||
    dl.preselected_metadata_external_id
  )

  return (
    <PageShell title={t('downloadDetail.title')} description={t('downloadDetail.description')}>
      <div className="grid gap-4">
        {/* 动作栏 */}
        <div className="flex flex-wrap items-center gap-2">
          {isPaused ? (
            <Button size="sm" onClick={() => resumeMutation.mutate()} disabled={resumeMutation.isPending}>
              <Play className="mr-1.5 h-4 w-4" />
              {resumeMutation.isPending ? t('common.loading') : t('downloadDetail.resume')}
            </Button>
          ) : active ? (
            <Button size="sm" variant="secondary" onClick={() => pauseMutation.mutate()} disabled={pauseMutation.isPending}>
              <Pause className="mr-1.5 h-4 w-4" />
              {pauseMutation.isPending ? t('common.loading') : t('downloadDetail.pause')}
            </Button>
          ) : null}
          <Button size="sm" variant="secondary" onClick={() => refreshMutation.mutate()} disabled={refreshMutation.isPending}>
            <RefreshCw className={`mr-1.5 h-4 w-4 ${refreshMutation.isPending ? 'animate-spin' : ''}`} />
            {t('downloadDetail.refresh')}
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button size="sm" variant="ghost">
                <MoreHorizontal className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => retryMutation.mutate()} disabled={retryMutation.isPending}>
                <RotateCw className="mr-2 h-4 w-4" />
                {t('downloadDetail.retrySync')}
              </DropdownMenuItem>
              <DropdownMenuItem
                onClick={() => {
                  if (deleteConfirm) {
                    deleteMutation.mutate()
                    setDeleteConfirm(false)
                  } else {
                    setDeleteConfirm(true)
                  }
                }}
                disabled={deleteMutation.isPending}
                className="text-destructive focus:text-destructive"
              >
                <Trash2 className="mr-2 h-4 w-4" />
                {deleteConfirm ? t('downloadDetail.confirmDelete') : t('downloadDetail.delete')}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>

        {/* 状态提示 */}
        <div className="flex items-center gap-3 rounded-lg border border-border bg-surface p-4">
          <span className={`h-3 w-3 rounded-full ${
            dl.status === 'completed' || dl.status === 'completed_pending_ingest' ? 'bg-green-500' :
            dl.status === 'failed' || dl.status === 'sync_failed' ? 'bg-destructive' :
            isPaused ? 'bg-yellow-500' :
            active ? 'bg-blue-500 animate-pulse' :
            'bg-muted-foreground'
          }`} />
          <span className="text-sm font-medium text-surface-foreground">
            {getDownloadStatusLabel(dl.status)}
          </span>
        </div>

        {/* 进度 */}
        {(active || isPaused || dl.progress > 0) && (
          <Section title={t('downloadDetail.progress')}>
            <div className="grid gap-3">
              <div className="flex items-center gap-3">
                <div className="h-2 flex-1 rounded-full bg-muted">
                  <div
                    className="h-2 rounded-full bg-primary transition-all"
                    style={{ width: `${Math.min(dl.progress * 100, 100)}%` }}
                  />
                </div>
                <span className="text-sm tabular-nums text-surface-foreground">
                  {formatPercent(dl.progress)}
                </span>
              </div>
              <div className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-4">
                <DetailRow label={t('downloadDetail.speed')} value={formatSpeed(dl.download_speed_bytes_per_second)} />
                <DetailRow label={t('downloadDetail.uploadSpeed')} value={formatSpeed(dl.upload_speed_bytes_per_second)} />
                <DetailRow label={t('downloadDetail.connections')} value={dl.connections != null ? String(dl.connections) : '—'} />
                <DetailRow label={t('downloadDetail.seeders')} value={`${dl.seeders} / ${dl.leechers}`} />
              </div>
            </div>
          </Section>
        )}

        {/* 基础信息 */}
        <Section title={t('downloadDetail.basicInfo')}>
          <dl className="grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2">
            {showOwner && <DetailRow label="创建者" value={dl.owner_username ?? '系统'} />}
            <DetailRow label={t('downloadDetail.titleLabel')} value={dl.title} />
            <DetailRow label={t('downloadDetail.source')} value={getDownloadSourceLabel(dl.source)} />
            <DetailRow label={t('downloadDetail.savePath')} value={dl.save_path} />
            <DetailRow label={t('downloadDetail.contentPath')} value={dl.content_path || '—'} />
            <DetailRow label={t('downloadDetail.createdAt')} value={formatDateTime(dl.created_at)} />
            <DetailRow label={t('downloadDetail.updatedAt')} value={formatDateTime(dl.updated_at)} />
          </dl>
        </Section>

        {/* 预选元数据 */}
        {hasPreselected && (
          <Section title={t('downloadDetail.preselectedMetadata')}>
            <dl className="grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2">
              {dl.preselected_metadata_profile && (
                <DetailRow label={t('downloadDetail.profile')} value={dl.preselected_metadata_profile} />
              )}
              {dl.preselected_metadata_provider && (
                <DetailRow label={t('downloadDetail.provider')} value={dl.preselected_metadata_provider} />
              )}
              {dl.preselected_metadata_external_id && (
                <DetailRow label={t('downloadDetail.externalId')} value={dl.preselected_metadata_external_id} />
              )}
            </dl>
          </Section>
        )}

        {/* 错误信息 */}
        {dl.error_message && (
          <Section title={t('downloadDetail.errorInfo')}>
            <pre className="whitespace-pre-wrap break-all text-sm text-destructive">
              {dl.error_message}
            </pre>
          </Section>
        )}
      </div>
    </PageShell>
  )
}
