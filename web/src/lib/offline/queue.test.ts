import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { clearOfflineDB } from './db'
import {
  enqueueWrite,
  flushQueue,
  isNetworkError,
  listQueuedWrites,
  operationChecksum,
  removeQueuedWrite,
  resolveQueuedWrite,
} from './queue'

describe('isNetworkError', () => {
  it('recognizes a TypeError (fetch network failure) as a network error', () => {
    expect(isNetworkError(new TypeError('Failed to fetch'))).toBe(true)
  })

  it('does not treat a generic Error as a network error', () => {
    expect(isNetworkError(new Error('API 400'))).toBe(false)
  })

  it('does not treat a plain object as a network error', () => {
    expect(isNetworkError({ status: 500 })).toBe(false)
  })
})

describe('offline write queue', () => {
  beforeEach(async () => {
    await clearOfflineDB()
    vi.restoreAllMocks()
  })

  afterEach(async () => {
    await clearOfflineDB()
  })

  it('enqueues a write and lists it', async () => {
    const id = await enqueueWrite({
      method: 'POST',
      path: '/api/tasks',
      body: { title: 'Acheter du lait' },
      label: 'Nouvelle tâche : Acheter du lait',
    })

    const writes = await listQueuedWrites()
    expect(writes).toHaveLength(1)
    expect(writes[0].id).toBe(id)
    expect(writes[0].attempts).toBe(0)
    expect(writes[0].status).toBe('pending')
    expect(writes[0].checksum).toMatch(/^[a-f0-9]{64}$/)
    expect(writes[0].label).toBe('Nouvelle tâche : Acheter du lait')
  })

  it('preserves chronological order across multiple writes', async () => {
    await enqueueWrite({ method: 'POST', path: '/api/tasks', body: { title: 'A' }, label: 'A' })
    await enqueueWrite({ method: 'POST', path: '/api/tasks', body: { title: 'B' }, label: 'B' })
    await enqueueWrite({ method: 'POST', path: '/api/tasks', body: { title: 'C' }, label: 'C' })

    const writes = await listQueuedWrites()
    expect(writes.map((w) => w.label)).toEqual(['A', 'B', 'C'])
  })

  it('removes a queued write by id', async () => {
    const id = await enqueueWrite({ method: 'POST', path: '/api/tasks', body: {}, label: 'X' })
    await removeQueuedWrite(id)
    expect(await listQueuedWrites()).toHaveLength(0)
  })

  it('flushQueue replays writes and clears them on success', async () => {
    await enqueueWrite({ method: 'POST', path: '/api/tasks', body: { title: 'A' }, label: 'A' })
    await enqueueWrite({ method: 'POST', path: '/api/tasks', body: { title: 'B' }, label: 'B' })

    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 })
    vi.stubGlobal('fetch', fetchMock)

    const result = await flushQueue()

    expect(result).toEqual({ ok: 2, failed: 0, conflicts: 0 })
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(await listQueuedWrites()).toHaveLength(0)
  })

  it('keeps a write queued when the server is unreachable (network error)', async () => {
    await enqueueWrite({ method: 'POST', path: '/api/tasks', body: { title: 'A' }, label: 'A' })
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    const result = await flushQueue()

    expect(result.failed).toBe(1)
    expect(await listQueuedWrites()).toHaveLength(1)
  })

  it('keeps a definitive 4xx visible for manual retry or discard', async () => {
    await enqueueWrite({ method: 'POST', path: '/api/tasks', body: {}, label: 'invalid' })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 400 }))

    const result = await flushQueue()

    expect(result.failed).toBe(1)
    const writes = await listQueuedWrites()
    expect(writes).toHaveLength(1)
    expect(writes[0].status).toBe('failed')
  })

  it('keeps a write queued on 401 (session expirée, pas une erreur définitive)', async () => {
    await enqueueWrite({ method: 'POST', path: '/api/tasks', body: {}, label: 'x' })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 401 }))

    const result = await flushQueue()

    expect(await listQueuedWrites()).toHaveLength(1)
    expect(result.failed).toBe(1)
  })

  it('stops trying subsequent writes once a network error occurs (avoids hammering)', async () => {
    await enqueueWrite({ method: 'POST', path: '/api/tasks', body: {}, label: 'A' })
    await enqueueWrite({ method: 'POST', path: '/api/tasks', body: {}, label: 'B' })
    const fetchMock = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'))
    vi.stubGlobal('fetch', fetchMock)

    await flushQueue()

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(await listQueuedWrites()).toHaveLength(2)
  })

  it('processes remaining writes after one succeeds', async () => {
    await enqueueWrite({ method: 'POST', path: '/api/tasks', body: { title: 'A' }, label: 'A' })
    await enqueueWrite({ method: 'POST', path: '/api/tasks', body: { title: 'B' }, label: 'B' })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 201 }))

    const result = await flushQueue()

    expect(result.ok).toBe(2)
    expect(await listQueuedWrites()).toHaveLength(0)
  })

  it('uses the exact checksum contract shared with the backend', async () => {
    await expect(operationChecksum('POST', '/api/tasks', { title: 'A' })).resolves.toBe(
      '860f1b02931f3949ef0958229e867c4abfb9a99e23d38726a219b021f8193d5e',
    )
  })

  it('marks a version conflict without replaying later writes for the same entity', async () => {
    await enqueueWrite({
      method: 'POST',
      path: '/api/tasks',
      body: { title: 'A' },
      label: 'A',
      baseVersion: 1,
    })
    await enqueueWrite({ method: 'POST', path: '/api/tasks', body: { title: 'B' }, label: 'B' })
    const conflict = new Response(JSON.stringify({
      error: 'sync_version_conflict',
      server_version: 2,
    }), { status: 409 })
    const fetchMock = vi.fn().mockResolvedValue(conflict)
    vi.stubGlobal('fetch', fetchMock)

    const result = await flushQueue()

    expect(result).toEqual({ ok: 0, failed: 0, conflicts: 1 })
    expect(fetchMock).toHaveBeenCalledOnce()
    const writes = await listQueuedWrites()
    expect(writes[0]).toMatchObject({ status: 'conflict', serverVersion: 2 })
    expect(writes[1].status).toBe('pending')
  })

  it('supports explicit client-wins and server-wins resolution', async () => {
    const clientWrite = await enqueueWrite({
      method: 'PATCH',
      path: '/api/tasks/1',
      body: { status: 'done' },
      label: 'Client',
      baseVersion: 1,
    })
    const serverWrite = await enqueueWrite({
      method: 'DELETE',
      path: '/api/tasks/2',
      body: undefined,
      label: 'Serveur',
      baseVersion: 1,
    })
    const { updateQueuedWrite } = await import('./queue')
    await updateQueuedWrite(clientWrite, { status: 'conflict', serverVersion: 3 })
    await updateQueuedWrite(serverWrite, { status: 'conflict', serverVersion: 3 })

    await resolveQueuedWrite(clientWrite, 'client_wins')
    await resolveQueuedWrite(serverWrite, 'server_wins')

    const writes = await listQueuedWrites()
    expect(writes).toHaveLength(1)
    expect(writes[0]).toMatchObject({
      id: clientWrite,
      status: 'pending',
      baseVersion: 3,
      conflictStrategy: 'client_wins',
    })
  })
})
