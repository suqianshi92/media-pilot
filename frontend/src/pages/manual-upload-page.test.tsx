import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'

import { ManualUploadPage, reducer } from './manual-upload-page'

function renderPage() {
  render(
    <MemoryRouter initialEntries={['/manual-upload']}>
      <ManualUploadPage />
    </MemoryRouter>,
  )
}

function getFileInput(): HTMLInputElement {
  const el = document.querySelector('input[type="file"]')
  if (!el) throw new Error('file input not found')
  return el as HTMLInputElement
}

let originalFetch: typeof globalThis.fetch

beforeEach(() => {
  originalFetch = globalThis.fetch
})

afterEach(() => {
  cleanup()
  globalThis.fetch = originalFetch
})

describe('ManualUploadPage', () => {
  it('renders the page title and description', () => {
    renderPage()

    expect(screen.getByRole('heading', { level: 1, name: '手动上传' })).toBeInTheDocument()
    expect(screen.getAllByText('上传 .torrent 文件或粘贴 magnet 链接，批量导入下载任务').length).toBeGreaterThanOrEqual(1)
  })

  it('renders custom file select button and empty file state', () => {
    renderPage()

    expect(screen.getByRole('button', { name: '选择文件' })).toBeInTheDocument()
    expect(screen.getByText('未选择任何文件')).toBeInTheDocument()
    // 没有选中文件时不显示已选择文件列表
    expect(screen.queryByText('已选择的文件')).not.toBeInTheDocument()
  })

  it('renders magnet textarea', () => {
    renderPage()

    expect(screen.getByText('粘贴 magnet 链接')).toBeInTheDocument()
    expect(screen.getByRole('textbox')).toBeInTheDocument()
  })

  it('renders the Add to List button', () => {
    renderPage()

    expect(screen.getByRole('button', { name: '添加到列表' })).toBeInTheDocument()
  })

  it('shows empty state when no items', () => {
    renderPage()

    expect(screen.getByText('还没有待导入条目，请上传 .torrent 文件或粘贴 magnet 链接')).toBeInTheDocument()
    expect(screen.getAllByText('上传 .torrent 文件或粘贴 magnet 链接，批量导入下载任务').length).toBeGreaterThanOrEqual(1)
  })

  it('shows unified batch limit hint', () => {
    renderPage()

    expect(screen.getByText('单次最多新增 5 个下载对象（.torrent 与 magnet 合并计数）')).toBeInTheDocument()
  })

  it('shows drag hint', () => {
    renderPage()

    expect(screen.getByText(/或将 \.torrent 文件拖拽到此处/)).toBeInTheDocument()
  })

  it('shows item count indicator', () => {
    renderPage()

    expect(screen.getByText('0 个条目')).toBeInTheDocument()
  })

  it('shows selected file names in the upload area', () => {
    renderPage()

    const input = getFileInput()
    const file1 = new File(['dummy'], 'movie.torrent', { type: 'application/x-bittorrent' })
    const file2 = new File(['dummy'], 'show.torrent', { type: 'application/x-bittorrent' })
    fireEvent.change(input, { target: { files: [file1, file2] } })

    // 文件列表标题出现
    expect(screen.getByText('已选择的文件')).toBeInTheDocument()
    // 文件名可见
    expect(screen.getByText('movie.torrent')).toBeInTheDocument()
    expect(screen.getByText('show.torrent')).toBeInTheDocument()
    // 计数更新
    expect(screen.getByText('已选择 2 个文件')).toBeInTheDocument()
  })

  it('accumulates files when selecting one at a time', () => {
    renderPage()

    const input = getFileInput()
    fireEvent.change(input, { target: { files: [new File(['dummy'], 'first.torrent')] } })
    fireEvent.change(input, { target: { files: [new File(['dummy'], 'second.torrent')] } })

    expect(screen.getByText('first.torrent')).toBeInTheDocument()
    expect(screen.getByText('second.torrent')).toBeInTheDocument()
    expect(screen.getByText('已选择 2 个文件')).toBeInTheDocument()
  })

  it('deduplicates truly identical files (same name, size, lastModified)', () => {
    renderPage()

    const input = getFileInput()
    const file = new File(['data'], 'dup.torrent')
    fireEvent.change(input, { target: { files: [file] } })
    fireEvent.change(input, { target: { files: [file] } })

    expect(screen.getByText('已选择 1 个文件')).toBeInTheDocument()
  })

  it('keeps files with same name but different size', () => {
    renderPage()

    const input = getFileInput()
    fireEvent.change(input, { target: { files: [new File(['a'], 'same.torrent')] } })
    fireEvent.change(input, { target: { files: [new File(['bb'], 'same.torrent')] } })

    expect(screen.getByText('已选择 2 个文件')).toBeInTheDocument()
  })

  it('keeps files with same name and size but different lastModified', () => {
    renderPage()

    const input = getFileInput()
    fireEvent.change(input, { target: { files: [new File(['data'], 'same.torrent', { lastModified: 1000 })] } })
    fireEvent.change(input, { target: { files: [new File(['data'], 'same.torrent', { lastModified: 2000 })] } })

    expect(screen.getByText('已选择 2 个文件')).toBeInTheDocument()
  })

  it('selected files list does not appear in pending items area', () => {
    renderPage()

    // 选中文件后，文件名不应出现在待导入条目列表中（那是点击"添加到列表"后才生成的）
    const input = getFileInput()
    fireEvent.change(input, { target: { files: [new File(['dummy'], 'test.torrent')] } })

    expect(screen.getByText('test.torrent')).toBeInTheDocument()
    // 待导入条目仍为空
    expect(screen.getByText('还没有待导入条目，请上传 .torrent 文件或粘贴 magnet 链接')).toBeInTheDocument()
  })

  it('does not render clearAll button', () => {
    renderPage()

    expect(screen.queryByText('清空全部')).not.toBeInTheDocument()
    expect(screen.queryByText('清空待处理')).not.toBeInTheDocument()
  })

  it('shows preselected badge and re-identify button after selecting a candidate', async () => {
    const user = userEvent.setup()

    // Mock fetch: parse → returns item, identify → returns candidates
    globalThis.fetch = vi.fn(async (url, _init) => {
      const urlStr = String(url)
      if (urlStr.includes('/manual-upload/parse')) {
        return new Response(JSON.stringify({
          status: 'success',
          data: {
            items: [{
              key: 'magnet-0', kind: 'magnet', display_name: 'Test Movie',
              size_label: '未知', size_bytes: null, valid: true, source_index: 0,
            }],
            errors: [],
          },
        }))
      }
      if (urlStr.includes('/resource-discovery/identify')) {
        return new Response(JSON.stringify({
          status: 'success',
          data: {
            keyword_used: 'Test Movie',
            candidates: [{
              provider: 'tmdb', provider_id: 'movie-123', title: 'Test Movie',
              original_title: 'Test Movie', year: 2024, media_type: 'movie',
              overview: 'A test movie.', poster_url: null, confidence: 0.95,
              match_reason: 'title_exact',
            }],
          },
        }))
      }
      return new Response(JSON.stringify({ status: 'error', messages: [{ text: 'unknown' }] }))
    })

    renderPage()

    // Step 1: Type magnet link and click "添加到列表"
    const textarea = screen.getByRole('textbox')
    await user.type(textarea, 'magnet:?xt=urn:btih:abc123')
    await user.click(screen.getByRole('button', { name: '添加到列表' }))

    // Wait for item to appear in pending list
    await waitFor(() => {
      expect(screen.getByText('Test Movie')).toBeInTheDocument()
    })

    // Step 2: Click "识别影片" to open identify panel
    await user.click(screen.getByRole('button', { name: '识别影片' }))
    await waitFor(() => {
      expect(screen.getByRole('button', { name: '搜索' })).toBeInTheDocument()
    })

    // Step 3: Click "搜索" to search candidates
    await user.click(screen.getByRole('button', { name: '搜索' }))
    await waitFor(() => {
      expect(screen.getByRole('button', { name: '选择候选' })).toBeInTheDocument()
    })

    // Step 4: Click "选择候选" on the candidate card
    await user.click(screen.getByRole('button', { name: '选择候选' }))

    // Step 5: Assert preselected badge appears
    await waitFor(() => {
      expect(screen.getByText(/已关联：Test Movie/)).toBeInTheDocument()
    })

    // Step 6: Assert button changed to "重新识别"
    expect(screen.getByRole('button', { name: '重新识别' })).toBeInTheDocument()

    // Step 7: Identify panel should be closed
    expect(screen.queryByRole('button', { name: '搜索' })).not.toBeInTheDocument()
  })
})

// ── reducer unit tests ──

function makeItem(overrides: Record<string, unknown> = {}) {
  return {
    key: 'magnet-0',
    kind: 'magnet' as const,
    displayName: 'Test',
    sizeLabel: '未知',
    sizeBytes: null,
    rawData: '',
    submitted: false,
    submitOk: false,
    submitMessage: '',
    preselectedProfile: null,
    preselectedProvider: null,
    preselectedExternalId: null,
    preselectedTitle: null,
    ...overrides,
  }
}

describe('reducer ADD_ITEMS', () => {
  it('accumulates items with different keys across batches', () => {
    const state1 = reducer({ items: [] }, { type: 'ADD_ITEMS', items: [makeItem({ key: 'magnet-1-0' })] })
    expect(state1.items).toHaveLength(1)

    const state2 = reducer(state1, { type: 'ADD_ITEMS', items: [makeItem({ key: 'magnet-2-0' })] })
    expect(state2.items).toHaveLength(2)
    expect(state2.items[0].key).toBe('magnet-1-0')
    expect(state2.items[1].key).toBe('magnet-2-0')
  })

  it('deduplicates items with the same key within a batch', () => {
    const state = reducer(
      { items: [makeItem({ key: 'existing' })] },
      { type: 'ADD_ITEMS', items: [makeItem({ key: 'existing' })] },
    )
    expect(state.items).toHaveLength(1)
  })
})

describe('reducer SET_PRESELECTION', () => {
  it('sets preselected fields on the target item', () => {
    const state = reducer(
      { items: [makeItem({ key: 'a' }), makeItem({ key: 'b' })] },
      {
        type: 'SET_PRESELECTION',
        key: 'a',
        profile: 'tmdb_movie',
        provider: 'tmdb',
        externalId: 'movie-123',
        title: 'Test Movie',
      },
    )
    expect(state.items).toHaveLength(2)
    const a = state.items.find((it) => it.key === 'a')!
    expect(a.preselectedProfile).toBe('tmdb_movie')
    expect(a.preselectedProvider).toBe('tmdb')
    expect(a.preselectedExternalId).toBe('movie-123')
    expect(a.preselectedTitle).toBe('Test Movie')
    // 另一条不受影响
    const b = state.items.find((it) => it.key === 'b')!
    expect(b.preselectedProfile).toBeNull()
    expect(b.preselectedTitle).toBeNull()
  })
})

describe('apiSubmitUpload preselected fields', () => {
  it('includes preselected_profile/provider/external_id in submit body', async () => {
    const capturedBody: Array<string> = []
    const originalFetch = globalThis.fetch
    globalThis.fetch = vi.fn(async (_url, init) => {
      capturedBody.push((init as RequestInit).body as string)
      return new Response(JSON.stringify({
        status: 'success',
        data: { results: [{ key: 'a', success: true, message: 'ok' }] },
      }))
    })

    try {
      const { apiSubmitUpload } = await import('./manual-upload-page')
      await apiSubmitUpload([{
        ...makeItem({ key: 'a' }),
        preselectedProfile: 'tmdb_movie',
        preselectedProvider: 'tmdb',
        preselectedExternalId: 'movie-123',
      }])

      expect(capturedBody).toHaveLength(1)
      const parsed = JSON.parse(capturedBody[0])
      const submitted = parsed.items[0]
      expect(submitted.preselected_profile).toBe('tmdb_movie')
      expect(submitted.preselected_provider).toBe('tmdb')
      expect(submitted.preselected_external_id).toBe('movie-123')
    } finally {
      globalThis.fetch = originalFetch
    }
  })

  it('sends null preselected fields when not linked', async () => {
    const capturedBody: Array<string> = []
    const originalFetch = globalThis.fetch
    globalThis.fetch = vi.fn(async (_url, init) => {
      capturedBody.push((init as RequestInit).body as string)
      return new Response(JSON.stringify({
        status: 'success',
        data: { results: [{ key: 'a', success: true, message: 'ok' }] },
      }))
    })

    try {
      const { apiSubmitUpload } = await import('./manual-upload-page')
      await apiSubmitUpload([makeItem({ key: 'a' })])

      expect(capturedBody).toHaveLength(1)
      const parsed = JSON.parse(capturedBody[0])
      const submitted = parsed.items[0]
      expect(submitted.preselected_profile).toBeNull()
      expect(submitted.preselected_provider).toBeNull()
      expect(submitted.preselected_external_id).toBeNull()
    } finally {
      globalThis.fetch = originalFetch
    }
  })
})
