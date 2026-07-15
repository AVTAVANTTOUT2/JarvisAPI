/**
 * Service REST central — BASE vide : même origine (FastAPI prod) ou proxy Vite (/api → backend).
 */
import type { ApiPerson, NotificationItem } from '@unified/types/jarvis'
import type {
  AppUsageRow,
  AudioDaemonStatus,
  AuthSession,
  AuthStatus,
  CalendarEvent,
  ConversationDetail,
  ConversationSearchResult,
  ConversationSummary,
  DeviceInfo,
  LlmActionLog,
  ScreenActivityRow,
  ServiceInfo,
  SupervisorStatus,
  VoiceDebugTrace,
  WeeklyStats,
} from '@unified/types/api'

export type * from '@unified/types/api'

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
export const API_BASE = ''

/** Point réseau unique pour les vues desktop, mobile et la file hors-ligne. */
export function jarvisRawFetch(path: string, options?: RequestInit): Promise<Response> {
  const root = API_BASE.replace(/\/$/, '')
  const p = path.startsWith('/') ? path : `/${path}`
  const headers: HeadersInit = {
    ...(options?.body && !(options.body instanceof FormData)
      ? { 'Content-Type': 'application/json' }
      : {}),
    ...options?.headers,
  }
  return fetch(`${root}${p}`, { ...options, credentials: 'include', headers })
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const p = path.startsWith('/') ? path : `/${path}`
  const res = await jarvisRawFetch(p, options)
  const text = await res.text()
  if (!res.ok) {
    if ((res.status === 401 || res.status === 428) && !p.startsWith('/api/auth/')) {
      if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent('jarvis:auth-required'))
      }
    }
    throw new ApiError(`API ${res.status}`, res.status, text)
  }
  if (!text) return {} as T
  try {
    return JSON.parse(text) as T
  } catch {
    return {} as T
  }
}

/** Appel générique partagé par les vues mobiles et desktop. */
export async function jarvisFetch<T = unknown>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 15_000)
  try {
    return await request<T>(path, {
      ...options,
      signal: options?.signal ?? controller.signal,
    })
  } finally {
    clearTimeout(timeout)
  }
}

export const api = {
  getStatus: () => request('/api/status'),

  getAuthStatus: () => request<AuthStatus>('/api/auth/status'),
  authSetup: (secret: string) =>
    request<{ ok: boolean }>('/api/auth/setup', { method: 'POST', body: JSON.stringify({ secret }) }),
  authUnlock: (secret: string) =>
    request<{ ok: boolean }>('/api/auth/unlock', { method: 'POST', body: JSON.stringify({ secret }) }),
  authVerify: (secret: string) =>
    request<{ ok: boolean }>('/api/auth/verify', { method: 'POST', body: JSON.stringify({ secret }) }),
  authLogout: () => request<{ ok: boolean }>('/api/auth/logout', { method: 'POST' }),
  authChangeSecret: (current: string, next: string) =>
    request<{ ok: boolean }>('/api/auth/change-secret', {
      method: 'POST',
      body: JSON.stringify({ current, new: next }),
    }),
  authSessions: () => request<{ sessions: AuthSession[] }>('/api/auth/sessions'),
  authRevokeSession: (id: number) =>
    request<{ ok: boolean }>(`/api/auth/sessions/${id}/revoke`, { method: 'POST' }),

  startMobilePairing: () =>
    request<{ code: string; expires_at: string }>('/api/mobile/pairing/start', { method: 'POST' }),
  getMobileDevices: () =>
    request<{ devices: Array<{
      device_id: string
      name: string
      model: string
      app_version: string
      paired_at: string
      last_seen_at: string
      revoked: boolean
      push_enabled: boolean
      capabilities: Record<string, boolean>
    }> }>('/api/mobile/devices'),
  revokeMobileDevice: (deviceId: string) =>
    request<{ ok: boolean }>(`/api/mobile/devices/${encodeURIComponent(deviceId)}/revoke`, {
      method: 'POST',
    }),

  getVapidPublicKey: () => request<{ key: string }>('/api/push/vapid-public-key'),
  subscribePush: (subscription: { endpoint: string; keys: { p256dh: string; auth: string } }) =>
    request<{ ok: boolean }>('/api/push/subscribe', {
      method: 'POST',
      body: JSON.stringify(subscription),
    }),
  unsubscribePush: (endpoint: string) =>
    request<{ ok: boolean }>('/api/push/unsubscribe', {
      method: 'POST',
      body: JSON.stringify({ endpoint }),
    }),

  getWeeklyStats: (days = 7) => request<WeeklyStats>(`/api/stats/weekly?days=${days}`),
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

  getNotifications: () => request<{ notifications?: NotificationItem[] }>('/api/notifications'),
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

  getPeople: () => request<{ people?: ApiPerson[] }>('/api/people'),
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
    return request('/upload', { method: 'POST', body: form })
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
    return request(`/api/conversations/${convId}/upload`, {
      method: 'POST',
      body: form,
    })
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

/** URL WebSocket vers le superviseur (port 9000). */
export function supervisorWsUrl(): string {
  const p = typeof window !== 'undefined' && window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  // En dev (Vite sur 5173) ou en prod (superviseur sert le frontend) — meme hote
  if (typeof window !== 'undefined') {
    return `${p}//${window.location.hostname}:9000/ws/supervisor`
  }
  return 'ws://127.0.0.1:9000/ws/supervisor'
}
