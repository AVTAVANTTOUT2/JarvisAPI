import { getOfflineDB } from './db'

export const DEFAULT_READ_CACHE_TTL_MS = 24 * 60 * 60 * 1000

/** Met en cache la dernière réponse JSON connue d'une route de lecture. */
export async function cacheRead(key: string, data: unknown): Promise<void> {
  const db = await getOfflineDB()
  await db.put('readCache', { key, data, cachedAt: Date.now() })
}

/** Lit le cache si présent et pas plus vieux que `maxAgeMs`. */
export async function getCachedRead<T>(
  key: string,
  maxAgeMs = DEFAULT_READ_CACHE_TTL_MS,
): Promise<{ data: T; staleMs: number } | null> {
  const db = await getOfflineDB()
  const row = await db.get('readCache', key)
  if (!row) return null
  const staleMs = Date.now() - row.cachedAt
  if (staleMs > maxAgeMs) return null
  return { data: row.data as T, staleMs }
}

/** Invalide les lectures d'une ressource après une mutation confirmée. */
export async function invalidateCachedReads(resourcePath: string): Promise<void> {
  const db = await getOfflineDB()
  const prefix = resourcePath.split('?')[0].replace(/\/$/, '')
  const keys = await db.getAllKeys('readCache')
  await Promise.all(
    keys
      .filter((key) => key === prefix || key.startsWith(`${prefix}?`) || key.startsWith(`${prefix}/`))
      .map((key) => db.delete('readCache', key)),
  )
}
