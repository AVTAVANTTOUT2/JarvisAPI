/**
 * Contrat : le WebSocket principal est toujours même-origine.
 * Régression couverte : URL codée en dur vers :8081 qui cassait le chat
 * lorsque l'app était servie par le supervisor (port 9000).
 */
import { afterEach, describe, expect, it, vi } from 'vitest'

import { resolveWsUrl, WS } from './websocket'

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

describe('WS lifecycle', () => {
  it('ignore la fermeture tardive d’une socket remplacée', () => {
    const instances: FakeWebSocket[] = []

    class FakeWebSocket {
      static readonly CONNECTING = 0
      static readonly OPEN = 1
      static readonly CLOSING = 2
      static readonly CLOSED = 3

      readonly sent: unknown[] = []
      readyState = FakeWebSocket.CONNECTING
      binaryType: BinaryType = 'blob'
      onopen: (() => void) | null = null
      onmessage: ((event: { data: unknown }) => void) | null = null
      onclose: (() => void) | null = null
      onerror: (() => void) | null = null

      constructor(readonly url: string) {
        instances.push(this)
      }

      send(data: unknown) {
        this.sent.push(data)
      }

      close() {
        this.readyState = FakeWebSocket.CLOSED
      }

      emitOpen() {
        this.readyState = FakeWebSocket.OPEN
        this.onopen?.()
      }

      emitClose() {
        this.readyState = FakeWebSocket.CLOSED
        this.onclose?.()
      }
    }

    mockLocation('http:', 'localhost:9000')
    vi.stubGlobal('WebSocket', FakeWebSocket)

    const client = new WS()
    client.connect()
    const first = instances[0]

    client.disconnect()
    client.connect()
    const second = instances[1]

    first.emitClose()
    second.emitOpen()

    expect(client.sendText('Toujours connecté')).toBe(true)
    expect(second.sent).toEqual([
      JSON.stringify({
        type: 'text',
        content: 'Toujours connecté',
        stream: true,
        tts: false,
      }),
    ])

    client.disconnect()
  })
})
