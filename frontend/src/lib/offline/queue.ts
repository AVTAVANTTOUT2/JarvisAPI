import { jarvisRawFetch } from '../http'
import { getOfflineDB, type QueuedWrite } from './db'
import { invalidateCachedReads } from './readCache'

export function isNetworkError(error: unknown): boolean {
  return error instanceof TypeError
}

type EnqueueWriteInput = Omit<
  QueuedWrite,
  | 'id'
  | 'createdAt'
  | 'attempts'
  | 'checksum'
  | 'status'
  | 'serverVersion'
  | 'conflictStrategy'
  | 'lastAttemptAt'
  | 'lastError'
  | 'entityKey'
  | 'baseVersion'
> & {
  entityKey?: string
  baseVersion?: number | null
}

export async function operationChecksum(
  method: QueuedWrite['method'],
  path: string,
  body: unknown,
): Promise<string> {
  const serializedBody = body === undefined ? '' : JSON.stringify(body)
  const bytes = new TextEncoder().encode(`${method}\n${path}\n${serializedBody}`)
  const digest = await crypto.subtle.digest('SHA-256', bytes)
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('')
}

export async function enqueueWrite(
  input: EnqueueWriteInput,
): Promise<string> {
  const db = await getOfflineDB()
  const id = crypto.randomUUID()
  const entityKey = input.entityKey ?? resourceRoot(input.path)
  const record: QueuedWrite = {
    ...input,
    id,
    entityKey,
    baseVersion: input.baseVersion ?? null,
    checksum: await operationChecksum(input.method, input.path, input.body),
    status: 'pending',
    createdAt: Date.now(),
    attempts: 0,
  }
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

export async function updateQueuedWrite(
  id: string,
  patch: Partial<QueuedWrite>,
): Promise<void> {
  const db = await getOfflineDB()
  const key = await db.getKeyFromIndex('writeQueue', 'by-id', id)
  if (key === undefined) return
  const current = await db.get('writeQueue', key)
  if (current) await db.put('writeQueue', { ...current, ...patch }, key)
}

export async function resolveQueuedWrite(
  id: string,
  strategy: 'server_wins' | 'client_wins' | 'retry' | 'discard',
): Promise<void> {
  if (strategy === 'server_wins' || strategy === 'discard') {
    await removeQueuedWrite(id)
    return
  }
  const writes = await listQueuedWrites()
  const write = writes.find((item) => item.id === id)
  if (!write) return
  await updateQueuedWrite(id, {
    status: 'pending',
    baseVersion: strategy === 'client_wins'
      ? (write.serverVersion ?? write.baseVersion)
      : write.baseVersion,
    conflictStrategy: strategy === 'client_wins' ? 'client_wins' : undefined,
    lastError: undefined,
  })
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

export interface FlushResult {
  ok: number
  failed: number
  conflicts: number
}

async function conflictPayload(response: Response): Promise<{
  error?: string
  server_version?: number
}> {
  try {
    return await response.clone().json() as { error?: string; server_version?: number }
  } catch {
    return {}
  }
}

export async function flushQueue(): Promise<FlushResult> {
  if (flushing) return { ok: 0, failed: 0, conflicts: 0 }
  flushing = true
  let ok = 0
  let failed = 0
  let conflicts = 0
  const blockedEntities = new Set<string>()
  try {
    const writes = await listQueuedWrites()
    for (const storedWrite of writes) {
      const write: QueuedWrite = {
        ...storedWrite,
        entityKey: storedWrite.entityKey ?? resourceRoot(storedWrite.path),
        baseVersion: storedWrite.baseVersion ?? null,
        checksum: storedWrite.checksum
          ?? await operationChecksum(storedWrite.method, storedWrite.path, storedWrite.body),
        status: storedWrite.status ?? 'pending',
      }
      if (
        write.entityKey !== storedWrite.entityKey
        || write.baseVersion !== storedWrite.baseVersion
        || write.checksum !== storedWrite.checksum
        || write.status !== storedWrite.status
      ) {
        await updateQueuedWrite(write.id, write)
      }
      if (write.status === 'conflict' || write.status === 'failed') continue
      if (blockedEntities.has(write.entityKey)) continue
      try {
        await updateQueuedWrite(write.id, {
          attempts: write.attempts + 1,
          lastAttemptAt: Date.now(),
          lastError: undefined,
        })
        const response = await jarvisRawFetch(write.path, {
          method: write.method,
          headers: {
            ...(write.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
            'X-Idempotency-Key': write.id,
            'X-Jarvis-Sync-Operation': '1',
            'X-Jarvis-Operation-Checksum': write.checksum,
            ...(write.baseVersion !== null
              ? { 'X-Jarvis-Entity-Version': String(write.baseVersion) }
              : {}),
            ...(write.conflictStrategy
              ? { 'X-Jarvis-Conflict-Strategy': write.conflictStrategy }
              : {}),
          },
          body: write.body !== undefined ? JSON.stringify(write.body) : undefined,
        })
        if (response.ok) {
          await removeQueuedWrite(write.id)
          await invalidateCachedReads(resourceRoot(write.path))
          ok++
        } else if (response.status === 409 || response.status === 412) {
          const conflict = await conflictPayload(response)
          await updateQueuedWrite(write.id, {
            status: 'conflict',
            serverVersion: conflict.server_version,
            lastError: conflict.error ?? 'sync_version_conflict',
          })
          blockedEntities.add(write.entityKey)
          conflicts++
        } else if (
          response.status >= 400
          && response.status < 500
          && response.status !== 401
          && response.status !== 409
          && response.status !== 429
        ) {
          await updateQueuedWrite(write.id, {
            status: 'failed',
            lastError: `HTTP ${response.status}`,
          })
          failed++
        } else {
          await updateQueuedWrite(write.id, { lastError: `HTTP ${response.status}` })
          failed++
        }
      } catch (error) {
        await updateQueuedWrite(write.id, {
          lastError: error instanceof Error ? error.message : 'network_error',
        })
        failed++
        break
      }
    }
  } finally {
    flushing = false
  }
  return { ok, failed, conflicts }
}

/** Racine de ressource utilisée pour invalider les variantes GET associées. */
export function resourceRoot(path: string): string {
  const segments = path.split('?')[0].split('/').filter(Boolean)
  return segments.length >= 2 ? `/${segments.slice(0, 2).join('/')}` : path
}

export function initOfflineSync(
  onFlushed?: (result: FlushResult) => void,
): () => void {
  stopActiveSync?.()

  const tryFlush = async () => {
    if (!navigator.onLine) return
    const result = await flushQueue()
    if (result.ok > 0 || result.failed > 0 || result.conflicts > 0) onFlushed?.(result)
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
