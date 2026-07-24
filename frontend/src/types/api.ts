/** Contrats de données partagés par le client API et les vues frontend. */

/** Réponse publique de `/api/auth/status`, volontairement découplée du package React. */
export interface AuthStatus {
  configured: boolean
  authenticated: boolean
  csrf_token: string | null
  locked_out: boolean
  lockout_seconds: number
  lockout_scope: 'client' | 'global' | null
  local_recovery_available: boolean
  auto_lock_minutes: number
}

export interface AuthSession {
  id: number
  created_at: string
  expires_at: string
  last_seen_at: string
  user_agent: string
  ip: string
  current: boolean
}

export interface DailyActivity {
  date: string
  msg_count: number
  voice_count: number
  /** Un tour correspond à un message utilisateur. */
  turn_count: number
  tokens_in: number
  tokens_out: number
  cost: number
}

export interface WeeklyStats {
  days: DailyActivity[]
  change: {
    messages_pct: number | null
    voice_pct: number | null
    turns_pct: number | null
    /** Alias de compatibilité de turns_pct. */
    interactions_pct: number | null
    cost_pct: number | null
  }
  totals: {
    msg_count: number
    voice_count: number
    turn_count: number
    tokens_in: number
    tokens_out: number
    cost: number
  }
}

export interface ServiceInfo {
  id: string
  name: string
  description: string
  category: string
  running: boolean
  state?: string
  status?: string
  can_control: boolean
  healthy?: boolean
  port?: number
  latency_ms?: number | null
  models?: Array<{
    name: string
    size?: number
    parameter_size?: string
    family?: string
  }>
  vision_model?: string
  vision_model_resolved?: string | null
  vision_model_available?: boolean
  error?: string | null
  autostart?: boolean
  last_heartbeat?: string | null
  last_capture_at?: string | null
  last_analysis_at?: string | null
  error_count?: number
  detail?: string | null
}

export interface ConversationSummary {
  id: number
  title: string | null
  started_at: string
  last_message_at: string | null
  message_count: number
  pinned: boolean
  archived: boolean
  tags: string | null
  last_message: string | null
  msg_count: number
}

export interface ConversationMessage {
  id: number
  role: 'user' | 'assistant' | 'system'
  content: string
  agent?: string
  model?: string
  created_at: string
}

export interface ConversationDocument {
  id: number
  original_name: string
  file_type: string
  file_size: number
  summary: string | null
  cloud_consent: boolean
  created_at: string
}

export interface ConversationDetail extends ConversationSummary {
  messages: ConversationMessage[]
  documents: ConversationDocument[]
}

export interface ConversationSearchResult {
  id: number
  title: string | null
  started_at: string
  last_message_at: string | null
  message_count: number
  matching_message: string | null
  match_date: string | null
}

export interface CalendarEvent {
  id: string
  title: string
  start: string
  end: string
  location: string
  notes: string
  calendar: string
}

export interface LlmActionLog {
  id: number
  created_at: string
  agent: string | null
  action_type: string | null
  payload: string | null
  status: 'success' | 'error' | 'pending'
  execution_time_ms: number | null
}

export interface DeviceInfo {
  id: number
  device_id: string
  device_name: string
  device_type: string
  is_active: 0 | 1 | boolean
  is_online: 0 | 1 | boolean
  last_heartbeat: string | null
  last_screen_at: string | null
  ip_tailscale: string | null
  auth_token?: string | null
  created_at: string
}

export interface ScreenActivityRow {
  id: number
  device: string
  app: string | null
  activity: string | null
  mood: string | null
  notable: string | null
  screenshot_hash: string | null
  change_pct: number | null
  created_at: string
}

export interface AppUsageRow {
  id: number
  device: string
  app: string
  date: string
  duration_seconds: number
  session_count: number
  created_at: string
}

export interface AudioDaemonStatus {
  enabled: boolean
  state: 'idle' | 'wake_listening' | 'listening' | 'processing' | 'speaking' | 'error'
  wake_word_enabled: boolean
  continuous_mode: boolean
  last_interaction: number
  stt_engine: string
  tts_engine: string
  has_porcupine: boolean
}

export interface SupervisorService {
  id: string
  name: string
  description: string
  category: string
  port: number
  running: boolean
  can_control: boolean
  status?: string
  healthy?: boolean
  latency_ms?: number | null
  models?: ServiceInfo['models']
  vision_model?: string
  vision_model_resolved?: string | null
  vision_model_available?: boolean
  error?: string | null
  sub_services?: ServiceInfo[]
}

export interface SupervisorStatus {
  supervisor: {
    pid: number
    port: number
    uptime_s: number
  }
  services: SupervisorService[]
}

export interface VoiceDebugTrace {
  id: number
  created_at: string
  input_text: string
  system_prompt: string
  messages_json: string
  raw_response: string
  response_clean: string
  emotion: string
  action_json: string | null
  model: string
  tokens_in: number
  tokens_out: number
  cost: number
  latency_stt_ms: number
  latency_llm1_ms: number
  latency_llm2_ms: number
  latency_tts_ms: number
  latency_total_ms: number
  stt_engine: string
  tts_engine: string
  audio_duration_ms: number
}
