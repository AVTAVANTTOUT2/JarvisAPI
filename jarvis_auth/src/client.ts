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

let activeCsrfToken: string | null = null

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
