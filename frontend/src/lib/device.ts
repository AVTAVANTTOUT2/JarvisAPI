/**
 * Routes exportées statiquement par le build Next.js.
 *
 * La détection mobile a quitté ce module : elle se fait désormais côté
 * serveur (api/web_mobile.py), avant même que le bundle bureau ne soit
 * téléchargé.
 */
export const UNIFIED_ROUTES = [
  'chat', 'voice', 'tasks', 'fitness', 'documents', 'memory', 'status', 'dashboard',
  'contacts', 'map', 'analytics', 'search', 'data', 'conversations', 'calendar',
  'logs', 'monitoring', 'voice-debug', 'control', 'mission', 'mobile', 'mails', 'config',
  'cognitive',
] as const

export function routeSegment(pathname: string): string {
  return pathname.split('?')[0]?.split('#')[0]?.split('/').filter(Boolean)[0] ?? 'dashboard'
}
