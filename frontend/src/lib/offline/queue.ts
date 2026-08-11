import { jarvisRawFetch } from '../http'
import { getOfflineDB, type QueuedWrite } from './db'
import { invalidateCachedReads } from './readCache'

export function isNetworkError(error: unknown): boolean {
  return error instanceof TypeError
}

export async function enqueueWrite(
  input: Omit<QueuedWrite, 'id' | 'createdAt' | 'attempts'>,
): Promise<string> {
  const db = await getOfflineDB()
  const id = crypto.randomUUID()
  const record: QueuedWrite = { ...input, id, createdAt: Date.now(), attempts: 0 }
  await db.add('writeQueue', record)
  void requestBackgroundSync()
  return id
}

export async function listQueuedWrites(): Promise<QueuedWrite[]> {
  const db = await getOfflineDB()
  return db.getAll('writeQueue')
}

export async function removeQueuedWrite(id: string): Promise<void> {
  const db = await getOfflineDB()
  const key = await db.getKeyFromIndex('writeQueue', 'by-id', id)
  if (key !== undefined) await db.delete('writeQueue', key)
}

async function requestBackgroundSync(): Promise<void> {
  try {
    if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)) return
    const reg = await navigator.serviceWorker.ready
    const syncReg = reg as ServiceWorkerRegistration & {
      sync?: { register(tag: string): Promise<void> }
    }
    if (syncReg.sync) await syncReg.sync.register('jarvis-offline-queue')
  } catch {
    // Safari/iOS et certains contextes privés refusent Background Sync.
  }
}

let flushing = false
let stopActiveSync: (() => void) | null = null

export async function flushQueue(): Promise<{ ok: number; failed: number }> {
  if (flushing) return { ok: 0, failed: 0 }
  flushing = true
  let ok = 0
  let failed = 0
  try {
    const writes = await listQueuedWrites()
    for (const write of writes) {
      try {
        const response = await jarvisRawFetch(write.path, {
          method: write.method,
          headers: {
            ...(write.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
            'X-Idempotency-Key': write.id,
          },
          body: write.body !== undefined ? JSON.stringify(write.body) : undefined,
        })
        if (response.ok) {
          await removeQueuedWrite(write.id)
          await invalidateCachedReads(resourceRoot(write.path))
          ok++
        } else if (
          response.status >= 400
          && response.status < 500
          && response.status !== 401
          && response.status !== 409
          && response.status !== 429
        ) {
          await removeQueuedWrite(write.id)
          failed++
        } else {
          failed++
        }
      } catch {
        failed++
        break
      }
    }
  } finally {
    flushing = false
  }
  return { ok, failed }
}

/** Racine de ressource utilisée pour invalider les variantes GET associées. */
export function resourceRoot(path: string): string {
  const segments = path.split('?')[0].split('/').filter(Boolean)
  return segments.length >= 2 ? `/${segments.slice(0, 2).join('/')}` : path
}

export function initOfflineSync(
  onFlushed?: (result: { ok: number; failed: number }) => void,
): () => void {
  stopActiveSync?.()

  const tryFlush = async () => {
    if (!navigator.onLine) return
    const result = await flushQueue()
    if (result.ok > 0 || result.failed > 0) onFlushed?.(result)
  }

  const onMessage = (event: MessageEvent) => {
    if (event.data?.type === 'jarvis:flush-offline-queue') void tryFlush()
  }

  window.addEventListener('online', tryFlush)
  navigator.serviceWorker?.addEventListener('message', onMessage)
  const interval = setInterval(tryFlush, 30_000)
  void tryFlush()

  const stop = () => {
    window.removeEventListener('online', tryFlush)
    navigator.serviceWorker?.removeEventListener('message', onMessage)
    clearInterval(interval)
    if (stopActiveSync === stop) stopActiveSync = null
  }
  stopActiveSync = stop
  return stop
}
