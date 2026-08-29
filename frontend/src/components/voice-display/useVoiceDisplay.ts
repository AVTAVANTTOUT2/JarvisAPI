'use client'

import { useEffect, useReducer, useState } from 'react'
import { jarvisRawFetch } from '@unified/lib/http'
import { initialVoiceHudState, voiceHudReducer } from './state'
import type { VoiceDisplayEvent, VoiceDisplaySnapshot } from './types'

function websocketUrl(sequence: number): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/ws/voice-display?since=${sequence}`
}

export function useVoiceDisplay() {
  const [state, dispatch] = useReducer(voiceHudReducer, undefined, initialVoiceHudState)
  const [staleSeconds, setStaleSeconds] = useState(0)

  useEffect(() => {
    let disposed = false
    let socket: WebSocket | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let attempt = 0

    const connect = async () => {
      dispatch({ type: 'connection', value: 'connecting' })
      try {
        const response = await jarvisRawFetch('/api/voice-display/snapshot', {
          cache: 'no-store',
        })
        if (!response.ok) throw new Error(`snapshot:${response.status}`)
        const snapshot = await response.json() as VoiceDisplaySnapshot
        if (disposed) return
        dispatch({ type: 'snapshot', snapshot })
        socket = new WebSocket(websocketUrl(snapshot.session.last_sequence))
        socket.onopen = () => {
          attempt = 0
          dispatch({ type: 'connection', value: 'connected' })
        }
        socket.onmessage = (message) => {
          const packet = JSON.parse(String(message.data)) as Record<string, unknown>
          if (packet.type === 'voice.display.snapshot') {
            dispatch({ type: 'snapshot', snapshot: packet.snapshot as VoiceDisplaySnapshot })
          } else if (packet.type !== 'voice.display.heartbeat') {
            dispatch({ type: 'event', event: packet as VoiceDisplayEvent })
          }
        }
        socket.onclose = () => {
          if (disposed) return
          dispatch({ type: 'connection', value: 'disconnected', at: Date.now() })
          const delay = Math.min(8_000, 1_000 * 2 ** attempt++)
          reconnectTimer = setTimeout(() => { void connect() }, delay)
        }
        socket.onerror = () => socket?.close()
      } catch {
        if (disposed) return
        dispatch({ type: 'connection', value: 'disconnected', at: Date.now() })
        const delay = Math.min(8_000, 1_000 * 2 ** attempt++)
        reconnectTimer = setTimeout(() => { void connect() }, delay)
      }
    }

    const reconnectAfterWake = () => {
      if (!document.hidden && (!socket || socket.readyState > WebSocket.OPEN)) {
        if (reconnectTimer) clearTimeout(reconnectTimer)
        void connect()
      }
    }
    document.addEventListener('visibilitychange', reconnectAfterWake)
    void connect()
    return () => {
      disposed = true
      document.removeEventListener('visibilitychange', reconnectAfterWake)
      if (reconnectTimer) clearTimeout(reconnectTimer)
      socket?.close()
    }
  }, [])

  useEffect(() => {
    const timer = setInterval(() => {
      const since = state.disconnectedAt ?? state.lastEventAt
      setStaleSeconds(since ? Math.max(0, Math.floor((Date.now() - since) / 1_000)) : 0)
    }, 1_000)
    return () => clearInterval(timer)
  }, [state.disconnectedAt, state.lastEventAt])

  useEffect(() => {
    if (state.session.privacy_mode || state.session.state === 'idle') return
    const timer = setTimeout(
      () => dispatch({ type: 'privacy-timeout' }),
      state.privacyTimeoutSeconds * 1_000,
    )
    return () => clearTimeout(timer)
  }, [state.privacyTimeoutSeconds, state.session.privacy_mode, state.session.state, state.session.updated_at])

  return { state, staleSeconds }
}
