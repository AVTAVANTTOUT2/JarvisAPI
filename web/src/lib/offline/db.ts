import { openDB, type DBSchema, type IDBPDatabase } from 'idb'

export interface QueuedWrite {
  id: string
  method: 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  path: string
  body: unknown
  createdAt: number
  attempts: number
  /** Libellé lisible pour l'UI, ex. "Nouvelle tâche : Acheter du lait" */
  label: string
}

export interface CachedRead {
  key: string
  data: unknown
  cachedAt: number
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

/**
 * Base IndexedDB partagée — file d'écriture hors-ligne + cache de lecture.
 *
 * `writeQueue` utilise une clé primaire auto-incrémentée (`seq`, hors-ligne
 * dans le record) pour garantir l'ordre chronologique d'insertion même si
 * plusieurs écritures arrivent dans la même milliseconde — `Date.now()`
 * seul n'est pas assez fin pour ça. `id` (uuid) reste la référence externe
 * stable (indexée) pour la suppression après synchronisation.
 */
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

/** Purge complète — appelée à la déconnexion (verrouillage/logout) par hygiène de confidentialité. */
export async function clearOfflineDB(): Promise<void> {
  const db = await getOfflineDB()
  await db.clear('writeQueue')
  await db.clear('readCache')
}
