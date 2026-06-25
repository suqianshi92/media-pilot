import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const mockData = vi.hoisted(() => ({
  status: 'success',
  data: {
    app_settings: {
      enabled_metadata_profiles: ['tmdb_movie'],
      enabled_library_formats: ['jellyfin'],
      suspicious_file_threshold_bytes: 314572800,
      metadata_auto_confirm_confidence: 0.9,
      metadata_auto_confirm_margin: 0.08,
      preferred_metadata_language: 'zh',
      source_cleanup_policy: 'keep' as const,
      download_rate_limit_bytes_per_second: 0,
      upload_rate_limit_bytes_per_second: 0,
    },
    env_status: {
      tmdb_api_key: 'configured' as const,
      llm_api_key: 'not_configured' as const,
      llm_base_url: 'not_configured' as const,
      llm_model: 'not_configured' as const,
      tpdb_api_key: 'not_configured' as const,
      trash_dir: 'not_configured' as const,
    },
    available_profiles: [
      { value: 'tmdb_movie', label: 'TMDB 电影', supported: true, enabled: true },
      { value: 'tmdb_show', label: 'TMDB 剧集', supported: true, enabled: true },
      { value: 'tpdb_adult_movie', label: 'TPDB 成人影片', supported: false, enabled: false },
    ],
    available_library_formats: [
      { value: 'jellyfin', label: 'Jellyfin', supported: true, enabled: true },
    ],
  },
  messages: [] as Array<{ level: string; code: string; text: string }>,
  meta: {},
}))

const mockProbes = vi.hoisted(() => ({
  probes: [
    { provider: 'tmdb', status: 'ok' as const, message: 'TMDB API 连接正常', checked_at: '2026-01-01T00:00:00Z', latency_ms: 45 },
    { provider: 'tpdb', status: 'not_configured' as const, message: '未配置 TPDB API Key', checked_at: '2026-01-01T00:00:00Z', latency_ms: null },
    { provider: 'llm', status: 'not_configured' as const, message: '未配置 LLM API Key', checked_at: '2026-01-01T00:00:00Z', latency_ms: null },
  ],
}))

vi.mock('@/services/settings-service', () => ({
  createSettingsService: () => ({
    getSettings: vi.fn().mockResolvedValue(mockData),
    updateSettings: vi.fn().mockResolvedValue({
      status: 'success',
      data: mockData.data.app_settings,
      messages: [{ level: 'info', code: 'settings_updated', text: '配置已保存' }],
      meta: {},
    }),
    getConnectivity: vi.fn().mockResolvedValue({
      status: 'success',
      data: mockProbes,
      messages: [],
      meta: {},
    }),
  }),
}))

import { SettingsDialog } from './settings-dialog'

describe('SettingsDialog', () => {
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('renders nothing when closed', () => {
    const { container } = render(<SettingsDialog open={false} onClose={() => {}} />)
    expect(container.textContent).toBe('')
  })

  it('renders the dialog when open and shows heading', async () => {
    render(<SettingsDialog open={true} onClose={() => {}} />)

    await waitFor(() => {
      const headings = screen.getAllByRole('heading', { level: 2 })
      expect(headings[0]).toHaveTextContent('应用配置')
    })

    expect(screen.getByText('环境配置状态')).toBeInTheDocument()
  })

  it('shows TPDB profile as disabled and TMDB as checked', async () => {
    render(<SettingsDialog open={true} onClose={() => {}} />)

    await waitFor(() => {
      const checkboxes = screen.getAllByRole('checkbox')
      expect(checkboxes.length).toBeGreaterThanOrEqual(2)
    })

    const checkboxes = screen.getAllByRole('checkbox') as HTMLInputElement[]
    const disabled = checkboxes.filter((cb) => cb.disabled)
    const checked = checkboxes.filter((cb) => cb.checked)
    expect(disabled.length).toBeGreaterThanOrEqual(1)
    expect(checked.length).toBeGreaterThanOrEqual(1)
  })

  it('shows threshold with 300 MB default', async () => {
    render(<SettingsDialog open={true} onClose={() => {}} />)

    await waitFor(() => {
      const texts = screen.getAllByText('300 MB')
      expect(texts.length).toBeGreaterThanOrEqual(1)
    })
  })

  it('calls onClose when clicking cancel button', async () => {
    let closed = false
    render(<SettingsDialog open={true} onClose={() => { closed = true }} />)

    await waitFor(() => {
      expect(screen.getByRole('heading', { level: 2 })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(closed).toBe(true)
  })

  it('renders three source cleanup policy radios with keep default checked', async () => {
    render(<SettingsDialog open={true} onClose={() => {}} />)

    await waitFor(() => {
      expect(screen.getByText('源文件清理策略')).toBeInTheDocument()
    })

    const radios = screen.getAllByRole('radio', { name: /保留（不动源文件）|询问（每次任务都让用户选择）|自动移到回收区/ })
    expect(radios).toHaveLength(3)
    const checked = radios.find((r) => (r as HTMLInputElement).checked) as HTMLInputElement | undefined
    expect(checked?.value).toBe('keep')
  })

  it('shows downgrade hint when trash policy picked without trash_dir', async () => {
    render(<SettingsDialog open={true} onClose={() => {}} />)

    await waitFor(() => {
      expect(screen.getByText('源文件清理策略')).toBeInTheDocument()
    })

    const trashRadio = screen.getByRole('radio', { name: /自动移到回收区/ })
    fireEvent.click(trashRadio)

    await waitFor(() => {
      expect(screen.getByText('回收区未配置')).toBeInTheDocument()
    })
  })
})
