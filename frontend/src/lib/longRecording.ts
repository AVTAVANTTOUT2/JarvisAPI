import type { RecordingChunkAck } from '@unified/types/api'

export const LONG_RECORDING_SEGMENT_MS = 30_000
export const LONG_RECORDING_MAX_PENDING_UPLOADS = 2
export const LONG_RECORDING_MAX_UPLOAD_ATTEMPTS = 3
export const LONG_RECORDING_STORAGE_KEY = 'jarvis.longRecording.v1'

export interface StoredLongRecording {
  clientRecordingId: string
  sessionId: string
  label: string
  nextSequence: number
  durationMs: number
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

export function hasRecordingUploadCapacity(pendingSegments: number): boolean {
  return Number.isInteger(pendingSegments)
    && pendingSegments >= 0
    && pendingSegments < LONG_RECORDING_MAX_PENDING_UPLOADS
}

export async function sha256Hex(blob: Blob): Promise<string> {
  const digest = await globalThis.crypto.subtle.digest('SHA-256', await blob.arrayBuffer())
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('')
}

export function isRetryableRecordingUpload(error: unknown): boolean {
  if (error instanceof TypeError) return true
  const status = Number((error as { status?: unknown } | null)?.status)
  return status === 408 || status === 429 || status >= 500
}

export async function uploadRecordingSegmentWithRetry(
  upload: () => Promise<RecordingChunkAck>,
  wait: (delayMs: number) => Promise<void> = (delayMs) =>
    new Promise((resolve) => globalThis.setTimeout(resolve, delayMs)),
): Promise<RecordingChunkAck> {
  let lastError: unknown
  for (let attempt = 0; attempt < LONG_RECORDING_MAX_UPLOAD_ATTEMPTS; attempt += 1) {
    try {
      return await upload()
    } catch (error) {
      lastError = error
      if (!isRetryableRecordingUpload(error) || attempt + 1 >= LONG_RECORDING_MAX_UPLOAD_ATTEMPTS) {
        throw error
      }
      await wait(250 * (2 ** attempt))
    }
  }
  throw lastError
}

export function readStoredLongRecording(storage: Storage = globalThis.localStorage): StoredLongRecording | null {
  try {
    const raw = storage.getItem(LONG_RECORDING_STORAGE_KEY)
    if (!raw) return null
    const value = JSON.parse(raw) as Partial<StoredLongRecording>
    if (
      !UUID_RE.test(String(value.clientRecordingId ?? ''))
      || !UUID_RE.test(String(value.sessionId ?? ''))
      || typeof value.label !== 'string'
      || !Number.isInteger(value.nextSequence)
      || Number(value.nextSequence) < 0
      || !Number.isFinite(value.durationMs)
      || Number(value.durationMs) < 0
    ) return null
    return {
      clientRecordingId: String(value.clientRecordingId).toLowerCase(),
      sessionId: String(value.sessionId).toLowerCase(),
      label: value.label.slice(0, 200),
      nextSequence: Number(value.nextSequence),
      durationMs: Number(value.durationMs),
    }
  } catch {
    return null
  }
}

export function writeStoredLongRecording(
  value: StoredLongRecording,
  storage: Storage = globalThis.localStorage,
): void {
  try {
    storage.setItem(LONG_RECORDING_STORAGE_KEY, JSON.stringify(value))
  } catch {
    // La capture reste fonctionnelle en navigation privée sans stockage durable.
  }
}

export function clearStoredLongRecording(storage: Storage = globalThis.localStorage): void {
  try {
    storage.removeItem(LONG_RECORDING_STORAGE_KEY)
  } catch {
    // Aucun état sensible ne doit bloquer le nettoyage de l'interface.
  }
}
