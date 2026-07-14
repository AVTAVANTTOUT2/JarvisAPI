'use client';

import { useEffect, type ReactNode } from 'react';
import { LockGate } from '@jarvis/auth';

import { isSecureContextForGeo, startTracking, stopTracking } from '@mobile/lib/geolocation';

import Providers from './providers';

function startAuthenticatedTracking(): () => void {
  let active = true;
  let permissionStatus: PermissionStatus | null = null;
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
          permissionStatus = result;
          if (!active) return;
          if (result.state === 'granted' || result.state === 'prompt') {
            startTracking();
          }
          result.onchange = () => {
            if (!active) return;
            if (result.state === 'granted') startTracking();
            else stopTracking();
          };
        })
        .catch(() => {
          if (active) startTracking();
        });
    } else {
      startTracking();
    }
  }
  return () => {
    active = false;
    if (permissionStatus) permissionStatus.onchange = null;
    stopTracking();
  };
}

export function ClientLayout({ children }: { children: ReactNode }) {
  useEffect(() => {
    // Service worker (cache offline + push notifications). Le même composant
    // fonctionne dans l'ancien build /m/ et dans le frontend unifié à la racine.
    if ('serviceWorker' in navigator) {
      const serviceWorkerUrl = window.location.pathname.startsWith('/m/') ? '/m/sw.js' : '/sw.js';
      navigator.serviceWorker
        .register(serviceWorkerUrl)
        .then((reg) => {
          console.log('[SW] Registered:', reg.scope);
        })
        .catch((err) => console.error('[SW] Registration failed:', err));
    }

  }, []);

  return (
    <LockGate onAuthenticated={startAuthenticatedTracking}>
      <Providers>
        <>{children}</>
      </Providers>
    </LockGate>
  );
}
