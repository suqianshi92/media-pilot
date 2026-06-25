export type SourceCleanupPolicy = 'keep' | 'ask' | 'trash'

export interface AppSettings {
  enabled_metadata_profiles: string[]
  enabled_library_formats: string[]
  suspicious_file_threshold_bytes: number
  metadata_auto_confirm_confidence: number
  metadata_auto_confirm_margin: number
  preferred_metadata_language: string
  source_cleanup_policy: SourceCleanupPolicy
  download_rate_limit_bytes_per_second: number
  upload_rate_limit_bytes_per_second: number
}

export interface EnvConfigStatus {
  tmdb_api_key: 'configured' | 'not_configured'
  llm_api_key: 'configured' | 'not_configured'
  llm_base_url: 'configured' | 'not_configured'
  llm_model: 'configured' | 'not_configured'
  tpdb_api_key: 'configured' | 'not_configured' | 'unsupported'
  trash_dir: 'configured' | 'not_configured'
}

export interface ProfileOption {
  value: string
  label: string
  supported: boolean
  enabled: boolean
}

export interface LibraryFormatOption {
  value: string
  label: string
  supported: boolean
  enabled: boolean
}

export interface AppSettingsResponse {
  app_settings: AppSettings
  env_status: EnvConfigStatus
  available_profiles: ProfileOption[]
  available_library_formats: LibraryFormatOption[]
}

export interface AppSettingsUpdateRequest {
  enabled_metadata_profiles?: string[]
  enabled_library_formats?: string[]
  suspicious_file_threshold_bytes?: number
  metadata_auto_confirm_confidence?: number
  metadata_auto_confirm_margin?: number
  preferred_metadata_language?: string
  source_cleanup_policy?: SourceCleanupPolicy
  download_rate_limit_bytes_per_second?: number
  upload_rate_limit_bytes_per_second?: number
}

export type ProbeStatus = 'ok' | 'not_configured' | 'failed' | 'unsupported' | 'probing'

export interface ProbeResult {
  provider: string
  status: ProbeStatus
  message: string
  checked_at: string | null
  latency_ms: number | null
}

export interface ConnectivityResponse {
  probes: ProbeResult[]
}
