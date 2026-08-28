import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertCircle, CheckCircle, Loader2, Mic, RotateCcw, Square, X } from 'lucide-react'

import { api, type RecordingSessionStatus } from '@unified/lib/api'
import {
  LONG_RECORDING_SEGMENT_MS,
  clearStoredLongRecording,
  hasRecordingUploadCapacity,
  readStoredLongRecording,
  sha256Hex,
  uploadRecordingSegmentWithRetry,
  writeStoredLongRecording,
} from '@unified/lib/longRecording'

type LocalPhase = 'idle' | 'paused' | 'recording' | 'stopping'

function recorderMimeType(): string | undefined {
  for (const mime of ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4']) {
    if (typeof MediaRecorder !== 'undefined' && MediaRecorder.isTypeSupported(mime)) return mime
  }
  return undefined
}

function formatElapsed(durationMs: number): string {
  const seconds = Math.max(0, Math.floor(durationMs / 1000))
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const rest = seconds % 60
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, '0')}:${String(rest).padStart(2, '0')}`
    : `${minutes}:${String(rest).padStart(2, '0')}`
}

function stateLabel(phase: LocalPhase, status: RecordingSessionStatus | null): string {
  if (phase === 'recording') return 'Enregistrement en cours'
  if (phase === 'stopping') return 'Clôture des segments…'
  if (phase === 'paused') return 'Capture interrompue — reprise disponible'
  const labels: Record<string, string> = {
    queued: 'En attente de transcription locale',
    processing: 'Transcription et synthèse en cours',
    retry: 'Traitement à réessayer',
    completed: 'Enregistrement traité',
    failed: 'Traitement en échec',
    cancelled: 'Enregistrement annulé',
    expired: 'Audio brut expiré',
  }
  return status ? (labels[status.state] ?? status.state) : 'Prêt'
}

export function LongRecordingControl({ onCompleted }: { onCompleted?: () => void }) {
  const [label, setLabel] = useState('Enregistrement long')
  const [phase, setPhase] = useState<LocalPhase>('idle')
  const [status, setStatus] = useState<RecordingSessionStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [pendingUploads, setPendingUploads] = useState(0)
  const [elapsedMs, setElapsedMs] = useState(0)

  const streamRef = useRef<MediaStream | null>(null)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const rotateTimerRef = useRef<number | null>(null)
  const activeRef = useRef(false)
  const discardCurrentRef = useRef(false)
  const stopResolveRef = useRef<(() => void) | null>(null)
  const sessionIdRef = useRef<string | null>(null)
  const clientRecordingIdRef = useRef<string | null>(null)
  const assignedSequenceRef = useRef(0)
  const acknowledgedSequenceRef = useRef(0)
  const durableDurationRef = useRef(0)
  const capturedDurationRef = useRef(0)
  const segmentStartedRef = useRef(0)
  const pendingCountRef = useRef(0)
  const pendingChainRef = useRef<Promise<void>>(Promise.resolve())
  const uploadFailureRef = useRef<unknown>(null)
  const launchSegmentRef = useRef<() => void>(() => undefined)

  const stopTracks = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
  }, [])

  const rememberAck = useCallback((nextSequence: number, durationMs: number) => {
    const sessionId = sessionIdRef.current
    const clientRecordingId = clientRecordingIdRef.current
    if (!sessionId || !clientRecordingId) return
    writeStoredLongRecording({
      clientRecordingId,
      sessionId,
      label,
      nextSequence,
      durationMs,
    })
  }, [label])

  const failCapture = useCallback((message: string, cause?: unknown) => {
    uploadFailureRef.current = cause ?? new Error(message)
    activeRef.current = false
    discardCurrentRef.current = true
    if (rotateTimerRef.current !== null) window.clearTimeout(rotateTimerRef.current)
    rotateTimerRef.current = null
    const recorder = recorderRef.current
    if (recorder?.state === 'recording') {
      try { recorder.stop() } catch { /* recorder déjà fermé */ }
    }
    stopTracks()
    setPhase('paused')
    setError(message)
  }, [stopTracks])

  const enqueueSegment = useCallback((blob: Blob, durationMs: number): boolean => {
    const sessionId = sessionIdRef.current
    if (!sessionId) return false
    if (!hasRecordingUploadCapacity(pendingCountRef.current)) {
      failCapture('La file locale d’upload est saturée. La capture est arrêtée au dernier ACK.')
      return false
    }
    const sequence = assignedSequenceRef.current
    assignedSequenceRef.current += 1
    pendingCountRef.current += 1
    setPendingUploads(pendingCountRef.current)

    const task = pendingChainRef.current.then(async () => {
      const checksum = await sha256Hex(blob)
      const ack = await uploadRecordingSegmentWithRetry(() =>
        api.uploadRecordingChunk(sessionId, sequence, blob, checksum, durationMs),
      )
      acknowledgedSequenceRef.current = ack.next_sequence
      durableDurationRef.current = ack.duration_ms
      rememberAck(ack.next_sequence, ack.duration_ms)
      setStatus((previous) => previous ? {
        ...previous,
        state: 'capturing',
        next_sequence: ack.next_sequence,
        received_chunks: ack.received_chunks,
        size_bytes: ack.size_bytes,
        duration_ms: ack.duration_ms,
        duration_seconds: Math.ceil(ack.duration_ms / 1000),
        checksum: ack.checksum,
      } : previous)
    }).catch((cause: unknown) => {
      failCapture('Upload interrompu après trois essais. Reprenez depuis le dernier segment confirmé.', cause)
    }).finally(() => {
      pendingCountRef.current = Math.max(0, pendingCountRef.current - 1)
      setPendingUploads(pendingCountRef.current)
    })
    pendingChainRef.current = task
    return true
  }, [failCapture, rememberAck])

  const launchSegment = useCallback(() => {
    const stream = streamRef.current
    if (!activeRef.current || !stream) return
    const mime = recorderMimeType()
    const chunks: Blob[] = []
    const recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined)
    recorderRef.current = recorder
    segmentStartedRef.current = performance.now()
    discardCurrentRef.current = false
    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) chunks.push(event.data)
    }
    recorder.onerror = () => failCapture('Le navigateur a interrompu MediaRecorder.')
    recorder.onstop = () => {
      if (rotateTimerRef.current !== null) window.clearTimeout(rotateTimerRef.current)
      rotateTimerRef.current = null
      const measuredDurationMs = Math.max(
        1,
        Math.round(performance.now() - segmentStartedRef.current),
      )
      if (!discardCurrentRef.current && measuredDurationMs > 60_000) {
        discardCurrentRef.current = true
        failCapture('Le navigateur a suspendu la capture trop longtemps. Reprenez depuis le dernier ACK.')
      }
      const durationMs = Math.min(60_000, measuredDurationMs)
      if (!discardCurrentRef.current && chunks.length > 0) {
        const blob = new Blob(chunks, { type: mime || recorder.mimeType || 'audio/webm' })
        if (blob.size >= 800) {
          capturedDurationRef.current += durationMs
          setElapsedMs(capturedDurationRef.current)
          enqueueSegment(blob, durationMs)
        }
      }
      recorderRef.current = null
      stopResolveRef.current?.()
      stopResolveRef.current = null
      if (activeRef.current) launchSegmentRef.current()
    }
    recorder.start()
    rotateTimerRef.current = window.setTimeout(() => {
      if (recorder.state === 'recording') recorder.stop()
    }, LONG_RECORDING_SEGMENT_MS)
  }, [enqueueSegment, failCapture])
  launchSegmentRef.current = launchSegment

  const stopCurrentSegment = useCallback((discard: boolean): Promise<void> => {
    const recorder = recorderRef.current
    if (!recorder || recorder.state !== 'recording') return Promise.resolve()
    discardCurrentRef.current = discard
    return new Promise((resolve) => {
      stopResolveRef.current = resolve
      try { recorder.stop() } catch { resolve() }
    })
  }, [])

  const applyStatus = useCallback((next: RecordingSessionStatus) => {
    setStatus(next)
    setLabel(next.label || 'Enregistrement long')
    sessionIdRef.current = next.session_id
    assignedSequenceRef.current = next.next_sequence
    acknowledgedSequenceRef.current = next.next_sequence
    durableDurationRef.current = next.duration_ms
    capturedDurationRef.current = next.duration_ms
    setElapsedMs(next.duration_ms)
    if (next.state === 'capturing') setPhase('paused')
    else setPhase('idle')
    if (next.state === 'completed') {
      clearStoredLongRecording()
      onCompleted?.()
    }
  }, [onCompleted])

  useEffect(() => {
    const stored = readStoredLongRecording()
    if (!stored) return
    clientRecordingIdRef.current = stored.clientRecordingId
    api.getRecordingSession(stored.sessionId)
      .then(applyStatus)
      .catch(() => clearStoredLongRecording())
  }, [applyStatus])

  useEffect(() => {
    if (!status || !['queued', 'processing', 'retry'].includes(status.state)) return
    const timer = window.setInterval(() => {
      api.getRecordingSession(status.session_id).then(applyStatus).catch(() => undefined)
    }, 2_000)
    return () => window.clearInterval(timer)
  }, [applyStatus, status])

  useEffect(() => {
    if (phase !== 'recording') return
    const timer = window.setInterval(() => {
      const current = recorderRef.current?.state === 'recording'
        ? performance.now() - segmentStartedRef.current
        : 0
      setElapsedMs(capturedDurationRef.current + current)
    }, 500)
    return () => window.clearInterval(timer)
  }, [phase])

  useEffect(() => () => {
    activeRef.current = false
    discardCurrentRef.current = true
    if (rotateTimerRef.current !== null) window.clearTimeout(rotateTimerRef.current)
    const recorder = recorderRef.current
    if (recorder?.state === 'recording') {
      try { recorder.stop() } catch { /* démontage */ }
    }
    stopTracks()
  }, [stopTracks])

  const beginCapture = useCallback(async (resume: boolean) => {
    setBusy(true)
    setError(null)
    uploadFailureRef.current = null
    try {
      if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
        throw new Error('Microphone ou MediaRecorder indisponible dans ce navigateur.')
      }
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      })
      streamRef.current = stream
      let next = status
      if (resume && sessionIdRef.current) {
        next = await api.getRecordingSession(sessionIdRef.current)
        if (next.state !== 'capturing') throw new Error('Cette capture est déjà scellée.')
      } else {
        const clientRecordingId = globalThis.crypto.randomUUID()
        clientRecordingIdRef.current = clientRecordingId
        next = await api.startRecordingSession({ client_recording_id: clientRecordingId, label })
      }
      if (!next) throw new Error('Session d’enregistrement indisponible.')
      applyStatus(next)
      rememberAck(next.next_sequence, next.duration_ms)
      activeRef.current = true
      setPhase('recording')
      launchSegmentRef.current()
    } catch (cause) {
      stopTracks()
      setError(cause instanceof Error ? cause.message : 'Impossible de démarrer la capture.')
      setPhase(status?.state === 'capturing' ? 'paused' : 'idle')
    } finally {
      setBusy(false)
    }
  }, [applyStatus, label, rememberAck, status, stopTracks])

  const completeCapture = useCallback(async () => {
    const sessionId = sessionIdRef.current
    if (!sessionId) return
    setBusy(true)
    setError(null)
    setPhase('stopping')
    activeRef.current = false
    await stopCurrentSegment(false)
    stopTracks()
    await pendingChainRef.current
    try {
      if (uploadFailureRef.current) throw uploadFailureRef.current
      if (acknowledgedSequenceRef.current < 1) {
        throw new Error('Aucun segment audio assez long n’a été reçu.')
      }
      const next = await api.completeRecordingSession(sessionId, {
        expected_chunks: acknowledgedSequenceRef.current,
        duration_seconds: Math.ceil(durableDurationRef.current / 1000),
      })
      applyStatus(next)
      rememberAck(next.next_sequence, next.duration_ms)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Clôture impossible.')
      setPhase('paused')
    } finally {
      setBusy(false)
    }
  }, [applyStatus, rememberAck, stopCurrentSegment, stopTracks])

  const cancelCapture = useCallback(async () => {
    const sessionId = sessionIdRef.current
    if (!sessionId) return
    setBusy(true)
    activeRef.current = false
    await stopCurrentSegment(true)
    stopTracks()
    await pendingChainRef.current
    try {
      const next = await api.cancelRecordingSession(sessionId)
      applyStatus(next)
      clearStoredLongRecording()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Annulation impossible.')
    } finally {
      setBusy(false)
    }
  }, [applyStatus, stopCurrentSegment, stopTracks])

  const retryProcessing = useCallback(async () => {
    if (!status?.retryable) return
    setBusy(true)
    setError(null)
    try {
      applyStatus(await api.retryRecordingSession(status.session_id))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Nouvel essai impossible.')
    } finally {
      setBusy(false)
    }
  }, [applyStatus, status])

  const canResume = phase === 'paused' && status?.state === 'capturing'
  const canCancel = status?.state === 'capturing'
  const terminalSuccess = status?.state === 'completed'

  return (
    <div className="glass-panel rounded-xl border border-white/10 p-4 mb-4" data-testid="long-recording-control">
      <div className="flex flex-wrap items-center gap-3">
        <div className="w-10 h-10 rounded-xl bg-white/5 border border-white/10 flex items-center justify-center">
          {terminalSuccess
            ? <CheckCircle className="w-4 h-4 text-emerald-400" />
            : <Mic className={`w-4 h-4 ${phase === 'recording' ? 'text-red-400' : 'text-muted-foreground'}`} />}
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium">Enregistrement long segmenté</p>
          <p className="font-mono text-xs text-muted-foreground">
            {stateLabel(phase, status)} · {formatElapsed(elapsedMs)} · {status?.received_chunks ?? 0} ACK
            {pendingUploads > 0 ? ` · ${pendingUploads} envoi(s)` : ''}
          </p>
        </div>
        <input
          value={label}
          onChange={(event) => setLabel(event.target.value.slice(0, 200))}
          disabled={busy || phase !== 'idle' || Boolean(status && !['completed', 'cancelled', 'expired'].includes(status.state))}
          aria-label="Nom de l’enregistrement"
          className="min-w-48 rounded-lg bg-white/5 border border-white/10 px-3 py-2 text-sm disabled:opacity-50"
        />
        {phase === 'recording' ? (
          <button onClick={() => void completeCapture()} disabled={busy} className="flex items-center gap-2 rounded-lg bg-red-500/15 border border-red-400/30 px-3 py-2 text-sm">
            <Square className="w-3.5 h-3.5" /> Terminer
          </button>
        ) : canResume ? (
          <button onClick={() => void beginCapture(true)} disabled={busy} className="flex items-center gap-2 rounded-lg bg-cyan-500/15 border border-cyan-400/30 px-3 py-2 text-sm">
            <RotateCcw className="w-3.5 h-3.5" /> Reprendre
          </button>
        ) : status?.retryable ? (
          <button onClick={() => void retryProcessing()} disabled={busy} className="flex items-center gap-2 rounded-lg bg-amber-500/15 border border-amber-400/30 px-3 py-2 text-sm">
            <RotateCcw className="w-3.5 h-3.5" /> Réessayer
          </button>
        ) : (
          <button onClick={() => void beginCapture(false)} disabled={busy || Boolean(status && ['queued', 'processing'].includes(status.state))} className="flex items-center gap-2 rounded-lg bg-white/10 border border-white/15 px-3 py-2 text-sm disabled:opacity-50">
            {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Mic className="w-3.5 h-3.5" />}
            Démarrer
          </button>
        )}
        {canCancel && (
          <button onClick={() => void cancelCapture()} disabled={busy} aria-label="Annuler l’enregistrement" className="w-9 h-9 rounded-lg bg-white/5 border border-white/10 flex items-center justify-center disabled:opacity-50">
            <X className="w-3.5 h-3.5" />
          </button>
        )}
      </div>
      {error && (
        <p role="alert" className="mt-3 flex items-center gap-2 text-xs text-amber-300">
          <AlertCircle className="w-3.5 h-3.5 shrink-0" /> {error}
        </p>
      )}
      <p className="mt-2 text-[11px] text-muted-foreground">
        Segments autonomes de 30 s, deux uploads maximum en mémoire et trois essais réseau. La transcription reste locale.
      </p>
    </div>
  )
}
