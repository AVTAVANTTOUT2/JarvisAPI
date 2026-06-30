/**
 * Service REST central — BASE vide : même origine (FastAPI prod) ou proxy Vite (/api → backend).
 */
export const BASE = ''

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public body?: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

/** Compat imports existants (`lib/api.ts`). */
export const API_BASE = (import.meta.env.VITE_API_URL as string | undefined) || ''

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const root = API_BASE.replace(/\/$/, '')
  const p = path.startsWith('/') ? path : `/${path}`
  const headers: HeadersInit = {
    ...(options?.body && !(options.body instanceof FormData)
      ? { 'Content-Type': 'application/json' }
      : {}),
    ...options?.headers,
  }
  const res = await fetch(`${root}${p}`, { ...options, headers })
  const text = await res.text()
  if (!res.ok) {
    throw new ApiError(`API ${res.status}`, res.status, text)
  }
  if (!text) return {} as T
  try {
    return JSON.parse(text) as T
  } catch {
    return {} as T
  }
}

export const api = {
  getStatus: () => request('/api/status'),
  getIntegrations: () => request('/api/integrations'),

  getTTSSetting: () => request<{ engine: string }>('/api/settings/tts'),
  setTTSSetting: (engine: string) =>
    request<{ engine: string; ok: boolean }>('/api/settings/tts', {
      method: 'PATCH',
      body: JSON.stringify({ engine }),
    }),

  getLogs: (params?: { limit?: number; type?: string }) => {
    const sp = new URLSearchParams()
    if (params?.limit != null) sp.set('limit', String(params.limit))
    if (params?.type) sp.set('type', params.type)
    const q = sp.toString()
    return request<{ logs: LlmActionLog[]; count: number }>(`/api/logs${q ? `?${q}` : ''}`)
  },

  getNotifications: () => request('/api/notifications'),
  markRead: (id: number) => request(`/api/notifications/${id}/read`, { method: 'POST' }),
  markAllRead: () => request('/api/notifications/read-all', { method: 'POST' }),

  getTasks: (status?: string) =>
    request('/api/tasks' + (status ? `?status=${encodeURIComponent(status)}` : '')),
  createTask: (body: Record<string, unknown>) =>
    request('/api/tasks', { method: 'POST', body: JSON.stringify(body) }),
  updateTask: (id: number, status: string) =>
    request(`/api/tasks/${id}`, { method: 'PATCH', body: JSON.stringify({ status }) }),
  deleteTask: (id: number) =>
    request<{ ok: boolean; deleted_id: number }>(`/api/tasks/${id}`, { method: 'DELETE' }),
  deleteAllTasks: () =>
    request<{ ok: boolean; deleted_count: number }>('/api/tasks', { method: 'DELETE' }),

  getLifeProfile: () => request('/api/life-profile'),
  addProfileEntry: (category: string, content: string) =>
    request('/api/life-profile', {
      method: 'POST',
      body: JSON.stringify({ category, content }),
    }),
  updateProfileEntry: (id: number, content: string) =>
    request(`/api/life-profile/${id}`, { method: 'PUT', body: JSON.stringify({ content }) }),
  deleteProfileEntry: (id: number) => request(`/api/life-profile/${id}`, { method: 'DELETE' }),

  getPeople: () => request('/api/people'),
  getPerson: (name: string) => request(`/api/people/${encodeURIComponent(name)}`),
  updatePerson: (name: string, data: Record<string, unknown>) =>
    request(`/api/people/${encodeURIComponent(name)}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  askAboutPerson: (name: string, question: string) =>
    request<{ response: string; model?: string; cost?: number }>(
      `/api/people/${encodeURIComponent(name)}/ask`,
      { method: 'POST', body: JSON.stringify({ question }) },
    ),
  getPersonAnalytics: (name: string) =>
    request(`/api/people/${encodeURIComponent(name)}/analytics`),
  getPersonTimeline: (name: string) =>
    request<{ events: Array<Record<string, unknown>>; updated_at: string | null; from_cache: boolean }>(
      `/api/people/${encodeURIComponent(name)}/timeline`,
    ),
  regenerateTimeline: (name: string) =>
    request<{ events: Array<Record<string, unknown>>; updated_at: string | null; from_cache: boolean }>(
      `/api/people/${encodeURIComponent(name)}/timeline/regenerate`,
      { method: 'POST' },
    ),
  sendImessage: (name: string, text: string) =>
    request<{ ok: boolean; message?: string }>(
      `/api/people/${encodeURIComponent(name)}/send`,
      { method: 'POST', body: JSON.stringify({ text }) },
    ),
  suggestMessage: (name: string) =>
    request<{ suggestion: string; model?: string; cost?: number }>(
      `/api/people/${encodeURIComponent(name)}/suggest-message`,
      { method: 'POST' },
    ),
  remindContact: (name: string, when: string) =>
    request<{ ok: boolean; task_id?: number }>(
      `/api/people/${encodeURIComponent(name)}/remind`,
      { method: 'POST', body: JSON.stringify({ when }) },
    ),
  getPersonDescription: (name: string) =>
    request<{ description?: string; model?: string; cost?: number }>(
      `/api/people/${encodeURIComponent(name)}/description`,
    ),
  refreshPersonDescription: (name: string) =>
    request<{ description?: string; model?: string; cost?: number }>(
      `/api/people/${encodeURIComponent(name)}/description/refresh`,
      { method: 'POST' },
    ),
  addPerson: (body: Record<string, unknown>) =>
    request('/api/people', { method: 'POST', body: JSON.stringify(body) }),
  getRelationship: (name: string) =>
    request(`/api/relationship/${encodeURIComponent(name)}`),
  analyzeContact: (name: string) =>
    request('/api/analyze-contact', { method: 'POST', body: JSON.stringify({ name }) }),
  getMacContacts: () => request('/api/contacts'),

  getJournal: () => request('/api/journal'),
  postJournal: (content: string) =>
    request('/api/journal', { method: 'POST', body: JSON.stringify({ content }) }),

  getPatterns: () => request('/api/patterns'),

  getMemory: () => request('/api/memory'),

  getOutputs: () => request('/api/outputs'),
  getOutputUrl: (path: string) =>
    `${API_BASE.replace(/\/$/, '')}/api/outputs/${path.split('/').map(encodeURIComponent).join('/')}`,
  uploadFile: async (file: File) => {
    const form = new FormData()
    form.append('file', file)
    const root = API_BASE.replace(/\/$/, '')
    const res = await fetch(`${root}/upload`, { method: 'POST', body: form })
    const text = await res.text()
    if (!res.ok) throw new ApiError(`Upload ${res.status}`, res.status, text)
    try {
      return JSON.parse(text)
    } catch {
      return { raw: text }
    }
  },

  getBriefing: (kind = 'morning') =>
    request(`/api/briefing?kind=${encodeURIComponent(kind)}`),

  getRecordings: (limit?: number) =>
    request(
      `/api/recordings${limit != null ? `?limit=${encodeURIComponent(String(limit))}` : ''}`,
    ),
  getRecording: (id: number) => request(`/api/recordings/${id}`),

  getLocationStatus: () => request('/api/location/status'),
  sendLocation: (body: Record<string, unknown>) =>
    request('/api/location', { method: 'POST', body: JSON.stringify(body) }),
  sendLocationBatch: (points: Record<string, unknown>[]) =>
    request('/api/location/batch', { method: 'POST', body: JSON.stringify({ points }) }),
  getLocationHistory: (hours = 24) =>
    request(`/api/location/history?hours=${encodeURIComponent(String(hours))}`),
  getPlaces: () => request('/api/places'),
  createPlace: (body: Record<string, unknown>) =>
    request('/api/places', { method: 'POST', body: JSON.stringify(body) }),
  updatePlace: (id: number, body: Record<string, unknown>) =>
    request(`/api/places/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  deletePlace: (id: number) => request(`/api/places/${id}`, { method: 'DELETE' }),
  getPlaceStats: (id: number) => request(`/api/places/${id}/stats`),
  nameCurrentLocation: (name: string, category: string) =>
    request('/api/location/name-current', {
      method: 'POST',
      body: JSON.stringify({ name, category }),
    }),
  getTodayVisits: () => request('/api/visits/today'),
  getVisits: (days = 7) => request(`/api/visits?days=${encodeURIComponent(String(days))}`),
  getTrips: (days = 7) => request(`/api/trips?days=${encodeURIComponent(String(days))}`),
  getLocationPatterns: () => request('/api/location/patterns'),

  // Calendar
  getCalendarEvents: (start: string, end: string) =>
    request<{ events: CalendarEvent[]; count: number }>(
      `/api/calendar?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`,
    ),
  createCalendarEvent: (body: { title: string; start: string; end: string; location?: string; notes?: string; calendar?: string }) =>
    request<{ ok: boolean; summary?: string; message?: string }>(
      '/api/calendar',
      { method: 'POST', body: JSON.stringify(body) },
    ),

  search: (q: string) => request(`/api/search?q=${encodeURIComponent(q)}`),
  exportJson: () => request('/api/export?format=json'),

  // Daemon JARVIS — devices, écran, app usage
  getDevices: () =>
    request<{ devices: DeviceInfo[]; active: DeviceInfo | null }>('/api/devices'),
  activateDevice: (deviceId: string) =>
    request<{ ok: boolean; active: string }>(`/api/devices/${encodeURIComponent(deviceId)}/activate`, {
      method: 'POST',
    }),
  getScreenActivity: (hours = 24, device?: string) => {
    const sp = new URLSearchParams({ hours: String(hours) })
    if (device) sp.set('device', device)
    return request<{ activity: ScreenActivityRow[] }>(`/api/screen-activity?${sp.toString()}`)
  },
  getCurrentScreenContext: (device?: string) =>
    request<{ context: ScreenActivityRow | null }>(
      `/api/screen-activity/current${device ? `?device=${encodeURIComponent(device)}` : ''}`,
    ),
  getAppUsage: (days = 7, device?: string) => {
    const sp = new URLSearchParams({ days: String(days) })
    if (device) sp.set('device', device)
    return request<{ usage: AppUsageRow[]; days: number }>(`/api/app-usage?${sp.toString()}`)
  },

  // Conversations
  getConversations: (archived = false, limit = 50) =>
    request<{ conversations: ConversationSummary[] }>(
      `/api/conversations?archived=${archived}&limit=${limit}`,
    ),
  getConversation: (id: number) => request<ConversationDetail>(`/api/conversations/${id}`),
  updateConversation: (id: number, data: Record<string, unknown>) =>
    request(`/api/conversations/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  deleteConversation: (id: number) =>
    request(`/api/conversations/${id}`, { method: 'DELETE' }),
  archiveConversation: (id: number) =>
    request(`/api/conversations/${id}/archive`, { method: 'POST' }),
  pinConversation: (id: number) =>
    request<{ ok: boolean; pinned: boolean }>(`/api/conversations/${id}/pin`, { method: 'POST' }),
  searchConversations: (q: string) =>
    request<{ results: ConversationSearchResult[]; count: number }>(
      `/api/conversations/search?q=${encodeURIComponent(q)}`,
    ),
  uploadToConversation: async (convId: number, file: File) => {
    const form = new FormData()
    form.append('file', file)
    const root = API_BASE.replace(/\/$/, '')
    const res = await fetch(`${root}/api/conversations/${convId}/upload`, {
      method: 'POST',
      body: form,
    })
    const text = await res.text()
    if (!res.ok) throw new ApiError(`Upload ${res.status}`, res.status, text)
    try {
      return JSON.parse(text)
    } catch {
      return { raw: text }
    }
  },

  // ── Audio Daemon ──
  getAudioDaemonStatus: () => request<AudioDaemonStatus>('/api/audio-daemon/status'),
  startAudioDaemon: () => request<{ ok: boolean }>('/api/audio-daemon/start', { method: 'POST' }),
  stopAudioDaemon: () => request<{ ok: boolean }>('/api/audio-daemon/stop', { method: 'POST' }),
  setWakeWord: (enabled: boolean) =>
    request<{ ok: boolean; wake_word_enabled: boolean }>('/api/audio-daemon/wake-word', {
      method: 'POST',
      body: JSON.stringify({ enabled }),
    }),
  setContinuousMode: (enabled: boolean) =>
    request<{ ok: boolean; continuous_mode: boolean }>('/api/audio-daemon/continuous', {
      method: 'POST',
      body: JSON.stringify({ enabled }),
    }),

  // ── Voice Debug ──
  getVoiceDebugLogs: (limit?: number) =>
    request<{ logs: VoiceDebugTrace[] }>(`/api/voice-debug?limit=${limit || 50}`),

  // ── Service Control ──
  getServices: () => request<{ services: ServiceInfo[] }>('/api/control/services'),
  startService: (id: string) =>
    request<{ ok: boolean; message?: string }>(`/api/control/${encodeURIComponent(id)}/start`, {
      method: 'POST',
    }),
  stopService: (id: string) =>
    request<{ ok: boolean; message?: string }>(`/api/control/${encodeURIComponent(id)}/stop`, {
      method: 'POST',
    }),
  restartService: (id: string) =>
    request<{ ok: boolean; message?: string }>(`/api/control/${encodeURIComponent(id)}/restart`, {
      method: 'POST',
    }),
  restartAll: () =>
    request<{ results: Record<string, { ok: boolean; message?: string; error?: string }> }>(
      '/api/control/restart-all',
      { method: 'POST' },
    ),
  stopAll: () =>
    request<{ results: Record<string, { ok: boolean; message?: string; error?: string }> }>(
      '/api/control/stop-all',
      { method: 'POST' },
    ),
  startAll: () =>
    request<{ results: Record<string, { ok: boolean; message?: string; error?: string }> }>(
      '/api/control/start-all',
      { method: 'POST' },
    ),
  getServiceLogs: (id: string, lines = 30) =>
    request<{ logs: string[]; count?: number; message?: string }>(
      `/api/control/${encodeURIComponent(id)}/logs?lines=${lines}`,
    ),

  // ── Supervisor (port 9000) ──
  getSupervisorStatus: () => request<SupervisorStatus>('/api/supervisor/status'),
  supervisorStart: (id: string) =>
    request<{ ok: boolean; message?: string }>(`/api/supervisor/${encodeURIComponent(id)}/start`, {
      method: 'POST',
    }),
  supervisorStop: (id: string) =>
    request<{ ok: boolean; message?: string }>(`/api/supervisor/${encodeURIComponent(id)}/stop`, {
      method: 'POST',
    }),
  supervisorRestart: (id: string) =>
    request<{ ok: boolean; message?: string }>(`/api/supervisor/${encodeURIComponent(id)}/restart`, {
      method: 'POST',
    }),
  supervisorStartAll: () =>
    request<{ results: Record<string, { ok: boolean; message?: string }> }>(
      '/api/supervisor/start-all',
      { method: 'POST' },
    ),
  supervisorStopAll: () =>
    request<{ results: Record<string, { ok: boolean; message?: string }> }>(
      '/api/supervisor/stop-all',
      { method: 'POST' },
    ),
  supervisorRestartAll: () =>
    request<{ results: Record<string, { ok: boolean; message?: string }> }>(
      '/api/supervisor/restart-all',
      { method: 'POST' },
    ),
  supervisorLogs: (id: string, lines = 50) =>
    request<{ logs: string[]; message?: string; error?: string }>(
      `/api/supervisor/${encodeURIComponent(id)}/logs?lines=${lines}`,
    ),
  getSubServices: () => request<{ available: boolean; services: ServiceInfo[]; message?: string; error?: string }>(
    '/api/supervisor/sub-services',
  ),
  subServiceAction: (id: string, action: 'start' | 'stop' | 'restart') =>
    request<{ ok: boolean; message?: string; error?: string }>(
      `/api/supervisor/sub/${encodeURIComponent(id)}/${encodeURIComponent(action)}`,
      { method: 'POST' },
    ),
}

export interface ServiceInfo {
  id: string
  name: string
  description: string
  category: string
  running: boolean
  state?: string
  can_control: boolean
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

// ── Audio Daemon ──
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

// ── Supervisor ──

export interface SupervisorService {
  id: string
  name: string
  description: string
  category: string
  port: number
  running: boolean
  can_control: boolean
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

// ── Voice Debug ──
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

/** URL WebSocket vers le superviseur (port 9000). */
export function supervisorWsUrl(): string {
  const p = typeof window !== 'undefined' && window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  // En dev (Vite sur 5173) ou en prod (superviseur sert le frontend) — meme hote
  if (typeof window !== 'undefined') {
    return `${p}//${window.location.hostname}:9000/ws/supervisor`
  }
  return 'ws://127.0.0.1:9000/ws/supervisor'
}
