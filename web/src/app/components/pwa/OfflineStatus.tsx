import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, CloudOff, CloudUpload, Database } from 'lucide-react';
import type { QueuedWrite } from '@desktop/lib/offline/db';
import {
  flushQueue,
  listQueuedWrites,
  resolveQueuedWrite,
} from '@desktop/lib/offline/queue';

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
  const [writes, setWrites] = useState<QueuedWrite[]>([]);
  const [cacheAge, setCacheAge] = useState<number | null>(null);
  const [resolvingId, setResolvingId] = useState<string | null>(null);

  const pending = writes.filter((write) => (write.status ?? 'pending') === 'pending').length;
  const actionable = writes.filter((write) => write.status === 'conflict' || write.status === 'failed');

  const refreshPending = useCallback(async () => {
    try {
      setWrites(await listQueuedWrites());
    } catch {
      setWrites([]);
    }
  }, []);

  const resolve = async (
    id: string,
    strategy: 'server_wins' | 'client_wins' | 'retry' | 'discard',
  ) => {
    setResolvingId(id);
    try {
      await resolveQueuedWrite(id, strategy);
      const result = strategy === 'client_wins' || strategy === 'retry'
        ? await flushQueue()
        : { ok: 0, failed: 0, conflicts: 0 };
      window.dispatchEvent(new CustomEvent('jarvis:offline-sync-done', { detail: result }));
      await refreshPending();
    } finally {
      setResolvingId(null);
    }
  };

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

  if (online && writes.length === 0 && cacheAge === null) return null;

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
      {actionable.map((write) => (
        <div
          className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3"
          key={write.id}
        >
          <div className="flex items-start gap-2 text-amber-500">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            <div>
              <p className="font-medium">
                {write.status === 'conflict' ? 'Conflit de synchronisation' : 'Écriture refusée'}
              </p>
              <p className="mt-0.5 text-xs text-muted-foreground">{write.label}</p>
            </div>
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            {write.status === 'conflict' ? (
              <>
                <button
                  className="rounded-md border border-border px-2 py-1 text-xs hover:bg-muted disabled:opacity-50"
                  disabled={resolvingId === write.id}
                  onClick={() => void resolve(write.id, 'server_wins')}
                  type="button"
                >
                  Garder le serveur
                </button>
                <button
                  className="rounded-md bg-amber-500 px-2 py-1 text-xs text-black hover:bg-amber-400 disabled:opacity-50"
                  disabled={resolvingId === write.id}
                  onClick={() => void resolve(write.id, 'client_wins')}
                  type="button"
                >
                  Envoyer ma version
                </button>
              </>
            ) : (
              <button
                className="rounded-md border border-border px-2 py-1 text-xs hover:bg-muted disabled:opacity-50"
                disabled={resolvingId === write.id}
                onClick={() => void resolve(write.id, 'retry')}
                type="button"
              >
                Réessayer
              </button>
            )}
            <button
              className="rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-muted disabled:opacity-50"
              disabled={resolvingId === write.id}
              onClick={() => void resolve(write.id, 'discard')}
              type="button"
            >
              Abandonner
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
