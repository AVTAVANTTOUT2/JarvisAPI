import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from './api'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('health API', () => {
  it('preserves the diagnostic report returned with HTTP 503', async () => {
    const report = {
      status: 'unavailable',
      checked_at: '2026-08-10T17:00:00+00:00',
      duration_ms: 12,
      summary: { healthy: 4, degraded: 0, unavailable: 1, unknown: 1 },
      components: [
        {
          name: 'database',
          state: 'unavailable',
          critical: true,
          reason: 'database_unreachable',
          details: {},
        },
      ],
    }
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(report), {
          status: 503,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    )

    await expect(api.getHealthDetail()).resolves.toEqual(report)
  })
})
