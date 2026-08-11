import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { jarvisFetch } from './api'
import { clearOfflineDB } from './offline/db'
import { listQueuedWrites } from './offline/queue'

describe('shared offline API policy', () => {
  beforeEach(async () => {
    await clearOfflineDB()
    vi.restoreAllMocks()
  })

  afterEach(async () => {
    await clearOfflineDB()
    vi.unstubAllGlobals()
  })

  it('serves the last JSON response to every view after a network failure', async () => {
    const payload = { tasks: [{ id: 1, title: 'Persistée' }] }
    const cacheHit = vi.fn()
    window.addEventListener('jarvis:offline-cache-hit', cacheHit)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(JSON.stringify(payload), { status: 200 }),
    ))

    await expect(jarvisFetch('/api/tasks')).resolves.toEqual(payload)
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    await expect(jarvisFetch('/api/tasks')).resolves.toEqual(payload)
    expect(cacheHit).toHaveBeenCalledOnce()
    window.removeEventListener('jarvis:offline-cache-hit', cacheHit)
  })

  it('lets volatile reads explicitly opt out of IndexedDB fallback', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: 'online' }), { status: 200 }),
    ))
    await jarvisFetch('/api/status', { offline: { cache: false } })
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    await expect(jarvisFetch('/api/status', { offline: { cache: false } })).rejects.toThrow(
      'Failed to fetch',
    )
  })

  it('never caches the public liveness probe', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: 'ok' }), { status: 200 }),
    ))
    await jarvisFetch('/api/health/live')
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    await expect(jarvisFetch('/api/health/live')).rejects.toThrow('Failed to fetch')
  })

  it('queues a safe data mutation and returns an explicit marker', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    const result = await jarvisFetch<{ queued: boolean; offline_queue_id: string }>('/api/tasks', {
      method: 'POST',
      body: JSON.stringify({ title: 'Acheter du lait' }),
    })

    expect(result.queued).toBe(true)
    expect(result.offline_queue_id).toEqual(expect.any(String))
    const writes = await listQueuedWrites()
    expect(writes).toHaveLength(1)
    expect(writes[0]).toMatchObject({
      method: 'POST',
      path: '/api/tasks',
      body: { title: 'Acheter du lait' },
    })
  })

  it('carries the last server entity version into an offline mutation', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ tasks: [] }), {
        status: 200,
        headers: { 'X-Jarvis-Entity-Version': '7' },
      }),
    ))
    await jarvisFetch('/api/tasks')
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    await jarvisFetch('/api/tasks', {
      method: 'POST',
      body: JSON.stringify({ title: 'Versionnée' }),
    })

    expect((await listQueuedWrites())[0]).toMatchObject({
      entityKey: '/api/tasks',
      baseVersion: 7,
    })
  })

  it('never queues commands or external side effects implicitly', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    await expect(jarvisFetch('/api/control/backend/restart', { method: 'POST' })).rejects.toThrow(
      'Failed to fetch',
    )
    await expect(jarvisFetch('/api/food/suggestions/1/order', { method: 'POST' })).rejects.toThrow(
      'Failed to fetch',
    )
    expect(await listQueuedWrites()).toHaveLength(0)
  })

  it('invalidates cached resource variants after a confirmed mutation', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ tasks: [{ id: 1 }] }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), { status: 200 }))
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
    vi.stubGlobal('fetch', fetchMock)

    await jarvisFetch('/api/tasks?status=todo')
    await jarvisFetch('/api/tasks/1', {
      method: 'PATCH',
      body: JSON.stringify({ status: 'done' }),
    })

    await expect(jarvisFetch('/api/tasks?status=todo')).rejects.toThrow('Failed to fetch')
  })
})
