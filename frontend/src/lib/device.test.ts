import { describe, expect, it } from 'vitest'

import { UNIFIED_ROUTES, routeSegment } from './device'

describe('routeSegment', () => {
  it('extrait le premier segment en ignorant requête et fragment', () => {
    expect(routeSegment('/tasks/?filter=todo')).toBe('tasks')
    expect(routeSegment('/chat#bas')).toBe('chat')
  })

  it('retombe sur dashboard à la racine', () => {
    expect(routeSegment('/')).toBe('dashboard')
    expect(routeSegment('')).toBe('dashboard')
  })
})

describe('UNIFIED_ROUTES', () => {
  it('ne contient aucun doublon', () => {
    expect(new Set(UNIFIED_ROUTES).size).toBe(UNIFIED_ROUTES.length)
  })
})
