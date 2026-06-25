import { useState, useRef, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import i18n from '@/i18n'
import { Search, Download, Loader2, Scan } from 'lucide-react'
import type { ResourceCandidate, ResourceIntent } from '@/types/discovery'
import { createTaskService, type TaskService } from '@/services/task-service'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { MetadataCandidateCard, type MetadataCardCandidate } from '@/components/shared/metadata-candidate-card'
import { useToast } from '@/components/shared/toast'

type TagGroup = 'resolutions' | 'sources' | 'codecs' | 'hdr_tags' | 'audio_tags'

const defaultService = createTaskService()

interface DiscoveryPageProps {
  service?: TaskService
}

export function DiscoveryPage({ service = defaultService }: DiscoveryPageProps) {
  const { t } = useTranslation()
  const [inputText, setInputText] = useState('')
  const [searchType, setSearchType] = useState('all')
  const [searchMode, setSearchMode] = useState<'auto' | 'manual'>('auto')
  const [lastSearchMode, setLastSearchMode] = useState<'auto' | 'manual' | null>(null)
  const [loading, setLoading] = useState(false)

  const [intent, setIntent] = useState<ResourceIntent | null>(null)
  const [candidates, setCandidates] = useState<ResourceCandidate[]>([])
  const [searchMessage, setSearchMessage] = useState('')
  const [submitting, setSubmitting] = useState<string | null>(null)

  // ── 4.x: 资源发布标签本地筛选 ──
  const [activeTagFilters, setActiveTagFilters] = useState<Record<TagGroup, Set<string>>>({
    resolutions: new Set(),
    sources: new Set(),
    codecs: new Set(),
    hdr_tags: new Set(),
    audio_tags: new Set(),
  })

  const lastSearchRef = useRef(0)
  const { showToast } = useToast()

  // 6: 从后端获取可用 profile 列表
  interface ProfileOption { value: string; label: string; supported: boolean; enabled: boolean }
  const [profileOptions, setProfileOptions] = useState<ProfileOption[]>([])
  useEffect(() => {
    service.getProfileOptions().then(opts => {
      // 适配 getProfileOptions 返回类型（可能是 ProfileOption 或 {value,label,disabled?}）
      const mapped: ProfileOption[] = (opts as any[]).map((o: any) => ({
        value: o.value,
        label: o.label,
        supported: o.enabled ?? !o.disabled,
        enabled: o.enabled ?? !o.disabled,
      }))
      setProfileOptions(mapped)
    }).catch(() => {})
  }, [service])

  // ---- 6.x: 候选识别状态 per-card ----
  const [identifyStates, setIdentifyStates] = useState<Record<string, IdentifyState>>({})

  interface IdentifyCandidate {
    provider: string
    provider_id: string
    title: string
    original_title: string | null
    year: number | null
    media_type: string
    overview: string | null
    poster_url: string | null
    confidence: number
    match_reason: string
  }

  interface IdentifyState {
    panelOpen: boolean
    loading: boolean
    error: string | null
    keyword: string
    profile: string
    useCleanup: boolean
    candidates: IdentifyCandidate[]
    selected: IdentifyCandidate | null
  }

  function getIdentifyState(token: string): IdentifyState {
    return identifyStates[token] ?? {
      panelOpen: false, loading: false, error: null, keyword: '', profile: defaultProfile(),
      useCleanup: false, candidates: [], selected: null,
    }
  }

  function setIdentifyState(token: string, update: Partial<IdentifyState>) {
    setIdentifyStates(prev => ({
      ...prev,
      [token]: { ...(prev[token] ?? getIdentifyState(token)), ...update },
    }))
  }

  function defaultProfile(): string {
    const enabled = profileOptions.filter(p => p.enabled && p.value !== 'all')
    if (enabled.length === 0) return 'tmdb_movie'
    if (intent?.profile_hint && intent.profile_hint !== 'unknown') {
      const hinted = enabled.find(p => p.value === intent.profile_hint)
      if (hinted) return hinted.value
    }
    return enabled[0]?.value || 'tmdb_movie'
  }
  // 3.3: intent-aware default keyword — preferred_title/adult_identifier > resource_keywords > title
  function getDefaultKeyword(c: ResourceCandidate): string {
    if (!intent) return c.title
    const profile = defaultProfile()
    const isTpdb = profile.includes('adult')
    if (isTpdb) {
      return intent.adult_identifier_candidates?.[0]
        || intent.resource_keywords?.[0]
        || c.title
    }
    return intent.preferred_title_candidates?.[0]
      || intent.resource_keywords?.[0]
      || c.title
  }


  // ---- 搜索 ----
  async function handleSearch() {
    const now = Date.now()
    if (now - lastSearchRef.current < 500) return
    lastSearchRef.current = now
    const text = inputText.trim()
    if (!text) return
    setLoading(true)

    setCandidates([])
    setIntent(null)
    setSearchMessage('')
    setIdentifyStates({})  // 6.5: 整页重搜清空临时状态
    setActiveTagFilters({
      resolutions: new Set(),
      sources: new Set(),
      codecs: new Set(),
      hdr_tags: new Set(),
      audio_tags: new Set(),
    })  // 4.x: 新搜索清空标签筛选
    try {
      const result = await service.searchResources(text, searchType, searchMode === 'manual')
      if (result.status === 'error' && result.messages?.[0]) {
        showToast(result.messages[0].text, 'error')
        return
      }
      const data = result.data
      setCandidates(data.candidates)
      setIntent(data.intent)
      setSearchMessage(data.message)
      setLastSearchMode(searchMode)
    } catch (e) {
      showToast(e instanceof Error ? e.message : t('discovery.searchFailed'), 'error')
    } finally {
      setLoading(false)
    }
  }

  // ---- 下载 ----
  async function handleDownload(c: ResourceCandidate) {
    const state = getIdentifyState(c.candidate_token)
    setSubmitting(c.title)
    try {
      const result = await service.submitDownload({
        candidate_token: c.candidate_token,
        title: c.title,
        source: c.source,
        indexer: c.indexer,
        preselected_profile: state.selected ? state.profile : undefined,
        preselected_provider: state.selected?.provider,
        preselected_external_id: state.selected?.provider_id,
      })
      if (result.status === 'error' && result.messages?.[0]) {
        showToast(result.messages[0].text, 'error')
      } else {
        const successMsg = t('discovery.downloadSubmitted', { title: c.title })
        if (state.selected) {
          showToast(successMsg + t('discovery.downloadWithPreselection', { title: state.selected.title }), 'success')
        } else {
          showToast(successMsg + t('discovery.downloadAutoIngest'), 'success')
        }
      }
    } catch (e) {
      showToast(e instanceof Error ? e.message : t('discovery.downloadSubmitFailed'), 'error')
    } finally {
      setSubmitting(null)
    }
  }

  // ---- 6.x: 候选识别 ----
  async function handleIdentify(c: ResourceCandidate) {
    const token = c.candidate_token
    const state = getIdentifyState(token)
    const keyword = state.keyword || getDefaultKeyword(c)

    setIdentifyState(token, { keyword, loading: true, error: null, candidates: [], selected: null })

    try {
      const resp = await fetch('/api/v1/resource-discovery/identify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          candidate_handle: token,
          profile: state.profile,
          keyword,
          use_lightweight_cleanup: state.useCleanup,
        }),
      })
      const data = await resp.json()
      if (data.status === 'error') {
        setIdentifyState(token, { loading: false, error: data.messages?.[0]?.text ?? t('discovery.identifyFailed') })
        return
      }
      setIdentifyState(token, {
        loading: false,
        keyword: data.data.keyword_used,
        candidates: data.data.candidates,
        selected: null,
      })
    } catch (e) {
      setIdentifyState(token, { loading: false, error: e instanceof Error ? e.message : t('discovery.identifyRequestFailed') })
    }
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter' && !loading) handleSearch()
  }

  // ---- helper: IdentifyCandidate → MetadataCardCandidate ----
  function toCardCandidate(c: IdentifyCandidate): MetadataCardCandidate {
    return {
      title: c.title,
      original_title: c.original_title,
      year: c.year,
      provider: c.provider,
      provider_id: c.provider_id,
      poster_url: c.poster_url,
      confidence: c.confidence,
      overview: c.overview,
      media_type: c.media_type,
      match_reason: c.match_reason,
    }
  }

  // ── 标签筛选 ──
  const filteredCandidates = candidates.filter((c) => {
    const tags = c.release_tags
    const groups: TagGroup[] = ['resolutions', 'sources', 'codecs', 'hdr_tags', 'audio_tags']
    for (const group of groups) {
      const selected = activeTagFilters[group]
      if (selected.size === 0) continue
      const candidateTags: string[] = tags?.[group] ?? []
      if (candidateTags.length === 0) return false
      const hasMatch = candidateTags.some((t) => selected.has(t))
      if (!hasMatch) return false
    }
    return true
  })

  // ---- 7.1-7.2: 首屏限量 + 显示更多 ----
  const FIRST_SCREEN = 10
  const [showCount, setShowCount] = useState(FIRST_SCREEN)
  const displayedCandidates = filteredCandidates.slice(0, showCount)

  return (
    <div className="flex flex-col gap-6">
      {/* 搜索栏 */}
      <div className="flex flex-col gap-2">
        <div className="flex gap-2">
          <Input
            placeholder={t('discovery.searchPlaceholder')}
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={loading}
            className="flex-1"
            data-testid="discovery-search-input"
          />
          <Button onClick={handleSearch} disabled={loading || !inputText.trim()}>
            {loading ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : <Search className="h-4 w-4 mr-2" />}
            {t('common.search')}
          </Button>
        </div>
        <div className="flex gap-3 text-sm text-muted-foreground">
          <label className="flex items-center gap-1 cursor-pointer">
            <input
              type="radio"
              name="search-mode"
              value="auto"
              checked={searchMode === 'auto'}
              onChange={() => { setSearchMode('auto'); setShowCount(FIRST_SCREEN) }}
              className="accent-primary"
            />
            {t('discovery.searchMode.auto')}
          </label>
          <label className="flex items-center gap-1 cursor-pointer">
            <input
              type="radio"
              name="search-mode"
              value="manual"
              checked={searchMode === 'manual'}
              onChange={() => { setSearchMode('manual'); setSearchType('all'); setShowCount(FIRST_SCREEN) }}
              className="accent-primary"
            />
            {t('discovery.searchMode.manual')}
          </label>
          {searchMode === 'auto' ? (
            <span className="text-xs text-muted-foreground self-center ml-2">{t('discovery.searchMode.autoTip')}</span>
          ) : (
            <span className="text-xs text-muted-foreground self-center ml-2">{t('discovery.searchMode.manualTip')}</span>
          )}
        </div>
      </div>

      {/* LLM 解析摘要 — 仅当本次结果由自动模式搜索得到时显示 */}
      {intent && lastSearchMode === 'auto' && (
        <div className="rounded-md border border-border bg-surface px-4 py-3 text-sm" data-testid="intent-summary">
          <p className="font-medium mb-1">{t('discovery.searchKeywords')}</p>
          <p className="text-muted-foreground">
            {intent.resource_keywords.length > 0
              ? intent.resource_keywords.join('、')
              : intent.query_text}
          </p>
          {intent.reason && (
            <>
              <p className="font-medium mt-2 mb-1">{t('discovery.analysisReason')}</p>
              <p className="text-muted-foreground">{intent.reason}</p>
            </>
          )}
          <p className="mt-2 text-xs text-muted-foreground">
            {t('discovery.searchTypeLabel')}: {t(`discovery.searchType.${intent.search_type}`)}
            {intent.profile_hint !== 'unknown' && ` · ${t('discovery.profileHint')}: ${intent.profile_hint}`}
          </p>
        </div>
      )}

      {/* 搜索结果 */}
      {searchMessage && (
        <p className="text-sm text-muted-foreground" role="status">{searchMessage}</p>
      )}

      {/* ── 4.x: 标签筛选条 ── */}
      {candidates.length > 0 && (
        <TagFilterBar
          candidates={candidates}
          active={activeTagFilters}
          onToggle={(group, tag) => {
            setActiveTagFilters((prev) => {
              const next = new Set(prev[group])
              if (next.has(tag)) next.delete(tag)
              else next.add(tag)
              return { ...prev, [group]: next }
            })
            setShowCount(FIRST_SCREEN)
          }}
          onReset={() => {
            setActiveTagFilters({
              resolutions: new Set(),
              sources: new Set(),
              codecs: new Set(),
              hdr_tags: new Set(),
              audio_tags: new Set(),
            })
            setShowCount(FIRST_SCREEN)
          }}
        />
      )}

      {candidates.length > 0 && (
        <div className="grid gap-3">
          {displayedCandidates.map((c) => {
            const state = getIdentifyState(c.candidate_token)
            return (
              <article
                key={c.candidate_token}
                data-testid="resource-candidate"
                className="flex flex-col gap-3 rounded-md border border-border bg-surface px-4 py-3 overflow-hidden"
              >
                {/* ---- 主行: 标题 + 操作按钮 ---- */}
                <div className="flex flex-wrap items-center gap-3">
                  <div className="min-w-0 flex-1 basis-64">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="truncate font-medium text-sm">{c.title}</span>
                      <span className={`shrink-0 text-[10px] px-1.5 py-0.5 rounded-full font-medium ${
                        c.relevance_level === 'high' ? 'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300' :
                        c.relevance_level === 'medium' ? 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900 dark:text-yellow-300' :
                        'bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400'
                      }`}>
                        {t(`discovery.relevance.${c.relevance_level}`)}
                      </span>
                      {/* 6.5: 已选摘要 */}
                      {state.selected && (
                        <span className="text-xs text-green-600 dark:text-green-400">
                          {t('discovery.selected')}: {state.selected.title} ({state.selected.year})
                        </span>
                      )}
                    </div>
                    {c.match_reasons.length > 0 && (
                      <p className="text-xs text-muted-foreground mt-0.5 break-words">
                        {c.match_reasons.join(' · ')}
                      </p>
                    )}
                    {/* 4.x: 资源发布标签展示 */}
                    {c.display_tags.length > 0 && (
                      <div className="mt-1 flex flex-wrap gap-1">
                        {c.display_tags.map((tag) => (
                          <span
                            key={tag}
                            className="inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300"
                          >
                            {tag}
                          </span>
                        ))}
                      </div>
                    )}
                    <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
                      <span>{c.indexer}</span>
                      {c.size_bytes != null && <span>{formatSize(c.size_bytes)}</span>}
                      <span>{t('discovery.seeders')}: {c.seeders}</span>
                      {c.leechers > 0 && <span>{t('discovery.leechers')}: {c.leechers}</span>}
                      {c.publish_date && <span>{formatDate(c.publish_date)}</span>}
                    </div>
                  </div>
                  <div className="flex-shrink-0 flex items-center gap-2">
                    {/* 6.1: LLM 清洗开关 — 位于主操作区 */}
                    <label className="flex items-center gap-1 text-xs cursor-pointer text-muted-foreground whitespace-nowrap">
                      <input
                        type="checkbox"
                        checked={state.useCleanup}
                        onChange={() => setIdentifyState(c.candidate_token, { useCleanup: !state.useCleanup })}
                        className="accent-primary"
                      />
                      {t('discovery.llmCleanup')}
                    </label>
                    {/* 6.2: 识别影片按钮 */}
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => {
                        setIdentifyState(c.candidate_token, {
                          panelOpen: true,
                          keyword: state.keyword || getDefaultKeyword(c),
                          profile: state.profile || defaultProfile(),
                          useCleanup: state.useCleanup,
                        })
                        handleIdentify(c)
                      }}
                      disabled={state.loading}
                      data-testid="identify-button"
                    >
                      {state.loading ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Scan className="h-4 w-4 mr-1" />}
                      {t('discovery.identify')}
                    </Button>
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => handleDownload(c)}
                      disabled={!!submitting || !c.downloadable}
                      data-testid="download-button"
                    >
                      {submitting === c.title ? (
                        <Loader2 className="h-4 w-4 animate-spin mr-1" />
                      ) : (
                        <Download className="h-4 w-4 mr-1" />
                      )}
                      {t('discovery.download')}
                    </Button>
                  </div>
                </div>

                {/* ---- 6.2-6.3: 识别面板（panelOpen 持久展开） ---- */}
                {state.panelOpen && (
                  <div className="border-t border-border pt-3 grid gap-3">
                    <div className="flex items-center gap-2 flex-wrap">
                      {/* 关键词输入 */}
                      <Input
                        value={state.keyword}
                        onChange={(e) => setIdentifyState(c.candidate_token, { keyword: e.target.value })}
                        className="flex-1 text-xs h-8"
                        placeholder={t('discovery.keywordPlaceholder')}
                      />
                      {/* Profile 切换 — 从后端获取可用列表 */}
                      <select
                        value={state.profile}
                        onChange={(e) => setIdentifyState(c.candidate_token, { profile: e.target.value })}
                        className="text-xs border border-border rounded bg-surface px-2 py-1"
                      >
                        {profileOptions.filter(p => p.enabled && p.value !== 'all').map(p => (
                          <option key={p.value} value={p.value}>{i18n.t(`taskLabel.profile.${p.value}`, p.label)}</option>
                        ))}
                      </select>
                      {/* 重新搜索 */}
                      <Button variant="secondary" size="sm" onClick={() => handleIdentify(c)} disabled={state.loading}>
                        {state.loading ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : null}
                        {t('discovery.research')}
                      </Button>
                    </div>
                    {/* 候选列表 */}
                    <div className="grid gap-2">
                      {state.loading && (
                        <p className="text-xs text-muted-foreground flex items-center gap-1">
                          <Loader2 className="h-3 w-3 animate-spin" /> {t('discovery.searching')}
                        </p>
                      )}
                      {!state.loading && state.candidates.map((candidate) => (
                        <MetadataCandidateCard
                          key={candidate.provider_id}
                          variant="medium"
                          candidate={toCardCandidate(candidate)}
                          selected={state.selected?.provider_id === candidate.provider_id}
                          onClick={() => setIdentifyState(c.candidate_token, { selected: candidate })}
                        />
                      ))}
                      {state.candidates.length === 0 && !state.loading && (
                        <p className="text-xs text-muted-foreground">{t('discovery.noMatch')}</p>
                      )}
                    </div>
                    {state.error && (
                      <p className="text-xs text-destructive">{state.error}</p>
                    )}
                  </div>
                )}
              </article>
            )
          })}
        </div>
      )}

      {/* 7.2: 显示更多 */}
      {filteredCandidates.length > showCount && (
        <Button
          variant="secondary"
          onClick={() => setShowCount(prev => prev + FIRST_SCREEN)}
          className="self-center"
        >
          {t('discovery.showMore', { current: showCount, total: filteredCandidates.length })}
        </Button>
      )}

      {/* 下载成功提示 */}

    </div>
  )
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString(i18n.language === 'en' ? 'en-US' : 'zh-CN')
  } catch {
    return iso.slice(0, 10)
  }
}

function formatSize(bytes: number): string {
  const units = ['B', 'KB', 'MB', 'GB']
  let size = bytes
  let unitIdx = 0
  while (size >= 1024 && unitIdx < units.length - 1) {
    size /= 1024
    unitIdx++
  }
  return `${size.toFixed(1)} ${units[unitIdx]}`
}


// ── 4.x: 标签筛选条组件 ──


const TAG_GROUP_LABEL_KEYS: Record<TagGroup, string> = {
  resolutions: 'discovery.tagGroup.resolutions',
  sources: 'discovery.tagGroup.sources',
  codecs: 'discovery.tagGroup.codecs',
  hdr_tags: 'discovery.tagGroup.hdr_tags',
  audio_tags: 'discovery.tagGroup.audio_tags',
}

function TagFilterBar({
  candidates,
  active,
  onToggle,
  onReset,
}: {
  candidates: ResourceCandidate[]
  active: Record<TagGroup, Set<string>>
  onToggle: (group: TagGroup, tag: string) => void
  onReset: () => void
}) {
  const { t } = useTranslation()
  const availableTags: Record<TagGroup, Set<string>> = {
    resolutions: new Set(),
    sources: new Set(),
    codecs: new Set(),
    hdr_tags: new Set(),
    audio_tags: new Set(),
  }
  for (const c of candidates) {
    const tags = c.release_tags
    if (!tags) continue
    const groups: TagGroup[] = ['resolutions', 'sources', 'codecs', 'hdr_tags', 'audio_tags']
    for (const g of groups) {
      const values = tags[g] ?? []
      for (const v of values) {
        if (v) availableTags[g].add(v)
      }
    }
  }
  const groups: TagGroup[] = ['resolutions', 'sources', 'codecs', 'hdr_tags', 'audio_tags']
  const hasAnyTags = groups.some((g) => availableTags[g].size > 0)
  if (!hasAnyTags) return null

  return (
    <div className="flex flex-wrap gap-3 rounded-md border border-border bg-surface-dim p-3">
      {groups.map((group) => {
        const tags = [...availableTags[group]].sort()
        if (tags.length === 0) return null
        return (
          <div key={group} className="flex flex-wrap items-center gap-1">
            <span className="text-xs font-medium text-muted-foreground mr-1">
              {t(TAG_GROUP_LABEL_KEYS[group])}:
            </span>
            {tags.map((tag) => {
              const isActive = active[group].has(tag)
              return (
                <button
                  key={tag}
                  type="button"
                  onClick={() => onToggle(group, tag)}
                  className={`inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium transition-colors ${
                    isActive
                      ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/50 dark:text-blue-300 ring-1 ring-blue-400'
                      : 'bg-muted text-muted-foreground hover:bg-muted/80'
                  }`}
                >
                  {tag}
                </button>
              )
            })}
          </div>
        )
      })}
      {groups.some((g) => active[g].size > 0) && (
        <button
          type="button"
          onClick={onReset}
          className="text-[10px] text-muted-foreground hover:text-surface-foreground underline self-end"
        >
          {t('discovery.clearFilters')}
        </button>
      )}
    </div>
  )
}
