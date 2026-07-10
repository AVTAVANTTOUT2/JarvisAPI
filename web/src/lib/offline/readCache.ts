import { getOfflineDB } from './db'

const DEFAULT_TTL_MS = 24 * 60 * 60 * 1000 // 24h — juste pour affichage "dernières données connues"

/** Met en cache la dernière réponse connue d'une vue (ex. liste des tâches). */
export async function cacheRead(key: string, data: unknown): Promise<void> {
  const db = await getOfflineDB()
  await db.put('readCache', { key, data, cachedAt: Date.now() })
}

/** Lit le cache si présent et pas plus vieux que `maxAgeMs`. */
export async function getCachedRead<T>(
  key: string,
  maxAgeMs = DEFAULT_TTL_MS,
): Promise<{ data: T; staleMs: number } | null> {
  const db = await getOfflineDB()
  const row = await db.get('readCache', key)
  if (!row) return null
  const staleMs = Date.now() - row.cachedAt
  if (staleMs > maxAgeMs) return null
  return { data: row.data as T, staleMs }
}
