import { afterEach, describe, expect, it, vi } from 'vitest'

import { api, ApiError } from './api'

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

  it('surfaces only the stable structured API error message', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: {
            code: 'service_logs_unavailable',
            message: 'Logs du service indisponibles',
          },
        }),
        { status: 500 },
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(api.getServiceLogs('scheduler')).rejects.toMatchObject({
      name: 'ApiError',
      message: 'Logs du service indisponibles',
      status: 500,
    } satisfies Partial<ApiError>)
  })

  it('does not expose an unstructured proxy error body', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response('<html>private reverse proxy trace</html>', { status: 502 }),
      ),
    )

    await expect(api.getServiceLogs('scheduler')).rejects.toMatchObject({
      message: 'API 502',
      status: 502,
    })
  })
})
