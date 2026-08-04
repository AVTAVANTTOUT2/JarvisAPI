import { describe, expect, it } from 'vitest'

import { formatRelativeTime, parseBackendTimestamp } from './timeFormat'

describe('parseBackendTimestamp', () => {
  it('treats timezone-less SQLite timestamps as UTC instants', () => {
    expect(parseBackendTimestamp('2026-07-16 17:23:50')).toBe(
      Date.parse('2026-07-16T17:23:50Z'),
    )
  })

  it('preserves explicit offsets', () => {
    expect(parseBackendTimestamp('2026-07-16T19:23:50+02:00')).toBe(
      Date.parse('2026-07-16T17:23:50Z'),
    )
  })

  it('preserves epoch values emitted by in-memory monitors', () => {
    expect(parseBackendTimestamp(1_721_136_000_000)).toBe(1_721_136_000_000)
  })

  it('keeps calendar-only dates in the local civil calendar', () => {
    const parsed = new Date(parseBackendTimestamp('2026-07-16'))
    expect([parsed.getFullYear(), parsed.getMonth(), parsed.getDate()]).toEqual([
      2026,
      6,
      16,
    ])
  })
})

describe('formatRelativeTime', () => {
  const now = Date.parse('2026-07-16T12:00:00Z')

  it('uses one contract for past and future instants', () => {
    expect(formatRelativeTime('2026-07-16T11:45:00Z', now)).toBe('il y a 15 min')
    expect(formatRelativeTime('2026-07-16T14:00:00Z', now)).toBe('dans 2h')
    expect(formatRelativeTime('2026-07-15T12:00:00Z', now)).toBe('hier')
    expect(formatRelativeTime('2026-07-17T12:00:00Z', now)).toBe('demain')
  })

  it('returns a stable empty marker for invalid values', () => {
    expect(formatRelativeTime(null, now)).toBe('—')
    expect(formatRelativeTime('invalid', now)).toBe('—')
  })
})
