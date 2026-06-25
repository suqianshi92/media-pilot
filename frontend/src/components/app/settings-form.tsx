import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import i18n from '@/i18n'
import { Button } from '@/components/ui/button'
import { InlineMessage } from '@/components/app/shared-ui'
import { getProfileLabel } from '@/components/app/task-labels'
import { createSettingsService } from '@/services/settings-service'
import type {
  AppSettings,
  AppSettingsResponse,
  AppSettingsUpdateRequest,
  ProbeResult,
} from '@/types/settings'

const settingsService = createSettingsService()

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

export function SettingsForm({ onCancel }: { onCancel?: () => void }) {
  const { t } = useTranslation()
  const [settings, setSettings] = useState<AppSettingsResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [warning, setWarning] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const [probes, setProbes] = useState<ProbeResult[]>([])
  const [probing, setProbing] = useState(false)

  useEffect(() => {
    setLoading(true)
    setError(null)
    setSaved(false)
    setProbes([])
    settingsService
      .getSettings()
      .then((res) => setSettings(res.data))
      .catch((err) => setError(err instanceof Error ? err.message : t('settings.loadError', '加载配置失败')))
      .finally(() => setLoading(false))
    fetchProbes()
  }, [])

  const fetchProbes = () => {
    setProbing(true)
    settingsService
      .getConnectivity()
      .then((res) => setProbes(res.data.probes))
      .catch(() => {})
      .finally(() => setProbing(false))
  }

  const handleSave = async () => {
    if (!settings) return
    setSaving(true)
    setError(null)
    setWarning(null)
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
      const response = await settingsService.updateSettings(update)
      setSettings({ ...settings, app_settings: response.data })
      const syncWarning = response.messages.find((msg) => msg.level === 'warning')
      setWarning(syncWarning?.text ?? null)
      setSaved(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : t('settings.saveError', '保存配置失败'))
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

  if (loading) {
    return <div className="py-8 text-center text-sm text-muted-foreground">{t('settings.loading')}</div>
  }

  if (error && !settings) {
    return (
      <InlineMessage variant="error" title={t('settings.loadError')} description={error} />
    )
  }

  if (!settings) return null

  return (
    <div className="grid gap-6 max-w-6xl">
      {/* 环境配置状态 */}
      <section className="grid gap-3 rounded-lg border border-border bg-surface p-4">
        <h3 className="text-sm font-medium text-surface-foreground">{t('settings.environmentStatus')}</h3>
        <div className="grid gap-2 rounded-md border border-border bg-background p-3">
          <StatusRow label="TMDB API Key" status={settings.env_status.tmdb_api_key} />
          <StatusRow label="LLM API Key" status={settings.env_status.llm_api_key} />
          <StatusRow label="LLM Base URL" status={settings.env_status.llm_base_url} />
          <StatusRow label="LLM Model" status={settings.env_status.llm_model} />
          <StatusRow label="TPDB API Key" status={settings.env_status.tpdb_api_key} />
          <StatusRow label={t('settings.trashDir')} status={settings.env_status.trash_dir} />
        </div>
      </section>

      {/* 连通性探测 */}
      <section className="grid gap-3 rounded-lg border border-border bg-surface p-4">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-medium text-surface-foreground">{t('settings.connectivityProbe')}</h3>
          <button
            type="button"
            disabled={probing}
            onClick={fetchProbes}
            className="rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-surface-foreground disabled:opacity-50"
          >
            {probing ? t('common.loading') : t('settings.probeButton')}
          </button>
        </div>
        <div className="grid gap-2 rounded-md border border-border bg-background p-3">
          {probes.length === 0 && !probing ? (
            <span className="text-sm text-muted-foreground">{t('settings.probeEmptyHint')}</span>
          ) : probing && probes.length === 0 ? (
            <span className="text-sm text-muted-foreground">{t('common.loading')}</span>
          ) : (
            probes.map((probe) => (
              <ProbeRow key={probe.provider} probe={probe} />
            ))
          )}
        </div>
      </section>

      {/* 元数据配置档案 */}
      <section className="grid gap-3 rounded-lg border border-border bg-surface p-4">
        <h3 className="text-sm font-medium text-surface-foreground">{t('settings.metadataProfiles')}</h3>
        <p className="text-xs text-muted-foreground">{t('settings.metadataProfilesDesc')}</p>
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
                <span className="text-sm text-surface-foreground">{getProfileLabel(profile.value)}</span>
                <span className="text-xs text-muted-foreground">
                  {profile.supported ? (profile.enabled ? t('settings.enabled') : t('settings.disabled')) : t('settings.notSupported')}
                </span>
              </div>
            </label>
          ))}
        </div>
      </section>

      {/* 媒体库格式 */}
      <section className="grid gap-3 rounded-lg border border-border bg-surface p-4">
        <h3 className="text-sm font-medium text-surface-foreground">{t('settings.libraryFormats')}</h3>
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
                  {fmt.supported ? (fmt.enabled ? t('settings.enabled') : t('settings.disabled')) : t('settings.notSupported')}
                </span>
              </div>
            </label>
          ))}
        </div>
      </section>

      {/* 下载器全局限速 */}
      <section className="grid gap-3 rounded-lg border border-border bg-surface p-4">
        <h3 className="text-sm font-medium text-surface-foreground">{t('settings.downloaderRateLimits')}</h3>
        <p className="text-xs text-muted-foreground">{t('settings.downloaderRateLimitsDesc')}</p>
        <div className="grid gap-3 rounded-md border border-border bg-background p-3 md:grid-cols-2">
          <div className="grid gap-1">
            <label className="text-xs text-muted-foreground">{t('settings.globalDownloadLimit')}</label>
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
            <label className="text-xs text-muted-foreground">{t('settings.globalUploadLimit')}</label>
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
        <p className="text-xs text-muted-foreground">{t('settings.rateLimitZeroHint')}</p>
      </section>

      {/* 可疑文件阈值 */}
      <section className="grid gap-3 rounded-lg border border-border bg-surface p-4">
        <h3 className="text-sm font-medium text-surface-foreground">{t('settings.suspiciousThreshold')}</h3>
        <p className="text-xs text-muted-foreground">{t('settings.suspiciousThresholdDesc')}</p>
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
      <section className="grid gap-3 rounded-lg border border-border bg-surface p-4">
        <h3 className="text-sm font-medium text-surface-foreground">{t('settings.sourceCleanupPolicy')}</h3>
        <p className="text-xs text-muted-foreground">{t('settings.sourceCleanupPolicyDesc')}</p>
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
                    ? t('settings.sourceCleanupKeep')
                    : policy === 'ask'
                      ? t('settings.sourceCleanupAsk')
                      : t('settings.sourceCleanupTrash')}
                </span>
                <span className="text-xs text-muted-foreground">
                  {policy === 'keep'
                    ? t('settings.sourceCleanupKeepDesc')
                    : policy === 'ask'
                      ? t('settings.sourceCleanupAskDesc')
                      : t('settings.sourceCleanupTrashDesc')}
                </span>
              </div>
            </label>
          ))}
        </div>
        {settings.app_settings.source_cleanup_policy === 'trash' &&
        settings.env_status.trash_dir === 'not_configured' ? (
          <InlineMessage
            variant="warning"
            title={t('settings.sourceCleanupTrashDowngradeTitle')}
            description={t('settings.sourceCleanupTrashDowngradeHint')}
          />
        ) : null}
      </section>

      {/* 元数据语言偏好 */}
      <section className="grid gap-3 rounded-lg border border-border bg-surface p-4">
        <h3 className="text-sm font-medium text-surface-foreground">{t('settings.metadataLanguage')}</h3>
        <p className="text-xs text-muted-foreground">{t('settings.metadataLanguageDesc')}</p>
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
              <span className="text-sm text-surface-foreground">{lang === 'zh' ? t('settings.languageZh') : t('settings.languageEn')}</span>
            </label>
          ))}
        </div>
      </section>

      {/* 自动确认置信度 */}
      <section className="grid gap-3 rounded-lg border border-border bg-surface p-4">
        <h3 className="text-sm font-medium text-surface-foreground">{t('settings.autoConfirm')}</h3>
        <p className="text-xs text-muted-foreground">{t('settings.autoConfirmDesc')}</p>
        <div className="grid grid-cols-2 gap-3 rounded-md border border-border bg-background p-3">
          <div className="grid gap-1">
            <label className="text-xs text-muted-foreground">{t('settings.confidenceThreshold')}</label>
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
            <label className="text-xs text-muted-foreground">{t('settings.confidenceMargin')}</label>
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

      {/* 消息和按钮 */}
      {error ? (
        <InlineMessage variant="error" title={t('settings.saveError')} description={error} />
      ) : null}
      {warning ? (
        <InlineMessage variant="warning" title={t('settings.rateLimitSyncWarning')} description={warning} />
      ) : null}
      {saved ? (
        <InlineMessage variant="success" title={t('settings.saveSuccess')} />
      ) : null}

      <div className="flex items-center justify-end gap-3 border-t border-border pt-4">
        {onCancel ? (
          <Button variant="secondary" onClick={onCancel}>
            {t('common.cancel')}
          </Button>
        ) : null}
        <Button onClick={handleSave} disabled={saving}>
          {saving ? t('common.loading') : t('common.save')}
        </Button>
      </div>
    </div>
  )
}

const ENV_STATUS_I18N: Record<string, string> = {
  configured: 'settings.envConfigured',
  not_configured: 'settings.envNotConfigured',
  unsupported: 'settings.envUnsupported',
}

const PROBE_STATUS_I18N: Record<string, string> = {
  ok: 'settings.probeOk',
  not_configured: 'settings.probeNotConfigured',
  failed: 'settings.probeFailed',
  unsupported: 'settings.probeUnsupported',
  probing: 'settings.probeProbing',
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
      <span className={color}>{i18n.t(ENV_STATUS_I18N[status] ?? status)}</span>
    </div>
  )
}

const PROBE_PROVIDER_LABELS: Record<string, string> = {
  tmdb: 'TMDB',
  tpdb: 'TPDB',
  llm: 'LLM',
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
        <span className={statusColor}>{i18n.t(PROBE_STATUS_I18N[probe.status] ?? probe.status)}</span>
      </div>
    </div>
  )
}
