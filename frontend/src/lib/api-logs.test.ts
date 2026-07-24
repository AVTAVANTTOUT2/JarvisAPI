import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from './api'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('action logs API', () => {
  it('uses the protected DELETE endpoint to clear logs', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          ok: true,
          deleted_count: 2,
          deleted: { llm_action_logs: 1, dev_loop_log: 1 },
        }),
        { status: 200 },
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(api.clearLogs()).resolves.toMatchObject({
      ok: true,
      deleted_count: 2,
    })
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/logs',
      expect.objectContaining({
        method: 'DELETE',
        credentials: 'include',
      }),
    )
  })
})
