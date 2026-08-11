import { getOfflineDB } from './db'

export const DEFAULT_READ_CACHE_TTL_MS = 24 * 60 * 60 * 1000

/** Met en cache la dernière réponse JSON connue d'une route de lecture. */
export async function cacheRead(key: string, data: unknown, entityVersion?: number): Promise<void> {
  const db = await getOfflineDB()
  await db.put('readCache', { key, data, cachedAt: Date.now(), entityVersion })
}

/** Lit le cache si présent et pas plus vieux que `maxAgeMs`. */
export async function getCachedRead<T>(
  key: string,
  maxAgeMs = DEFAULT_READ_CACHE_TTL_MS,
): Promise<{ data: T; staleMs: number; entityVersion?: number } | null> {
  const db = await getOfflineDB()
  const row = await db.get('readCache', key)
  if (!row) return null
  const staleMs = Date.now() - row.cachedAt
  if (staleMs > maxAgeMs) return null
  return { data: row.data as T, staleMs, entityVersion: row.entityVersion }
}

/** Version serveur la plus récente connue pour une racine de ressource. */
export async function getLatestCachedVersion(resourcePath: string): Promise<number | null> {
  const db = await getOfflineDB()
  const rows = await db.getAll('readCache')
  const prefix = resourcePath.replace(/\/$/, '')
  const candidate = rows
    .filter((row) => (
      (row.key === prefix || row.key.startsWith(`${prefix}?`) || row.key.startsWith(`${prefix}/`))
      && Number.isInteger(row.entityVersion)
    ))
    .sort((left, right) => right.cachedAt - left.cachedAt)[0]
  return candidate?.entityVersion ?? null
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
