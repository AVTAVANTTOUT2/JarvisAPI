import { getActiveProfileId, getCsrfToken } from '@jarvis/auth'

export const API_BASE = ''

/** Transport HTTP unique, sans politique de cache ni de reprise. */
export function jarvisRawFetch(path: string, options?: RequestInit): Promise<Response> {
  const root = API_BASE.replace(/\/$/, '')
  const p = path.startsWith('/') ? path : `/${path}`
  const headers = new Headers(options?.headers)
  if (!headers.has('X-Jarvis-Profile')) headers.set('X-Jarvis-Profile', getActiveProfileId())
  if (options?.body && !(options.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  const method = (options?.method ?? 'GET').toUpperCase()
  const csrfToken = getCsrfToken()
  if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method) && csrfToken) {
    headers.set('X-CSRF-Token', csrfToken)
  }
  return fetch(`${root}${p}`, { ...options, credentials: 'include', headers })
}
