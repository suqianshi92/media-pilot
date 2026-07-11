import { useReducer, useRef, useState, type ChangeEvent, type DragEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { apiFetch } from '@/services/http-client'

import { EmptyState, PageShell } from '@/components/app/shared-ui'
import { MetadataCandidateCard, type MetadataCardCandidate } from '@/components/shared/metadata-candidate-card'
import { getMediaTypeLabel, getMatchReasonLabel, getProfileLabel, getProviderLabel } from '@/components/app/task-labels'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''

// ── types ──

interface UploadItem {
  key: string
  kind: 'torrent' | 'magnet'
  displayName: string
  sizeLabel: string
  sizeBytes: number | null
  /** raw magnet URI or torrent base64 data */
  rawData: string
  /** torrent file name (only for torrent) */
  fileName?: string
  /** submission state */
  submitted: boolean
  submitOk: boolean
  submitMessage: string
  /** metadata preselection */
  preselectedProfile: string | null
  preselectedProvider: string | null
  preselectedExternalId: string | null
  preselectedTitle: string | null
}

interface IdentifyState {
  keyword: string
  profile: string
  candidates: MetadataCandidate[]
  searching: boolean
  error: string | null
}

interface MetadataCandidate {
  provider: string
  provider_id: string
  title: string
  original_title: string
  year: number | null
  media_type: string
  overview: string
  poster_url: string | null
  confidence: number
  match_reason: string
}

type Action =
  | { type: 'ADD_ITEMS'; items: UploadItem[] }
  | { type: 'REMOVE_ITEM'; key: string }
  | { type: 'MARK_SUBMITTING'; key: string }
  | { type: 'MARK_SUBMITTED'; key: string; ok: boolean; message: string }
  | { type: 'SET_PRESELECTION'; key: string; profile: string; provider: string; externalId: string; title: string }

interface State {
  items: UploadItem[]
}

export function reducer(state: State, action: Action): State {
  switch (action.type) {
    case 'ADD_ITEMS': {
      const existing = new Set(state.items.map((it) => it.key))
      const incoming = action.items.filter((it) => !existing.has(it.key))
      return { items: [...state.items, ...incoming] }
    }
    case 'REMOVE_ITEM':
      return { items: state.items.filter((it) => it.key !== action.key) }
    case 'MARK_SUBMITTING':
      return {
        items: state.items.map((it) =>
          it.key === action.key ? { ...it, submitted: true, submitOk: false, submitMessage: '' } : it,
        ),
      }
    case 'MARK_SUBMITTED':
      return {
        items: state.items.map((it) =>
          it.key === action.key
            ? { ...it, submitOk: action.ok, submitMessage: action.message }
            : it,
        ),
      }
    case 'SET_PRESELECTION':
      return {
        items: state.items.map((it) =>
          it.key === action.key
            ? { ...it, preselectedProfile: action.profile, preselectedProvider: action.provider, preselectedExternalId: action.externalId, preselectedTitle: action.title }
            : it,
        ),
      }
  }
}

// ── API helpers ──

async function apiParseUpload(torrents: File[], magnets: string) {
  const form = new FormData()
  torrents.forEach((f) => form.append('torrents', f))
  form.append('magnets', magnets)
  const resp = await apiFetch(`${BASE_URL}/api/v1/manual-upload/parse`, {
    method: 'POST',
    body: form,
  })
  return resp.json()
}

export async function apiSubmitUpload(items: UploadItem[]) {
  const body = {
    items: items.map((it) => ({
      key: it.key,
      kind: it.kind,
      torrent_data_b64: it.kind === 'torrent' ? it.rawData : null,
      magnet_uri: it.kind === 'magnet' ? it.rawData : null,
      display_name: it.displayName,
      preselected_profile: it.preselectedProfile,
      preselected_provider: it.preselectedProvider,
      preselected_external_id: it.preselectedExternalId,
    })),
  }
  const resp = await apiFetch(`${BASE_URL}/api/v1/manual-upload/submit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return resp.json()
}

async function apiIdentify(keyword: string, profile: string) {
  const resp = await apiFetch(`${BASE_URL}/api/v1/resource-discovery/identify`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      profile,
      keyword,
      use_lightweight_cleanup: true,
    }),
  })
  return resp.json()
}

// ── helpers ──

function toBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve((reader.result as string).split(',')[1])
    reader.onerror = reject
    reader.readAsDataURL(file)
  })
}

// ── sub-components ──

function IdentifyPanel({
  item,
  state,
  onSearch,
  onSelect,
  onClose,
}: {
  item: UploadItem
  state: IdentifyState
  onSearch: (keyword: string, profile: string) => void
  onSelect: (candidate: MetadataCandidate) => void
  onClose: () => void
}) {
  const { t } = useTranslation()
  const [keyword, setKeyword] = useState(state.keyword || item.displayName)
  const [profile, setProfile] = useState(state.profile || 'tmdb_movie')

  return (
    <div className="rounded-md border border-border/70 bg-background p-4">
      <div className="flex items-center justify-between mb-3">
        <h4 className="text-sm font-medium text-surface-foreground">{t('manualUpload.identifyPanel')}</h4>
        <Button variant="ghost" size="sm" className="text-xs" onClick={onClose}>
          {t('manualUpload.closeIdentify')}
        </Button>
      </div>
      <div className="grid gap-2 mb-3">
        <Input
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          placeholder={t('manualUpload.identifyKeyword')}
        />
        <select
          className="h-9 w-full rounded-md border border-border bg-background px-3 text-sm text-surface-foreground outline-none"
          value={profile}
          onChange={(e) => setProfile(e.target.value)}
          aria-label={t('manualUpload.identifyScope')}
        >
          <option value="tmdb_movie">{getProfileLabel('tmdb_movie')}</option>
          <option value="tmdb_show">{getProfileLabel('tmdb_show')}</option>
          <option value="tpdb_adult_movie">{getProfileLabel('tpdb_adult_movie')}</option>
        </select>
      </div>
      <Button
        variant="default"
        size="sm"
        disabled={state.searching || !keyword.trim()}
        onClick={() => onSearch(keyword.trim(), profile)}
      >
        {state.searching ? t('manualUpload.identifySearching') : t('manualUpload.identifySearch')}
      </Button>
      {state.error ? (
        <p className="mt-2 text-sm text-destructive">{state.error}</p>
      ) : null}
      {state.candidates.length > 0 ? (
        <div className="mt-3 grid gap-2">
          {state.candidates.map((c) => {
            const cardCandidate: MetadataCardCandidate = {
              title: c.title,
              original_title: c.original_title,
              year: c.year,
              provider: getProviderLabel(c.provider as Parameters<typeof getProviderLabel>[0]),
              provider_id: c.provider_id,
              poster_url: c.poster_url,
              confidence: c.confidence,
              overview: c.overview,
              media_type: getMediaTypeLabel(c.media_type as Parameters<typeof getMediaTypeLabel>[0]),
              match_reason: getMatchReasonLabel(c.match_reason),
            }
            return (
              <MetadataCandidateCard key={c.provider_id} variant="compact" candidate={cardCandidate}>
                <div className="flex justify-end mt-2">
                  <Button
                    variant="default"
                    size="sm"
                    className="h-8 text-xs"
                    onClick={() => onSelect(c)}
                  >
                    {t('manualUpload.selectCandidate')}
                  </Button>
                </div>
              </MetadataCandidateCard>
            )
          })}
        </div>
      ) : state.searching ? null : state.keyword ? (
        <p className="mt-3 text-sm text-muted-foreground">{t('manualUpload.noCandidates')}</p>
      ) : null}
    </div>
  )
}

// ── main page ──

export function ManualUploadPage() {
  const { t } = useTranslation()
  const [state, dispatch] = useReducer(reducer, { items: [] })
  const [magnetText, setMagnetText] = useState('')
  const [globalError, setGlobalError] = useState<string | null>(null)
  const [batchSubmitting, setBatchSubmitting] = useState(false)
  const [identifyOpen, setIdentifyOpen] = useState<string | null>(null)
  const [identifyStates, setIdentifyStates] = useState<Record<string, IdentifyState>>({})
  const fileInputRef = useRef<HTMLInputElement>(null)
  const nextIdRef = useRef(0)
  const [selectedFiles, setSelectedFiles] = useState<File[]>([])
  const [dragOver, setDragOver] = useState(false)

  const pendingItems = state.items.filter((it) => !it.submitted)
  const submittedItems = state.items.filter((it) => it.submitted)

  const handleAddItems = async () => {
    setGlobalError(null)
    const files = fileInputRef.current?.files
    const torrentFiles: File[] = files ? Array.from(files) : []
    const magnets = magnetText.trim()

    if (torrentFiles.length === 0 && !magnets) return

    const newCount = torrentFiles.length + (magnets ? magnets.split('\n').filter(Boolean).length : 0)
    if (newCount > 5) {
      setGlobalError(t('manualUpload.limitReached'))
      return
    }

    // Parse via backend
    try {
      const result = await apiParseUpload(torrentFiles, magnets)
      if (result.status === 'error') {
        setGlobalError(result.messages?.[0]?.text ?? t('manualUpload.parseFailed'))
        return
      }

      const parsedItems: UploadItem[] = []
      const batchId = ++nextIdRef.current
      for (const pi of result.data.items) {
        if (!pi.valid) continue
        const sourceIdx = pi.source_index ?? 0
        const uniqueKey = `${pi.kind}-${batchId}-${sourceIdx}`
        let rawData = ''
        if (pi.kind === 'magnet') {
          const magnetLines = magnets.split('\n').filter(Boolean)
          rawData = magnetLines[sourceIdx] ?? ''
        } else if (pi.kind === 'torrent') {
          if (torrentFiles[sourceIdx]) {
            rawData = await toBase64(torrentFiles[sourceIdx])
          }
        }
        parsedItems.push({
          key: uniqueKey,
          kind: pi.kind as 'torrent' | 'magnet',
          displayName: pi.display_name || pi.key,
          sizeLabel: pi.size_label || t('manualUpload.sizeUnknown'),
          sizeBytes: pi.size_bytes,
          rawData,
          fileName: pi.kind === 'torrent' ? torrentFiles[sourceIdx]?.name : undefined,
          submitted: false,
          submitOk: false,
          submitMessage: '',
          preselectedProfile: null,
          preselectedProvider: null,
          preselectedExternalId: null,
          preselectedTitle: null,
        })
      }

      dispatch({ type: 'ADD_ITEMS', items: parsedItems })

      // Show parse errors
      if (result.data.errors?.length > 0) {
        const errMsgs = result.data.errors.map((e: Record<string, string>) => e.error).join('；')
        setGlobalError(errMsgs)
      }
    } catch (err) {
      setGlobalError(err instanceof Error ? err.message : t('manualUpload.parseFailed'))
    }

    // Reset inputs
    setMagnetText('')
    setSelectedFiles([])
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  const handleSubmitOne = async (item: UploadItem) => {
    dispatch({ type: 'MARK_SUBMITTING', key: item.key })
    try {
      const result = await apiSubmitUpload([item])
      const r = result.data?.results?.[0]
      dispatch({
        type: 'MARK_SUBMITTED',
        key: item.key,
        ok: r?.success ?? false,
        message: r?.message ?? '',
      })
    } catch (err) {
      dispatch({
        type: 'MARK_SUBMITTED',
        key: item.key,
        ok: false,
        message: err instanceof Error ? err.message : t('manualUpload.submitFailed'),
      })
    }
  }

  const handleBatchSubmit = async () => {
    if (pendingItems.length === 0) return
    setBatchSubmitting(true)
    setGlobalError(null)

    // Mark all as submitting
    for (const item of pendingItems) {
      dispatch({ type: 'MARK_SUBMITTING', key: item.key })
    }

    try {
      const result = await apiSubmitUpload(pendingItems)
      const results: Array<{ key: string; success: boolean; message: string }> = result.data?.results ?? []
      for (const r of results) {
        dispatch({
          type: 'MARK_SUBMITTED',
          key: r.key,
          ok: r.success,
          message: r.message,
        })
      }
      const ok = results.filter((r: { success: boolean }) => r.success).length
      if (ok === 0) {
        setGlobalError(t('manualUpload.submitFailed'))
      }
    } catch (err) {
      setGlobalError(err instanceof Error ? err.message : t('manualUpload.submitFailed'))
      // Mark all as failed
      for (const item of pendingItems) {
        dispatch({
          type: 'MARK_SUBMITTED',
          key: item.key,
          ok: false,
          message: err instanceof Error ? err.message : t('manualUpload.submitFailed'),
        })
      }
    } finally {
      setBatchSubmitting(false)
    }
  }

  const handleIdentify = (itemKey: string) => {
    const existing = identifyStates[itemKey]
    setIdentifyStates((prev) => ({
      ...prev,
      [itemKey]: existing || { keyword: '', profile: 'tmdb_movie', candidates: [], searching: false, error: null },
    }))
    setIdentifyOpen(itemKey)
  }

  const handleIdentifySearch = async (itemKey: string, keyword: string, profile: string) => {
    setIdentifyStates((prev) => ({
      ...prev,
      [itemKey]: { ...prev[itemKey], keyword, profile, searching: true, error: null, candidates: [] },
    }))
    try {
      const result = await apiIdentify(keyword, profile)
      if (result.status === 'error') {
        setIdentifyStates((prev) => ({
          ...prev,
          [itemKey]: { ...prev[itemKey], searching: false, error: result.messages?.[0]?.text ?? 'Search failed' },
        }))
        return
      }
      setIdentifyStates((prev) => ({
        ...prev,
        [itemKey]: {
          ...prev[itemKey],
          searching: false,
          candidates: result.data?.candidates ?? [],
          keyword: result.data?.keyword_used ?? keyword,
        },
      }))
    } catch (err) {
      setIdentifyStates((prev) => ({
        ...prev,
        [itemKey]: { ...prev[itemKey], searching: false, error: err instanceof Error ? err.message : 'Search failed' },
      }))
    }
  }

  const handleSelectCandidate = (itemKey: string, candidate: MetadataCandidate) => {
    const identState = identifyStates[itemKey]
    dispatch({
      type: 'SET_PRESELECTION',
      key: itemKey,
      profile: identState?.profile || 'tmdb_movie',
      provider: candidate.provider,
      externalId: candidate.provider_id,
      title: candidate.title,
    })
    setIdentifyOpen(null)
  }

  const handleDragOver = (e: DragEvent) => {
    e.preventDefault()
    setDragOver(true)
  }

  const handleDragLeave = () => setDragOver(false)

  const fileKey = (f: File) => `${f.name}|${f.size}|${f.lastModified}`

  const mergeFiles = (incoming: File[]) => {
    const byKey = new Map(selectedFiles.map((f) => [fileKey(f), f]))
    for (const f of incoming) {
      const k = fileKey(f)
      if (!byKey.has(k)) byKey.set(k, f)
    }
    const merged = Array.from(byKey.values())
    setSelectedFiles(merged)
    if (typeof DataTransfer !== 'undefined' && fileInputRef.current) {
      const dt = new DataTransfer()
      merged.forEach((f) => dt.items.add(f))
      fileInputRef.current.files = dt.files
    }
  }

  const handleDrop = (e: DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    if (e.dataTransfer.files.length > 0) {
      mergeFiles(Array.from(e.dataTransfer.files))
    }
  }

  return (
    <PageShell title={t('manualUpload.title')} description={t('manualUpload.description')}>
      <div className="grid gap-6 max-w-4xl">
        {/* Input Area */}
        <section
          className={`grid gap-4 rounded-lg border-2 p-4 transition-colors ${
            dragOver
              ? 'border-solid border-primary bg-primary/5'
              : 'border-dashed border-border bg-surface'
          }`}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
        >
          {/* Section heading */}
          <h2 className="text-base font-medium text-surface-foreground">{t('manualUpload.title')}</h2>

          {/* Torrent upload — custom button + hidden native input */}
          <div className="grid gap-2">
            <label className="text-sm font-medium text-surface-foreground">{t('manualUpload.torrentUpload')}</label>
            <div className="flex items-center gap-3">
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept=".torrent"
                className="sr-only"
                onChange={(e) => {
                  if (e.target.files && e.target.files.length > 0) {
                    mergeFiles(Array.from(e.target.files))
                  }
                }}
              />
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => fileInputRef.current?.click()}
              >
                {t('manualUpload.torrentSelectFiles')}
              </Button>
              <span className="text-sm text-muted-foreground">
                {selectedFiles.length > 0
                  ? t('manualUpload.torrentFileCount', { count: selectedFiles.length })
                  : t('manualUpload.torrentNoFile')}
              </span>
            </div>
            <p className="text-xs text-muted-foreground">
              {t('manualUpload.torrentHint')} &middot; {t('manualUpload.dragHint')}
            </p>
          </div>

          {/* Selected files list */}
          {selectedFiles.length > 0 ? (
            <div className="grid gap-1.5">
              <span className="text-xs font-medium text-surface-foreground">{t('manualUpload.selectedFilesLabel')}</span>
              <ul className="grid gap-1">
                {selectedFiles.map((f, i) => (
                  <li key={`${f.name}-${i}`} className="flex items-center gap-2 rounded-md border border-border bg-background px-3 py-1.5 text-sm text-surface-foreground">
                    <span className="truncate">{f.name}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {/* Magnet input */}
          <div className="grid gap-2">
            <label className="text-sm font-medium text-surface-foreground">{t('manualUpload.magnetInput')}</label>
            <textarea
              className="h-24 w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-surface-foreground outline-none focus-visible:ring-2 focus-visible:ring-primary resize-y"
              value={magnetText}
              onChange={(e: ChangeEvent<HTMLTextAreaElement>) => setMagnetText(e.target.value)}
              placeholder={t('manualUpload.magnetHint')}
            />
          </div>

          {/* Footer: unified limit hint + add button */}
          <div className="flex items-center justify-between">
            <div className="grid gap-0.5">
              <p className="text-xs text-muted-foreground">{t('manualUpload.batchLimitHint')}</p>
              <span className="text-xs text-muted-foreground">
                {t('manualUpload.itemCount', { count: state.items.length })}
              </span>
            </div>
            <Button
              variant="default"
              onClick={handleAddItems}
            >
              {t('manualUpload.addItems')}
            </Button>
          </div>

          {globalError ? (
            <div className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
              {globalError}
            </div>
          ) : null}
        </section>

        {/* Pending Items */}
        {pendingItems.length > 0 ? (
          <section className="grid gap-3">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-medium text-surface-foreground">{t('manualUpload.pendingItems')}</h2>
              <Button
                variant="default"
                size="sm"
                disabled={batchSubmitting}
                onClick={handleBatchSubmit}
              >
                {batchSubmitting ? t('manualUpload.submitting') : t('manualUpload.downloadAll')}
              </Button>
            </div>

            {pendingItems.map((item) => (
              <div key={item.key}>
                <div className={`flex items-center justify-between rounded-lg border p-4 ${
                  item.preselectedTitle
                    ? 'border-success/50 bg-success/[0.04]'
                    : 'border-border bg-surface'
                }`}>
                  <div className="grid gap-1.5 min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium text-surface-foreground truncate">{item.displayName}</span>
                      <span className="shrink-0 rounded-full border border-border/70 px-2 py-0.5 text-xs text-muted-foreground">
                        {item.kind === 'torrent' ? '.torrent' : 'magnet'}
                      </span>
                      {item.preselectedTitle ? (
                        <span className="shrink-0 inline-flex items-center gap-1 rounded-full bg-success/15 border border-success/40 px-2.5 py-0.5 text-xs font-medium text-success">
                          <svg className="h-3 w-3" viewBox="0 0 16 16" fill="currentColor"><path fillRule="evenodd" d="M13.78 4.22a.75.75 0 010 1.06l-7.25 7.25a.75.75 0 01-1.06 0L2.22 9.28a.75.75 0 011.06-1.06L6 10.94l6.72-6.72a.75.75 0 011.06 0z" clipRule="evenodd"/></svg>
                          {t('manualUpload.preselected', { title: item.preselectedTitle })}
                        </span>
                      ) : null}
                    </div>
                    <div className="flex items-center gap-3 text-xs text-muted-foreground">
                      <span>{item.sizeLabel}</span>
                      {item.fileName ? <span className="truncate">{item.fileName}</span> : null}
                    </div>
                  </div>
                  <div className="flex items-center gap-2 shrink-0 ml-4">
                    <Button
                      variant={item.preselectedTitle ? 'default' : 'secondary'}
                      size="sm"
                      className="h-8 text-xs"
                      onClick={() => handleIdentify(item.key)}
                    >
                      {item.preselectedTitle ? t('manualUpload.reidentify') : t('manualUpload.identify')}
                    </Button>
                    <Button
                      variant="default"
                      size="sm"
                      className="h-8 text-xs"
                      onClick={() => handleSubmitOne(item)}
                    >
                      {t('manualUpload.download')}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-8 w-8 p-0 text-muted-foreground hover:text-destructive"
                      onClick={() => dispatch({ type: 'REMOVE_ITEM', key: item.key })}
                      aria-label={t('manualUpload.deleteItem')}
                    >
                      &times;
                    </Button>
                  </div>
                </div>

                {/* Identify panel */}
                {identifyOpen === item.key ? (
                  <div className="mt-2">
                    <IdentifyPanel
                      item={item}
                      state={identifyStates[item.key] ?? { keyword: '', profile: 'tmdb_movie', candidates: [], searching: false, error: null }}
                      onSearch={(kw, pf) => handleIdentifySearch(item.key, kw, pf)}
                      onSelect={(c) => handleSelectCandidate(item.key, c)}
                      onClose={() => setIdentifyOpen(null)}
                    />
                  </div>
                ) : null}
              </div>
            ))}
          </section>
        ) : null}

        {/* Empty State */}
        {state.items.length === 0 ? (
          <EmptyState
            title={t('manualUpload.noItems')}
            description={t('manualUpload.description')}
          />
        ) : null}

        {/* Submitted Items */}
        {submittedItems.length > 0 ? (
          <section className="grid gap-3">
            <h2 className="text-lg font-medium text-surface-foreground">{t('manualUpload.submittedItems')}</h2>
            {submittedItems.map((item) => (
              <div
                key={item.key}
                className={`flex items-center justify-between rounded-lg border p-4 ${
                  item.submitOk
                    ? 'border-success/30 bg-success/5'
                    : 'border-destructive/30 bg-destructive/5'
                }`}
              >
                <div className="grid gap-0.5 min-w-0 flex-1">
                  <span className="text-sm font-medium text-surface-foreground truncate">{item.displayName}</span>
                  <span className={`text-xs ${item.submitOk ? 'text-success' : 'text-destructive'}`}>
                    {item.submitOk ? t('manualUpload.submitted') : t('manualUpload.submitFailed')}
                    {item.submitMessage ? ` — ${item.submitMessage}` : ''}
                  </span>
                </div>
                <span className="shrink-0 rounded-full border border-border/70 px-2 py-0.5 text-xs text-muted-foreground ml-4">
                  {item.kind === 'torrent' ? '.torrent' : 'magnet'}
                </span>
              </div>
            ))}
          </section>
        ) : null}
      </div>
    </PageShell>
  )
}
