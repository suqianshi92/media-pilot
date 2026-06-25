export type ApiStatus = 'success' | 'accepted' | 'error'

export type MessageLevel = 'info' | 'success' | 'warning' | 'error'

export interface ApiMessage {
  level: MessageLevel
  code: string
  text: string
  details?: Record<string, unknown> | null
}

export interface PaginationMeta {
  page: number
  page_size: number
  total: number
  filters: Record<string, unknown>
}

export interface ValidationMeta {
  validation?: Record<string, string[]>
}

export interface ApiEnvelope<TData, TMeta extends object = Record<string, never>> {
  status: ApiStatus
  data: TData
  messages: ApiMessage[]
  meta: TMeta
}

export type ApiListEnvelope<TItem> = ApiEnvelope<
  { items: TItem[] },
  PaginationMeta & ValidationMeta
>
