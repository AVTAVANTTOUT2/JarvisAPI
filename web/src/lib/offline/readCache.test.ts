import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { clearOfflineDB } from './db'
import { cacheRead, getCachedRead } from './readCache'

// Note : `vi.useFakeTimers()` bloque indéfiniment les transactions IndexedDB
// (fake-indexeddb dépend du scheduling réel) — on mocke `Date.now()` seul.

describe('offline read cache', () => {
  beforeEach(async () => {
    await clearOfflineDB()
  })

  afterEach(async () => {
    await clearOfflineDB()
    vi.restoreAllMocks()
  })

  it('returns null when nothing cached', async () => {
    expect(await getCachedRead('tasks')).toBeNull()
  })

  it('round-trips cached data', async () => {
    await cacheRead('tasks', [{ id: 1, title: 'Faire les courses' }])
    const result = await getCachedRead<{ id: number; title: string }[]>('tasks')
    expect(result).not.toBeNull()
    expect(result?.data).toEqual([{ id: 1, title: 'Faire les courses' }])
  })

  it('reports staleness in milliseconds', async () => {
    vi.spyOn(Date, 'now').mockReturnValue(0)
    await cacheRead('tasks', ['x'])
    vi.spyOn(Date, 'now').mockReturnValue(5000)

    const result = await getCachedRead('tasks')
    expect(result?.staleMs).toBe(5000)
  })

  it('returns null once past maxAgeMs', async () => {
    vi.spyOn(Date, 'now').mockReturnValue(0)
    await cacheRead('tasks', ['x'])
    vi.spyOn(Date, 'now').mockReturnValue(10_000)

    expect(await getCachedRead('tasks', 5_000)).toBeNull()
  })

  it('overwrites previous value for the same key', async () => {
    await cacheRead('tasks', ['old'])
    await cacheRead('tasks', ['new'])
    const result = await getCachedRead('tasks')
    expect(result?.data).toEqual(['new'])
  })

  it('keeps separate keys independent', async () => {
    await cacheRead('tasks', ['t'])
    await cacheRead('notifications', ['n'])
    expect((await getCachedRead('tasks'))?.data).toEqual(['t'])
    expect((await getCachedRead('notifications'))?.data).toEqual(['n'])
  })
})
