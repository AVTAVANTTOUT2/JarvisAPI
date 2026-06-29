/**
 * WebSocket JARVIS — singleton.
 * En dev (Vite) : `ws(s)://<host>/ws` est proxifié vers le backend.
 * Sinon : `VITE_WS_URL` ou, en secours, `hostname:8081` (backend direct).
 */
export type WsHandler = (data: Record<string, unknown> & { _type?: string }) => void

function resolveWsUrl(): string {
  const explicit = import.meta.env.VITE_WS_URL as string | undefined
  if (explicit) return explicit
  const p = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  // Proxy Vite : même host que la page
  if (import.meta.env.DEV) {
    return `${p}//${window.location.host}/ws`
  }
  // Prod sans proxy : backend sur 8081 par défaut (WEB_PORT)
  return `${p}//${window.location.hostname}:8081/ws`
}

export class WS {
  private ws: WebSocket | null = null
  private handlers = new Map<string, WsHandler[]>()
  private starHandlers: WsHandler[] = []
  private binaryHandler: ((blob: Blob) => void) | null = null
  private reconnectDelay = 1000
  private maxReconnectDelay = 10000
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private shouldReconnect = true

  public conversationId: number | null = null
  private _connected = false

  get connected() {
    return this._connected
  }

  /** Prêt à envoyer (binaire ou JSON). */
  isSocketOpen(): boolean {
    return this.ws !== null && this.ws.readyState === WebSocket.OPEN
  }

  connect() {
    if (this.ws?.readyState === WebSocket.OPEN) return
    this.shouldReconnect = true
    this._open()
  }

  private _open() {
    this.clearReconnectTimer()
    try {
      this.ws = new WebSocket(resolveWsUrl())
    } catch (e) {
      console.error('[WS] connect', e)
      this.scheduleReconnect()
      return
    }
    this.ws.binaryType = 'blob'

    this.ws.onopen = () => {
      this._connected = true
      this.reconnectDelay = 1000
      this.emit('connection', { connected: true })
    }

    this.ws.onmessage = (e) => {
      if (e.data instanceof Blob) {
        this.binaryHandler?.(e.data)
        return
      }
      try {
        const d = JSON.parse(String(e.data)) as Record<string, unknown>
        const t = typeof d.type === 'string' ? d.type : ''
        if (t === 'connected' && typeof d.conversation_id === 'number') {
          this.conversationId = d.conversation_id
        }
        if (t) this.emit(t, d)
      } catch (err) {
        console.error('[WS] parse', err)
      }
    }

    this.ws.onclose = () => {
      this._connected = false
      this.ws = null
      this.emit('connection', { connected: false })
      if (this.shouldReconnect) this.scheduleReconnect()
    }

    this.ws.onerror = () => this.ws?.close()
  }

  private clearReconnectTimer() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
  }

  private scheduleReconnect() {
    this.clearReconnectTimer()
    const d = this.reconnectDelay
    this.reconnectTimer = setTimeout(() => {
      this.reconnectDelay = Math.min(this.reconnectDelay * 2, this.maxReconnectDelay)
      this._open()
    }, d)
  }

  on(type: string, handler: WsHandler) {
    if (type === '*') {
      this.starHandlers.push(handler)
      return () => {
        this.starHandlers = this.starHandlers.filter((h) => h !== handler)
      }
    }
    if (!this.handlers.has(type)) this.handlers.set(type, [])
    this.handlers.get(type)!.push(handler)
    return () => {
      const a = this.handlers.get(type)
      if (a) this.handlers.set(
        type,
        a.filter((h) => h !== handler),
      )
    }
  }

  onBinary(handler: (blob: Blob) => void) {
    this.binaryHandler = handler
    return () => {
      if (this.binaryHandler === handler) this.binaryHandler = null
    }
  }

  private emit(type: string, data: Record<string, unknown>) {
    this.handlers.get(type)?.forEach((h) => h(data))
    const starData = { ...data, _type: type }
    this.starHandlers.forEach((h) => h(starData))
  }

  sendText(content: string, stream = true, tts = false) {
    if (!this.isSocketOpen()) return false
    this.ws!.send(JSON.stringify({ type: 'text', content, stream, tts }))
    return true
  }

  sendBinary(buffer: ArrayBuffer) {
    if (!this.isSocketOpen()) return false
    this.ws!.send(buffer)
    return true
  }

  send(data: object) {
    if (!this.isSocketOpen()) return false
    this.ws!.send(JSON.stringify(data))
    return true
  }

  disconnect() {
    this.shouldReconnect = false
    this.clearReconnectTimer()
    this.ws?.close()
    this.ws = null
    this._connected = false
  }
}

export const ws = new WS()
/** Compat code existant (`JarvisContext`, Chat, Voice). */
export const jarvisWs = ws
