import { afterEach, describe, expect, it, vi } from 'vitest'

import { AuthClient, AuthError, getCsrfToken, setCsrfToken } from '@jarvis/auth'
import { api } from '@unified/lib/api'

afterEach(() => {
  setCsrfToken(null)
  vi.unstubAllGlobals()
})

describe('AuthClient', () => {
  it('uses same-origin credentials for the complete auth contract', async () => {
    const fetchImpl = vi.fn(async () =>
      new Response(JSON.stringify({ ok: true, csrf_token: 'csrf-from-session' }), { status: 200 }),
    )
    const client = new AuthClient({ fetchImpl })

    await client.setup('123456')
    await client.unlock('123456')
    await client.verify('123456')
    await client.logout()

    const calls = fetchImpl.mock.calls as unknown as Array<[RequestInfo | URL, RequestInit?]>
    expect(calls.map((call) => call[0])).toEqual([
      '/api/auth/setup', '/api/auth/unlock', '/api/auth/verify', '/api/auth/logout',
    ])
    for (const [, init] of fetchImpl.mock.calls) {
      expect(init?.credentials).toBe('include')
    }
    expect(new Headers(calls[1][1]?.headers).get('X-CSRF-Token')).toBe('csrf-from-session')
    expect(new Headers(calls[3][1]?.headers).get('X-CSRF-Token')).toBe('csrf-from-session')
    expect(getCsrfToken()).toBeNull()
  })

  it('raises a typed error and reports an expired session', async () => {
    const onUnauthorized = vi.fn()
    const client = new AuthClient({
      fetchImpl: vi.fn(async () => new Response('expired', { status: 401 })),
      onUnauthorized,
    })

    await expect(client.verify('wrong')).rejects.toEqual(expect.objectContaining<AuthError>({ status: 401 }))
    expect(onUnauthorized).toHaveBeenCalledOnce()
  })
})

describe('shared API client', () => {
  it('keeps authenticated uploads on the common network wrapper', async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }))
    vi.stubGlobal('fetch', fetchImpl)
    setCsrfToken('csrf-upload')

    await api.uploadFile(new File(['content'], 'note.txt'))

    const [, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit]
    expect(init.credentials).toBe('include')
    expect(init.body).toBeInstanceOf(FormData)
    expect(new Headers(init.headers).has('Content-Type')).toBe(false)
    expect(new Headers(init.headers).get('X-CSRF-Token')).toBe('csrf-upload')
  })

  it('sends document cloud consent as an explicit per-upload form field', async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({
      ok: true,
      processing_mode: 'cloud_anonymized',
      data_left_device: true,
    }), { status: 200 }))
    vi.stubGlobal('fetch', fetchImpl)
    setCsrfToken('csrf-document')

    await api.uploadToConversation(42, new File(['private'], 'note.txt'), true)

    const [path, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit]
    const form = init.body as FormData
    expect(path).toBe('/api/conversations/42/upload')
    expect(form.get('cloud_consent')).toBe('true')
    expect((form.get('file') as File).name).toBe('note.txt')
    expect(init.credentials).toBe('include')
    expect(new Headers(init.headers).get('X-CSRF-Token')).toBe('csrf-document')
  })

  it('keeps strict-local configuration separate from per-upload consent', async () => {
    const fetchImpl = vi.fn(async () =>
      new Response(JSON.stringify({
        ok: true,
        mode: 'hybrid',
        strict_local: false,
        cloud_summary_available: true,
      }), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchImpl)
    setCsrfToken('csrf-settings')

    await api.getDocumentPrivacy()
    await api.setDocumentStrictLocal(false)

    const calls = fetchImpl.mock.calls as unknown as Array<[string, RequestInit]>
    expect(calls[0][0]).toBe('/api/privacy/documents')
    expect(calls[1][0]).toBe('/api/privacy/documents')
    expect(calls[1][1].method).toBe('PUT')
    expect(calls[1][1].body).toBe(JSON.stringify({ strict_local: false }))
    expect(new Headers(calls[1][1].headers).get('X-CSRF-Token')).toBe('csrf-settings')
  })
})
