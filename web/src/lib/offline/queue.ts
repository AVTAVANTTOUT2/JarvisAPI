import { getOfflineDB, type QueuedWrite } from './db'
import { jarvisRawFetch } from '@unified/lib/api'

/**
 * True si `error` vient d'un vrai échec réseau (hors ligne) plutôt que
 * d'une réponse HTTP d'erreur — `fetch()` lève un `TypeError` dans tous
 * les navigateurs quand la requête n'a même pas pu partir.
 */
export function isNetworkError(error: unknown): boolean {
  return error instanceof TypeError
}

/** Ajoute une écriture à la file hors-ligne. Retourne son id. */
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

/** Écritures en attente, dans l'ordre chronologique d'insertion (clé auto-incrémentée). */
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
    if (!('serviceWorker' in navigator)) return
    const reg = await navigator.serviceWorker.ready
    const syncReg = reg as ServiceWorkerRegistration & {
      sync?: { register(tag: string): Promise<void> }
    }
    if (syncReg.sync) await syncReg.sync.register('jarvis-offline-queue')
  } catch {
    // Background Sync indisponible (Safari/iOS, ou navigateur qui refuse) —
    // le filet de sécurité periodique + l'événement 'online' prennent le relais.
  }
}

let flushing = false

/**
 * Rejoue les écritures en attente dans l'ordre chronologique.
 *
 * Politique de conflit volontairement simple : "dernière écriture gagne",
 * pas de fusion — chaque requête est rejouée telle quelle contre l'état
 * serveur actuel. Une vraie résolution de conflits multi-device nécessite
 * un versioning par entité, hors scope de ce lot.
 */
export async function flushQueue(): Promise<{ ok: number; failed: number }> {
  if (flushing) return { ok: 0, failed: 0 }
  flushing = true
  let ok = 0
  let failed = 0
  try {
    const writes = await listQueuedWrites()
    for (const w of writes) {
      try {
        const res = await jarvisRawFetch(w.path, {
          method: w.method,
          headers: w.body !== undefined ? { 'Content-Type': 'application/json' } : {},
          body: w.body !== undefined ? JSON.stringify(w.body) : undefined,
        })
        if (res.ok) {
          await removeQueuedWrite(w.id)
          ok++
        } else if (res.status >= 400 && res.status < 500 && res.status !== 401 && res.status !== 429) {
          // Erreur définitive (validation, etc.) — la rejouer indéfiniment ne servirait à rien.
          await removeQueuedWrite(w.id)
          failed++
        } else {
          failed++
        }
      } catch {
        failed++
        break // toujours hors ligne — inutile d'essayer les suivantes maintenant
      }
    }
  } finally {
    flushing = false
  }
  return { ok, failed }
}

/**
 * Démarre la resynchronisation automatique : au retour réseau (`online`),
 * au message du Service Worker (Background Sync), et par un filet de
 * sécurité périodique (Safari/iOS n'a pas Background Sync). Retourne une
 * fonction de nettoyage.
 */
export function initOfflineSync(onFlushed?: (result: { ok: number; failed: number }) => void): () => void {
  const tryFlush = async () => {
    if (!navigator.onLine) return
    const result = await flushQueue()
    if (result.ok > 0) onFlushed?.(result)
  }

  const onMessage = (event: MessageEvent) => {
    if (event.data?.type === 'jarvis:flush-offline-queue') void tryFlush()
  }

  window.addEventListener('online', tryFlush)
  navigator.serviceWorker?.addEventListener('message', onMessage)
  const interval = setInterval(tryFlush, 30_000)
  void tryFlush()

  return () => {
    window.removeEventListener('online', tryFlush)
    navigator.serviceWorker?.removeEventListener('message', onMessage)
    clearInterval(interval)
  }
}
