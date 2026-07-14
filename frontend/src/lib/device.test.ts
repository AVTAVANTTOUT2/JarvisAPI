import { describe, expect, it } from 'vitest'

import { routeSegment, shouldUseMobileLayout } from './device'

describe('responsive layout selection', () => {
  it('uses the mobile layout for phones and narrow viewports', () => {
    expect(shouldUseMobileLayout('Mozilla/5.0 (iPhone)', 1170)).toBe(true)
    expect(shouldUseMobileLayout('Mozilla/5.0 (Macintosh)', 390)).toBe(true)
  })

  it('keeps Android tablets and wide desktop screens on desktop', () => {
    expect(shouldUseMobileLayout('Mozilla/5.0 (Linux; Android 15; Pixel Tablet)', 600)).toBe(false)
    expect(shouldUseMobileLayout('Mozilla/5.0 (Macintosh)', 1440)).toBe(false)
  })

  it('extracts a stable route segment', () => {
    expect(routeSegment('/tasks/?filter=todo')).toBe('tasks')
    expect(routeSegment('/')).toBe('dashboard')
  })
})
