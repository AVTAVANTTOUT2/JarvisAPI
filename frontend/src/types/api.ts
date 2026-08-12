/** Contrats de données partagés par le client API et les vues frontend. */

export type * from './agentic'

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
  /** Nombre de réponses dont les jetons ont été estimés localement. */
  estimated_usage_count: number
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
    estimated_usage_count: number
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
  checkpoint_id: string
  title: string | null
  title_status: 'pending' | 'ready' | 'fallback' | 'manual'
  title_source: string | null
  title_updated_at: string | null
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
  checkpoint_id: string
  title: string | null
  title_status: 'pending' | 'ready' | 'fallback' | 'manual'
  started_at: string
  last_message_at: string | null
  message_count: number
  matching_message: string | null
  match_date: string | null
}

export type UnifiedSearchCategory =
  | 'conversations'
  | 'contacts'
  | 'tasks'
  | 'documents'
  | 'memory'

export interface UnifiedSearchResult {
  type: 'conversation' | 'person' | 'task' | 'document' | 'episode' | 'fact'
  category: UnifiedSearchCategory
  id: number
  checkpoint_id?: string | null
  title: string
  subtitle: string
  meta: string | null
  url: string
  score: number
}

export interface UnifiedSearchResponse {
  query: string
  total: number
  categories: Partial<Record<UnifiedSearchCategory, number>>
  results: UnifiedSearchResult[]
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

export interface FoodSuggestionItem {
  name: string
  quantity: number
}

export interface FoodSuggestion {
  id: number
  slot: number
  restaurant: string
  items: FoodSuggestionItem[]
  estimated_price: number | null
  /** Montant maximum que le clic autorise. Renvoyé tel quel à la commande. */
  max_price: number | null
  currency: string
  reasoning: string | null
  score: number
  factors: Record<string, number>
  expires_at: string | null
}

export interface FoodOrder {
  id: number
  restaurant: string
  items_json: string
  total_price: number | null
  currency: string
  status: string
  dry_run: number
  delivery_status: string | null
  eta_minutes: number | null
  delivered_at: string | null
  rating: number | null
  suggestion_id: number | null
  error: string | null
  created_at: string
}

export interface FoodIntegrationStatus {
  enabled: boolean
  dry_run: boolean
  can_browse: boolean
  can_place_real_order: boolean
  can_scrape: boolean
  suggestions_enabled: boolean
  selectors_verified: boolean
  reasons: string[]
  max_order_price: number
  max_daily_spend: number
  max_daily_orders: number
}

export interface FoodStatusResponse {
  integration: FoodIntegrationStatus
  today: { date: string; orders: number; spend: number }
}

export interface FoodQuickOrderResult {
  ok: boolean
  status: string
  restaurant: string
  items_label: string
  total_price: number | null
  currency: string
  dry_run: boolean
  error?: string | null
  slot: number
  suggestion_id: number
  authorised_price?: number
}

export interface FoodMenuSummary {
  restaurant: string
  item_count: number
  scraped_at: string
}

export interface FoodMenuItem {
  id: number
  restaurant: string
  item_name: string
  category: string | null
  price: number | null
  currency: string
  available: number
  scraped_at: string
}

export interface FoodSettings {
  enabled: boolean
  dry_run: boolean
  menu_scrape_enabled: boolean
  suggestions_enabled: boolean
  headless: boolean
  max_order_price: number
  max_daily_spend: number
  max_daily_orders: number
  max_items: number
  max_item_quantity: number
}

/** Bornes dures issues du `.env` : l'interface peut descendre, jamais monter. */
export interface FoodSettingsCeilings {
  enabled: boolean
  dry_run_forced: boolean
  menu_scrape_enabled: boolean
  suggestions_enabled: boolean
  headless: boolean
  max_order_price: number
  max_daily_spend: number
  max_daily_orders: number
  max_items: number
  max_item_quantity: number
}

export interface FoodSettingsResponse {
  settings: FoodSettings
  ceilings: FoodSettingsCeilings
}

export interface FoodCartPlan {
  plan_id: string
  restaurant: string
  items: FoodSuggestionItem[]
  items_label: string
  total_price: number
  currency: string
  dry_run: boolean
  expires_in_seconds: number
  needs_confirmation?: boolean
}

export interface FoodOrderOutcome {
  ok: boolean
  status: string
  restaurant: string
  items_label: string
  total_price: number | null
  currency: string
  dry_run: boolean
  error: string | null
  timestamp: string
}

export interface FoodSelectorsReport {
  ok: boolean
  path: string
  verified: boolean
  captured_at?: string | null
  version?: number
  roles: Record<string, number>
  missing_required: string[]
  missing_optional?: string[]
  error?: string
}

export interface FoodCaptureStatus {
  running: boolean
  mode: string
  started_at: number | null
  finished_at: number | null
  returncode: number | null
  output: string
}

export interface FoodSessionReport {
  path: string
  exists: boolean
  readable: boolean
  age_hours: number | null
  capture: FoodCaptureStatus
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
