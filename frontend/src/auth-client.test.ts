import { describe, expect, it, vi } from 'vitest'

import { AuthClient, AuthError } from '@jarvis/auth'
import { api } from '@unified/lib/api'

describe('AuthClient', () => {
  it('uses same-origin credentials for the complete auth contract', async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }))
    const client = new AuthClient({ fetchImpl })

    await client.setup('1234')
    await client.unlock('1234')
    await client.verify('1234')
    await client.logout()

    const calls = fetchImpl.mock.calls as unknown as Array<[RequestInfo | URL, RequestInit?]>
    expect(calls.map((call) => call[0])).toEqual([
      '/api/auth/setup', '/api/auth/unlock', '/api/auth/verify', '/api/auth/logout',
    ])
    for (const [, init] of fetchImpl.mock.calls) {
      expect(init?.credentials).toBe('include')
    }
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

    await api.uploadFile(new File(['content'], 'note.txt'))

    const [, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit]
    expect(init.credentials).toBe('include')
    expect(init.body).toBeInstanceOf(FormData)
    expect(init.headers).not.toHaveProperty('Content-Type')
    vi.unstubAllGlobals()
  })
})
