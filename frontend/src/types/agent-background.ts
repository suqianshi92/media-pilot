/**
 * 后台 Agent 状态类型 — 对应 GET /api/v1/agent-background/status 响应
 *
 * 这些字段由后端 BackgroundStatusSnapshot 序列化得到；为防止内部
 * 字段泄漏, 序列化端只暴露下列键.
 */

export type BackgroundAgentState =
  | 'disabled'
  | 'idle'
  | 'syncing_downloads'
  | 'scanning_watch'
  | 'processing_task'
  | 'needs_attention'
  | 'recently_failed'

export type HistoryLevel = 'info' | 'success' | 'warning' | 'error'

export interface AgentBackgroundHistoryEntry {
  timestamp: string
  phase: string
  level: HistoryLevel
  summary: string
  task_id: string | null
  download_id: string | null
}

export interface AgentBackgroundStatusData {
  enabled: boolean
  state: BackgroundAgentState
  summary: string
  disabled_reasons: string[]
  waiting_user_count: number
  agent_failed_count: number
  last_run: string | null
  history: AgentBackgroundHistoryEntry[]
  current_task_id: string | null
  current_download_id: string | null
}
