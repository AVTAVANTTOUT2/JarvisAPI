/**
 * Contrat : le WebSocket principal est toujours même-origine.
 * Régression couverte : URL codée en dur vers :8081 qui cassait le chat
 * lorsque l'app était servie par le supervisor (port 9000).
 */
import { afterEach, describe, expect, it, vi } from 'vitest'

import { resolveWsUrl } from './websocket'

function mockLocation(protocol: string, host: string) {
  vi.stubGlobal('window', {
    ...globalThis.window,
    location: { protocol, host, hostname: host.split(':')[0] } as Location,
  })
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('resolveWsUrl', () => {
  it('utilise la même origine que la page (supervisor 9000)', () => {
    mockLocation('http:', 'localhost:9000')
    expect(resolveWsUrl()).toBe('ws://localhost:9000/ws')
  })

  it('utilise la même origine que la page (backend 8081)', () => {
    mockLocation('http:', 'localhost:8081')
    expect(resolveWsUrl()).toBe('ws://localhost:8081/ws')
  })

  it('bascule en wss: sur une page HTTPS', () => {
    mockLocation('https:', 'jarvis.local:8081')
    expect(resolveWsUrl()).toBe('wss://jarvis.local:8081/ws')
  })

  it("ne contient jamais de port codé en dur différent de l'origine", () => {
    mockLocation('http:', 'localhost:9000')
    expect(resolveWsUrl()).not.toContain(':8081')
  })
})
