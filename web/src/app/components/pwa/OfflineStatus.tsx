import { useCallback, useEffect, useState } from 'react';
import { CloudOff, CloudUpload, Database } from 'lucide-react';
import { listQueuedWrites } from '@desktop/lib/offline/queue';

interface CacheHitDetail {
  staleMs?: number;
}

function formatAge(milliseconds: number): string {
  const minutes = Math.max(0, Math.floor(milliseconds / 60_000));
  if (minutes < 1) return 'à l’instant';
  if (minutes < 60) return `il y a ${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return `il y a ${hours} h`;
}

/** Statut global et accessible du cache privé IndexedDB et de la file de reprise. */
export function OfflineStatus() {
  const [online, setOnline] = useState(() => (
    typeof navigator === 'undefined' ? true : navigator.onLine
  ));
  const [pending, setPending] = useState(0);
  const [cacheAge, setCacheAge] = useState<number | null>(null);

  const refreshPending = useCallback(async () => {
    try {
      setPending((await listQueuedWrites()).length);
    } catch {
      setPending(0);
    }
  }, []);

  useEffect(() => {
    let cacheTimer: ReturnType<typeof setTimeout> | undefined;
    const onOnline = () => {
      setOnline(true);
      setCacheAge(null);
      void refreshPending();
    };
    const onOffline = () => setOnline(false);
    const onCacheHit = (event: Event) => {
      const detail = (event as CustomEvent<CacheHitDetail>).detail;
      setCacheAge(detail?.staleMs ?? 0);
      if (cacheTimer) clearTimeout(cacheTimer);
      cacheTimer = setTimeout(() => setCacheAge(null), 8_000);
    };
    const onQueueChanged = () => void refreshPending();

    window.addEventListener('online', onOnline);
    window.addEventListener('offline', onOffline);
    window.addEventListener('jarvis:offline-cache-hit', onCacheHit);
    window.addEventListener('jarvis:offline-write-queued', onQueueChanged);
    window.addEventListener('jarvis:offline-sync-done', onQueueChanged);
    void refreshPending();

    return () => {
      window.removeEventListener('online', onOnline);
      window.removeEventListener('offline', onOffline);
      window.removeEventListener('jarvis:offline-cache-hit', onCacheHit);
      window.removeEventListener('jarvis:offline-write-queued', onQueueChanged);
      window.removeEventListener('jarvis:offline-sync-done', onQueueChanged);
      if (cacheTimer) clearTimeout(cacheTimer);
    };
  }, [refreshPending]);

  if (online && pending === 0 && cacheAge === null) return null;

  return (
    <div
      aria-live="polite"
      className="fixed bottom-4 left-4 z-[100] flex max-w-sm flex-col gap-2 rounded-xl border border-border/80 bg-background/95 px-4 py-3 text-sm shadow-xl backdrop-blur"
      data-testid="offline-status"
    >
      {!online && (
        <div className="flex items-center gap-2 text-amber-500">
          <CloudOff className="h-4 w-4 shrink-0" aria-hidden="true" />
          <span>Hors ligne — JARVIS affiche les dernières données connues.</span>
        </div>
      )}
      {cacheAge !== null && (
        <div className="flex items-center gap-2 text-muted-foreground">
          <Database className="h-4 w-4 shrink-0" aria-hidden="true" />
          <span>Données locales enregistrées {formatAge(cacheAge)}.</span>
        </div>
      )}
      {pending > 0 && (
        <div className="flex items-center gap-2 text-sky-500">
          <CloudUpload className="h-4 w-4 shrink-0" aria-hidden="true" />
          <span>{pending} modification{pending > 1 ? 's' : ''} en attente de synchronisation.</span>
        </div>
      )}
    </div>
  );
}
