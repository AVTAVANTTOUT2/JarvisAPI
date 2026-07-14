export interface AuthStatus {
  configured: boolean
  authenticated: boolean
  locked_out: boolean
  lockout_seconds: number
  auto_lock_minutes: number
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
    const response = await this.fetchImpl(`${this.baseUrl}${path}`, {
      ...init,
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        ...init?.headers,
      },
    })

    if (!response.ok) {
      const body = await response.text().catch(() => '')
      if (response.status === 401 && path !== '/api/auth/unlock') {
        this.onUnauthorized()
      }
      throw new AuthError(`Auth request failed (${response.status})`, response.status, body)
    }

    return (await response.json()) as T
  }

  status(): Promise<AuthStatus> {
    return this.request<AuthStatus>('/api/auth/status')
  }

  setup(secret: string): Promise<{ ok: boolean }> {
    return this.request('/api/auth/setup', {
      method: 'POST',
      body: JSON.stringify({ secret }),
    })
  }

  unlock(secret: string): Promise<{ ok: boolean }> {
    return this.request('/api/auth/unlock', {
      method: 'POST',
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
