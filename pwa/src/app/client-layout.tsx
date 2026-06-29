'use client';

import { useEffect, type ReactNode } from 'react';

import { isSecureContextForGeo, startTracking, stopTracking } from '@/lib/geolocation';

import Providers from './providers';

export function ClientLayout({ children }: { children: ReactNode }) {
  useEffect(() => {
    // Service worker (cache offline + push notifications)
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker
        .register('/sw.js')
        .then((reg) => {
          console.log('[SW] Registered:', reg.scope);
        })
        .catch((err) => console.error('[SW] Registration failed:', err));
    }

    // Tracking GPS — demarre le watcher uniquement en contexte securise.
    //
    // Safari iOS bloque l'API Geolocation sur HTTP non-localhost (contexte non
    // securise). Si la PWA est accedee via http://<ip>:3000 (Tailscale),
    // window.isSecureContext === false et getCurrentPosition echoue
    // silencieusement sans jamais afficher le prompt de permission.
    //
    // La solution : le serveur Next.js doit tourner en HTTPS
    // (--experimental-https dans package.json). Safari montrera un
    // avertissement de certificat auto-signe au 1er acces — une fois
    // accepte, le contexte devient securise et la geolocation fonctionne.
    //
    // Cas 1: contexte non securise → on n'essaie meme pas, on log un
    //        avertissement explicite (le widget LocationWidget affiche
    //        aussi un message visible).
    // Cas 2: permission "granted" → startTracking() silencieux.
    // Cas 3: permission "prompt" (1ere visite) → startTracking() declenche
    //        le prompt natif iOS.
    // Cas 4: permission "denied" → on ne fait rien.
    if (!isSecureContextForGeo()) {
      console.warn(
        '[geo] Contexte non securise (HTTP non-localhost). ' +
          'La geolocation est bloquee par le navigateur. ' +
          'Passer en HTTPS pour activer le tracking GPS.'
      );
    } else if (typeof navigator !== 'undefined' && 'geolocation' in navigator) {
      if ('permissions' in navigator) {
        navigator.permissions
          .query({ name: 'geolocation' as PermissionName })
          .then((result) => {
            if (result.state === 'granted' || result.state === 'prompt') {
              startTracking();
            }
            result.onchange = () => {
              if (result.state === 'granted') startTracking();
              else stopTracking();
            };
          })
          .catch(() => {
            startTracking();
          });
      } else {
        startTracking();
      }
    }

    return () => {
      stopTracking();
    };
  }, []);

  return (
    <Providers>
      <>{children}</>
    </Providers>
  );
}
