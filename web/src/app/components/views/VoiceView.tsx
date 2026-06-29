/**
 * VoiceView — Mode mains libres JARVIS.
 *
 * Pipeline : getUserMedia → AnalyserNode (VAD local) → MediaRecorder (WebM/Opus)
 * → ws.sendBinary → backend STT/LLM/TTS → ws binary MP3 → Audio playback.
 *
 * Anti-écho strict : micro désactivé pendant processing + speaking.
 * Le cycle reprend après done_playing envoyé au serveur.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { NavLink } from 'react-router-dom'
import { MessageSquare, Mic, MicOff, Settings, Square, Volume2, Headphones } from 'lucide-react'
import { ws } from '@/services/websocket'
import { api, type AudioDaemonStatus } from '@/services/api'

type SessionPhase = 'idle' | 'listening' | 'processing' | 'speaking'
type MicPermissionState = 'idle' | 'pending' | 'granted' | 'denied'

const SILENCE_DURATION_MS = 1500
const MIN_SPEECH_DURATION_MS = 800
const VOLUME_THRESHOLD = 0.015
const INTERRUPT_THRESHOLD = 0.035
const INTERRUPT_DURATION_MS = 300
const MAX_UTTERANCE_MS = 7000
const TTS_ENGINES = ['kokoro', 'edge', 'elevenlabs', 'macos'] as const

function getAudioContextCtor(): (typeof AudioContext) | null {
  const w = window as typeof window & { webkitAudioContext?: typeof AudioContext }
  return window.AudioContext ?? w.webkitAudioContext ?? null
}

/** Ordre : Opus WebM → WebM → MP4 (Safari). */
function pickRecorderMime(): string | undefined {
  const candidates = [
    'audio/webm;codecs=opus',
    'audio/webm',
    'audio/mp4',
  ]
  for (const m of candidates) {
    if (typeof MediaRecorder !== 'undefined' && MediaRecorder.isTypeSupported(m)) {
      return m
    }
  }
  return undefined
}

let _lastVadLogMs = 0
function logVadVolumeThrottled(rms: number) {
  const now = performance.now()
  if (now - _lastVadLogMs < 400) return
  _lastVadLogMs = now
  if (import.meta.env.DEV) console.log(`[VAD] Volume détecté: ${rms.toFixed(5)}`)
}

export function VoiceView() {
  const [phase, setPhase] = useState<SessionPhase>('idle')
  const [transcript, setTranscript] = useState('')
  const [response, setResponse] = useState('')
  const [volume, setVolume] = useState(0)
  const [ttsEngine, setTtsEngine] = useState('edge')
  const [ttsLoading, setTtsLoading] = useState(false)

  const [sessionError, setSessionError] = useState<string | null>(null)
  const [micPermission, setMicPermission] = useState<MicPermissionState>('idle')
  const [debugOpen, setDebugOpen] = useState(true)
  const [debugWsConnected, setDebugWsConnected] = useState(false)
  const [debugLastWsEvent, setDebugLastWsEvent] = useState('—')
  const [debugLastBlobSent, setDebugLastBlobSent] = useState('—')
  const [debugSttRaw, setDebugSttRaw] = useState('—')
  const [debugSttClean, setDebugSttClean] = useState('—')
  const [debugVadByte, setDebugVadByte] = useState(0)
  const [audioCtxStateLabel, setAudioCtxStateLabel] = useState('—')

  // ── Audio Daemon state ──
  const [daemon, setDaemon] = useState<AudioDaemonStatus>({
    enabled: false, state: 'idle', wake_word_enabled: false,
    continuous_mode: false, last_interaction: 0, stt_engine: 'none', tts_engine: 'macos', has_porcupine: false,
  })
  const [daemonLoading, setDaemonLoading] = useState(false)

  const fetchDaemonStatus = useCallback(async () => {
    try {
      const s = await api.getAudioDaemonStatus()
      setDaemon(s)
    } catch { /* ignore */ }
  }, [])

  const phaseRef = useRef<SessionPhase>('idle')
  const streamRef = useRef<MediaStream | null>(null)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const audioCtxRef = useRef<AudioContext | null>(null)
  const analyserRef = useRef<AnalyserNode | null>(null)
  const rafRef = useRef<number>(0)
  const audioPlayerRef = useRef<HTMLAudioElement | null>(null)
  const audioChunksRef = useRef<Blob[]>([])
  const audioMimeRef = useRef('audio/mpeg')
  const hasSpokeRef = useRef(false)
  const silenceStartRef = useRef<number>(0)
  const activeRef = useRef(false)
  const responseAccRef = useRef('')
  const preferredRecorderMimeRef = useRef<string | undefined>(undefined)
  const lastChunkDebugLogRef = useRef(0)
  const debugVadUiRef = useRef(0)
  const interruptStartRef = useRef<number>(0)
  const speechStartRef = useRef<number>(0)
  const recordingStartedAtRef = useRef<number>(0)

  const updatePhase = useCallback((p: SessionPhase) => {
    phaseRef.current = p
    setPhase(p)
  }, [])

  const syncCtxStateLabel = useCallback(() => {
    const c = audioCtxRef.current
    setAudioCtxStateLabel(c ? c.state : '—')
  }, [])

  // --- TTS engine selector ---
  useEffect(() => {
    api.getTTSSetting()
      .then((r) => setTtsEngine(r.engine))
      .catch(() => {})
  }, [])

  // --- Audio Daemon status ---
  useEffect(() => {
    fetchDaemonStatus()
    // Polling backup toutes les 15s
    const id = window.setInterval(fetchDaemonStatus, 15000)
    return () => clearInterval(id)
  }, [fetchDaemonStatus])

  const handleTtsChange = useCallback(async (engine: string) => {
    setTtsLoading(true)
    try {
      await api.setTTSSetting(engine)
      setTtsEngine(engine)
    } catch { /* ignore */ }
    setTtsLoading(false)
  }, [])

  // ── Audio Daemon toggles ──
  const handleDaemonToggle = useCallback(async () => {
    setDaemonLoading(true)
    try {
      if (daemon.enabled) {
        await api.stopAudioDaemon()
      } else {
        await api.startAudioDaemon()
      }
      await fetchDaemonStatus()
    } catch { /* ignore */ }
    setDaemonLoading(false)
  }, [daemon.enabled, fetchDaemonStatus])

  const handleDaemonWakeWord = useCallback(async () => {
    setDaemonLoading(true)
    try {
      await api.setWakeWord(!daemon.wake_word_enabled)
      await fetchDaemonStatus()
    } catch { /* ignore */ }
    setDaemonLoading(false)
  }, [daemon.wake_word_enabled, fetchDaemonStatus])

  const handleDaemonContinuous = useCallback(async () => {
    setDaemonLoading(true)
    try {
      await api.setContinuousMode(!daemon.continuous_mode)
      await fetchDaemonStatus()
    } catch { /* ignore */ }
    setDaemonLoading(false)
  }, [daemon.continuous_mode, fetchDaemonStatus])

  // --- WebSocket diagnostic + état connexion ---
  useEffect(() => {
    const offConn = ws.on('connection', (d) => {
      setDebugWsConnected(Boolean(d.connected))
    })
    const offStar = ws.on('*', (d) => {
      const t = typeof d._type === 'string' ? d._type : ''
      if (!t) return
      const ts = new Date().toLocaleTimeString('fr-FR', { hour12: false })
      if (t === 'chunk') {
        const now = Date.now()
        if (now - lastChunkDebugLogRef.current < 900) return
        lastChunkDebugLogRef.current = now
        setDebugLastWsEvent(`chunk (stream) @ ${ts}`)
        return
      }
      setDebugLastWsEvent(`${t} @ ${ts}`)
    })
    setDebugWsConnected(ws.isSocketOpen())
    return () => {
      offConn()
      offStar()
    }
  }, [])

  useEffect(() => {
    if (!debugOpen) return
    const id = window.setInterval(() => syncCtxStateLabel(), 300)
    return () => clearInterval(id)
  }, [debugOpen, syncCtxStateLabel])

  const safeSendJson = useCallback((payload: object, context: string): boolean => {
    if (!ws.isSocketOpen()) {
      const msg = 'WebSocket non connecté'
      console.error(`[WS] ${context}: ${msg}`)
      setSessionError(msg)
      return false
    }
    const ok = ws.send(payload)
    if (!ok) {
      console.error(`[WS] ${context}: envoi refusé`)
      setSessionError('Échec envoi WebSocket')
    }
    return ok
  }, [])

  const safeSendBinary = useCallback((buf: ArrayBuffer, context: string): boolean => {
    if (!ws.isSocketOpen()) {
      const msg = 'WebSocket non connecté'
      console.error(`[WS] ${context}: ${msg}`)
      setSessionError(msg)
      return false
    }
    const ok = ws.sendBinary(buf)
    if (!ok) {
      console.error(`[WS] ${context}: envoi binaire refusé`)
      setSessionError('Échec envoi audio (WebSocket)')
    }
    return ok
  }, [])

  // --- WS event listeners ---
  useEffect(() => {
    const offs: Array<() => void> = []

    offs.push(ws.on('conversation_started', () => {
      if (!activeRef.current) return
      updatePhase('listening')
    }))

    offs.push(ws.on('transcript', (d) => {
      setTranscript(String(d.content ?? ''))
    }))

    offs.push(ws.on('voice_debug', (d) => {
      if (typeof d.blob_bytes === 'number') {
        const t = new Date().toLocaleTimeString('fr-FR', { hour12: false })
        setDebugLastBlobSent(`${t} · ${d.blob_bytes} o`)
      }
      setDebugSttRaw(String(d.stt_raw ?? '—'))
      setDebugSttClean(String(d.stt_clean ?? '—'))
    }))

    offs.push(ws.on('processing', () => {
      if (!activeRef.current) return
      updatePhase('processing')
      stopRecording()
    }))

    offs.push(ws.on('classification', () => {
      if (!activeRef.current) return
      updatePhase('processing')
    }))

    offs.push(ws.on('chunk', (d) => {
      if (!activeRef.current) return
      const text = String(d.content ?? '')
      responseAccRef.current += text
      setResponse(responseAccRef.current)
    }))

    offs.push(ws.on('response', (d) => {
      if (!activeRef.current) return
      responseAccRef.current = String(d.content ?? '')
      setResponse(responseAccRef.current)
    }))

    offs.push(ws.on('response_followup', (d) => {
      if (!activeRef.current) return
      responseAccRef.current = String(d.content ?? '')
      setResponse(responseAccRef.current)
    }))

    offs.push(ws.on('response_clean', (d) => {
      if (!activeRef.current) return
      responseAccRef.current = String(d.content ?? '')
      setResponse(responseAccRef.current)
    }))

    offs.push(ws.on('done', () => {
      if (!activeRef.current) return
      if (phaseRef.current !== 'speaking' && phaseRef.current !== 'processing') {
        updatePhase('listening')
        if (activeRef.current) startRecording()
      }
    }))

    offs.push(ws.on('speaking', (d) => {
      if (!activeRef.current) return
      updatePhase('speaking')
      audioChunksRef.current = []
      audioMimeRef.current = (typeof d.audio_mime === 'string' ? d.audio_mime : 'audio/mpeg')
      interruptStartRef.current = 0
    }))

    offs.push(ws.on('speech_done', () => {
      if (!activeRef.current) return
      playBufferedAudio()
    }))

    offs.push(ws.on('listening', () => {
      if (!activeRef.current) return
      updatePhase('listening')
      if (activeRef.current) startRecording()
    }))

    offs.push(ws.on('error', (d) => {
      if (!activeRef.current) return
      setResponse(`Erreur: ${d.message ?? d.content ?? 'inconnue'}`)
      updatePhase('listening')
      if (activeRef.current) startRecording()
    }))

    // Audio Daemon state (temps réel)
    offs.push(ws.on('audio_daemon_state', (d) => {
      setDaemon((prev) => ({
        ...prev,
        enabled: Boolean(d.enabled ?? prev.enabled),
        state: String(d.state ?? prev.state) as AudioDaemonStatus['state'],
        wake_word_enabled: Boolean(d.wake_word_enabled ?? prev.wake_word_enabled),
        continuous_mode: Boolean(d.continuous_mode ?? prev.continuous_mode),
        last_interaction: typeof d.last_interaction === 'number' ? d.last_interaction : prev.last_interaction,
      }))
    }))

    offs.push(ws.onBinary((blob: Blob) => {
      if (!activeRef.current) return
      audioChunksRef.current.push(blob)
    }))

    return () => offs.forEach((off) => off())
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // --- Audio playback ---
  const playBufferedAudio = useCallback(() => {
    const chunks = audioChunksRef.current
    if (chunks.length === 0) {
      safeSendJson({ type: 'done_playing' }, 'done_playing (empty)')
      updatePhase('listening')
      if (activeRef.current) startRecording()
      return
    }

    const blob = new Blob(chunks, { type: audioMimeRef.current })
    audioChunksRef.current = []
    const url = URL.createObjectURL(blob)
    const audio = new Audio(url)
    audioPlayerRef.current = audio

    const afterDone = () => {
      URL.revokeObjectURL(url)
      audioPlayerRef.current = null
      safeSendJson({ type: 'done_playing' }, 'done_playing')
      responseAccRef.current = ''
      setResponse('')
      updatePhase('listening')
      if (activeRef.current) startRecording()
    }

    audio.onended = afterDone

    audio.onerror = () => {
      afterDone()
    }

    audio.play().catch(() => {
      afterDone()
    })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [safeSendJson])

  // --- MediaRecorder ---
  const startRecording = useCallback(() => {
    if (!streamRef.current) return
    if (phaseRef.current === 'processing') return
    if (recorderRef.current?.state === 'recording') return

    const mime = preferredRecorderMimeRef.current
    try {
      const opts: MediaRecorderOptions = mime ? { mimeType: mime } : {}
      const recorder = new MediaRecorder(streamRef.current, opts)
      recorderRef.current = recorder
      hasSpokeRef.current = false
      silenceStartRef.current = 0
      recordingStartedAtRef.current = performance.now()

      const chunks: Blob[] = []
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunks.push(e.data)
      }

      recorder.onstop = () => {
        if (chunks.length === 0 || !hasSpokeRef.current) {
          if (activeRef.current && phaseRef.current === 'listening') {
            startRecording()
          }
          return
        }
        const blobType = mime || recorder.mimeType || 'audio/webm'
        const blob = new Blob(chunks, { type: blobType })
        if (import.meta.env.DEV) console.log(`[VAD] Blob enregistré, type: ${blobType}, taille: ${blob.size} bytes`)
        if (blob.size < 2000) {
          if (activeRef.current && phaseRef.current === 'listening') startRecording()
          return
        }
        blob.arrayBuffer().then((buf) => {
          if (import.meta.env.DEV) console.log('[WS] Envoi du blob audio…')
          if (!safeSendBinary(buf, 'blob audio')) {
            updatePhase('listening')
            if (activeRef.current) startRecording()
            return
          }
          const t = new Date().toLocaleTimeString('fr-FR', { hour12: false })
          setDebugLastBlobSent(`${t} · ${blob.size} o`)
          updatePhase('processing')
          setResponse('')
          responseAccRef.current = ''
        })
      }

      recorder.start()
    } catch (e) {
      console.error('[Voice] MediaRecorder error', e)
      setSessionError(
        `MediaRecorder : ${e instanceof Error ? e.message : String(e)}`,
      )
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [safeSendBinary])

  const stopRecording = useCallback(() => {
    const r = recorderRef.current
    if (r && r.state === 'recording') {
      try { r.stop() } catch { /* ignore */ }
    }
    recorderRef.current = null
  }, [])

  // --- VAD loop ---
  const startVADLoop = useCallback(() => {
    const analyser = analyserRef.current
    if (!analyser) return

    const floatBuf = new Float32Array(analyser.fftSize)
    const byteBuf = new Uint8Array(analyser.frequencyBinCount)

    const tick = () => {
      if (!activeRef.current) return

      analyser.getFloatTimeDomainData(floatBuf)
      let sum = 0
      for (let i = 0; i < floatBuf.length; i++) sum += floatBuf[i] * floatBuf[i]
      const rms = Math.sqrt(sum / floatBuf.length)
      setVolume(rms)
      if (rms > VOLUME_THRESHOLD) logVadVolumeThrottled(rms)

      analyser.getByteTimeDomainData(byteBuf)
      let peak = 0
      for (let i = 0; i < byteBuf.length; i++) {
        const dev = Math.abs(byteBuf[i] - 128)
        if (dev > peak) peak = dev
      }
      const scaled = Math.min(255, Math.round(peak * 2))
      const now = performance.now()
      if (now - debugVadUiRef.current > 80) {
        debugVadUiRef.current = now
        setDebugVadByte(scaled)
      }

      // Interruption : couper JARVIS si l'utilisateur parle fort pendant le playback
      if (phaseRef.current === 'speaking' && rms > INTERRUPT_THRESHOLD) {
        if (interruptStartRef.current === 0) {
          interruptStartRef.current = performance.now()
        } else if (performance.now() - interruptStartRef.current > INTERRUPT_DURATION_MS) {
          if (import.meta.env.DEV) console.log('[VAD] Interruption — utilisateur coupe la parole')
          interruptStartRef.current = 0
          const player = audioPlayerRef.current
          if (player) {
            player.onended = null
            player.onerror = null
            player.pause()
            audioPlayerRef.current = null
          }
          audioChunksRef.current = []
          safeSendJson({ type: 'done_playing' }, 'interrupt')
          updatePhase('listening')
          startRecording()
        }
      } else if (phaseRef.current === 'speaking') {
        interruptStartRef.current = 0
      }

      if (phaseRef.current === 'listening' && recorderRef.current?.state === 'recording') {
        const recordingElapsed = performance.now() - recordingStartedAtRef.current
        if (recordingElapsed >= MAX_UTTERANCE_MS) {
          // Filet de sécurité: on force un envoi périodique, même si le VAD n'a pas
          // détecté clairement la fin de phrase (inspiration comportement zeldrisDASH).
          hasSpokeRef.current = true
          silenceStartRef.current = 0
          speechStartRef.current = 0
          if (import.meta.env.DEV) console.log(`[VAD] Flush forcé après ${Math.round(recordingElapsed)}ms`)
          stopRecording()
          rafRef.current = requestAnimationFrame(tick)
          return
        }

        if (rms > VOLUME_THRESHOLD) {
          if (!hasSpokeRef.current) {
            hasSpokeRef.current = true
            speechStartRef.current = performance.now()
          }
          silenceStartRef.current = 0
        } else if (hasSpokeRef.current) {
          if (silenceStartRef.current === 0) {
            silenceStartRef.current = performance.now()
          } else if (performance.now() - silenceStartRef.current > SILENCE_DURATION_MS) {
            const speechDuration = silenceStartRef.current - speechStartRef.current
            if (speechDuration < MIN_SPEECH_DURATION_MS) {
              if (import.meta.env.DEV) console.log(`[VAD] Parole trop courte (${Math.round(speechDuration)}ms) — on continue`)
              silenceStartRef.current = 0
              hasSpokeRef.current = false
              speechStartRef.current = 0
            } else {
              if (import.meta.env.DEV) console.log(`[VAD] Fin de phrase detectee (parole=${Math.round(speechDuration)}ms, silence=${SILENCE_DURATION_MS}ms)`)
              silenceStartRef.current = 0
              hasSpokeRef.current = false
              speechStartRef.current = 0
              stopRecording()
            }
          }
        }
      }

      rafRef.current = requestAnimationFrame(tick)
    }

    rafRef.current = requestAnimationFrame(tick)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stopRecording])

  const abortAudioSetup = useCallback((stream: MediaStream | null, ctx: AudioContext | null) => {
    stream?.getTracks().forEach((t) => t.stop())
    if (ctx && ctx.state !== 'closed') {
      ctx.close().catch(() => {})
    }
  }, [])

  // --- Start/stop session ---
  const startSession = useCallback(async () => {
    setSessionError(null)
    setMicPermission('pending')

    if (!ws.isSocketOpen()) {
      setMicPermission('idle')
      const msg =
        'WebSocket non connecté. Ouvrez l’app depuis le serveur JARVIS ou rechargez la page.'
      setSessionError(msg)
      window.alert(msg)
      return
    }

    let stream: MediaStream | null = null
    let ctx: AudioContext | null = null

    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      })
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      console.error('[Voice] getUserMedia', e)
      setMicPermission('denied')
      setSessionError(`Micro : ${msg}`)
      window.alert(`Micro inaccessible ou refusé.\n\n${msg}`)
      return
    }

    setMicPermission('granted')

    const AC = getAudioContextCtor()
    if (!AC) {
      abortAudioSetup(stream, null)
      setMicPermission('denied')
      const msg = 'AudioContext non supporté par ce navigateur.'
      setSessionError(msg)
      window.alert(msg)
      return
    }

    try {
      ctx = new AC()
      await ctx.resume()
      if (ctx.state === 'suspended') {
        await ctx.resume()
      }
    } catch (e) {
      abortAudioSetup(stream, ctx)
      setMicPermission('denied')
      const msg = e instanceof Error ? e.message : String(e)
      setSessionError(`AudioContext : ${msg}`)
      window.alert(`Impossible d’activer l’audio (AudioContext).\n\n${msg}`)
      return
    }

    syncCtxStateLabel()
    ctx.onstatechange = () => syncCtxStateLabel()

    if (!ws.isSocketOpen()) {
      abortAudioSetup(stream, ctx)
      setMicPermission('idle')
      const msg = 'WebSocket non connecté'
      setSessionError(msg)
      window.alert(msg)
      return
    }

    preferredRecorderMimeRef.current = pickRecorderMime()
    if (preferredRecorderMimeRef.current) {
      if (import.meta.env.DEV) console.log(`[Voice] MediaRecorder mime: ${preferredRecorderMimeRef.current}`)
    } else {
      if (import.meta.env.DEV) console.warn('[Voice] Aucun mime enregistrable listé — défaut navigateur')
    }

    streamRef.current = stream
    audioCtxRef.current = ctx

    const source = ctx.createMediaStreamSource(stream)
    const analyser = ctx.createAnalyser()
    analyser.fftSize = 2048
    source.connect(analyser)
    analyserRef.current = analyser

    activeRef.current = true
    setTranscript('')
    setResponse('')
    responseAccRef.current = ''

    if (!safeSendJson({ type: 'conversation_start' }, 'conversation_start')) {
      abortAudioSetup(stream, ctx)
      streamRef.current = null
      audioCtxRef.current = null
      analyserRef.current = null
      activeRef.current = false
      setMicPermission('idle')
      window.alert('WebSocket non connecté')
      return
    }

    updatePhase('listening')
    startRecording()
    startVADLoop()
  }, [
    abortAudioSetup,
    safeSendJson,
    startRecording,
    startVADLoop,
    syncCtxStateLabel,
    updatePhase,
  ])

  const stopSession = useCallback(() => {
    activeRef.current = false
    cancelAnimationFrame(rafRef.current)

    stopRecording()

    audioPlayerRef.current?.pause()
    audioPlayerRef.current = null

    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null

    const c = audioCtxRef.current
    if (c) {
      c.onstatechange = null
      c.close().catch(() => {})
    }
    audioCtxRef.current = null
    analyserRef.current = null
    setAudioCtxStateLabel('—')

    updatePhase('idle')
    setVolume(0)
    setMicPermission('idle')
    setDebugVadByte(0)
  }, [updatePhase, stopRecording])

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      activeRef.current = false
      cancelAnimationFrame(rafRef.current)
      if (recorderRef.current?.state === 'recording') {
        try { recorderRef.current.stop() } catch { /* ignore */ }
      }
      audioPlayerRef.current?.pause()
      streamRef.current?.getTracks().forEach((t) => t.stop())
      const c = audioCtxRef.current
      if (c) {
        c.onstatechange = null
        c.close().catch(() => {})
      }
    }
  }, [])

  const isActive = phase !== 'idle'

  const micLabel =
    micPermission === 'granted'
      ? 'Autorisé'
      : micPermission === 'denied'
        ? 'Refusé / erreur'
        : micPermission === 'pending'
          ? 'En attente…'
          : '—'

  return (
    <div className="flex flex-col h-full relative overflow-hidden">
      {/* Background glow */}
      <div
        className="pointer-events-none absolute inset-0 transition-opacity duration-700"
        style={{
          background: `radial-gradient(ellipse at center, ${
            phase === 'listening'
              ? 'rgba(34,211,238,0.06)'
              : phase === 'speaking'
                ? 'rgba(251,191,36,0.06)'
                : phase === 'processing'
                  ? 'rgba(168,85,247,0.04)'
                  : 'transparent'
          } 0%, transparent 70%)`,
        }}
      />

      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-white/5">
        <div className="flex items-center gap-3 flex-wrap">
          <NavLink
            to="/chat"
            className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1 text-[10px] font-mono uppercase tracking-wider text-muted-foreground hover:border-white/25 hover:text-foreground transition-colors"
          >
            <MessageSquare size={12} />
            Chat
          </NavLink>
          <div className="flex items-center gap-3">
            <div
              className={`w-2 h-2 rounded-full transition-colors ${
                phase === 'listening'
                  ? 'bg-cyan-400 shadow-[0_0_6px_rgba(34,211,238,0.6)]'
                  : phase === 'processing'
                    ? 'bg-purple-400 animate-pulse'
                    : phase === 'speaking'
                      ? 'bg-amber-400 shadow-[0_0_6px_rgba(251,191,36,0.6)]'
                      : 'bg-white/20'
              }`}
            />
            <span className="font-mono text-xs text-muted-foreground tracking-wider uppercase">
              {phase === 'idle' ? 'Voice · Inactif' : `Voice · ${phase}`}
            </span>
          </div>
        </div>

        {/* TTS Engine selector */}
        <div className="relative">
          <select
            value={ttsEngine}
            onChange={(e) => handleTtsChange(e.target.value)}
            disabled={ttsLoading}
            className="appearance-none bg-white/5 backdrop-blur-md border border-white/10 rounded-lg px-3 py-1.5 text-xs text-muted-foreground cursor-pointer focus:outline-none focus:border-white/20 transition-colors"
          >
            {TTS_ENGINES.map((e) => (
              <option key={e} value={e} className="bg-neutral-900 text-white">
                {e === 'kokoro' ? 'Kokoro (Local)' : e === 'elevenlabs' ? 'ElevenLabs' : e === 'macos' ? 'Apple (Mac)' : 'Edge'}
              </option>
            ))}
          </select>
          {ttsLoading && (
            <div className="absolute right-2 top-1/2 -translate-y-1/2">
              <div className="w-3 h-3 border border-white/30 border-t-transparent rounded-full animate-spin" />
            </div>
          )}
        </div>
      </div>

      {/* Audio Daemon control */}
      <div className="mx-6 mt-3 glass-panel rounded-xl p-4 border border-white/10">
        <div className="flex items-center gap-3 mb-3">
          <Headphones size={14} className="text-muted-foreground" />
          <span className="font-mono text-xs text-muted-foreground tracking-wider uppercase">
            DAEMON AUDIO NATIF
          </span>
          <div className="flex items-center gap-1.5 ml-auto">
            <div
              className={`w-2 h-2 rounded-full ${
                daemon.state === 'error' ? 'bg-red-400' :
                daemon.state === 'listening' || daemon.state === 'wake_listening' ? 'bg-cyan-400 shadow-[0_0_6px_rgba(34,211,238,0.6)]' :
                daemon.state === 'processing' ? 'bg-purple-400 animate-pulse' :
                daemon.state === 'speaking' ? 'bg-amber-400 shadow-[0_0_6px_rgba(251,191,36,0.6)]' :
                'bg-white/20'
              }`}
            />
            <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              {daemon.enabled ? daemon.state.replace('_', ' ') : 'INACTIF'}
            </span>
          </div>
        </div>

        <div className="flex items-center gap-4 flex-wrap">
          <button
            type="button"
            onClick={handleDaemonToggle}
            disabled={daemonLoading}
            className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border font-mono text-xs transition-all ${
              daemon.enabled
                ? 'bg-red-500/10 border-red-500/20 text-red-400 hover:bg-red-500/20'
                : 'bg-white/5 border-white/10 text-white/80 hover:bg-white/10'
            }`}
          >
            {daemon.enabled ? 'Arrêter' : 'Activer'}
          </button>

          <button
            type="button"
            onClick={handleDaemonWakeWord}
            disabled={daemonLoading || !daemon.enabled}
            className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border font-mono text-xs transition-all ${
              !daemon.enabled ? 'opacity-30 cursor-not-allowed' : ''
            } ${
              daemon.wake_word_enabled
                ? 'bg-cyan-500/10 border-cyan-500/20 text-cyan-300'
                : 'bg-white/5 border-white/10 text-white/60'
            }`}
          >
            Wake: {daemon.wake_word_enabled ? 'ON' : 'OFF'}
          </button>

          <button
            type="button"
            onClick={handleDaemonContinuous}
            disabled={daemonLoading || !daemon.enabled}
            className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border font-mono text-xs transition-all ${
              !daemon.enabled ? 'opacity-30 cursor-not-allowed' : ''
            } ${
              daemon.continuous_mode
                ? 'bg-purple-500/10 border-purple-500/20 text-purple-300'
                : 'bg-white/5 border-white/10 text-white/60'
            }`}
          >
            Continu: {daemon.continuous_mode ? 'ON' : 'OFF'}
          </button>
        </div>

        <div className="flex items-center gap-4 mt-3 text-[10px] font-mono text-muted-foreground">
          <span>
            Dernière: {daemon.last_interaction > 0
              ? new Date(daemon.last_interaction * 1000).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })
              : '—'}
          </span>
          <span>TTS: {daemon.tts_engine}</span>
          <span>STT: {daemon.stt_engine}</span>
          <span>Porcupine: {daemon.has_porcupine ? '✓' : '✗'}</span>
        </div>
      </div>

      {sessionError && (
        <div className="mx-6 mt-3 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs font-mono text-red-200">
          {sessionError}
        </div>
      )}

      {/* Central orb area */}
      <div className="flex-1 flex flex-col items-center justify-center gap-8 relative">
        <VoiceOrb phase={phase} volume={volume} />

        <div className="text-center space-y-1">
          <p
            className={`text-sm font-medium transition-colors duration-300 ${
              phase === 'listening'
                ? 'text-cyan-400'
                : phase === 'speaking'
                  ? 'text-amber-400'
                  : phase === 'processing'
                    ? 'text-purple-400'
                    : 'text-muted-foreground'
            }`}
          >
            {phase === 'listening' && micPermission !== 'granted'
              ? 'Micro non autorisé'
              : phase === 'idle'
              ? 'Appuyez pour démarrer'
              : phase === 'listening'
                ? 'Écoute en cours...'
                : phase === 'processing'
                  ? 'Réflexion...'
                  : 'JARVIS parle...'}
          </p>
        </div>
      </div>

      {/* Transcript area */}
      <div className="px-6 pb-4 space-y-3 min-h-[140px]">
        {transcript && (
          <div className="bg-white/5 backdrop-blur-md border border-white/10 rounded-xl px-4 py-3">
            <p className="text-xs text-muted-foreground mb-1 font-mono">VOUS</p>
            <p className="text-sm text-white/80 italic">{transcript}</p>
          </div>
        )}
        {response && (
          <div className="bg-white/5 backdrop-blur-md border border-cyan-500/10 rounded-xl px-4 py-3">
            <p className="text-xs text-cyan-400/60 mb-1 font-mono">JARVIS</p>
            <p className="text-sm text-white/90 leading-relaxed">{response}</p>
          </div>
        )}
      </div>

      {/* Action button */}
      <div className="flex justify-center pb-8 pt-2">
        <button
          type="button"
          onClick={isActive ? stopSession : startSession}
          className={`group relative flex items-center gap-3 px-8 py-3.5 rounded-2xl font-mono text-sm tracking-wider uppercase transition-all duration-300 border ${
            isActive
              ? 'bg-red-500/10 border-red-500/30 text-red-400 hover:bg-red-500/20 hover:border-red-500/50'
              : 'bg-white/5 border-white/10 text-white hover:bg-white/10 hover:border-white/20'
          }`}
        >
          {isActive ? (
            <>
              <Square size={16} className="fill-current" />
              Arrêter
            </>
          ) : (
            <>
              <Mic size={16} />
              Démarrer la conversation
            </>
          )}
          <div
            className={`absolute inset-0 rounded-2xl transition-opacity duration-500 ${
              isActive ? 'opacity-100' : 'opacity-0'
            }`}
            style={{
              background:
                'radial-gradient(ellipse at center, rgba(239,68,68,0.08) 0%, transparent 70%)',
            }}
          />
        </button>
      </div>

      {/* Debug toggle */}
      <button
        type="button"
        onClick={() => setDebugOpen((v) => !v)}
        className="fixed top-4 right-4 z-40 flex items-center gap-2 rounded-xl border border-white/15 bg-black/50 px-3 py-2 text-xs font-mono text-muted-foreground backdrop-blur-md hover:border-white/30 hover:text-foreground"
        aria-expanded={debugOpen}
        aria-label="Mode diagnostic"
      >
        <Settings size={14} />
        Debug
      </button>

      {debugOpen && (
        <div
          className="fixed top-16 right-4 z-40 w-[min(100vw-2rem,24rem)] rounded-xl border border-white/15 bg-black/55 p-4 font-mono text-[11px] text-white/85 shadow-xl backdrop-blur-md"
          role="region"
          aria-label="Diagnostic voix"
        >
          <p className="mb-3 border-b border-white/10 pb-2 text-xs uppercase tracking-wider text-muted-foreground">
            Diagnostic
          </p>
          <dl className="space-y-2">
            <div className="flex justify-between gap-2">
              <dt className="text-muted-foreground">Micro</dt>
              <dd>{micLabel}</dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-muted-foreground">AudioContext</dt>
              <dd className="uppercase">{audioCtxStateLabel}</dd>
            </div>
            <div>
              <div className="flex justify-between gap-2">
                <dt className="text-muted-foreground">VAD (0–255)</dt>
                <dd>{debugVadByte}</dd>
              </div>
              <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-white/10">
                <div
                  className="h-full bg-cyan-400/80 transition-[width] duration-75"
                  style={{ width: `${(debugVadByte / 255) * 100}%` }}
                />
              </div>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-muted-foreground">WebSocket</dt>
              <dd>{debugWsConnected ? 'Connecté' : 'Déconnecté'}</dd>
            </div>
            <div className="border-t border-white/10 pt-2">
              <dt className="text-muted-foreground">Dernier événement WS</dt>
              <dd className="mt-1 break-all text-white/70">{debugLastWsEvent}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Dernier envoi blob</dt>
              <dd className="mt-1 break-all text-white/70">{debugLastBlobSent}</dd>
            </div>
            <div className="border-t border-white/10 pt-2">
              <dt className="text-muted-foreground">STT brut</dt>
              <dd className="mt-1 break-all text-white/70">{debugSttRaw}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">STT nettoyé</dt>
              <dd className="mt-1 break-all text-white/70">{debugSttClean}</dd>
            </div>
          </dl>
        </div>
      )}
    </div>
  )
}

// --- Visualizer orb ---
function VoiceOrb({ phase, volume }: { phase: SessionPhase; volume: number }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const animRef = useRef<number>(0)
  const timeRef = useRef(0)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const size = 240
    const dpr = window.devicePixelRatio || 1
    canvas.width = size * dpr
    canvas.height = size * dpr
    ctx.scale(dpr, dpr)

    const draw = () => {
      timeRef.current += 0.012
      const t = timeRef.current
      ctx.clearRect(0, 0, size, size)

      const cx = size / 2
      const cy = size / 2
      const baseR = 70

      let color: string
      let intensity: number
      let glowColor: string

      switch (phase) {
        case 'listening':
          color = 'rgba(34,211,238,'
          glowColor = 'rgba(34,211,238,'
          intensity = 0.4 + Math.min(volume * 30, 0.6)
          break
        case 'speaking':
          color = 'rgba(251,191,36,'
          glowColor = 'rgba(251,191,36,'
          intensity = 0.7
          break
        case 'processing':
          color = 'rgba(168,85,247,'
          glowColor = 'rgba(168,85,247,'
          intensity = 0.2 + Math.sin(t * 2) * 0.15
          break
        default:
          color = 'rgba(255,255,255,'
          glowColor = 'rgba(255,255,255,'
          intensity = 0.08
      }

      // Outer glow
      const glowGrad = ctx.createRadialGradient(cx, cy, baseR * 0.5, cx, cy, baseR * 2)
      glowGrad.addColorStop(0, `${glowColor}${(intensity * 0.12).toFixed(3)})`)
      glowGrad.addColorStop(1, `${glowColor}0)`)
      ctx.beginPath()
      ctx.arc(cx, cy, baseR * 2, 0, Math.PI * 2)
      ctx.fillStyle = glowGrad
      ctx.fill()

      // Concentric rings
      for (let ring = 3; ring >= 1; ring--) {
        const r = baseR - ring * 8 + Math.sin(t * 0.8 + ring * 0.9) * 2 * intensity
        ctx.beginPath()
        ctx.arc(cx, cy, r, 0, Math.PI * 2)
        ctx.strokeStyle = `${color}${(0.04 + intensity * 0.04).toFixed(3)})`
        ctx.lineWidth = 0.8
        ctx.stroke()
      }

      // Main orb with organic deformation
      ctx.beginPath()
      const segments = 80
      for (let i = 0; i <= segments; i++) {
        const angle = (i / segments) * Math.PI * 2
        const n1 = Math.sin(angle * 3 + t * 1.2) * 3 * intensity
        const n2 = Math.cos(angle * 5 - t * 0.9) * 2 * intensity
        const n3 = Math.sin(angle * 7 + t * 0.6) * 1.2 * intensity
        const r = baseR + n1 + n2 + n3
        const x = cx + r * Math.cos(angle)
        const y = cy + r * Math.sin(angle)
        if (i === 0) ctx.moveTo(x, y)
        else ctx.lineTo(x, y)
      }
      ctx.closePath()

      const orbGrad = ctx.createRadialGradient(cx, cy, 0, cx, cy, baseR)
      orbGrad.addColorStop(0, `${color}${(0.35 + intensity * 0.25).toFixed(3)})`)
      orbGrad.addColorStop(0.5, `${color}${(0.15 + intensity * 0.1).toFixed(3)})`)
      orbGrad.addColorStop(1, `${color}0.02)`)
      ctx.fillStyle = orbGrad
      ctx.fill()

      // Specular highlight
      const hlGrad = ctx.createRadialGradient(
        cx - baseR * 0.2, cy - baseR * 0.25, 0,
        cx - baseR * 0.2, cy - baseR * 0.25, baseR * 0.4,
      )
      hlGrad.addColorStop(0, `rgba(255,255,255,${(0.04 + intensity * 0.04).toFixed(3)})`)
      hlGrad.addColorStop(1, 'rgba(255,255,255,0)')
      ctx.beginPath()
      ctx.arc(cx - baseR * 0.2, cy - baseR * 0.25, baseR * 0.4, 0, Math.PI * 2)
      ctx.fillStyle = hlGrad
      ctx.fill()

      animRef.current = requestAnimationFrame(draw)
    }

    animRef.current = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(animRef.current)
  }, [phase, volume])

  return (
    <div className="relative">
      <canvas
        ref={canvasRef}
        className="w-[240px] h-[240px]"
        style={{ imageRendering: 'auto' }}
      />
      <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
        {phase === 'listening' ? (
          <Mic size={28} className="text-cyan-400/40" />
        ) : phase === 'speaking' ? (
          <Volume2 size={28} className="text-amber-400/40" />
        ) : phase === 'processing' ? (
          <div className="flex gap-1.5">
            {[0, 1, 2].map((i) => (
              <div
                key={i}
                className="w-1.5 h-1.5 rounded-full bg-purple-400/60 animate-bounce"
                style={{ animationDelay: `${i * 150}ms` }}
              />
            ))}
          </div>
        ) : (
          <MicOff size={24} className="text-white/15" />
        )}
      </div>
    </div>
  )
}
