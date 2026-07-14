export const MOBILE_ROUTES = ['dashboard', 'map', 'mails', 'tasks', 'config'] as const

export const UNIFIED_ROUTES = [
  'chat', 'voice', 'tasks', 'documents', 'memory', 'status', 'dashboard',
  'contacts', 'map', 'analytics', 'search', 'data', 'conversations', 'calendar',
  'logs', 'monitoring', 'voice-debug', 'control', 'mission', 'mails', 'config',
] as const

const PHONE_USER_AGENT = /Android.*Mobile|iPhone|iPod|webOS|Windows Phone|Opera Mini|BlackBerry|IEMobile/i

/** Sélectionne le layout mobile pour un téléphone ou un viewport étroit. */
export function shouldUseMobileLayout(userAgent: string, viewportWidth: number): boolean {
  if (/Android(?!.*Mobile)/i.test(userAgent)) return false
  return viewportWidth < 768 || PHONE_USER_AGENT.test(userAgent)
}

export function routeSegment(pathname: string): string {
  return pathname.split('?')[0]?.split('#')[0]?.split('/').filter(Boolean)[0] ?? 'dashboard'
}
