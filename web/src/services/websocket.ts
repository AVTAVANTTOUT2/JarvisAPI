/**
 * WebSocket JARVIS — singleton.
 * En dev Next : `ws(s)://<host>/ws` est relayé vers le backend.
 * En production : même origine que la page — FastAPI (8081) expose /ws nativement
 * et le supervisor (9000) relaie /ws vers le backend. Aucun port codé en dur.
 */
export type WsHandler = (data: Record<string, unknown> & { _type?: string }) => void

export const CONVERSATION_CHECKPOINT_STORAGE_KEY = 'jarvis.activeConversationCheckpoint'

function isCheckpointId(value: unknown): value is string {
  return typeof value === 'string'
    && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value)
}

function storedCheckpointId(): string | null {
  try {
    const value = window.localStorage.getItem(CONVERSATION_CHECKPOINT_STORAGE_KEY)
    if (isCheckpointId(value)) return value.toLowerCase()
    if (value !== null) window.localStorage.removeItem(CONVERSATION_CHECKPOINT_STORAGE_KEY)
    return null
  } catch {
    return null
  }
}

function createCheckpointId(): string {
  if (typeof globalThis.crypto?.randomUUID === 'function') {
    return globalThis.crypto.randomUUID()
  }
  const bytes = new Uint8Array(16)
  globalThis.crypto.getRandomValues(bytes)
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const value = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
  return `${value.slice(0, 8)}-${value.slice(8, 12)}-${value.slice(12, 16)}-${value.slice(16, 20)}-${value.slice(20)}`
}

export function resolveWsUrl(checkpointId?: string | null): string {
  const p = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const url = new URL(`${p}//${window.location.host}/ws`)
  if (isCheckpointId(checkpointId)) url.searchParams.set('checkpoint_id', checkpointId)
  return url.toString()
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
  public checkpointId: string | null = null
  private _connected = false

  get connected() {
    return this._connected
  }

  /** Prêt à envoyer (binaire ou JSON). */
  isSocketOpen(): boolean {
    return this.ws !== null && this.ws.readyState === WebSocket.OPEN
  }

  connect() {
    if (
      this.ws?.readyState === WebSocket.OPEN
      || this.ws?.readyState === WebSocket.CONNECTING
    ) return
    this.shouldReconnect = true
    this._open()
  }

  private _open() {
    this.clearReconnectTimer()
    this.checkpointId = this.checkpointId || storedCheckpointId()
    let socket: WebSocket
    try {
      socket = new WebSocket(resolveWsUrl(this.checkpointId))
      this.ws = socket
    } catch (e) {
      console.error('[WS] connect', e)
      this.scheduleReconnect()
      return
    }
    socket.binaryType = 'blob'

    socket.onopen = () => {
      if (this.ws !== socket) {
        socket.close()
        return
      }
      this._connected = true
      this.reconnectDelay = 1000
      this.emit('connection', { connected: true })
    }

    socket.onmessage = (e) => {
      if (this.ws !== socket) return
      if (e.data instanceof Blob) {
        this.binaryHandler?.(e.data)
        return
      }
      try {
        const d = JSON.parse(String(e.data)) as Record<string, unknown>
        const t = typeof d.type === 'string' ? d.type : ''
        if ((t === 'connected' || t === 'conversation_switched') && typeof d.conversation_id === 'number') {
          this.conversationId = d.conversation_id
          if (isCheckpointId(d.checkpoint_id)) {
            this.checkpointId = d.checkpoint_id.toLowerCase()
            try {
              window.localStorage.setItem(
                CONVERSATION_CHECKPOINT_STORAGE_KEY,
                this.checkpointId,
              )
            } catch {
              // Le chat reste fonctionnel si le stockage privé est indisponible.
            }
          }
        }
        if (t) this.emit(t, d)
      } catch (err) {
        console.error('[WS] parse', err)
      }
    }

    socket.onclose = () => {
      if (this.ws !== socket) return
      this._connected = false
      this.ws = null
      this.emit('connection', { connected: false })
      if (this.shouldReconnect) this.scheduleReconnect()
    }

    socket.onerror = () => socket.close()
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
    this.ws!.send(JSON.stringify({
      type: 'text',
      content,
      stream,
      tts,
      ...(this.checkpointId ? { checkpoint_id: this.checkpointId } : {}),
    }))
    return true
  }

  startNewConversation(): boolean {
    if (!this.isSocketOpen()) return false
    const checkpointId = createCheckpointId()
    this.ws!.send(JSON.stringify({ type: 'new_conversation', checkpoint_id: checkpointId }))
    this.conversationId = null
    this.checkpointId = checkpointId
    return true
  }

  switchConversation(conversationId: number, checkpointId: string): boolean {
    if (!this.isSocketOpen() || !isCheckpointId(checkpointId)) return false
    this.ws!.send(JSON.stringify({
      type: 'switch_conversation',
      conversation_id: conversationId,
      checkpoint_id: checkpointId,
    }))
    this.conversationId = conversationId
    this.checkpointId = checkpointId.toLowerCase()
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
    const socket = this.ws
    this.ws = null
    this._connected = false
    socket?.close()
  }
}

export const ws = new WS()
/** Compat code existant (`JarvisContext`, Chat, Voice). */
export const jarvisWs = ws
