/**
 * Contrat : le WebSocket principal est toujours même-origine.
 * Régression couverte : URL codée en dur vers :8081 qui cassait le chat
 * lorsque l'app était servie par le supervisor (port 9000).
 */
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  CONVERSATION_CHECKPOINT_STORAGE_KEY,
  resolveWsUrl,
  WS,
} from './websocket'

function mockLocation(protocol: string, host: string) {
  const values = new Map<string, string>()
  const localStorage = {
    get length() { return values.size },
    clear: () => values.clear(),
    getItem: (key: string) => values.get(key) ?? null,
    key: (index: number) => Array.from(values.keys())[index] ?? null,
    removeItem: (key: string) => values.delete(key),
    setItem: (key: string, value: string) => values.set(key, value),
  } as Storage
  vi.stubGlobal('window', {
    ...globalThis.window,
    location: { protocol, host, hostname: host.split(':')[0] } as Location,
    localStorage,
  })
}

afterEach(() => {
  try {
    window.localStorage.clear()
  } catch {
    // Certains stubs de test n'exposent pas le stockage navigateur.
  }
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

  it('transmet le checkpoint durable pendant la reconnexion', () => {
    mockLocation('https:', 'jarvis.local:8081')
    const checkpoint = '7cd42b5e-f035-4d9c-8a0f-d33a7cbfb5c2'
    expect(resolveWsUrl(checkpoint)).toBe(
      `wss://jarvis.local:8081/ws?checkpoint_id=${checkpoint}`,
    )
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

      emitMessage(data: Record<string, unknown>) {
        this.onmessage?.({ data: JSON.stringify(data) })
      }
    }

    mockLocation('http:', 'localhost:9000')
    vi.stubGlobal('WebSocket', FakeWebSocket)

    const client = new WS()
    client.connect()
    const first = instances[0]
    const checkpoint = '7cd42b5e-f035-4d9c-8a0f-d33a7cbfb5c2'
    first.emitOpen()
    first.emitMessage({
      type: 'connected',
      conversation_id: 42,
      checkpoint_id: checkpoint,
    })
    expect(window.localStorage.getItem(CONVERSATION_CHECKPOINT_STORAGE_KEY)).toBe(checkpoint)

    client.disconnect()
    client.connect()
    const second = instances[1]
    expect(second.url).toBe(`ws://localhost:9000/ws?checkpoint_id=${checkpoint}`)

    first.emitClose()
    second.emitOpen()

    expect(client.sendText('Toujours connecté')).toBe(true)
    expect(second.sent).toEqual([
      JSON.stringify({
        type: 'text',
        content: 'Toujours connecté',
        stream: true,
        tts: false,
        checkpoint_id: checkpoint,
      }),
    ])

    client.disconnect()
  })
})
