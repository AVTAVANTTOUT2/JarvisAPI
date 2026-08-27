import { describe, expect, it, vi } from 'vitest'

import {
  LONG_RECORDING_MAX_PENDING_UPLOADS,
  LONG_RECORDING_STORAGE_KEY,
  clearStoredLongRecording,
  hasRecordingUploadCapacity,
  readStoredLongRecording,
  uploadRecordingSegmentWithRetry,
  writeStoredLongRecording,
} from './longRecording'

const ACK = {
  ok: true as const,
  protocol_version: 1 as const,
  session_id: '96a4cb74-b255-4fce-8d4e-90ffcf569db8',
  sequence: 4,
  status: 'duplicate' as const,
  accepted: false,
  duplicate: true,
  next_sequence: 5,
  received_chunks: 5,
  size_bytes: 4096,
  duration_ms: 150_000,
  checksum: '0'.repeat(64),
}

describe('long recording retry and resume contract', () => {
  it('bounds the in-memory upload queue to two pending segments', () => {
    expect(LONG_RECORDING_MAX_PENDING_UPLOADS).toBe(2)
    expect(hasRecordingUploadCapacity(0)).toBe(true)
    expect(hasRecordingUploadCapacity(1)).toBe(true)
    expect(hasRecordingUploadCapacity(2)).toBe(false)
    expect(hasRecordingUploadCapacity(3)).toBe(false)
  })

  it('retries transient failures at most three times and accepts a duplicate ACK', async () => {
    const upload = vi.fn()
      .mockRejectedValueOnce(Object.assign(new Error('busy'), { status: 503 }))
      .mockRejectedValueOnce(new TypeError('network'))
      .mockResolvedValueOnce(ACK)
    const wait = vi.fn(async () => undefined)

    await expect(uploadRecordingSegmentWithRetry(upload, wait)).resolves.toEqual(ACK)
    expect(upload).toHaveBeenCalledTimes(3)
    expect(wait.mock.calls).toEqual([[250], [500]])
  })

  it('does not retry a deterministic 4xx conflict', async () => {
    const conflict = Object.assign(new Error('conflict'), { status: 409 })
    const upload = vi.fn().mockRejectedValue(conflict)
    const wait = vi.fn(async () => undefined)

    await expect(uploadRecordingSegmentWithRetry(upload, wait)).rejects.toBe(conflict)
    expect(upload).toHaveBeenCalledTimes(1)
    expect(wait).not.toHaveBeenCalled()
  })

  it('persists only the durable ACK cursor needed to resume', () => {
    const storage = window.localStorage
    clearStoredLongRecording(storage)
    writeStoredLongRecording({
      clientRecordingId: '59acefae-8e09-48f8-8aa1-ea67fca04039',
      sessionId: ACK.session_id,
      label: 'Réunion',
      nextSequence: ACK.next_sequence,
      durationMs: ACK.duration_ms,
    }, storage)

    expect(readStoredLongRecording(storage)).toEqual({
      clientRecordingId: '59acefae-8e09-48f8-8aa1-ea67fca04039',
      sessionId: ACK.session_id,
      label: 'Réunion',
      nextSequence: 5,
      durationMs: 150_000,
    })
    expect(storage.getItem(LONG_RECORDING_STORAGE_KEY)).not.toContain('audio')
    clearStoredLongRecording(storage)
  })
})
