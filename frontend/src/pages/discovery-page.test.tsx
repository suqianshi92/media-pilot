import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { ToastProvider } from '@/components/shared/toast'
import { DiscoveryPage } from './discovery-page'
import type { ResourceCandidate, ResourceSearchData } from '@/types/discovery'
import type { TaskService } from '@/services/task-service'

function getInput() {
  return screen.getAllByTestId('discovery-search-input')[0]
}
function getSearchBtn() {
  return screen.getAllByRole('button', { name: /搜索/ })[0]
}

function makeCandidate(overrides: Partial<ResourceCandidate> = {}): ResourceCandidate {
  return {
    candidate_token: 'mock_token_001',
    title: 'Test Movie',
    indexer: 'TestIndexer',
    source: 'prowlarr',
    size_bytes: null,
    seeders: 0,
    leechers: 0,
    publish_date: null,
    download_count: 0,
    category: '',
    match_reason: '',
    downloadable: false,
    relevance_score: 0,
    relevance_level: 'low',
    match_reasons: [],
    release_tags: null,
    display_tags: [],
    ...overrides,
  }
}

function mockSearchResult(
  message: string,
  candidates: ResourceCandidate[] = [],
  intentOverride: Record<string, unknown> = {},
): { status: string; data: ResourceSearchData; messages: unknown[]; meta: Record<string, unknown> } {
  return {
    status: 'success',
    data: {
      candidates,
      query_used: 'test',
      search_type: 'movie',
      source: 'prowlarr',
      message,
      intent: {
        query_text: 'test',
        search_type: 'movie',
        title_candidates: [],
        resource_keywords: ['test'],
        profile_hint: 'unknown',
        preferred_title_candidates: [],
        adult_identifier_candidates: [],
        resource_search_keywords: ['test'],
        reason: 't',
        preferred_resolutions: [],
        preferred_sources: [],
        preferred_video_codecs: [],
        preferred_hdr_tags: [],
        preferred_audio_tags: [],
        ...intentOverride,
      },
    },
    messages: [],
    meta: {},
  }
}

function mockService(overrides: Partial<TaskService> = {}): TaskService {
  return {
    searchResources: vi.fn().mockResolvedValue(mockSearchResult('ok')),
    submitDownload: vi.fn().mockResolvedValue({ status: 'success', data: {}, messages: [], meta: {} }),
    getProfileOptions: vi.fn().mockResolvedValue([
      { value: 'tmdb_movie', label: 'TMDB Movie', disabled: false },
      { value: 'tmdb_show', label: 'TMDB Show', disabled: false },
      { value: 'tpdb_adult_movie', label: 'TPDB JAV', disabled: true, reason: '未配置' },
    ]),
    ...overrides,
  } as unknown as TaskService
}

describe('DiscoveryPage', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('renders candidate cards with download button', async () => {
    const searchFn = vi.fn().mockResolvedValue(
      mockSearchResult('ok', [
        makeCandidate({
          candidate_token: 'tok_card_test',
          title: 'A Very Long Movie Title That Should Be Truncated 2024 2160p UHD BluRay x265',
          indexer: 'LongIndexerName',
          downloadable: true,
          seeders: 100,
          relevance_level: 'high',
          match_reasons: ['匹配片名', '匹配年份'],
        }),
      ])
    )
    const svc = mockService({ searchResources: searchFn })
    render(<ToastProvider><MemoryRouter initialEntries={['/discovery']}><Routes><Route path="/discovery" element={<DiscoveryPage service={svc} />} /></Routes></MemoryRouter></ToastProvider>)
    await userEvent.type(getInput(), 'movie')
    await userEvent.click(getSearchBtn())
    await waitFor(() => { expect(screen.getByTestId('resource-candidate')).toBeInTheDocument() })

    // 下载按钮可见且有 data-testid
    const downloadBtns = screen.getAllByTestId('download-button')
    expect(downloadBtns.length).toBeGreaterThanOrEqual(1)
    expect(downloadBtns[0]).toBeVisible()
    expect(downloadBtns[0]).not.toBeDisabled()
  })

  it('does not render raw JSON in candidate cards', async () => {
    const searchFn = vi.fn().mockResolvedValue(
      mockSearchResult('ok', [
        makeCandidate({
          candidate_token: 'tok_no_json',
          title: 'Clean Movie',
          downloadable: true,
        }),
      ])
    )
    const svc = mockService({ searchResources: searchFn })
    render(<ToastProvider><MemoryRouter initialEntries={['/discovery']}><Routes><Route path="/discovery" element={<DiscoveryPage service={svc} />} /></Routes></MemoryRouter></ToastProvider>)
    await userEvent.type(getInput(), 'clean')
    await userEvent.click(getSearchBtn())
    await waitFor(() => { expect(screen.getByText('Clean Movie')).toBeInTheDocument() })

    // 不应出现原始 JSON 片段
    expect(screen.queryByText(/download_url/)).not.toBeInTheDocument()
    expect(screen.queryByText(/magnet_url/)).not.toBeInTheDocument()
    expect(screen.queryByText(/"candidate_token"/)).not.toBeInTheDocument()
  })
  afterEach(() => { cleanup() })

  it('renders search input and button', () => {
    render(<ToastProvider><MemoryRouter initialEntries={['/discovery']}><Routes><Route path="/discovery" element={<DiscoveryPage />} /></Routes></MemoryRouter></ToastProvider>)
    expect(getInput()).toBeInTheDocument()
    expect(getSearchBtn()).toBeInTheDocument()
  })

  it('disables search button when input is empty', () => {
    render(<ToastProvider><MemoryRouter initialEntries={['/discovery']}><Routes><Route path="/discovery" element={<DiscoveryPage />} /></Routes></MemoryRouter></ToastProvider>)
    expect(getSearchBtn()).toBeDisabled()
  })

  it('shows search type radio buttons', () => {
    render(<ToastProvider><MemoryRouter initialEntries={['/discovery']}><Routes><Route path="/discovery" element={<DiscoveryPage />} /></Routes></MemoryRouter></ToastProvider>)
    expect(screen.getByText('全部')).toBeInTheDocument()
    expect(screen.getByText('电影')).toBeInTheDocument()
    expect(screen.getByText('成人')).toBeInTheDocument()
  })

  it('shows results and calls searchResources with search type', async () => {
    const searchFn = vi.fn().mockResolvedValue(
      mockSearchResult('找到 1 个候选', [makeCandidate({ title: 'T', downloadable: false })])
    )
    const svc = mockService({ searchResources: searchFn })
    render(<ToastProvider><MemoryRouter initialEntries={['/discovery']}><Routes><Route path="/discovery" element={<DiscoveryPage service={svc} />} /></Routes></MemoryRouter></ToastProvider>)
    await userEvent.type(getInput(), 'test')
    await userEvent.click(getSearchBtn())
    await waitFor(() => { expect(screen.getByText('找到 1 个候选')).toBeInTheDocument() })
    expect(searchFn).toHaveBeenCalledWith('test', 'all', true)
  })

  it('does not display LLM intent summary for direct resource search', async () => {
    const searchFn = vi.fn().mockResolvedValue(
      mockSearchResult('ok', [], { query_text: '天气之子 1080p', reason: '动画电影', resource_keywords: ['天气之子 1080p'] })
    )
    const svc = mockService({ searchResources: searchFn })
    render(<ToastProvider><MemoryRouter initialEntries={['/discovery']}><Routes><Route path="/discovery" element={<DiscoveryPage service={svc} />} /></Routes></MemoryRouter></ToastProvider>)
    // 默认是自动模式，直接搜索
    await userEvent.type(getInput(), '天气之子')
    await userEvent.click(getSearchBtn())
    await waitFor(() => {
      expect(searchFn).toHaveBeenCalledWith('天气之子', 'all', true)
    })
    expect(screen.queryByTestId('intent-summary')).not.toBeInTheDocument()
    expect(screen.queryByText(/\"query_text\"/)).not.toBeInTheDocument()
    expect(screen.queryByText('动画电影')).not.toBeInTheDocument()
  })

  it('uses selected search type without enabling intent parsing', async () => {
    const searchFn = vi.fn().mockResolvedValue(
      mockSearchResult('ok', [], { query_text: '天气之子 1080p', reason: '动画电影', resource_keywords: ['天气之子 1080p'] })
    )
    const svc = mockService({ searchResources: searchFn })
    render(<ToastProvider><MemoryRouter initialEntries={['/discovery']}><Routes><Route path="/discovery" element={<DiscoveryPage service={svc} />} /></Routes></MemoryRouter></ToastProvider>)
    await userEvent.click(screen.getByText('电影'))
    await userEvent.type(getInput(), '天气之子')
    await userEvent.click(getSearchBtn())
    await waitFor(() => { expect(searchFn).toHaveBeenCalledWith('天气之子', 'movie', true) })
    expect(screen.queryByTestId('intent-summary')).not.toBeInTheDocument()
  })

  it('streams markdown content discovery messages', async () => {
    const streamContentDiscovery = vi.fn(async (_messages, onDelta) => {
      onDelta('1. **赴汤蹈火**（2016）')
      onDelta('\n   - 推荐搜索词：赴汤蹈火 2016')
    })
    const svc = mockService({ streamContentDiscovery } as Partial<TaskService>)
    render(<ToastProvider><MemoryRouter initialEntries={['/discovery']}><Routes><Route path="/discovery" element={<DiscoveryPage service={svc} />} /></Routes></MemoryRouter></ToastProvider>)

    await userEvent.type(screen.getByTestId('content-discovery-input'), '推荐现代西部片')
    await userEvent.click(screen.getByRole('button', { name: '发送' }))

    await waitFor(() => {
      expect(screen.getByText('赴汤蹈火')).toBeInTheDocument()
      expect(screen.getByText(/推荐搜索词：赴汤蹈火 2016/)).toBeInTheDocument()
    })
    expect(streamContentDiscovery).toHaveBeenCalledWith(
      [{ role: 'user', content: '推荐现代西部片' }],
      expect.any(Function),
    )
  })

  it('keeps local content discovery context across turns', async () => {
    const streamContentDiscovery = vi.fn(async (_messages, onDelta) => {
      onDelta('收到')
    })
    const svc = mockService({ streamContentDiscovery } as Partial<TaskService>)
    render(<ToastProvider><MemoryRouter initialEntries={['/discovery']}><Routes><Route path="/discovery" element={<DiscoveryPage service={svc} />} /></Routes></MemoryRouter></ToastProvider>)

    await userEvent.type(screen.getByTestId('content-discovery-input'), '推荐现代西部片')
    await userEvent.click(screen.getByRole('button', { name: '发送' }))
    await waitFor(() => { expect(streamContentDiscovery).toHaveBeenCalledTimes(1) })

    await userEvent.type(screen.getByTestId('content-discovery-input'), '更冷峻一点')
    await userEvent.click(screen.getByRole('button', { name: '发送' }))
    await waitFor(() => { expect(streamContentDiscovery).toHaveBeenCalledTimes(2) })

    expect(streamContentDiscovery.mock.calls[1][0]).toEqual([
      { role: 'user', content: '推荐现代西部片' },
      { role: 'assistant', content: '收到' },
      { role: 'user', content: '更冷峻一点' },
    ])
  })

  it('clears content discovery session', async () => {
    const streamContentDiscovery = vi.fn(async (_messages, onDelta) => {
      onDelta('1. **边境杀手**（2015）')
    })
    const svc = mockService({ streamContentDiscovery } as Partial<TaskService>)
    render(<ToastProvider><MemoryRouter initialEntries={['/discovery']}><Routes><Route path="/discovery" element={<DiscoveryPage service={svc} />} /></Routes></MemoryRouter></ToastProvider>)

    await userEvent.type(screen.getByTestId('content-discovery-input'), '推荐冷峻犯罪片')
    await userEvent.click(screen.getByRole('button', { name: '发送' }))
    await waitFor(() => { expect(screen.getByText('边境杀手')).toBeInTheDocument() })

    await userEvent.click(screen.getByRole('button', { name: '新会话' }))
    expect(screen.queryByText('边境杀手')).not.toBeInTheDocument()
    expect(screen.getByTestId('content-discovery-empty')).toBeInTheDocument()
  })


  // --- Phase 3: 候选识别默认策略修复 ---

  it('uses intent profile_hint for default profile when TPDB is enabled', async () => {
    const fetchSpy = vi.spyOn(window, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ status: 'success', data: { keyword_used: 'ABP-123', profile: 'tpdb_adult_movie', candidates: [] }, messages: [], meta: {} }))
    )
    const searchFn = vi.fn().mockResolvedValue(
      mockSearchResult('ok', [
        makeCandidate({ candidate_token: 'tok_adult', title: 'ABP-123 1080p', downloadable: true }),
      ], { profile_hint: 'tpdb_adult_movie', search_type: 'adult' })
    )
    // TPDB enabled for this test
    const svc = mockService({
      searchResources: searchFn,
      getProfileOptions: vi.fn().mockResolvedValue([
        { value: 'tmdb_movie', label: 'TMDB Movie', disabled: false },
        { value: 'tpdb_adult_movie', label: 'TPDB JAV', disabled: false },
      ]),
    })
    render(<ToastProvider><MemoryRouter initialEntries={['/discovery']}><Routes><Route path="/discovery" element={<DiscoveryPage service={svc} />} /></Routes></MemoryRouter></ToastProvider>)
    await userEvent.type(getInput(), 'ABP-123')
    await userEvent.click(getSearchBtn())
    await waitFor(() => { expect(screen.getByTestId('resource-candidate')).toBeInTheDocument() })

    const identifyBtn = screen.getByTestId('identify-button')
    await userEvent.click(identifyBtn)
    await waitFor(() => {
      const call = fetchSpy.mock.calls.find(c => (c[0] as string).includes('/identify'))
      expect(call).toBeDefined()
      const body = JSON.parse((call![1] as RequestInit).body as string)
      expect(body.profile).toBe('tpdb_adult_movie')
    })
    fetchSpy.mockRestore()
  })

  it('default keyword from preferred_title_candidates for TMDB profile', async () => {
    const fetchSpy = vi.spyOn(window, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ status: 'success', data: { keyword_used: '天气之子', profile: 'tmdb_movie', candidates: [] }, messages: [], meta: {} }))
    )
    const searchFn = vi.fn().mockResolvedValue(
      mockSearchResult('ok', [
        makeCandidate({ candidate_token: 'tok_tmdb', title: '[TGx] Weathering With You 2019 1080p', downloadable: true }),
      ], {
        profile_hint: 'tmdb_movie',
        search_type: 'movie',
        preferred_title_candidates: ['天气之子', 'Weathering With You'],
        resource_keywords: ['天气之子 1080p'],
      })
    )
    const svc = mockService({ searchResources: searchFn })
    render(<ToastProvider><MemoryRouter initialEntries={['/discovery']}><Routes><Route path="/discovery" element={<DiscoveryPage service={svc} />} /></Routes></MemoryRouter></ToastProvider>)
    await userEvent.type(getInput(), '天气之子')
    await userEvent.click(getSearchBtn())
    await waitFor(() => { expect(screen.getByTestId('resource-candidate')).toBeInTheDocument() })

    const identifyBtn = screen.getByTestId('identify-button')
    await userEvent.click(identifyBtn)
    await waitFor(() => {
      const call = fetchSpy.mock.calls.find(c => (c[0] as string).includes('/identify'))
      expect(call).toBeDefined()
      const body = JSON.parse((call![1] as RequestInit).body as string)
      expect(body.keyword).toBe('天气之子')
      expect(body.profile).toBe('tmdb_movie')
    })
    fetchSpy.mockRestore()
  })

  it('default keyword from adult_identifier_candidates for TPDB profile', async () => {
    const fetchSpy = vi.spyOn(window, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ status: 'success', data: { keyword_used: 'ABP-123', profile: 'tpdb_adult_movie', candidates: [] }, messages: [], meta: {} }))
    )
    const searchFn = vi.fn().mockResolvedValue(
      mockSearchResult('ok', [
        makeCandidate({ candidate_token: 'tok_abp', title: 'ABP-123 1080p FHD', downloadable: true }),
      ], {
        profile_hint: 'tpdb_adult_movie',
        search_type: 'adult',
        adult_identifier_candidates: ['ABP-123'],
        resource_keywords: ['ABP-123'],
      })
    )
    const svc = mockService({
      searchResources: searchFn,
      getProfileOptions: vi.fn().mockResolvedValue([
        { value: 'tmdb_movie', label: 'TMDB Movie', disabled: false },
        { value: 'tpdb_adult_movie', label: 'TPDB JAV', disabled: false },
      ]),
    })
    render(<ToastProvider><MemoryRouter initialEntries={['/discovery']}><Routes><Route path="/discovery" element={<DiscoveryPage service={svc} />} /></Routes></MemoryRouter></ToastProvider>)
    await userEvent.type(getInput(), 'ABP-123')
    await userEvent.click(getSearchBtn())
    await waitFor(() => { expect(screen.getByTestId('resource-candidate')).toBeInTheDocument() })

    const identifyBtn = screen.getByTestId('identify-button')
    await userEvent.click(identifyBtn)
    await waitFor(() => {
      const call = fetchSpy.mock.calls.find(c => (c[0] as string).includes('/identify'))
      expect(call).toBeDefined()
      const body = JSON.parse((call![1] as RequestInit).body as string)
      expect(body.keyword).toBe('ABP-123')
      expect(body.profile).toBe('tpdb_adult_movie')
    })
    fetchSpy.mockRestore()
  })

  it('falls back to searchType when intent profile_hint points to disabled profile', async () => {
    const fetchSpy = vi.spyOn(window, 'fetch').mockImplementation((input, _init) => {
      const url = typeof input === 'string' ? input : (input as Request).url
      if (url.includes('/identify')) {
        return Promise.resolve(new Response(JSON.stringify({ status: 'success', data: { keyword_used: 'test', profile: 'tmdb_movie', candidates: [] } })))
      }
      return Promise.resolve(new Response(JSON.stringify({})))
    })
    const searchFn = vi.fn().mockResolvedValue(
      mockSearchResult('ok', [
        makeCandidate({ candidate_token: 'tok_fallback', title: 'Test Movie', downloadable: true }),
      ], {
        profile_hint: 'tpdb_adult_movie',  // hinted but TPDB is disabled
        search_type: 'all',
        resource_keywords: ['test'],
      })
    )
    // TPDB disabled in mock
    const svc = mockService({ searchResources: searchFn })
    render(<ToastProvider><MemoryRouter initialEntries={['/discovery']}><Routes><Route path="/discovery" element={<DiscoveryPage service={svc} />} /></Routes></MemoryRouter></ToastProvider>)
    await userEvent.type(getInput(), 'test')
    await userEvent.click(getSearchBtn())
    await waitFor(() => { expect(screen.getByTestId('resource-candidate')).toBeInTheDocument() })

    const identifyBtn = screen.getByTestId('identify-button')
    await userEvent.click(identifyBtn)
    await waitFor(() => {
      const call = fetchSpy.mock.calls.find(c => {
        const url = typeof c[0] === 'string' ? c[0] : (c[0] as Request).url
        return url.includes('/identify')
      })
      expect(call).toBeDefined()
      const body = JSON.parse((call![1] as RequestInit).body as string)
      // Should fall back to tmdb_movie (first enabled, TPDB disabled)
      expect(body.profile).toBe('tmdb_movie')
    })
    fetchSpy.mockRestore()
  })

  it('falls back to resource title when intent has no keyword candidates', async () => {
    const fetchSpy = vi.spyOn(window, 'fetch').mockImplementation((input, _init) => {
      const url = typeof input === 'string' ? input : (input as Request).url
      if (url.includes('/identify')) {
        return Promise.resolve(new Response(JSON.stringify({ status: 'success', data: { keyword_used: 'Original Title', profile: 'tmdb_movie', candidates: [] } })))
      }
      return Promise.resolve(new Response(JSON.stringify({})))
    })
    const searchFn = vi.fn().mockResolvedValue(
      mockSearchResult('ok', [
        makeCandidate({ candidate_token: 'tok_no_intent', title: 'Original Title 2024 1080p', downloadable: true }),
      ], {
        profile_hint: 'unknown',
        search_type: 'movie',
        preferred_title_candidates: [],       // empty
        resource_keywords: [],                  // empty
      })
    )
    const svc = mockService({ searchResources: searchFn })
    render(<ToastProvider><MemoryRouter initialEntries={['/discovery']}><Routes><Route path="/discovery" element={<DiscoveryPage service={svc} />} /></Routes></MemoryRouter></ToastProvider>)
    await userEvent.type(getInput(), 'unknown')
    await userEvent.click(getSearchBtn())
    await waitFor(() => { expect(screen.getByTestId('resource-candidate')).toBeInTheDocument() })

    const identifyBtn = screen.getByTestId('identify-button')
    await userEvent.click(identifyBtn)
    await waitFor(() => {
      const call = fetchSpy.mock.calls.find(c => {
        const url = typeof c[0] === 'string' ? c[0] : (c[0] as Request).url
        return url.includes('/identify')
      })
      expect(call).toBeDefined()
      const body = JSON.parse((call![1] as RequestInit).body as string)
      // Falls back to resource title (c.title in handleIdentify)
      expect(body.keyword).toBe('Original Title 2024 1080p')
    })
    fetchSpy.mockRestore()
  })

  it('shows error on failure', async () => {
    const searchFn = vi.fn().mockRejectedValue(new Error('LLM 未配置'))
    const svc = mockService({ searchResources: searchFn })
    render(<ToastProvider><MemoryRouter initialEntries={['/discovery']}><Routes><Route path="/discovery" element={<DiscoveryPage service={svc} />} /></Routes></MemoryRouter></ToastProvider>)
    await userEvent.type(getInput(), 'test')
    await userEvent.click(getSearchBtn())
    await waitFor(() => { expect(screen.getByText('LLM 未配置')).toBeInTheDocument() })
  })

  it('shows empty results message', async () => {
    const searchFn = vi.fn().mockResolvedValue(mockSearchResult('未找到与 "nobody" 相关的资源'))
    const svc = mockService({ searchResources: searchFn })
    render(<ToastProvider><MemoryRouter initialEntries={['/discovery']}><Routes><Route path="/discovery" element={<DiscoveryPage service={svc} />} /></Routes></MemoryRouter></ToastProvider>)
    await userEvent.type(getInput(), 'nobody')
    await userEvent.click(getSearchBtn())
    await waitFor(() => { expect(screen.getByText(/未找到/)).toBeInTheDocument() })
  })

  it('shows seeder/leecher counts with new DTO fields', async () => {
    const searchFn = vi.fn().mockResolvedValue(
      mockSearchResult('ok', [
        makeCandidate({
          candidate_token: 'tok_seeder_test',
          title: 'T',
          size_bytes: 1000,
          seeders: 42,
          leechers: 3,
          publish_date: '2026-01-01T00:00:00Z',
          downloadable: true,
        }),
      ])
    )
    const svc = mockService({ searchResources: searchFn })
    render(<ToastProvider><MemoryRouter initialEntries={['/discovery']}><Routes><Route path="/discovery" element={<DiscoveryPage service={svc} />} /></Routes></MemoryRouter></ToastProvider>)
    await userEvent.type(getInput(), 't')
    await userEvent.click(getSearchBtn())
    await waitFor(() => { expect(screen.getByText('做种: 42')).toBeInTheDocument() })
    expect(screen.getByText('下载: 3')).toBeInTheDocument()
  })

  it('submits download with candidate_token, not URL fields', async () => {
    const searchFn = vi.fn().mockResolvedValue(
      mockSearchResult('ok', [
        makeCandidate({
          candidate_token: 'tok_dl_test',
          title: 'Downloadable Movie',
          downloadable: true,
        }),
      ])
    )
    const downloadFn = vi.fn().mockResolvedValue({
      status: 'success',
      data: { title: 'Downloadable Movie', info_hash: 'abc' },
      messages: [{ level: 'info', code: 'submitted', text: '已提交' }],
      meta: {},
    })
    const svc = mockService({ searchResources: searchFn, submitDownload: downloadFn })
    render(<ToastProvider><MemoryRouter initialEntries={['/discovery']}><Routes><Route path="/discovery" element={<DiscoveryPage service={svc} />} /></Routes></MemoryRouter></ToastProvider>)
    await userEvent.type(getInput(), 'movie')
    await userEvent.click(getSearchBtn())
    await waitFor(() => { expect(screen.getByText('Downloadable Movie')).toBeInTheDocument() })
    // 点击下载
    await userEvent.click(screen.getByRole('button', { name: /下载/ }))
    await waitFor(() => {
      expect(downloadFn).toHaveBeenCalledWith(
        expect.objectContaining({
          candidate_token: 'tok_dl_test',
          title: 'Downloadable Movie',
        })
      )
    })
    // 确认调用不包含旧 URL 字段
    const callArg = downloadFn.mock.calls[0][0]
    expect(callArg).not.toHaveProperty('download_url')
    expect(callArg).not.toHaveProperty('magnet_url')
  })

  it('displays release tags on resource cards', async () => {
    const searchFn = vi.fn().mockResolvedValue(
      mockSearchResult('找到 1 个候选', [
        makeCandidate({
          candidate_token: 'tok_tags',
          title: '测试 2160p REMUX HEVC',
          display_tags: ['2160p', 'REMUX', 'HEVC'],
          downloadable: true,
        }),
      ])
    )
    const svc = mockService({ searchResources: searchFn })
    render(<ToastProvider><MemoryRouter initialEntries={['/discovery']}><Routes><Route path="/discovery" element={<DiscoveryPage service={svc} />} /></Routes></MemoryRouter></ToastProvider>)
    await userEvent.type(getInput(), 'test')
    await userEvent.click(getSearchBtn())
    await waitFor(() => {
      expect(screen.getByText('2160p')).toBeInTheDocument()
      expect(screen.getByText('REMUX')).toBeInTheDocument()
      expect(screen.getByText('HEVC')).toBeInTheDocument()
    })
  })

  it('resets tag filters on new search', async () => {
    const searchFn = vi.fn().mockResolvedValue(
      mockSearchResult('找到 1 个候选', [
        makeCandidate({
          candidate_token: 'tok_any',
          title: 'Any Movie 720p',
          display_tags: ['720p'],
          downloadable: true,
        }),
      ])
    )
    const svc = mockService({ searchResources: searchFn })
    render(<ToastProvider><MemoryRouter initialEntries={['/discovery']}><Routes><Route path="/discovery" element={<DiscoveryPage service={svc} />} /></Routes></MemoryRouter></ToastProvider>)

    // First search
    await userEvent.type(getInput(), 'first')
    await userEvent.click(getSearchBtn())
    await waitFor(() => { expect(screen.getByText('Any Movie 720p')).toBeInTheDocument() })

    // Second search with same mock — result should still be visible
    // Wait past 500ms debounce
    await new Promise(r => setTimeout(r, 600))
    await userEvent.clear(getInput())
    await userEvent.type(getInput(), 'second')
    await userEvent.click(getSearchBtn())
    await waitFor(() => { expect(screen.getByText('Any Movie 720p')).toBeInTheDocument() })

    // Verify searchResources was called twice (proving second search happened)
    expect(searchFn).toHaveBeenCalledTimes(2)
  })
})
