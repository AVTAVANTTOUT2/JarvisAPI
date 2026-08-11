import { openDB, type DBSchema, type IDBPDatabase } from 'idb'

export interface QueuedWrite {
  id: string
  method: 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  path: string
  body: unknown
  createdAt: number
  attempts: number
  checksum: string
  entityKey: string
  baseVersion: number | null
  serverVersion?: number
  status: 'pending' | 'conflict' | 'failed'
  conflictStrategy?: 'client_wins'
  lastAttemptAt?: number
  lastError?: string
  /** Libellé lisible et non sensible affiché dans l'état de synchronisation. */
  label: string
}

export interface CachedRead {
  key: string
  data: unknown
  cachedAt: number
  entityVersion?: number
}

interface JarvisOfflineDB extends DBSchema {
  writeQueue: {
    key: number
    value: QueuedWrite
    indexes: { 'by-id': string }
  }
  readCache: {
    key: string
    value: CachedRead
  }
}

let dbPromise: Promise<IDBPDatabase<JarvisOfflineDB>> | null = null

/** Base IndexedDB commune aux vues desktop et mobiles. */
export function getOfflineDB(): Promise<IDBPDatabase<JarvisOfflineDB>> {
  if (!dbPromise) {
    dbPromise = openDB<JarvisOfflineDB>('jarvis-offline', 1, {
      upgrade(db) {
        const writeStore = db.createObjectStore('writeQueue', { autoIncrement: true })
        writeStore.createIndex('by-id', 'id', { unique: true })
        db.createObjectStore('readCache', { keyPath: 'key' })
      },
    })
  }
  return dbPromise
}

/** Purge les données privées locales lors du verrouillage ou de la déconnexion. */
export async function clearOfflineDB(): Promise<void> {
  const db = await getOfflineDB()
  await db.clear('writeQueue')
  await db.clear('readCache')
}
