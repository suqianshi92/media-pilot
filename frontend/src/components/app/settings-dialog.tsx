import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { InlineMessage } from '@/components/app/shared-ui'
import { createSettingsService } from '@/services/settings-service'
import type {
  AppSettings,
  AppSettingsResponse,
  AppSettingsUpdateRequest,
  ProbeResult,
  ProbeStatus,
} from '@/types/settings'

const settingsService = createSettingsService()

const STATUS_LABELS: Record<string, string> = {
  configured: '已配置',
  not_configured: '未配置',
  unsupported: '暂不支持',
}

function formatThreshold(bytes: number) {
  return `${(bytes / (1024 * 1024)).toFixed(0)} MB`
}

const RATE_LIMIT_KIB_MAX = 1024 * 1024
const BYTES_PER_KIB = 1024

function bytesToKib(bytes: number) {
  return Math.floor(bytes / BYTES_PER_KIB)
}

function kibToBytes(kib: number) {
  const normalized = Number.isFinite(kib) ? Math.trunc(kib) : 0
  return Math.max(0, Math.min(RATE_LIMIT_KIB_MAX, normalized)) * BYTES_PER_KIB
}

export function SettingsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [settings, setSettings] = useState<AppSettingsResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const [probes, setProbes] = useState<ProbeResult[]>([])
  const [probing, setProbing] = useState(false)

  useEffect(() => {
    if (!open) return
    setLoading(true)
    setError(null)
    setSaved(false)
    setProbes([])
    settingsService
      .getSettings()
      .then((res) => setSettings(res.data))
      .catch((err) => setError(err instanceof Error ? err.message : '加载配置失败'))
      .finally(() => setLoading(false))
    // 打开弹窗时触发探测
    fetchProbes()
  }, [open])

  const fetchProbes = () => {
    setProbing(true)
    settingsService
      .getConnectivity()
      .then((res) => setProbes(res.data.probes))
      .catch(() => {})
      .finally(() => setProbing(false))
  }

  if (!open) return null

  const handleSave = async () => {
    if (!settings) return
    setSaving(true)
    setError(null)
    setSaved(false)

    const update: AppSettingsUpdateRequest = {
      enabled_metadata_profiles: settings.app_settings.enabled_metadata_profiles,
      enabled_library_formats: settings.app_settings.enabled_library_formats,
      suspicious_file_threshold_bytes: settings.app_settings.suspicious_file_threshold_bytes,
      metadata_auto_confirm_confidence: settings.app_settings.metadata_auto_confirm_confidence,
      metadata_auto_confirm_margin: settings.app_settings.metadata_auto_confirm_margin,
      preferred_metadata_language: settings.app_settings.preferred_metadata_language,
      source_cleanup_policy: settings.app_settings.source_cleanup_policy,
      download_rate_limit_bytes_per_second: settings.app_settings.download_rate_limit_bytes_per_second,
      upload_rate_limit_bytes_per_second: settings.app_settings.upload_rate_limit_bytes_per_second,
    }

    try {
      await settingsService.updateSettings(update)
      setSaved(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存配置失败')
    } finally {
      setSaving(false)
    }
  }

  const updateField = <K extends keyof AppSettings>(key: K, value: AppSettings[K]) => {
    if (!settings) return
    setSettings({
      ...settings,
      app_settings: { ...settings.app_settings, [key]: value },
    })
  }

  const toggleProfile = (value: string) => {
    if (!settings) return
    const current = settings.app_settings.enabled_metadata_profiles
    const next = current.includes(value)
      ? current.filter((v) => v !== value)
      : [...current, value]
    updateField('enabled_metadata_profiles', next)
  }

  const toggleFormat = (value: string) => {
    if (!settings) return
    const current = settings.app_settings.enabled_library_formats
    const next = current.includes(value)
      ? current.filter((v) => v !== value)
      : [...current, value]
    updateField('enabled_library_formats', next)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto px-4 py-6">
      {/* 遮罩 */}
      <div className="fixed inset-0 bg-black/50" onClick={onClose} />

      {/* 弹窗 */}
      <div className="relative mx-4 w-full max-w-lg max-h-[calc(100vh-48px)] overflow-y-auto rounded-xl border border-border bg-surface p-6 shadow-2xl">
        <div className="mb-6 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-surface-foreground">应用配置</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-surface-foreground"
          >
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {loading ? (
          <div className="py-8 text-center text-sm text-muted-foreground">加载中...</div>
        ) : error && !settings ? (
          <InlineMessage variant="error" title="配置加载失败" description={error} />
        ) : settings ? (
          <div className="grid gap-6">
            {/* 环境配置状态 — 只读 */}
            <section className="grid gap-3">
              <h3 className="text-sm font-medium text-surface-foreground">环境配置状态</h3>
              <div className="grid gap-2 rounded-md border border-border bg-background p-3">
                <StatusRow label="TMDB API Key" status={settings.env_status.tmdb_api_key} />
                <StatusRow label="LLM API Key" status={settings.env_status.llm_api_key} />
                <StatusRow label="LLM Base URL" status={settings.env_status.llm_base_url} />
                <StatusRow label="LLM Model" status={settings.env_status.llm_model} />
                <StatusRow label="TPDB API Key" status={settings.env_status.tpdb_api_key} />
                <StatusRow label="回收区路径" status={settings.env_status.trash_dir} />
              </div>
            </section>

            {/* 连通性探测 */}
            <section className="grid gap-3">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-medium text-surface-foreground">连通性探测</h3>
                <button
                  type="button"
                  disabled={probing}
                  onClick={fetchProbes}
                  className="rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-surface-foreground disabled:opacity-50"
                >
                  {probing ? '检测中...' : '重新检测'}
                </button>
              </div>
              <div className="grid gap-2 rounded-md border border-border bg-background p-3">
                {probes.length === 0 && !probing ? (
                  <span className="text-sm text-muted-foreground">点击重新检测按钮获取连通性状态。</span>
                ) : probing && probes.length === 0 ? (
                  <span className="text-sm text-muted-foreground">检测中...</span>
                ) : (
                  probes.map((probe) => (
                    <ProbeRow key={probe.provider} probe={probe} />
                  ))
                )}
              </div>
            </section>

            {/* 元数据配置档案 — 有序多选 (任务 2.6) */}
            <section className="grid gap-3">
              <h3 className="text-sm font-medium text-surface-foreground">元数据配置档案</h3>
              <p className="text-xs text-muted-foreground">按顺序尝试检索，启用的档案按从上到下顺序执行。</p>
              <div className="grid gap-2 rounded-md border border-border bg-background p-3">
                {settings.available_profiles.map((profile) => (
                  <label
                    key={profile.value}
                    className={`flex items-center gap-3 rounded-md border p-3 ${
                      profile.supported
                        ? 'cursor-pointer border-border/70 hover:bg-muted/50'
                        : 'cursor-not-allowed border-border/40 opacity-70'
                    }`}
                  >
                    <input
                      type="checkbox"
                      checked={settings.app_settings.enabled_metadata_profiles.includes(profile.value)}
                      disabled={!profile.supported}
                      onChange={() => toggleProfile(profile.value)}
                      className="h-4 w-4 accent-primary"
                    />
                    <div className="grid gap-0.5">
                      <span className="text-sm text-surface-foreground">{profile.label}</span>
                      <span className="text-xs text-muted-foreground">
                        {profile.supported ? (profile.enabled ? '已启用' : '未启用') : '暂不支持（需安装对应 provider）'}
                      </span>
                    </div>
                  </label>
                ))}
              </div>
            </section>

            {/* 媒体库格式 — 多选 (任务 2.7) */}
            <section className="grid gap-3">
              <h3 className="text-sm font-medium text-surface-foreground">媒体库格式</h3>
              <div className="grid gap-2 rounded-md border border-border bg-background p-3">
                {settings.available_library_formats.map((fmt) => (
                  <label
                    key={fmt.value}
                    className={`flex items-center gap-3 rounded-md border p-3 ${
                      fmt.supported
                        ? 'cursor-pointer border-border/70 hover:bg-muted/50'
                        : 'cursor-not-allowed border-border/40 opacity-70'
                    }`}
                  >
                    <input
                      type="checkbox"
                      checked={settings.app_settings.enabled_library_formats.includes(fmt.value)}
                      disabled={!fmt.supported}
                      onChange={() => toggleFormat(fmt.value)}
                      className="h-4 w-4 accent-primary"
                    />
                    <div className="grid gap-0.5">
                      <span className="text-sm text-surface-foreground">{fmt.label}</span>
                      <span className="text-xs text-muted-foreground">
                        {fmt.supported ? (fmt.enabled ? '已启用' : '未启用') : '暂不支持'}
                      </span>
                    </div>
                  </label>
                ))}
              </div>
            </section>

            {/* 下载器全局限速 */}
            <section className="grid gap-3">
              <h3 className="text-sm font-medium text-surface-foreground">下载器全局限速</h3>
              <p className="text-xs text-muted-foreground">
                作用于整个 qBittorrent 实例，不按单个下载任务区分。0 表示不限速。
              </p>
              <div className="grid gap-3 rounded-md border border-border bg-background p-3">
                <div className="grid gap-1">
                  <label className="text-xs text-muted-foreground">全局下载限速</label>
                  <div className="flex items-center gap-2">
                    <input
                      type="number"
                      min={0}
                      max={RATE_LIMIT_KIB_MAX}
                      step={1}
                      value={bytesToKib(settings.app_settings.download_rate_limit_bytes_per_second)}
                      onChange={(e) =>
                        updateField('download_rate_limit_bytes_per_second', kibToBytes(Number(e.target.value)))
                      }
                      className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-surface-foreground focus:outline-none focus:ring-2 focus:ring-primary"
                    />
                    <span className="text-xs text-muted-foreground">KiB/s</span>
                  </div>
                </div>
                <div className="grid gap-1">
                  <label className="text-xs text-muted-foreground">全局上传限速</label>
                  <div className="flex items-center gap-2">
                    <input
                      type="number"
                      min={0}
                      max={RATE_LIMIT_KIB_MAX}
                      step={1}
                      value={bytesToKib(settings.app_settings.upload_rate_limit_bytes_per_second)}
                      onChange={(e) =>
                        updateField('upload_rate_limit_bytes_per_second', kibToBytes(Number(e.target.value)))
                      }
                      className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-surface-foreground focus:outline-none focus:ring-2 focus:ring-primary"
                    />
                    <span className="text-xs text-muted-foreground">KiB/s</span>
                  </div>
                </div>
              </div>
            </section>

            {/* 可疑文件阈值 (任务 2.5) */}
            <section className="grid gap-3">
              <h3 className="text-sm font-medium text-surface-foreground">可疑额外文件阈值</h3>
              <p className="text-xs text-muted-foreground">
                目录下载时，除主影片外存在大于此阈值的视频文件将阻塞自动清理，转为人工确认。
              </p>
              <div className="rounded-md border border-border bg-background p-3">
                <div className="flex items-center gap-3">
                  <input
                    type="range"
                    min={50 * 1024 * 1024}
                    max={2000 * 1024 * 1024}
                    step={50 * 1024 * 1024}
                    value={settings.app_settings.suspicious_file_threshold_bytes}
                    onChange={(e) =>
                      updateField('suspicious_file_threshold_bytes', Number(e.target.value))
                    }
                    className="flex-1 accent-primary"
                  />
                  <span className="min-w-[70px] text-right text-sm font-medium text-surface-foreground">
                    {formatThreshold(settings.app_settings.suspicious_file_threshold_bytes)}
                  </span>
                </div>
              </div>
            </section>

            {/* 源文件清理策略 */}
            <section className="grid gap-3">
              <h3 className="text-sm font-medium text-surface-foreground">源文件清理策略</h3>
              <p className="text-xs text-muted-foreground">
                Agent 在入库任务完成后处理源文件的方式。删除会触发额外的二次确认。
              </p>
              <div className="grid gap-2 rounded-md border border-border bg-background p-3">
                {(['keep', 'ask', 'trash'] as const).map((policy) => (
                  <label
                    key={policy}
                    className="flex cursor-pointer items-center gap-3 rounded-md border border-border/70 p-3 hover:bg-muted/50"
                  >
                    <input
                      type="radio"
                      name="source_cleanup_policy"
                      value={policy}
                      checked={settings.app_settings.source_cleanup_policy === policy}
                      onChange={() => updateField('source_cleanup_policy', policy)}
                      className="h-4 w-4 accent-primary"
                    />
                    <div className="grid gap-0.5">
                      <span className="text-sm text-surface-foreground">
                        {policy === 'keep'
                          ? '保留（不动源文件）'
                          : policy === 'ask'
                            ? '询问（每次任务都让用户选择）'
                            : '自动移到回收区'}
                      </span>
                      <span className="text-xs text-muted-foreground">
                        {policy === 'keep'
                          ? '默认安全策略，源文件保持原样。'
                          : policy === 'ask'
                            ? '每次任务完成后让用户选择 keep/trash/delete。'
                            : '回收区未配置时仍允许保存，运行时降级为询问。'}
                      </span>
                    </div>
                  </label>
                ))}
              </div>
              {settings.app_settings.source_cleanup_policy === 'trash' &&
              settings.env_status.trash_dir === 'not_configured' ? (
                <InlineMessage
                  variant="warning"
                  title="回收区未配置"
                  description="回收区未配置时此策略仍可保存，但运行时会降级为向用户询问。"
                />
              ) : null}
            </section>

            {/* 元数据语言偏好 — Phase 5.2 */}
            <section className="grid gap-3">
              <h3 className="text-sm font-medium text-surface-foreground">元数据搜索语言偏好</h3>
              <p className="text-xs text-muted-foreground">
                影响资源发现阶段的片名候选排序与轻量清洗的目标语言。不影响下载后的正式刮削语言。
              </p>
              <div className="grid gap-2 rounded-md border border-border bg-background p-3">
                {(['zh', 'en'] as const).map((lang) => (
                  <label
                    key={lang}
                    className="flex cursor-pointer items-center gap-3 rounded-md border border-border/70 p-3 hover:bg-muted/50"
                  >
                    <input
                      type="radio"
                      name="preferred_metadata_language"
                      value={lang}
                      checked={settings.app_settings.preferred_metadata_language === lang}
                      onChange={() => updateField('preferred_metadata_language', lang)}
                      className="h-4 w-4 accent-primary"
                    />
                    <span className="text-sm text-surface-foreground">{lang === 'zh' ? '中文' : 'English'}</span>
                  </label>
                ))}
              </div>
            </section>

            {/* 自动确认置信度 */}
            <section className="grid gap-3">
              <h3 className="text-sm font-medium text-surface-foreground">自动确认置信度</h3>
              <p className="text-xs text-muted-foreground">候选匹配置信度达到此阈值时自动确认，无需人工介入。</p>
              <div className="grid grid-cols-2 gap-3 rounded-md border border-border bg-background p-3">
                <div className="grid gap-1">
                  <label className="text-xs text-muted-foreground">最低置信度</label>
                  <input
                    type="number"
                    min={0}
                    max={1}
                    step={0.01}
                    value={settings.app_settings.metadata_auto_confirm_confidence}
                    onChange={(e) =>
                      updateField('metadata_auto_confirm_confidence', Number(e.target.value))
                    }
                    className="rounded-md border border-border bg-background px-3 py-2 text-sm text-surface-foreground focus:outline-none focus:ring-2 focus:ring-primary"
                  />
                </div>
                <div className="grid gap-1">
                  <label className="text-xs text-muted-foreground">最小差距</label>
                  <input
                    type="number"
                    min={0}
                    max={1}
                    step={0.01}
                    value={settings.app_settings.metadata_auto_confirm_margin}
                    onChange={(e) =>
                      updateField('metadata_auto_confirm_margin', Number(e.target.value))
                    }
                    className="rounded-md border border-border bg-background px-3 py-2 text-sm text-surface-foreground focus:outline-none focus:ring-2 focus:ring-primary"
                  />
                </div>
              </div>
            </section>

            {/* 错误/成功消息 */}
            {error ? (
              <InlineMessage variant="error" title="保存失败" description={error} />
            ) : null}
            {saved ? (
              <InlineMessage variant="success" title="配置已保存" />
            ) : null}

            {/* 操作按钮 */}
            <div className="flex items-center justify-end gap-3 border-t border-border pt-4">
              <Button variant="secondary" onClick={onClose}>
                取消
              </Button>
              <Button onClick={handleSave} disabled={saving}>
                {saving ? '保存中...' : '保存配置'}
              </Button>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  )
}

function StatusRow({ label, status }: { label: string; status: string }) {
  const color =
    status === 'configured'
      ? 'text-success'
      : status === 'unsupported'
        ? 'text-muted-foreground'
        : 'text-warning'

  return (
    <div className="flex items-center justify-between text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className={color}>{STATUS_LABELS[status] ?? status}</span>
    </div>
  )
}

const PROBE_PROVIDER_LABELS: Record<string, string> = {
  tmdb: 'TMDB',
  tpdb: 'TPDB',
  llm: 'LLM',
}

const PROBE_STATUS_LABELS: Record<ProbeStatus, string> = {
  ok: '正常',
  not_configured: '未配置',
  failed: '连接失败',
  unsupported: '暂不支持',
  probing: '检测中',
}

function ProbeRow({ probe }: { probe: ProbeResult }) {
  const statusColor =
    probe.status === 'ok'
      ? 'text-success'
      : probe.status === 'failed'
        ? 'text-destructive'
        : 'text-muted-foreground'

  return (
    <div className="flex items-center justify-between text-sm">
      <span className="text-muted-foreground">{PROBE_PROVIDER_LABELS[probe.provider] ?? probe.provider}</span>
      <div className="flex items-center gap-2">
        {probe.latency_ms != null ? (
          <span className="text-xs text-muted-foreground">{probe.latency_ms}ms</span>
        ) : null}
        <span className={statusColor}>{PROBE_STATUS_LABELS[probe.status]}</span>
      </div>
    </div>
  )
}
