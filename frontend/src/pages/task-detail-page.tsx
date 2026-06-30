import { type ReactNode, useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ChevronLeft, Loader2 } from 'lucide-react'

import {
  ConfidenceBadge,
  ErrorState,
  InlineMessage,
  MessageCallout,
  RiskCallout,
  SkeletonBlock,
  StatusBadge,
} from '@/components/app/shared-ui'
import {
  getMediaTypeLabel,
  getMatchReasonLabel,
  getProfileLabel,
  getProviderLabel,
  getSourceSelectionReasonLabel,
  getStatusLabel,
  getTaskStepLabel,
  getTimelineToneLabel,
  getWriteResultStatusLabel,
} from '@/components/app/task-labels'
import { MetadataCandidateCard, type MetadataCardCandidate } from '@/components/shared/metadata-candidate-card'
import { AgentPanel } from '@/components/agent/agent-panel'
import { createTaskService, type TaskService } from '@/services/task-service'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { Input } from '@/components/ui/input'
import type { MediaSourceSelectionDto, MetadataCandidateDto, ResearchResponseData, ResearchScope, TaskDetailDto } from '@/types/task'
import i18n from '@/i18n'

const defaultTaskService = createTaskService()
export type TaskDetailService = Pick<
  TaskService,
  'getTaskDetail' | 'getRevokePublishCheck' | 'executeRevokePublish' | 'researchCandidates' | 'getProfileOptions'
  | 'manualSelect'
  | 'publishWithoutMetadata'
  | 'listAgentMessages' | 'listAgentDecisions' | 'replyToAgentDecision' | 'listAgentToolCalls' | 'createAgentRun' | 'sendFreeformMessage'
  | 'recoverStuckAgentRun'
>

// ── helpers (use singleton i18n for plain functions) ──

function BackToTasksLink() {
  const { t } = useTranslation()
  return (
    <Link
      to="/tasks"
      data-testid="back-to-tasks"
      className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-surface-foreground"
    >
      <ChevronLeft className="h-4 w-4" />
      {t('taskWorkspace.backToTasks')}
    </Link>
  )
}

function resolveLatestMessage(msg: string): string {
  if (msg.startsWith('step:')) return getTaskStepLabel(msg.slice(5) as any)
  return msg
}

function DetailItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid gap-1">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="break-all text-sm text-surface-foreground">{value}</dd>
    </div>
  )
}

function formatDateTime(value: string) {
  return value.replace('T', ' ').replace('+08:00', ' UTC+08:00')
}

function formatFileSize(sizeBytes: number | null) {
  if (sizeBytes == null) return i18n.t('common.unknown')
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

function getFileAssetRoleLabel(role: string) {
  return i18n.t(`taskWorkspace.fileAssetRole.${role}`, role)
}

function formatDuration(durationMs: number | null) {
  if (durationMs == null) return i18n.t('common.unknown')
  if (durationMs < 1000) return `${durationMs} ms`
  return `${(durationMs / 1000).toFixed(1)} ${i18n.t('common.seconds')}`
}

function MediaImage({ src, alt, className, fallback }: { src: string | null; alt: string; className: string; fallback: ReactNode }) {
  const [hasError, setHasError] = useState(false)
  if (!src || hasError) return <>{fallback}</>
  return <img src={src} alt={alt} className={className} onError={() => setHasError(true)} />
}

// ── sections ──

function FileList({ title, files }: { title: string; files: MediaSourceSelectionDto['candidate_files'] }) {
  const { t } = useTranslation()
  if (files.length === 0) return <DetailItem label={title} value={t('common.noData')} />
  return (
    <details className="rounded-md border border-border bg-background px-3 py-2">
      <summary className="cursor-pointer text-sm text-surface-foreground">{title}</summary>
      <div className="mt-3 grid gap-2">
        {files.map((file) => (
          <div key={file.path} className="grid gap-1 rounded-md border border-border/70 bg-surface p-3">
            <span className="break-all text-sm text-surface-foreground">{file.path}</span>
            <span className="text-xs text-muted-foreground">{t('taskWorkspace.detailReason')}：{getSourceSelectionReasonLabel(file.reason)}</span>
          </div>
        ))}
      </div>
    </details>
  )
}

function BaseInfoSection({ detail }: { detail: TaskDetailDto }) {
  const { t } = useTranslation()
  const isShow = detail.task.media_type === 'show'
  const mappings = detail.episode_mappings ?? []
  const seasons = [...new Set(mappings.map((m) => m.season))].sort()
  const episodes = mappings.map((m) => m.episode).sort((a, b) => a - b)
  const episodeRange =
    episodes.length > 0
      ? seasons.length === 1
        ? `S${String(seasons[0]).padStart(2, '0')}E${String(episodes[0]).padStart(2, '0')}${episodes.length > 1 ? `-E${String(episodes[episodes.length - 1]).padStart(2, '0')}` : ''}`
        : t('taskWorkspace.multiSeason')
      : null
  return (
    <section className="grid gap-4 rounded-lg border border-border bg-surface p-4">
      <div className="grid gap-1">
        <h2 className="text-lg font-medium text-surface-foreground">{t('taskWorkspace.baseInfo')}</h2>
        <p className="text-sm text-muted-foreground">{t('taskWorkspace.baseInfoDesc')}</p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge status={detail.task.status_summary.status} />
        <ConfidenceBadge level={detail.task.status_summary.confidence_level} value={detail.task.status_summary.confidence} />
      </div>
      <dl className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        <DetailItem label={t('taskWorkspace.contentType')} value={getMediaTypeLabel(detail.task.media_type)} />
        <DetailItem label={t('taskWorkspace.status')} value={getStatusLabel(detail.task.status_summary.status)} />
        <DetailItem label={t('taskWorkspace.sourcePathShort')} value={detail.task.source_path} />
        <DetailItem label={t('taskWorkspace.currentStep')} value={getTaskStepLabel(detail.task.status_summary.current_step)} />
        <DetailItem label={t('taskWorkspace.createdAt')} value={formatDateTime(detail.task.created_at)} />
        <DetailItem label={t('taskWorkspace.updatedAt')} value={formatDateTime(detail.task.updated_at)} />
        {isShow && episodeRange ? (
          <DetailItem label={t('taskWorkspace.episodeRange')} value={episodeRange} />
        ) : null}
        <DetailItem label={t('taskWorkspace.failureReason')} value={detail.task.status_summary.failure_reason ?? t('common.none')} />
      </dl>
      {isShow && mappings.length > 0 ? (
        <div className="grid gap-2">
          <h3 className="text-sm font-medium text-surface-foreground">{t('taskWorkspace.episodeMapping')}</h3>
          <ul className="grid gap-1 text-sm text-muted-foreground">
            {mappings.map((m, i) => (
              <li key={i} className="flex items-center gap-2">
                <span className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono text-muted-foreground">
                  S{String(m.season).padStart(2, '0')}E{String(m.episode).padStart(2, '0')}
                </span>
                <span className="truncate">{m.file_path.split('/').pop() ?? m.file_path}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  )
}

function SourceSelectionSection({ sourceSelection, blockedReason }: { sourceSelection: TaskDetailDto['source_selection']; blockedReason: string | null }) {
  const { t } = useTranslation()
  if (!sourceSelection) {
    return <MessageCallout title={t('taskWorkspace.sourceSelection')} description={t('taskWorkspace.sourceSelectionEmpty')} />
  }
  return (
    <section className="grid gap-4 rounded-lg border border-border bg-surface p-4">
      <div className="grid gap-1">
        <h2 className="text-lg font-medium text-surface-foreground">{t('taskWorkspace.sourceSelection')}</h2>
        <p className="text-sm text-muted-foreground">{t('taskWorkspace.sourceSelectionDesc')}</p>
      </div>
      {blockedReason === 'bdmv_detected' || sourceSelection.bdmv_detected ? (
        <RiskCallout title={t('taskWorkspace.bdmvNeedManual')} description={t('taskWorkspace.bdmvWarning')} />
      ) : null}
      <dl className="grid gap-3 md:grid-cols-2">
        <DetailItem label={t('taskWorkspace.inputPath')} value={sourceSelection.input_path} />
        <DetailItem label={t('taskWorkspace.selectedFile')} value={sourceSelection.selected_path ?? t('common.notSelected')} />
        <DetailItem label={t('taskWorkspace.selectionReason')} value={getSourceSelectionReasonLabel(sourceSelection.reason)} />
        <DetailItem label={t('taskWorkspace.selectionConfidence')} value={sourceSelection.confidence == null ? t('common.unknown') : sourceSelection.confidence.toFixed(2)} />
      </dl>
      <div className="grid gap-3">
        <FileList title={t('taskWorkspace.candidateFiles')} files={sourceSelection.candidate_files} />
        <FileList title={t('taskWorkspace.excludedFiles')} files={sourceSelection.excluded_files} />
      </div>
    </section>
  )
}

function ManualMetadataResearchSection({ detail, service = defaultTaskService }: { detail: TaskDetailDto; service: TaskDetailService }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const taskId = detail.task.id
  const isAgentRunning = detail.task.status_summary.status === 'agent_running'
  const isNoMetadataPublished = detail.task.metadata_status === 'none'
  const canPublishWithoutMetadata = !isAgentRunning && !detail.metadata_detail && !isNoMetadataPublished

  const [keyword, setKeyword] = useState(detail.search_keyword?.keyword ?? '')
  const [scope, setScope] = useState<ResearchScope>('all')
  const [candidates, setCandidates] = useState<MetadataCandidateDto[]>([])
  const [searchSummary, setSearchSummary] = useState<ResearchResponseData['search_summary'] | null>(null)
  const [searchError, setSearchError] = useState<string | null>(null)
  const [feedback, setFeedback] = useState<{ title: string; description: string; variant: 'success' | 'warning' | 'error' } | null>(null)
  const [showNoMetadataConfirm, setShowNoMetadataConfirm] = useState(false)

  useEffect(() => {
    setKeyword(detail.search_keyword?.keyword ?? '')
    setScope('all')
    setCandidates([])
    setSearchSummary(null)
    setSearchError(null)
    setFeedback(null)
    setShowNoMetadataConfirm(false)
  }, [detail.task.id, detail.search_keyword?.keyword])

  const handleScopeLabel = (value: ResearchScope): string => {
    if (value === 'all') return t('taskWorkspace.allEnabledSources')
    return getProfileLabel(value)
  }

  const researchMutation = useMutation({
    mutationFn: () => service.researchCandidates(taskId, keyword.trim(), scope),
    onMutate: () => {
      setSearchError(null)
      setFeedback(null)
      setSearchSummary(null)
    },
    onSuccess: (result) => {
      const data = result.data
      setCandidates(data.candidates)
      setSearchSummary(data.search_summary)
      if (data.candidates.length === 0) {
        setFeedback({
          variant: 'warning',
          title: t('taskWorkspace.noCandidates'),
          description: t('taskWorkspace.noCandidatesHint'),
        })
      }
    },
    onError: (err) => {
      setCandidates([])
      setSearchSummary(null)
      setSearchError(err instanceof Error ? err.message : t('taskWorkspace.searchFailed'))
    },
  })

  const manualSelectMutation = useMutation({
    mutationFn: (candidate: MetadataCandidateDto) => service.manualSelect(taskId, {
      provider: candidate.provider,
      provider_id: candidate.provider_id,
      title: candidate.title,
      year: candidate.year,
      original_title: candidate.original_title,
      media_type: candidate.media_type,
    }),
    onMutate: (candidate) => {
      setFeedback({
        variant: 'warning',
        title: t('taskWorkspace.manualSelectSubmitted'),
        description: t('taskWorkspace.manualSelectSubmittedDesc', { title: candidate.title }),
      })
    },
    onSuccess: async (result) => {
      setShowNoMetadataConfirm(false)
      const status = result.data.status
      const summary = result.data.summary
      if (status === 'waiting_user') {
        setFeedback({
          variant: 'warning',
          title: t('taskWorkspace.manualSelectWaiting'),
          description: summary,
        })
      } else if (status === 'saved') {
        setFeedback({
          variant: 'success',
          title: t('taskWorkspace.manualSelectSaved'),
          description: summary,
        })
      } else if (status === 'agent_failed') {
        setFeedback({
          variant: 'error',
          title: t('taskWorkspace.manualSelectFailed'),
          description: summary,
        })
      } else {
        setFeedback({
          variant: 'success',
          title: t('taskWorkspace.manualSelectPublished'),
          description: summary,
        })
      }

      const taskDetailPromise = queryClient.refetchQueries({ queryKey: ['task-detail', taskId] })
      void queryClient.refetchQueries({ queryKey: ['agent-decisions', taskId] })
      void queryClient.refetchQueries({ queryKey: ['agent-messages', taskId] })
      void queryClient.refetchQueries({ queryKey: ['agent-tool-calls', taskId] })
      void queryClient.invalidateQueries({ queryKey: ['flows'] })

      await taskDetailPromise
    },
    onError: (err) => {
      setFeedback({
        variant: 'error',
        title: t('taskWorkspace.operationFailed'),
        description: err instanceof Error ? err.message : t('taskWorkspace.searchFailed'),
      })
    },
  })

  const publishWithoutMetadataMutation = useMutation({
    mutationFn: () => service.publishWithoutMetadata(taskId),
    onMutate: () => {
      setFeedback({
        variant: 'warning',
        title: t('taskWorkspace.noMetadataPublishSubmitted'),
        description: t('taskWorkspace.noMetadataPublishSubmittedDesc'),
      })
    },
    onSuccess: async (result) => {
      setShowNoMetadataConfirm(false)
      const status = result.data.status
      setFeedback({
        variant: status === 'waiting_user' ? 'warning' : 'success',
        title: status === 'waiting_user'
          ? t('taskWorkspace.noMetadataPublishWaiting')
          : t('taskWorkspace.noMetadataPublishDone'),
        description: status === 'waiting_user'
          ? t('taskWorkspace.noMetadataPublishWaitingDesc')
          : t('taskWorkspace.noMetadataPublishDoneDesc'),
      })
      const taskDetailPromise = queryClient.refetchQueries({ queryKey: ['task-detail', taskId] })
      void queryClient.refetchQueries({ queryKey: ['agent-decisions', taskId] })
      void queryClient.refetchQueries({ queryKey: ['agent-messages', taskId] })
      void queryClient.invalidateQueries({ queryKey: ['flows'] })
      await taskDetailPromise
    },
    onError: (err) => {
      setShowNoMetadataConfirm(false)
      setFeedback({
        variant: 'error',
        title: t('taskWorkspace.noMetadataPublishFailed'),
        description: err instanceof Error ? err.message : t('taskWorkspace.noMetadataPublishFailedDesc'),
      })
    },
  })

  const handleSearch = () => {
    const normalizedKeyword = keyword.trim()
    if (!normalizedKeyword) return
    researchMutation.mutate()
  }

  const handleSelect = (candidate: MetadataCandidateDto) => {
    if (isAgentRunning) return
    manualSelectMutation.mutate(candidate)
  }

  const handlePublishWithoutMetadata = () => {
    if (isAgentRunning || publishWithoutMetadataMutation.isPending) return
    setShowNoMetadataConfirm(true)
  }

  return (
    <section className="grid gap-4 rounded-lg border border-border bg-surface p-4">
      <div className="grid gap-1">
        <h2 className="text-lg font-medium text-surface-foreground">{t('taskWorkspace.manualMetadataResearch')}</h2>
        <p className="text-sm text-muted-foreground">{t('taskWorkspace.manualMetadataResearchDesc')}</p>
      </div>

      {isAgentRunning ? (
        <InlineMessage
          variant="warning"
          title={t('taskWorkspace.manualMetadataResearchBlockedTitle')}
          description={t('taskWorkspace.manualMetadataResearchBlocked')}
        />
      ) : null}

      {isNoMetadataPublished ? (
        <InlineMessage
          variant="warning"
          title={t('taskWorkspace.noMetadataPublishDone')}
          description={t('taskWorkspace.noMetadataPublishedHint')}
        />
      ) : null}

      {canPublishWithoutMetadata ? (
        <div className="flex flex-col gap-2 rounded-md border border-amber-200/70 bg-amber-50/70 p-3 text-sm dark:border-amber-900/60 dark:bg-amber-950/30 md:flex-row md:items-center md:justify-between">
          <p className="text-muted-foreground">{t('taskWorkspace.noMetadataPublishHint')}</p>
          <Button
            type="button"
            variant="secondary"
            size="sm"
            disabled={publishWithoutMetadataMutation.isPending}
            onClick={handlePublishWithoutMetadata}
          >
            {publishWithoutMetadataMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            {publishWithoutMetadataMutation.isPending ? t('taskWorkspace.noMetadataPublishing') : t('taskWorkspace.publishWithoutMetadata')}
          </Button>
        </div>
      ) : null}

      <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_160px_auto]">
        <Input
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault()
              if (!isAgentRunning) handleSearch()
            }
          }}
          placeholder={t('taskWorkspace.searchKeyword')}
          aria-label={t('taskWorkspace.searchKeyword')}
          disabled={isAgentRunning}
        />
        <select
          value={scope}
          onChange={(e) => setScope(e.target.value as ResearchScope)}
          aria-label={t('taskWorkspace.searchScopeAria')}
          disabled={isAgentRunning}
          className="h-10 rounded-md border border-border bg-background px-3 text-sm text-surface-foreground outline-none disabled:cursor-not-allowed disabled:opacity-50"
        >
          <option value="all">{handleScopeLabel('all')}</option>
          <option value="tmdb_movie">{handleScopeLabel('tmdb_movie')}</option>
          <option value="tmdb_show">{handleScopeLabel('tmdb_show')}</option>
          <option value="tpdb_adult_movie">{handleScopeLabel('tpdb_adult_movie')}</option>
        </select>
        <Button
          onClick={handleSearch}
          disabled={isAgentRunning || researchMutation.isPending || !keyword.trim()}
          size="sm"
          type="button"
        >
          {researchMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
          {researchMutation.isPending ? t('taskWorkspace.researching') : t('taskWorkspace.researchButton')}
        </Button>
      </div>

      <ConfirmDialog
        open={showNoMetadataConfirm}
        title={t('taskWorkspace.noMetadataPublishConfirmTitle')}
        description={t('taskWorkspace.noMetadataPublishConfirm')}
        confirmLabel={t('taskWorkspace.publishWithoutMetadata')}
        loading={publishWithoutMetadataMutation.isPending}
        onConfirm={() => publishWithoutMetadataMutation.mutate()}
        onCancel={() => {
          if (!publishWithoutMetadataMutation.isPending) setShowNoMetadataConfirm(false)
        }}
      />

      <p className="text-xs text-muted-foreground">
        {t('taskWorkspace.searchScope')}：{handleScopeLabel(scope)}
      </p>

      {searchError ? <InlineMessage variant="error" title={t('taskWorkspace.searchFailed')} description={searchError} /> : null}
      {feedback ? <InlineMessage variant={feedback.variant} title={feedback.title} description={feedback.description} /> : null}

      <div className="grid gap-2">
        {researchMutation.isPending ? (
          <p className="text-xs text-muted-foreground flex items-center gap-1">
            <Loader2 className="h-3 w-3 animate-spin" /> {t('taskWorkspace.researching')}
          </p>
        ) : null}

        {searchSummary ? (
          <p className="text-xs text-muted-foreground">
            {t('taskWorkspace.searchSummary')}：{t('taskWorkspace.searchSummaryTotal')} {searchSummary.total_candidates}（{t('taskWorkspace.searchSummaryScope')}：{handleScopeLabel(searchSummary.scope)}）
          </p>
        ) : null}

        {candidates.length > 0 ? (
          <div className="grid max-h-80 gap-2 overflow-y-auto pr-1">
            {candidates.map((candidate) => {
              const cardCandidate: MetadataCardCandidate = {
                title: candidate.title,
                original_title: candidate.original_title ?? undefined,
                year: candidate.year == null ? undefined : candidate.year,
                provider: getProviderLabel(candidate.provider),
                provider_id: candidate.provider_id,
                poster_url: candidate.poster_url ?? undefined,
                confidence: candidate.confidence,
                overview: candidate.overview ?? undefined,
                media_type: getMediaTypeLabel(candidate.media_type),
                match_reason: getMatchReasonLabel(candidate.match_reason),
              }

              return (
                <MetadataCandidateCard key={`${candidate.provider}:${candidate.media_type}:${candidate.provider_id}`} variant="compact" candidate={cardCandidate}>
                  <Button
                    variant="default"
                    size="sm"
                    className="h-8 shrink-0"
                    disabled={isAgentRunning || manualSelectMutation.isPending}
                    onClick={() => {
                      handleSelect(candidate)
                    }}
                  >
                    {manualSelectMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                    {manualSelectMutation.isPending ? t('taskWorkspace.manualSelectProcessing') : t('taskWorkspace.selectCandidate')}
                  </Button>
                </MetadataCandidateCard>
              )
            })}
          </div>
        ) : null}

        {!researchMutation.isPending && candidates.length === 0 && !searchSummary ? (
          <p className="text-xs text-muted-foreground">{t('taskWorkspace.searchHint')}</p>
        ) : null}
        {!researchMutation.isPending && candidates.length === 0 && searchSummary ? (
          <p className="text-xs text-muted-foreground">{t('taskWorkspace.noCandidatesHint')}</p>
        ) : null}
      </div>
    </section>
  )
}

function MetadataDetailSection({ metadataDetail }: { metadataDetail: TaskDetailDto['metadata_detail'] }) {
  const { t } = useTranslation()
  if (!metadataDetail) {
    return <MessageCallout title={t('taskWorkspace.metadataDetail')} description={t('taskWorkspace.metadataDetailEmpty')} />
  }
  const actorsStr = metadataDetail.actors.length === 0 ? '' : metadataDetail.actors.slice(0, 5).map((a) => a.role ? `${a.name}（${a.role}）` : a.name).join('、')
  const directorsStr = metadataDetail.directors.length === 0 ? '' : metadataDetail.directors.map((d) => d.name).join('、')

  return (
    <section className="grid gap-4 rounded-lg border border-border bg-surface p-4">
      <div className="grid gap-1">
        <h2 className="text-lg font-medium text-surface-foreground">{t('taskWorkspace.metadataDetail')}</h2>
        <p className="text-sm text-muted-foreground">{t('taskWorkspace.metadataDetailDesc')}</p>
      </div>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.5fr)_minmax(280px,1fr)]">
        <div className="grid gap-4">
          <div className="grid gap-2">
            <span className="text-xs text-muted-foreground">{t('taskWorkspace.overview')}</span>
            {metadataDetail.overview ? <p className="text-sm leading-7 text-surface-foreground">{metadataDetail.overview}</p> : <p className="text-sm text-muted-foreground">{t('taskWorkspace.overviewEmpty')}</p>}
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            <div className="grid gap-2 rounded-md border border-border/70 bg-background p-4">
              <span className="text-xs text-muted-foreground">{t('taskWorkspace.directors')}</span>
              <p className="text-sm leading-6 text-surface-foreground">{directorsStr || t('taskWorkspace.directorsEmpty')}</p>
            </div>
            <div className="grid gap-2 rounded-md border border-border/70 bg-background p-4">
              <span className="text-xs text-muted-foreground">{t('taskWorkspace.actors')}</span>
              <p className="text-sm leading-6 text-surface-foreground">{actorsStr || t('taskWorkspace.actorsEmpty')}</p>
            </div>
          </div>
        </div>
        <div className="grid gap-3 rounded-md border border-border/70 bg-background p-4">
          <h3 className="text-sm font-medium text-surface-foreground">{t('taskWorkspace.externalInfo')}</h3>
          <dl className="grid gap-3">
            {metadataDetail.tmdb_id ? <DetailItem label="TMDB ID" value={metadataDetail.tmdb_id} /> : null}
            {metadataDetail.imdb_id ? <DetailItem label="IMDb ID" value={metadataDetail.imdb_id} /> : null}
            {metadataDetail.release_date ? <DetailItem label={t('taskWorkspace.releaseDate')} value={metadataDetail.release_date} /> : null}
            {metadataDetail.studios.length > 0 ? <DetailItem label={t('taskWorkspace.studios')} value={metadataDetail.studios.join('、')} /> : null}
          </dl>
          {!metadataDetail.tmdb_id && !metadataDetail.imdb_id && !metadataDetail.release_date && metadataDetail.studios.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t('taskWorkspace.externalInfoEmpty')}</p>
          ) : null}
        </div>
      </div>
    </section>
  )
}

function CompletedHeroSection({ detail }: { detail: TaskDetailDto }) {
  const { t } = useTranslation()
  const metadata = detail.metadata_detail
  const isCompleted = detail.task.status_summary.status === 'library_import_complete' || detail.task.status_summary.status === 'completed'
  if (!metadata || !isCompleted) return null

  const title = metadata.title ?? detail.task.title ?? t('taskWorkspace.untitledItem')
  const subtitleParts = [metadata.original_title, metadata.year == null ? null : String(metadata.year)].filter(Boolean)
  const facts = [
    metadata.rating == null ? null : t('taskWorkspace.rating', { value: metadata.rating.toFixed(1) }),
    metadata.runtime_minutes == null ? null : t('taskWorkspace.runtime', { value: metadata.runtime_minutes }),
    metadata.genres.length > 0 ? metadata.genres.join(' / ') : null,
    metadata.countries.length > 0 ? metadata.countries.join(' / ') : null,
  ].filter(Boolean)

  return (
    <section className="overflow-hidden rounded-lg border border-border bg-surface">
      <div className="relative min-h-[320px]">
        {metadata.fanart_url ? (
          <>
            <MediaImage src={metadata.fanart_url} alt={`${title} fanart`} className="absolute inset-0 h-full w-full object-cover" fallback={<div className="absolute inset-0 bg-muted/40" aria-label={t('taskWorkspace.noFanart')} />} />
            <div className="absolute inset-0 bg-gradient-to-r from-background via-background/88 to-background/55" />
          </>
        ) : (
          <div className="absolute inset-0 bg-muted/40" aria-label={t('taskWorkspace.noFanart')} />
        )}
        <div className="relative grid min-h-[320px] gap-6 p-6 md:grid-cols-[180px_minmax(0,1fr)] md:p-8">
          <div className="flex items-start justify-center md:justify-start">
            <MediaImage src={metadata.poster_url} alt={`${title} poster`} className="h-[240px] w-[160px] rounded-md border border-border/70 object-cover shadow-lg" fallback={<div className="flex h-[240px] w-[160px] items-center justify-center rounded-md border border-dashed border-border/70 bg-background/70 p-4 text-center text-sm text-muted-foreground">{t('taskWorkspace.noPoster')}</div>} />
          </div>
          <div className="flex min-w-0 flex-col justify-end gap-4">
            <MediaImage src={metadata.clearlogo_url} alt={`${title} clearlogo`} className="max-h-16 w-auto max-w-full object-contain object-left" fallback={<div className="flex min-h-16 w-full items-center rounded-md border border-dashed border-border/60 bg-background/50 px-4 text-sm text-muted-foreground">{t('taskWorkspace.noClearlogo')}</div>} />
            <div className="grid gap-2">
              <h2 className="text-3xl font-semibold tracking-normal text-surface-foreground">{title}</h2>
              {subtitleParts.length > 0 ? <p className="text-sm text-muted-foreground">{subtitleParts.join(' · ')}</p> : null}
            </div>
            {facts.length > 0 ? (
              <div className="flex flex-wrap gap-2">
                {facts.map((fact) => <span key={fact} className="inline-flex items-center rounded-full border border-border/70 bg-background/75 px-3 py-1 text-sm text-surface-foreground">{fact}</span>)}
              </div>
            ) : null}
          </div>
        </div>
      </div>
    </section>
  )
}


function WriteResultSection({ writePlan, writeResult }: { writePlan: TaskDetailDto['write_plan']; writeResult: TaskDetailDto['write_result'] }) {
  const { t } = useTranslation()
  if (!writePlan && !writeResult) return <MessageCallout title={t('taskWorkspace.writeResult')} description={t('taskWorkspace.writeResultEmpty')} />
  return (
    <section className="grid gap-4 rounded-lg border border-border bg-surface p-4">
      <div className="grid gap-1">
        <h2 className="text-lg font-medium text-surface-foreground">{t('taskWorkspace.writeResult')}</h2>
        <p className="text-sm text-muted-foreground">{t('taskWorkspace.writeResultDesc')}</p>
      </div>
      {writePlan ? (
        <dl className="grid gap-3 md:grid-cols-2">
          <DetailItem label={t('taskWorkspace.targetDir')} value={writePlan.target_dir} />
          <DetailItem label={t('taskWorkspace.videoFile')} value={writePlan.target_file ?? t('common.none')} />
          <DetailItem label={t('taskWorkspace.nfoPath')} value={writePlan.nfo_path ?? t('common.none')} />
          <DetailItem label={t('taskWorkspace.posterPath')} value={writePlan.poster_path ?? t('common.none')} />
          <DetailItem label={t('taskWorkspace.fanartPath')} value={writePlan.fanart_path ?? t('common.none')} />
          <DetailItem label={t('taskWorkspace.clearlogoPath')} value={writePlan.clearlogo_path ?? t('common.none')} />
        </dl>
      ) : null}
      {writeResult ? (
        <dl className="grid gap-3 md:grid-cols-2">
          <DetailItem label={t('taskWorkspace.writeStatus')} value={getWriteResultStatusLabel(writeResult.status)} />
          <DetailItem label={t('taskWorkspace.failureReason')} value={writeResult.failure_reason ?? t('common.none')} />
          <DetailItem label={t('taskWorkspace.warnings')} value={writeResult.warnings.length === 0 ? t('common.none') : writeResult.warnings.join('；')} />
        </dl>
      ) : null}
    </section>
  )
}

function FileAssetsSection({ fileAssets }: { fileAssets: TaskDetailDto['file_assets'] }) {
  const { t } = useTranslation()
  if (fileAssets.length === 0) return <MessageCallout title={t('taskWorkspace.fileAssets')} description={t('taskWorkspace.fileAssetsEmpty')} />
  return (
    <section className="grid gap-4 rounded-lg border border-border bg-surface p-4">
      <div className="grid gap-1">
        <h2 className="text-lg font-medium text-surface-foreground">{t('taskWorkspace.fileAssets')}</h2>
        <p className="text-sm text-muted-foreground">{t('taskWorkspace.fileAssetsDesc')}</p>
      </div>
      <div className="grid gap-3">
        {fileAssets.map((asset) => (
          <article key={`${asset.role}-${asset.path}`} className="grid gap-2 rounded-md border border-border/70 bg-background p-4 md:grid-cols-[180px_140px_minmax(0,1fr)] md:items-start">
            <div className="grid gap-1"><span className="text-xs text-muted-foreground">{t('taskWorkspace.assetRole')}</span><strong className="text-sm font-medium text-surface-foreground">{getFileAssetRoleLabel(asset.role)}</strong></div>
            <div className="grid gap-1"><span className="text-xs text-muted-foreground">{t('taskWorkspace.fileSize')}</span><span className="text-sm text-surface-foreground">{formatFileSize(asset.size_bytes)}</span></div>
            <div className="grid gap-1"><span className="text-xs text-muted-foreground">{t('taskWorkspace.finalPath')}</span><span className="break-all text-sm text-surface-foreground">{asset.path}</span></div>
          </article>
        ))}
      </div>
    </section>
  )
}

function PublishInfoSection({ detail }: { detail: TaskDetailDto }) {
  const { t } = useTranslation()
  const copyOperation = detail.operation_records.find((r) => r.operation_type === 'copy_to_staging')
  const publishOperation = detail.operation_records.find((r) => r.operation_type === 'publish_to_library')
  if (!copyOperation && !publishOperation) return <MessageCallout title={t('taskWorkspace.publishInfo')} description={t('taskWorkspace.publishInfoEmpty')} />
  const transferMethod = typeof copyOperation?.details.transfer_method === 'string' ? copyOperation.details.transfer_method : null
  const durationMs = typeof copyOperation?.details.duration_ms === 'number' ? copyOperation.details.duration_ms : null
  const copiedBytes = detail.file_assets.find((a) => a.role === 'library_video')?.size_bytes ?? null
  return (
    <section className="grid gap-4 rounded-lg border border-border bg-surface p-4">
      <div className="grid gap-1"><h2 className="text-lg font-medium text-surface-foreground">{t('taskWorkspace.publishInfo')}</h2><p className="text-sm text-muted-foreground">{t('taskWorkspace.publishInfoDesc')}</p></div>
      <dl className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <DetailItem label={t('taskWorkspace.transferMethod')} value={transferMethod === 'copy' ? t('taskWorkspace.transferMethodCopy') : transferMethod ?? t('common.unknown')} />
        <DetailItem label={t('taskWorkspace.transferDuration')} value={formatDuration(durationMs)} />
        <DetailItem label={t('taskWorkspace.bytesCopied')} value={formatFileSize(copiedBytes)} />
        <DetailItem label={t('taskWorkspace.publishPhase')} value={getTaskStepLabel(detail.task.status_summary.current_step)} />
      </dl>
      <dl className="grid gap-3 md:grid-cols-2">
        <DetailItem label={t('taskWorkspace.copyTarget')} value={copyOperation?.target_path ?? t('common.none')} />
        <DetailItem label={t('taskWorkspace.finalPublishDir')} value={publishOperation?.target_path ?? t('common.none')} />
      </dl>
    </section>
  )
}

function TimelineSection({ timeline }: { timeline: TaskDetailDto['timeline'] }) {
  const { t } = useTranslation()
  if (timeline.length === 0) return <MessageCallout title={t('taskWorkspace.timeline')} description={t('taskWorkspace.timelineEmpty')} />
  return (
    <section className="grid gap-4 rounded-lg border border-border bg-surface p-4">
      <div className="grid gap-1"><h2 className="text-lg font-medium text-surface-foreground">{t('taskWorkspace.timeline')}</h2><p className="text-sm text-muted-foreground">{t('taskWorkspace.timelineDesc')}</p></div>
      <div className="grid gap-3">
        {timeline.map((event) => (
          <article key={event.key} className="grid gap-2 rounded-md border border-border/70 bg-background p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-sm font-medium text-surface-foreground">{event.title}</h3>
              <span className="font-mono text-xs text-muted-foreground">{event.created_at}</span>
            </div>
            {event.detail ? <p className="text-sm leading-6 text-muted-foreground">{event.detail}</p> : null}
            <span className="text-xs text-muted-foreground">{getTimelineToneLabel(event.tone)}</span>
          </article>
        ))}
      </div>
    </section>
  )
}

function RevokePublishSection({ detail, service = defaultTaskService }: { detail: TaskDetailDto; service?: TaskDetailService }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const taskId = detail.task.id
  const isPublished = detail.task.status_summary.status === 'library_import_complete'
  const [showConfirm, setShowConfirm] = useState(false)

  const checkQuery = useQuery({
    queryKey: ['revoke-publish-check', taskId],
    queryFn: () => service.getRevokePublishCheck(taskId),
    enabled: false,
  })

  const revokeMutation = useMutation({
    mutationFn: () => service.executeRevokePublish(taskId),
    onSuccess: (result) => {
      const data = result.data
      if (data.status === 'deleted') {
        void queryClient.invalidateQueries({ queryKey: ['tasks'] })
        navigate('/tasks', { replace: true })
      } else {
        // waiting_user: stay on page to show post-revoke decision in Agent panel
        void queryClient.invalidateQueries({ queryKey: ['task-detail', taskId] })
        void queryClient.invalidateQueries({ queryKey: ['tasks'] })
        void queryClient.invalidateQueries({ queryKey: ['agent-decisions', taskId] })
        void queryClient.invalidateQueries({ queryKey: ['agent-messages', taskId] })
        setShowConfirm(false)
      }
    },
  })

  if (!isPublished) return <MessageCallout title={t('taskWorkspace.revokePublish')} description={t('taskWorkspace.revokeOnlyPublished')} />

  const check = checkQuery.data?.data

  const handleOpenCheck = () => {
    void checkQuery.refetch().then((result) => { if (result.data) setShowConfirm(true) })
  }

  const isPending = checkQuery.isFetching || revokeMutation.isPending

  return (
    <section className="grid gap-4 rounded-lg border border-border bg-surface p-4">
      <div className="grid gap-1"><h2 className="text-lg font-medium text-surface-foreground">{t('taskWorkspace.revokePublish')}</h2><p className="text-sm text-muted-foreground">{t('taskWorkspace.revokePublishDesc')}</p></div>
      {!showConfirm ? (
        <div className="flex items-center justify-end">
          <button type="button" disabled={isPending} onClick={handleOpenCheck} className="inline-flex h-10 items-center rounded-md border border-destructive/40 bg-surface px-4 text-sm font-medium text-destructive hover:bg-destructive/10 disabled:opacity-50">
            {isPending ? t('taskWorkspace.revokeChecking') : t('taskWorkspace.revokeButton')}
          </button>
        </div>
      ) : null}
      {showConfirm && check ? (
        <div className="grid gap-4 rounded-md border border-border/70 bg-background p-4">
          {check.allowed ? (
            <>
              <div className="grid gap-2">
                <p className="text-sm leading-6 text-surface-foreground">{check.outcome_description}</p>
                {check.publish_dir ? <p className="text-xs text-muted-foreground">{t('taskWorkspace.revokePublishDirHint')}{check.publish_dir}</p> : null}
              </div>
              {revokeMutation.isError ? <InlineMessage variant="error" title={t('taskWorkspace.operationFailed')} description={revokeMutation.error instanceof Error ? revokeMutation.error.message : undefined} /> : null}
              <div className="flex items-center justify-end gap-3">
                <button type="button" disabled={isPending} onClick={() => setShowConfirm(false)} className="inline-flex h-10 items-center rounded-md border border-border bg-surface px-4 text-sm text-surface-foreground hover:bg-muted disabled:opacity-50">{t('common.cancel')}</button>
                <button type="button" disabled={isPending} onClick={() => revokeMutation.mutate()} className="inline-flex h-10 items-center rounded-md bg-destructive px-4 text-sm font-medium text-destructive-foreground hover:opacity-90 disabled:opacity-50">{revokeMutation.isPending ? t('taskWorkspace.revokeExecuting') : t('taskWorkspace.confirmRevoke')}</button>
              </div>
            </>
          ) : (
            <div className="grid gap-3">
              <p className="text-sm text-muted-foreground">{check.outcome_description}</p>
              <div className="flex items-center justify-end"><button type="button" onClick={() => setShowConfirm(false)} className="inline-flex h-10 items-center rounded-md border border-border bg-surface px-4 text-sm text-surface-foreground hover:bg-muted">{t('common.close')}</button></div>
            </div>
          )}
        </div>
      ) : null}
      {showConfirm && checkQuery.isError ? <InlineMessage variant="error" title={t('taskWorkspace.revokeCheckFailed')} description={checkQuery.error instanceof Error ? checkQuery.error.message : undefined} /> : null}
    </section>
  )
}

// ── main page ──

export function TaskDetailPage({ service = defaultTaskService }: { service?: TaskDetailService }) {
  const { t } = useTranslation()
  const { taskId } = useParams<{ taskId: string }>()

  const taskDetailQuery = useQuery({
    queryKey: ['task-detail', taskId],
    queryFn: () => service.getTaskDetail(taskId!),
    enabled: Boolean(taskId),
  })

  if (taskDetailQuery.isLoading) {
    return (
      <div className="grid gap-6">
        <BackToTasksLink />
        <div className="grid gap-1">
          <h1 className="text-3xl font-semibold tracking-normal text-balance">{t('taskWorkspace.taskDetail')}</h1>
          <p className="text-sm leading-6 text-muted-foreground">{`${t('taskWorkspace.taskIdLabel')}${taskId}`}</p>
        </div>
        <SkeletonBlock className="h-32" />
        <SkeletonBlock className="h-44" />
      </div>
    )
  }

  if (taskDetailQuery.isError || !taskDetailQuery.data) {
    return (
      <div className="grid gap-6">
        <BackToTasksLink />
        <div className="grid gap-1">
          <h1 className="text-3xl font-semibold tracking-normal text-balance">{t('taskWorkspace.taskDetail')}</h1>
          <p className="text-sm leading-6 text-muted-foreground">{`${t('taskWorkspace.taskIdLabel')}${taskId}`}</p>
        </div>
        <ErrorState title={t('taskWorkspace.loadFailed')} description={taskDetailQuery.error instanceof Error ? taskDetailQuery.error.message : t('common.pleaseRetry')} />
      </div>
    )
  }

  const detail = taskDetailQuery.data.data
  const status = detail.task.status_summary.status

  return (
    <div className="grid gap-6">
      <BackToTasksLink />

      {/* page header */}
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="grid min-w-0 max-w-5xl gap-1">
          <h1 className="text-3xl font-semibold tracking-normal text-balance">{t('taskWorkspace.taskDetail')}</h1>
          <p className="text-sm leading-6 text-muted-foreground">{`${t('taskWorkspace.taskIdLabel')}${detail.task.id}`}</p>
        </div>
      </div>

      {/* status badges + latest message */}
      <div className="grid gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge status={status} />
          <ConfidenceBadge level={detail.task.status_summary.confidence_level} value={detail.task.status_summary.confidence} />
        </div>
        <MessageCallout title={t('taskWorkspace.prompt')} description={detail.task.status_summary.latest_message ? resolveLatestMessage(detail.task.status_summary.latest_message) : t('taskWorkspace.noLatestMessage')} />
      </div>

      {/* two-pane workspace */}
      <div className="grid gap-6 lg:grid-cols-[1fr_400px]">
        {/* left: task facts */}
        <div className="min-w-0 space-y-4">
          <CompletedHeroSection detail={detail} />
          <BaseInfoSection detail={detail} />
          <SourceSelectionSection sourceSelection={detail.source_selection} blockedReason={null} />
          <ManualMetadataResearchSection detail={detail} service={service} />
          <MetadataDetailSection metadataDetail={detail.metadata_detail} />
          <WriteResultSection writePlan={detail.write_plan} writeResult={detail.write_result} />
          <PublishInfoSection detail={detail} />
          <FileAssetsSection fileAssets={detail.file_assets} />
          <TimelineSection timeline={detail.timeline} />
          <RevokePublishSection detail={detail} service={service} />
        </div>

        {/* right: Agent panel */}
        <div className="lg:sticky lg:top-4 self-start">
          <AgentPanel
            taskId={detail.task.id}
            agentStatus={detail.task.agent_status_summary}
            taskStatus={detail.task.status_summary?.status}
            service={service}
          />
        </div>
      </div>
    </div>
  )
}
