export interface JevTraceMeta {
  status: 'called' | 'cached' | 'skipped'
  skip_reason?: string
  error_message?: string
  cached?: boolean
  force_refresh?: boolean
  model?: string
}

export interface JevTraceGuards {
  pick_choice?: string | null
  confidence?: number | null
  probabilities?: Record<string, number>
  owner_safe?: Record<string, boolean>
  fallback_reason?: string | null
  thresholds?: {
    auto_min_confidence?: number
    auto_min_margin?: number
    owner_unsafe_probability?: number
  }
}

export interface JevTrace {
  meta: JevTraceMeta
  input?: {
    model: string
    state: Record<string, unknown>
    questions: Record<string, Record<string, unknown>>
    top_n_account_ids: string[]
  }
  output?: {
    answers?: Record<string, unknown>
    model?: string
    provider?: string
    usage?: Record<string, unknown>
    id?: string
  }
  guards?: JevTraceGuards
}

export interface JevTraceAccountLookup {
  account_id: string
  account_identifier?: string
  primary_member_name?: string | null
}
