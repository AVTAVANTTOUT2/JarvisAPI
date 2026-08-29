export type DisplayState =
  | 'idle'
  | 'listening'
  | 'understanding'
  | 'researching'
  | 'result'
  | 'speaking'
  | 'error'

export type SourceStatus =
  | 'discovered'
  | 'fetching'
  | 'verified'
  | 'used'
  | 'rejected'
  | 'unavailable'
  | 'conflicting'

export type VoiceSource = {
  id: string
  kind: string
  title: string
  provider?: string | null
  domain?: string | null
  url?: string | null
  locator?: string | null
  fetched_at?: string | null
  status: SourceStatus
  used: boolean
  excerpt?: string | null
  error?: string | null
}

export type VoiceClaim = {
  id: string
  text: string
  certainty: 'confirmed' | 'probable' | 'estimate' | 'unverified' | 'conflicting'
  source_ids: string[]
  status: string
  conflict: boolean
  notes?: string | null
}

export type VoiceSection = {
  id: string
  type: string
  title: string
  order: number
  data: Record<string, unknown>
  focusable_ids: string[]
  source_ids: string[]
}

export type SpeechSegment = {
  segment_id: string
  text: string
  visual_target_ids: string[]
  source_ids: string[]
  order: number
}

export type VisualAnswer = {
  title: string
  spoken_summary: string
  visual_summary: string
  sections: VoiceSection[]
  sources: VoiceSource[]
  claims: VoiceClaim[]
  suggested_voice_actions: Array<{ id: string; label: string; intent: string }>
  speech_segments: SpeechSegment[]
  status: 'building' | 'partial' | 'complete' | 'failed'
  created_at: string
  completed_at?: string | null
}

export type VoiceDisplaySession = {
  session_id: string
  turn_id?: string | null
  conversation_id?: number | null
  state: DisplayState
  started_at: string
  updated_at: string
  locale: string
  privacy_mode: boolean
  microphone_state: 'unknown' | 'listening' | 'muted' | 'unavailable'
  transcript_partial: string
  transcript_final: string
  understood_request: Record<string, unknown>
  current_focus?: Record<string, unknown> | null
  navigation_stack: Array<Record<string, unknown>>
  answer?: VisualAnswer | null
  activities: Array<Record<string, unknown>>
  active_speech_segment_id?: string | null
  last_sequence: number
}

export type VoiceDisplaySnapshot = {
  schema_version: 1
  enabled: boolean
  generated_at: string
  privacy_timeout_seconds: number
  session: VoiceDisplaySession
}

export type VoiceDisplayEvent = {
  schema_version: 1
  sequence: number
  event_id: string
  emitted_at: string
  session_id: string
  turn_id?: string | null
  type: string
  payload: Record<string, unknown>
  privacy: 'public' | 'private'
}
