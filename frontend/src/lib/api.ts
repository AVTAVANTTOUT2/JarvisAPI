/**
 * Service REST central — BASE vide : même origine que FastAPI ou le supervisor.
 */
import type { ApiPerson, NotificationItem } from '@unified/types/jarvis'
import { authClient } from '@jarvis/auth'
import { API_BASE, jarvisRawFetch } from './http'
import { enqueueWrite, isNetworkError, resourceRoot } from './offline/queue'
import {
  cacheRead,
  getCachedRead,
  getLatestCachedVersion,
  invalidateCachedReads,
} from './offline/readCache'
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
  AppleShortcutInstalledRow,
  AppleShortcutPlan,
  AppleShortcutRecipe,
  AppleShortcutRegistryRow,
  AppleShortcutRunRow,
  AppleShortcutsStatus,
  FoodCaptureStatus,
  FoodCartPlan,
  FoodMenuItem,
  FoodMenuSummary,
  FoodOrder,
  FoodOrderOutcome,
  FoodQuickOrderResult,
  FoodSelectorsReport,
  FoodSessionReport,
  FoodSettings,
  FoodSettingsResponse,
  FoodStatusResponse,
  FoodSuggestion,
  LlmActionLog,
  RecordingChunkAck,
  RecordingDetail,
  RecordingListResponse,
  RecordingSessionStatus,
  ScreenActivityRow,
  ServiceInfo,
  SupervisorStatus,
  UnifiedSearchResponse,
  VoiceDebugTrace,
  WeeklyStats,
} from '@unified/types/api'

export type * from '@unified/types/api'
export { API_BASE, jarvisRawFetch } from './http'

export const BASE = API_BASE

/**
 * Contrat de `GET /api/health/detail` — miroir exact de `jarvis/health.py`.
 *
 * `state` et `reason` viennent d'un vocabulaire fermé côté serveur, mais le
 * type reste ouvert sur `string` pour `reason` : un backend plus récent que le
 * frontend doit pouvoir ajouter un code sans casser l'affichage.
 */
export type HealthState = 'healthy' | 'degraded' | 'unavailable' | 'unknown'

export interface HealthComponent {
  name: string
  state: HealthState
  critical: boolean
  reason: string | null
  details: Record<string, string | number | boolean | null>
}

export interface HealthReport {
  status: HealthState
  checked_at: string
  duration_ms: number
  summary: Record<HealthState, number>
  components: HealthComponent[]
}

export interface VoiceLatencyStage {
  p50_ms: number
  p95_ms: number
  count: number
}

export interface VoiceLatencyMetrics {
  ok?: boolean
  samples: number
  days: number
  stages: Record<string, VoiceLatencyStage>
}

export interface MetricHistoryPoint {
  timestamp: string
  value: number
  last_value: number
  samples: number
}

export interface MetricHistorySeries {
  metric: string
  unit: string
  points: MetricHistoryPoint[]
  summary: {
    latest: number
    average: number
    minimum: number
    maximum: number
    trend_pct: number | null
    samples: number
  }
}

export interface MetricHistoryResponse {
  hours: number
  bucket_seconds: number
  retention_days: number
  series: MetricHistorySeries[]
}

export interface DocumentPrivacyPolicy {
  mode: 'strict_local' | 'hybrid'
  strict_local: boolean
  cloud_provider: string
  cloud_summary_available: boolean
  explicit_consent_required: boolean
  cloud_max_chars: number
  pii_protection: string
  features: {
    school_upload: {
      storage: string
      extraction: string
      summary: string
      data_leaving_device: string
    }
    conversation_document: {
      storage: string
      extraction: string
      default_summary: string
      cloud_summary: string
      cloud_chat_context: string
      data_leaving_device: string
    }
  }
}

export interface ConversationUploadResult {
  ok: boolean
  doc_id?: number
  filename: string
  file_type: string
  size: number
  content_length: number
  summary?: string | null
  processing_mode: 'local' | 'cloud' | 'local_fallback'
  cloud_consent: boolean
  cloud_request_attempted: boolean
  data_left_device: boolean
  pii_entities_masked: number
  cloud_payload_chars: number
}

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

function publicApiErrorMessage(status: number, body: string): string {
  try {
    const payload = JSON.parse(body) as {
      detail?: { message?: unknown }
    }
    const message = payload.detail?.message
    if (typeof message === 'string' && message.trim()) return message.trim()
  } catch {
    // Réponse non JSON : ne pas afficher un corps de proxy ou une stack HTML.
  }
  return `API ${status}`
}

export interface OfflineRequestPolicy {
  /** Force ou interdit la mise en file d'une mutation réseau. */
  queue?: boolean
  /** Libellé non sensible affiché dans le statut global. */
  label?: string
  /** Valeur retournée immédiatement quand l'écriture est mise en file. */
  optimistic?: unknown
  /** Permet d'exclure une lecture volatile du cache IndexedDB. */
  cache?: boolean
  cacheTtlMs?: number
}

export type JarvisRequestInit = RequestInit & { offline?: OfflineRequestPolicy }

const QUEUEABLE_DATA_MUTATIONS = [
  /^\/api\/tasks(?:\/\d+)?$/,
  /^\/api\/notifications\/(?:\d+\/read|read-all)$/,
  /^\/api\/settings\/tts$/,
  /^\/api\/fitness\/(?:sessions\/\d+\/progress|meals(?:\/from-text)?|water|weights|program(?:\/sessions\/\d+)?)$/,
  /^\/api\/life-profile(?:\/\d+)?$/,
  /^\/api\/people(?:\/[^/]+)?$/,
  /^\/api\/journal$/,
  /^\/api\/location(?:\/(?:batch|name-current))?$/,
  /^\/api\/places(?:\/\d+)?$/,
  /^\/api\/conversations\/\d+(?:\/archive)?$/,
  /^\/api\/privacy\/documents$/,
]

/** Sonde publique hors cache ; référence de politique, pas appel client. */
const LIVE_HEALTH_PATH = '/api/health/live' // architecture-audit: non-consumer-reference

function isCacheableRead(path: string, method: string, policy?: OfflineRequestPolicy): boolean {
  if (policy?.cache === false || method !== 'GET' || !path.startsWith('/api/')) return false
  return !path.startsWith('/api/auth/') && path !== LIVE_HEALTH_PATH
}

function isQueueableMutation(path: string, method: string, policy?: OfflineRequestPolicy): boolean {
  if (policy?.queue !== undefined) return policy.queue
  if (!['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) return false
  return QUEUEABLE_DATA_MUTATIONS.some((pattern) => pattern.test(path.split('?')[0]))
}

function serializableBody(body: BodyInit | null | undefined): { supported: boolean; data: unknown } {
  if (body === undefined || body === null) return { supported: true, data: undefined }
  if (typeof body !== 'string') return { supported: false, data: undefined }
  try {
    return { supported: true, data: JSON.parse(body) }
  } catch {
    return { supported: false, data: undefined }
  }
}

function dispatchOfflineEvent(name: string, detail: Record<string, unknown>): void {
  if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent(name, { detail }))
}

async function request<T>(
  path: string,
  options?: JarvisRequestInit,
  acceptedErrorStatuses: readonly number[] = [],
): Promise<T> {
  const p = path.startsWith('/') ? path : `/${path}`
  const { offline, ...networkOptions } = options ?? {}
  const method = (networkOptions.method ?? 'GET').toUpperCase()
  const cacheable = isCacheableRead(p, method, offline)
  let res: Response
  try {
    res = await jarvisRawFetch(p, networkOptions)
  } catch (error) {
    if (isNetworkError(error) && cacheable) {
      try {
        const cached = await getCachedRead<T>(p, offline?.cacheTtlMs)
        if (cached) {
          dispatchOfflineEvent('jarvis:offline-cache-hit', { path: p, staleMs: cached.staleMs })
          return cached.data
        }
      } catch {
        // IndexedDB indisponible : conserver l'erreur réseau d'origine.
      }
    }
    if (isNetworkError(error) && isQueueableMutation(p, method, offline)) {
      const body = serializableBody(networkOptions.body)
      if (body.supported) {
        try {
          const queueId = await enqueueWrite({
            method: method as 'POST' | 'PUT' | 'PATCH' | 'DELETE',
            path: p,
            body: body.data,
            entityKey: resourceRoot(p),
            baseVersion: await getLatestCachedVersion(resourceRoot(p)),
            label: offline?.label ?? `Modification ${resourceRoot(p).replace('/api/', '')}`,
          })
          const queuedResult = offline?.optimistic ?? { queued: true, offline_queue_id: queueId }
          dispatchOfflineEvent('jarvis:offline-write-queued', { path: p, queueId })
          return queuedResult as T
        } catch {
          // Si IndexedDB échoue, l'appelant doit recevoir l'erreur réseau réelle.
        }
      }
    }
    throw error
  }
  const text = await res.text()
  if (!res.ok && !acceptedErrorStatuses.includes(res.status)) {
    if ((res.status === 401 || res.status === 428) && !p.startsWith('/api/auth/')) {
      if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent('jarvis:auth-required'))
      }
    }
    throw new ApiError(publicApiErrorMessage(res.status, text), res.status, text)
  }
  let payload: T
  if (!text) payload = {} as T
  else {
    try {
      payload = JSON.parse(text) as T
    } catch {
      payload = {} as T
    }
  }
  if (cacheable) {
    const versionText = res.headers.get('X-Jarvis-Entity-Version')
    const entityVersion = versionText !== null && /^\d+$/.test(versionText)
      ? Number(versionText)
      : undefined
    await cacheRead(p, payload, entityVersion).catch(() => undefined)
  }
  if (method !== 'GET') await invalidateCachedReads(resourceRoot(p)).catch(() => undefined)
  return payload
}

/** Appel générique partagé par les vues mobiles et desktop. */
export async function jarvisFetch<T = unknown>(
  path: string,
  options?: JarvisRequestInit,
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

  /**
   * Diagnostic de santé agrégé (`GET /api/health/detail`).
   *
   * `signal` est obligatoire côté appelant pour que la page puisse annuler la
   * requête en vol au démontage : un composant démonté qui reçoit encore une
   * réponse est une fuite d'abonnement.
   */
  getHealthDetail: (options?: { refresh?: boolean; signal?: AbortSignal }) =>
    request<HealthReport>(
      `/api/health/detail${options?.refresh ? '?refresh=true' : ''}`,
      { signal: options?.signal },
      [503],
    ),

  /** Latences du pipeline vocal — source unique, déjà exposée par le backend. */
  getVoiceMetrics: (days = 7, signal?: AbortSignal) =>
    request<VoiceLatencyMetrics>(`/api/voice/metrics?days=${days}`, { signal }),

  /** Séries temporelles agrégées et conservées localement. */
  getMetricHistory: (hours = 24, signal?: AbortSignal) =>
    request<MetricHistoryResponse>(`/api/metrics/history?hours=${hours}`, { signal }),

  getAuthStatus: (): Promise<AuthStatus> => authClient.status(),
  authSetup: (secret: string) => authClient.setup(secret),
  authUnlock: (secret: string) => authClient.unlock(secret),
  authVerify: (secret: string) => authClient.verify(secret),
  authLogout: () => authClient.logout(),
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
  clearLogs: () =>
    request<{
      ok: boolean
      deleted_count: number
      deleted: { llm_action_logs: number; dev_loop_log: number }
    }>('/api/logs', { method: 'DELETE' }),

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

  getFoodStatus: () => request<FoodStatusResponse>('/api/food/status'),
  getFoodSuggestions: () => request<{ suggestions: FoodSuggestion[] }>('/api/food/suggestions'),
  generateFoodSuggestions: () =>
    request('/api/food/suggestions/generate', { method: 'POST' }),
  // `accepted_price` est le montant lu sur le bouton : le serveur refuse de
  // payer au-delà, donc un écran périmé ne peut pas engager plus que prévu.
  quickOrderFood: (slot: number, acceptedPrice: number) =>
    request<FoodQuickOrderResult>(`/api/food/suggestions/${slot}/order`, {
      method: 'POST',
      body: JSON.stringify({ accepted_price: acceptedPrice }),
    }),
  getFoodOrders: (limit = 30) => request<{ orders: FoodOrder[] }>(`/api/food/orders?limit=${limit}`),
  rateFoodOrder: (orderId: number, rating: number) =>
    request<{ order: FoodOrder }>(`/api/food/orders/${orderId}/rating`, {
      method: 'POST',
      body: JSON.stringify({ rating }),
    }),
  getFoodDelivery: () => request<{ orders: FoodOrder[] }>('/api/food/delivery'),
  refreshFoodDelivery: () => request('/api/food/delivery/refresh', { method: 'POST' }),
  getFoodMenus: () => request<{ restaurants: FoodMenuSummary[] }>('/api/food/menus'),
  refreshFoodMenus: (restaurants?: string[]) =>
    request('/api/food/menus/refresh', {
      method: 'POST',
      body: JSON.stringify(restaurants ? { restaurants } : {}),
    }),
  getFoodMenuItems: (restaurant: string) =>
    request<{ restaurant: string; items: FoodMenuItem[] }>(
      `/api/food/menus/${encodeURIComponent(restaurant)}`,
    ),

  getFoodSettings: () => request<FoodSettingsResponse>('/api/food/settings'),
  updateFoodSettings: (patch: Partial<FoodSettings>) =>
    request<FoodSettingsResponse>('/api/food/settings', {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),
  resetFoodSettings: () =>
    request<FoodSettingsResponse>('/api/food/settings/reset', { method: 'POST' }),

  // Panier libre : la préparation lit le total réel sans rien engager, la
  // confirmation consomme le plan une seule fois.
  prepareFoodCart: (restaurant: string, items: { name: string; quantity: number }[]) =>
    request<FoodCartPlan>('/api/food/cart/prepare', {
      method: 'POST',
      body: JSON.stringify({ restaurant, items }),
    }),
  confirmFoodCart: (planId: string) =>
    request<FoodOrderOutcome>(`/api/food/cart/${encodeURIComponent(planId)}/confirm`, {
      method: 'POST',
    }),
  cancelFoodCart: (planId: string) =>
    request<{ ok: boolean; revoked: boolean }>(`/api/food/cart/${encodeURIComponent(planId)}`, {
      method: 'DELETE',
    }),

  getFoodSelectors: () => request<FoodSelectorsReport>('/api/food/selectors'),
  reloadFoodSelectors: () =>
    request<FoodSelectorsReport>('/api/food/selectors/reload', { method: 'POST' }),
  getFoodSession: () => request<FoodSessionReport>('/api/food/session'),
  probeFoodSession: () =>
    request<{ ok: boolean; message: string; url?: string }>('/api/food/session/probe', {
      method: 'POST',
    }),
  startFoodCapture: (mode: 'session' | 'codegen' = 'session') =>
    request<FoodCaptureStatus>('/api/food/session/capture', {
      method: 'POST',
      body: JSON.stringify({ mode }),
    }),
  stopFoodCapture: () =>
    request<FoodCaptureStatus>('/api/food/session/capture', { method: 'DELETE' }),

  getAppleShortcutsStatus: () =>
    request<AppleShortcutsStatus>('/api/apple/shortcuts/status'),
  getAppleShortcutsInstalled: (folder?: string) => {
    const sp = new URLSearchParams()
    if (folder) sp.set('folder', folder)
    const q = sp.toString()
    return request<{
      shortcuts: AppleShortcutInstalledRow[]
      folders: string[]
      count: number
    }>('/api/apple/shortcuts/installed' + (q ? `?${q}` : ''))
  },
  getAppleShortcutsRegistry: (enabledOnly = false) =>
    request<{ shortcuts: AppleShortcutRegistryRow[]; count: number }>(
      `/api/apple/shortcuts/registry${enabledOnly ? '?enabled_only=true' : ''}`,
    ),
  createAppleShortcutRegistry: (body: {
    name: string
    alias?: string
    description?: string
    allow_input?: boolean
    requires_confirmation?: boolean
    enabled?: boolean
    risk?: 'low' | 'medium' | 'high'
  }) =>
    request<AppleShortcutRegistryRow>('/api/apple/shortcuts/registry', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  updateAppleShortcutRegistry: (
    id: number,
    body: Partial<{
      alias: string
      description: string
      allow_input: boolean
      requires_confirmation: boolean
      enabled: boolean
      risk: 'low' | 'medium' | 'high'
    }>,
  ) =>
    request<AppleShortcutRegistryRow>(`/api/apple/shortcuts/registry/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),
  deleteAppleShortcutRegistry: (id: number) =>
    request<{ status: string }>(`/api/apple/shortcuts/registry/${id}`, {
      method: 'DELETE',
    }),
  prepareAppleShortcutRun: (body: {
    name?: string
    alias?: string
    registry_id?: number
    input?: string
  }) =>
    request<AppleShortcutPlan>('/api/apple/shortcuts/prepare', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  confirmAppleShortcutRun: (planId: string) =>
    request<{ ok: boolean; shortcut_name: string; output: string; message: string }>(
      `/api/apple/shortcuts/${encodeURIComponent(planId)}/confirm`,
      { method: 'POST' },
    ),
  cancelAppleShortcutRun: (planId: string) =>
    request<{ ok: boolean; revoked: boolean }>(
      `/api/apple/shortcuts/${encodeURIComponent(planId)}`,
      { method: 'DELETE' },
    ),
  getAppleShortcutsRecipes: () =>
    request<{ recipes: AppleShortcutRecipe[]; count: number }>(
      '/api/apple/shortcuts/recipes',
    ),
  getAppleShortcutsRuns: (limit = 20) =>
    request<{ runs: AppleShortcutRunRow[]; count: number }>(
      `/api/apple/shortcuts/runs?limit=${limit}`,
    ),

  getFitnessDashboard: (date?: string) =>
    request(`/api/fitness/dashboard${date ? `?date=${encodeURIComponent(date)}` : ''}`),
  updateFitnessSession: (sessionId: number, body: Record<string, unknown>) =>
    request(`/api/fitness/sessions/${sessionId}/progress`, {
      method: 'PUT',
      body: JSON.stringify(body),
    }),
  addFitnessMeal: (body: Record<string, unknown>) =>
    request('/api/fitness/meals', { method: 'POST', body: JSON.stringify(body) }),
  addFitnessMealFromText: (body: Record<string, unknown>) =>
    request('/api/fitness/meals/from-text', { method: 'POST', body: JSON.stringify(body) }),
  addFitnessWater: (body: Record<string, unknown>) =>
    request('/api/fitness/water', { method: 'POST', body: JSON.stringify(body) }),
  addFitnessWeight: (body: Record<string, unknown>) =>
    request('/api/fitness/weights', { method: 'POST', body: JSON.stringify(body) }),
  getFitnessAdvice: (date?: string) =>
    request(`/api/fitness/advice${date ? `?date=${encodeURIComponent(date)}` : ''}`, {
      method: 'POST',
    }),
  updateFitnessProgram: (body: Record<string, unknown>) =>
    request('/api/fitness/program', { method: 'PATCH', body: JSON.stringify(body) }),
  updateFitnessProgramSession: (sessionId: number, body: Record<string, unknown>) =>
    request(`/api/fitness/program/sessions/${sessionId}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),
  getFitnessWeights: (limit = 52) => request(`/api/fitness/weights?limit=${limit}`),

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
    request<RecordingListResponse>(
      `/api/recordings${limit != null ? `?limit=${encodeURIComponent(String(limit))}` : ''}`,
    ),
  getRecording: (id: number) => request<RecordingDetail>(`/api/recordings/${id}`),
  startRecordingSession: (body: {
    client_recording_id: string
    label: string
    conversation_id?: number
  }) => request<RecordingSessionStatus>('/api/recording-sessions', {
    method: 'POST',
    body: JSON.stringify({ ...body, protocol_version: 1 }),
    offline: { queue: false },
  }),
  getRecordingSession: (sessionId: string) =>
    request<RecordingSessionStatus>(`/api/recording-sessions/${encodeURIComponent(sessionId)}`, {
      offline: { cache: false },
    }),
  uploadRecordingChunk: (
    sessionId: string,
    sequence: number,
    blob: Blob,
    sha256: string,
    durationMs: number,
  ) => request<RecordingChunkAck>(
    `/api/recording-sessions/${encodeURIComponent(sessionId)}/chunks/${encodeURIComponent(String(sequence))}`,
    {
      method: 'PUT',
      body: blob,
      headers: {
        'Content-Type': blob.type || 'audio/webm',
        'X-Chunk-SHA256': sha256,
        'X-Chunk-Duration-Ms': String(Math.max(1, Math.round(durationMs))),
        'X-Recording-Protocol-Version': '1',
      },
      offline: { queue: false },
    },
  ),
  completeRecordingSession: (
    sessionId: string,
    body: { expected_chunks: number; duration_seconds?: number },
  ) => request<RecordingSessionStatus>(
    `/api/recording-sessions/${encodeURIComponent(sessionId)}/complete`,
    {
      method: 'POST',
      body: JSON.stringify({ ...body, protocol_version: 1 }),
      offline: { queue: false },
    },
  ),
  cancelRecordingSession: (sessionId: string) =>
    request<RecordingSessionStatus>(
      `/api/recording-sessions/${encodeURIComponent(sessionId)}`,
      { method: 'DELETE', offline: { queue: false } },
    ),
  retryRecordingSession: (sessionId: string) =>
    request<RecordingSessionStatus>(
      `/api/recording-sessions/${encodeURIComponent(sessionId)}/retry`,
      { method: 'POST', offline: { queue: false } },
    ),

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

  search: (q: string, signal?: AbortSignal) =>
    request<UnifiedSearchResponse>(`/api/search?q=${encodeURIComponent(q)}`, { signal }),
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
  getDocumentPrivacy: () =>
    request<DocumentPrivacyPolicy>('/api/privacy/documents'),
  setDocumentStrictLocal: (strictLocal: boolean) =>
    request<DocumentPrivacyPolicy & { ok: boolean }>('/api/privacy/documents', {
      method: 'PUT',
      body: JSON.stringify({ strict_local: strictLocal }),
    }),
  uploadToConversation: async (convId: number, file: File, cloudConsent = false) => {
    const form = new FormData()
    form.append('file', file)
    form.append('cloud_consent', String(cloudConsent))
    return request<ConversationUploadResult>(`/api/conversations/${convId}/upload`, {
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
    request<{ ok: boolean; message?: string; error?: string; status?: string }>(
      `/api/supervisor/sub/${encodeURIComponent(id)}/${encodeURIComponent(action)}`,
      { method: 'POST' },
    ),
}

/**
 * Port du superviseur. Doit rester aligné sur `config.SUPERVISOR_PORT` ; un
 * test Python le vérifie, parce qu'une divergence ne se verrait qu'à l'usage,
 * sous la forme d'un plan de contrôle qui se dit injoignable.
 */
export const SUPERVISOR_PORT = 9000

/**
 * La page est-elle servie par le superviseur lui-même ?
 *
 * Le plan de contrôle (`/api/supervisor/*`, `/ws/supervisor`) n'existe que sur
 * le processus superviseur, et le serveur y exige `Origin == Host` : une page
 * servie par le backend ne peut donc **pas** l'atteindre, ni en REST (la route
 * n'existe pas) ni en WebSocket (fermeture 4403). Le savoir avant d'appeler
 * évite d'afficher une panne inexistante.
 */
export function isServedBySupervisor(): boolean {
  if (typeof window === 'undefined') return false
  return window.location.port === String(SUPERVISOR_PORT)
}

/** Origine du superviseur, pour proposer le bon lien à l'utilisateur. */
export function supervisorOrigin(): string {
  if (typeof window === 'undefined') return `http://127.0.0.1:${SUPERVISOR_PORT}`
  const { protocol, hostname } = window.location
  return `${protocol}//${hostname}:${SUPERVISOR_PORT}`
}

/**
 * URL WebSocket du superviseur — **toujours en même origine**.
 *
 * Une version antérieure visait `hostname:9000` depuis une page servie par le
 * backend. Le serveur refuse ce flux (`browser_websocket_origin_allowed`, code
 * 4403) : la connexion échouait en silence et l'interface l'interprétait comme
 * un superviseur arrêté. Pointer ailleurs qu'en même origine ne peut pas
 * marcher tant que le contrôle d'origine tient — et il doit tenir.
 */
export function supervisorWsUrl(): string {
  if (typeof window === 'undefined') {
    return `ws://127.0.0.1:${SUPERVISOR_PORT}/ws/supervisor`
  }
  const p = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${p}//${window.location.host}/ws/supervisor`
}
