export interface UserProfile {
  id: string
  display_name: string
  is_active: number
  created_at: string
  last_used_at: string | null
}

export interface ProfileListResponse {
  active_profile: string
  profiles: UserProfile[]
}

export interface AuthStatus {
  configured: boolean
  authenticated: boolean
  csrf_token: string | null
  locked_out: boolean
  lockout_seconds: number
  lockout_scope: 'client' | 'global' | null
  local_recovery_available: boolean
  auto_lock_minutes: number
  profile?: UserProfile | null
}

let activeCsrfToken: string | null = null
const PROFILE_STORAGE_KEY = 'jarvis:profile'
const DEFAULT_PROFILE_ID = 'default'
const PROFILE_ID_RE = /^[a-z0-9][a-z0-9_-]{0,31}$/

function readActiveProfileId(): string {
  if (typeof window === 'undefined') return DEFAULT_PROFILE_ID
  try {
    const stored = window.localStorage.getItem(PROFILE_STORAGE_KEY) ?? ''
    return PROFILE_ID_RE.test(stored) ? stored : DEFAULT_PROFILE_ID
  } catch {
    return DEFAULT_PROFILE_ID
  }
}

let activeProfileId = readActiveProfileId()

export function getActiveProfileId(): string {
  return activeProfileId
}

export function setActiveProfileId(profileId: string): void {
  const normalized = profileId.trim().toLowerCase()
  if (!PROFILE_ID_RE.test(normalized)) throw new Error('Identifiant de profil invalide')
  activeProfileId = normalized
  setCsrfToken(null)
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(PROFILE_STORAGE_KEY, normalized)
  } catch {
    // Le contexte mémoire reste utilisable si le stockage navigateur est refusé.
  }
  const secure = window.location.protocol === 'https:' ? '; Secure' : ''
  document.cookie = `jarvis_profile=${encodeURIComponent(normalized)}; Path=/; SameSite=Strict${secure}`
}

export function setCsrfToken(token: string | null | undefined): void {
  activeCsrfToken = token || null
}

export function getCsrfToken(): string | null {
  return activeCsrfToken
}

function isUnsafeMethod(method?: string): boolean {
  return ['POST', 'PUT', 'PATCH', 'DELETE'].includes((method ?? 'GET').toUpperCase())
}

export interface AuthClientOptions {
  baseUrl?: string
  fetchImpl?: typeof fetch
  onUnauthorized?: () => void
}

export class AuthError extends Error {
  readonly status: number
  readonly body: string

  constructor(message: string, status: number, body = '') {
    super(message)
    this.name = 'AuthError'
    this.status = status
    this.body = body
  }
}

function dispatchAuthRequired(): void {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('jarvis:auth-required'))
  }
}

/** Client HTTP partagé par tous les frontends JARVIS. */
export class AuthClient {
  private readonly baseUrl: string
  private readonly fetchImpl: typeof fetch
  private readonly onUnauthorized: () => void

  constructor(options: AuthClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? '').replace(/\/$/, '')
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis)
    this.onUnauthorized = options.onUnauthorized ?? dispatchAuthRequired
  }

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const headers = new Headers(init?.headers)
    if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
    if (!headers.has('X-Jarvis-Profile')) headers.set('X-Jarvis-Profile', activeProfileId)
    if (isUnsafeMethod(init?.method) && activeCsrfToken && !headers.has('X-CSRF-Token')) {
      headers.set('X-CSRF-Token', activeCsrfToken)
    }
    const response = await this.fetchImpl(`${this.baseUrl}${path}`, {
      ...init,
      credentials: 'include',
      headers,
    })

    if (!response.ok) {
      const body = await response.text().catch(() => '')
      if (response.status === 401 && path !== '/api/auth/unlock') {
        this.onUnauthorized()
      }
      throw new AuthError(`Auth request failed (${response.status})`, response.status, body)
    }

    const data = (await response.json()) as T & { csrf_token?: string | null }
    if (path === '/api/auth/status' || typeof data.csrf_token === 'string') {
      setCsrfToken(data.csrf_token)
    }
    if (path === '/api/auth/logout') setCsrfToken(null)
    return data
  }

  status(): Promise<AuthStatus> {
    return this.request<AuthStatus>('/api/auth/status')
  }

  profiles(): Promise<ProfileListResponse> {
    return this.request('/api/auth/profiles')
  }

  createProfile(displayName: string): Promise<{ profile: UserProfile }> {
    return this.request('/api/auth/profiles', {
      method: 'POST',
      body: JSON.stringify({ display_name: displayName }),
    })
  }

  deactivateProfile(profileId: string): Promise<{ ok: boolean }> {
    return this.request(`/api/auth/profiles/${encodeURIComponent(profileId)}/deactivate`, {
      method: 'POST',
    })
  }

  selectProfile(profileId: string): void {
    setActiveProfileId(profileId)
  }

  setup(secret: string): Promise<{ ok: boolean; csrf_token: string }> {
    return this.request('/api/auth/setup', {
      method: 'POST',
      body: JSON.stringify({ secret }),
    })
  }

  unlock(secret: string): Promise<{ ok: boolean; csrf_token: string }> {
    return this.request('/api/auth/unlock', {
      method: 'POST',
      body: JSON.stringify({ secret }),
    })
  }

  localUnlock(secret: string): Promise<{ ok: boolean; recovered: boolean; csrf_token: string }> {
    return this.request('/api/auth/local-unlock', {
      method: 'POST',
      headers: { 'X-Jarvis-Local-Recovery': '1' },
      body: JSON.stringify({ secret }),
    })
  }

  verify(secret: string): Promise<{ ok: boolean }> {
    return this.request('/api/auth/verify', {
      method: 'POST',
      body: JSON.stringify({ secret }),
    })
  }

  logout(): Promise<{ ok: boolean }> {
    return this.request('/api/auth/logout', { method: 'POST' })
  }
}

export const authClient = new AuthClient()
