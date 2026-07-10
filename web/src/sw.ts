/// <reference lib="webworker" />
/**
 * Service Worker JARVIS — app shell offline + notifications push.
 *
 * Précache l'app shell (JS/CSS/HTML) uniquement — jamais les réponses
 * /api/* (données personnelles) via le cache HTTP du navigateur. La
 * lecture hors-ligne de données passe par IndexedDB côté application
 * (src/lib/offline/), pas par ce Service Worker.
 */
import { ExpirationPlugin } from 'workbox-expiration'
import { createHandlerBoundToURL, precacheAndRoute } from 'workbox-precaching'
import { NavigationRoute, registerRoute } from 'workbox-routing'
import { CacheFirst, StaleWhileRevalidate } from 'workbox-strategies'

declare const self: ServiceWorkerGlobalScope

precacheAndRoute(self.__WB_MANIFEST)

registerRoute(
  ({ url }) => url.origin === 'https://fonts.googleapis.com',
  new StaleWhileRevalidate({ cacheName: 'google-fonts-stylesheets' }),
)
registerRoute(
  ({ url }) => url.origin === 'https://fonts.gstatic.com',
  new CacheFirst({
    cacheName: 'google-fonts-webfonts',
    plugins: [new ExpirationPlugin({ maxEntries: 8, maxAgeSeconds: 60 * 60 * 24 * 365 })],
  }),
)
registerRoute(
  ({ request }) => request.destination === 'image',
  new CacheFirst({
    cacheName: 'static-images',
    plugins: [new ExpirationPlugin({ maxEntries: 64, maxAgeSeconds: 60 * 60 * 24 * 30 })],
  }),
)

// App shell offline : toute navigation (hors /api, /ws) sert index.html depuis le précache.
registerRoute(
  new NavigationRoute(createHandlerBoundToURL('index.html'), {
    denylist: [/^\/api\//, /^\/ws/],
  }),
)

self.addEventListener('install', () => {
  self.skipWaiting()
})

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim())
})

// ── Notifications push ────────────────────────────────────────

self.addEventListener('push', (event) => {
  let data: { title?: string; body?: string; priority?: string } = {}
  try {
    data = event.data ? event.data.json() : {}
  } catch {
    data = { title: 'JARVIS', body: event.data ? event.data.text() : '' }
  }

  const title = data.title || 'JARVIS'
  event.waitUntil(
    self.registration.showNotification(title, {
      body: data.body || '',
      icon: '/icons/icon-192.png',
      badge: '/icons/icon-192.png',
      tag: data.priority === 'urgent' ? 'jarvis-urgent' : 'jarvis-notification',
      requireInteraction: data.priority === 'urgent',
      data,
    }),
  )
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if ('focus' in client) return client.focus()
      }
      return self.clients.openWindow('/chat')
    }),
  )
})

// ── File d'attente hors-ligne : synchronisation en arrière-plan ─
// Background Sync API (Chrome/Android) — meilleur effort, indisponible sur
// Safari/iOS où la reprise se fait uniquement via l'écouteur 'online' côté page.

self.addEventListener('sync', (event: Event) => {
  const syncEvent = event as unknown as { tag: string; waitUntil: (p: Promise<unknown>) => void }
  if (syncEvent.tag === 'jarvis-offline-queue') {
    syncEvent.waitUntil(
      self.clients.matchAll({ type: 'window' }).then((clients) => {
        for (const client of clients) {
          client.postMessage({ type: 'jarvis:flush-offline-queue' })
        }
      }),
    )
  }
})
