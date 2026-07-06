/**
 * 资源发现前端类型 — 搜索候选、下载提交、LLM 意图
 */

export interface ReleaseTags {
  resolutions: string[]
  sources: string[]
  codecs: string[]
  hdr_tags: string[]
  audio_tags: string[]
}

export interface ResourceCandidate {
  candidate_token: string
  title: string
  indexer: string
  source: string
  size_bytes: number | null
  seeders: number
  leechers: number
  publish_date: string | null
  download_count: number
  category: string
  match_reason: string
  downloadable: boolean
  relevance_score: number
  relevance_level: 'high' | 'medium' | 'low'
  match_reasons: string[]
  release_tags: ReleaseTags | null
  display_tags: string[]
}

export interface ResourceIntent {
  query_text: string
  search_type: string
  title_candidates: string[]
  resource_keywords: string[]
  profile_hint: string
  preferred_title_candidates: string[]
  adult_identifier_candidates: string[]
  resource_search_keywords: string[]
  reason: string
  preferred_resolutions: string[]
  preferred_sources: string[]
  preferred_video_codecs: string[]
  preferred_hdr_tags: string[]
  preferred_audio_tags: string[]
}

export interface ResourceSearchData {
  candidates: ResourceCandidate[]
  query_used: string
  search_type: string
  source: string
  message: string
  intent: ResourceIntent
}

export interface ContentDiscoveryMessage {
  role: 'user' | 'assistant'
  content: string
}
