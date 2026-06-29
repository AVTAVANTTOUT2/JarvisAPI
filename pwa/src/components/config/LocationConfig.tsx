'use client';

import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { MapPin, Navigation, ToggleLeft, ToggleRight } from 'lucide-react';

import { jarvisFetch } from '@/lib/api';
import {
  checkPermission,
  getTrackingInfo,
  isSecureContextForGeo,
  isTracking,
  requestPermission,
  startTracking,
  stopTracking,
} from '@/lib/geolocation';

// ─────────────────────────────────────────────────────────────
// Types backend
// ─────────────────────────────────────────────────────────────

interface Place {
  id: number;
  name: string;
  category: string | null;
  latitude: number;
  longitude: number;
  radius_meters: number;
  visit_count?: number;
  avg_duration_min?: number | null;
  last_visit?: string | null;
}

interface PlacesResponse {
  places: Place[];
}

interface Pattern {
  id: number;
  pattern_type: string;
  description: string;
  place_id?: number | null;
  occurrences: number;
  first_seen: string;
  last_seen: string;
  status: string;
}

interface PatternsResponse {
  patterns: Pattern[];
}

// ─────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────

function permissionLabel(state: PermissionState | 'unknown'): {
  text: string;
  color: string;
} {
  switch (state) {
    case 'granted':
      return { text: 'Accordee', color: '#30D158' };
    case 'denied':
      return { text: 'Refusee', color: '#FF453A' };
    case 'prompt':
      return { text: 'Non demandee', color: '#FFD60A' };
    default:
      return { text: 'Inconnue', color: '#888' };
  }
}

function formatLastSent(ts: number | null): string {
  if (!ts) return 'Jamais';
  const diffMs = Date.now() - ts;
  const min = Math.floor(diffMs / 60_000);
  if (min < 1) return "A l'instant";
  if (min < 60) return `Il y a ${min} min`;
  const h = Math.floor(min / 60);
  if (h < 24) return `Il y a ${h}h`;
  return new Date(ts).toLocaleDateString('fr-FR');
}

// ─────────────────────────────────────────────────────────────
// Composant
// ─────────────────────────────────────────────────────────────

export function LocationConfig() {
  const [tracking, setTracking] = useState(false);
  const [permission, setPermission] = useState<PermissionState | 'unknown'>('unknown');
  const [secureContext, setSecureContext] = useState(true);
  const [, forceTick] = useState(0);

  // Etat client-side seulement (apres mount)
  useEffect(() => {
    setTracking(isTracking());
    void checkPermission().then(setPermission);
    setSecureContext(isSecureContextForGeo());
    // Tick toutes les 5s pour rafraichir "Dernier envoi"
    const id = setInterval(() => forceTick((n) => n + 1), 5_000);
    return () => clearInterval(id);
  }, []);

  const places = useQuery<PlacesResponse>({
    queryKey: ['places'],
    queryFn: () => jarvisFetch<PlacesResponse>('/api/places'),
    refetchInterval: 5 * 60_000,
    retry: 0,
  });

  const patterns = useQuery<PatternsResponse>({
    queryKey: ['location-patterns'],
    queryFn: () => jarvisFetch<PatternsResponse>('/api/location/patterns'),
    refetchInterval: 5 * 60_000,
    retry: 0,
  });

  const placeList = places.data?.places ?? [];
  const patternList = (patterns.data?.patterns ?? []).filter((p) => p.status === 'active');
  const trackingInfo = getTrackingInfo();
  const permLabel = permissionLabel(permission);

  async function toggleTracking() {
    if (tracking) {
      stopTracking();
      setTracking(false);
      return;
    }
    // Si la permission n'est pas encore accordee, on la demande
    if (permission !== 'granted') {
      const granted = await requestPermission();
      if (granted) {
        setPermission('granted');
      } else {
        setPermission('denied');
        return;
      }
    }
    const ok = startTracking();
    setTracking(ok);
  }

  return (
    <section>
      <div className="flex items-center gap-2 mb-2 px-1">
        <MapPin size={12} className="text-[#555]" />
        <h2 className="text-[11px] font-bold tracking-[0.15em] uppercase text-[#555]">
          Localisation
        </h2>
      </div>

      <div className="space-y-3">
        {/* Blocage contexte non securise — commun HTTPS/Tailscale */}
        {!secureContext && (
          <div className="text-[12px] text-[#FFD60A] bg-[rgba(255,214,10,0.06)] border border-[rgba(255,214,10,0.18)] rounded-lg px-3 py-2.5 leading-relaxed">
            <strong>Safari bloque la geolocalisation sur HTTP.</strong>{' '}
            Le serveur PWA doit etre lance avec{' '}
            <code className="text-[#FFD60A]/90 bg-[rgba(255,214,10,0.08)] px-1 rounded text-[11px]">
              --experimental-https
            </code>{' '}
            (deja dans <code className="text-[#FFD60A]/90 bg-[rgba(255,214,10,0.08)] px-1 rounded text-[11px]">
              package.json
            </code>).
            Une fois le certificat auto-signe accepte dans Safari, la geolocalisation
            fonctionnera.
          </div>
        )}

        {/* Toggle tracking */}
        <div className="rounded-[20px] bg-[rgba(255,255,255,0.035)] border border-[rgba(255,255,255,0.07)] p-4">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-3 min-w-0">
              <div className="w-10 h-10 rounded-xl bg-[rgba(74,158,255,0.12)] flex items-center justify-center flex-shrink-0">
                <MapPin size={18} className="text-[#4A9EFF]" />
              </div>
              <div className="min-w-0">
                <div className="text-[13px] text-white font-medium">Tracking GPS</div>
                <div className="text-[11px] text-[#666] flex items-center gap-1.5">
                  Permission
                  <span style={{ color: permLabel.color }}>{permLabel.text.toLowerCase()}</span>
                </div>
              </div>
            </div>
            <button
              type="button"
              onClick={toggleTracking}
              disabled={!secureContext}
              className={`active:scale-90 transition-transform flex-shrink-0 ${!secureContext ? 'opacity-30' : ''}`}
              aria-label={tracking ? 'Desactiver le tracking' : 'Activer le tracking'}
            >
              {tracking ? (
                <ToggleRight size={36} className="text-[#30D158]" />
              ) : (
                <ToggleLeft size={36} className="text-[#555]" />
              )}
            </button>
          </div>

          {/* Stats tracking */}
          <div className="mt-3 pt-3 border-t border-[rgba(255,255,255,0.04)] space-y-1.5">
            <Row label="Etat" value={tracking ? 'Actif' : 'Arrete'} valueColor={tracking ? '#30D158' : '#888'} />
            <Row label="Intervalle" value={`${Math.round(trackingInfo.intervalMs / 60_000)} min`} mono />
            <Row label="Distance min" value={`${trackingInfo.minDistanceMeters} m`} mono />
            <Row label="Dernier envoi" value={formatLastSent(trackingInfo.lastSentAt)} />
            {trackingInfo.lastError && (
              <Row label="Derniere erreur" value={trackingInfo.lastError} valueColor="#FF453A" />
            )}
          </div>

          {permission === 'denied' && (
            <div className="mt-3 text-[11px] text-[#FF453A] bg-[rgba(255,69,58,0.06)] border border-[rgba(255,69,58,0.18)] rounded-lg px-2.5 py-1.5">
              Permission refusee — autorise la localisation dans les Reglages Safari pour
              ce site, puis recharge la PWA.
            </div>
          )}
        </div>

        {/* Lieux connus */}
        <div className="rounded-[20px] bg-[rgba(255,255,255,0.035)] border border-[rgba(255,255,255,0.07)] p-4">
          <div className="flex items-center justify-between mb-2">
            <div className="text-[12px] text-[#888] font-semibold uppercase tracking-wider">
              Lieux connus
            </div>
            <span className="text-[11px] text-[#555] tabular-nums">
              {places.isLoading ? '…' : placeList.length}
            </span>
          </div>
          {places.isLoading ? (
            <div className="space-y-2">
              {[1, 2, 3].map((i) => (
                <div
                  key={i}
                  className="h-3 rounded bg-[rgba(255,255,255,0.04)] animate-pulse"
                  style={{ width: `${80 - i * 10}%` }}
                />
              ))}
            </div>
          ) : placeList.length === 0 ? (
            <p className="text-[12px] text-[#666] py-1">
              Aucun lieu nomme pour le moment. Depuis le Dashboard, appuie sur "Nommer cet
              endroit" pour creer ton premier lieu.
            </p>
          ) : (
            <ul className="space-y-0">
              {placeList.slice(0, 10).map((p) => (
                <li
                  key={p.id}
                  className="flex items-center justify-between gap-3 py-1.5 border-b border-[rgba(255,255,255,0.03)] last:border-b-0"
                >
                  <div className="flex items-center gap-2 min-w-0">
                    <Navigation size={11} className="text-[#444] flex-shrink-0" />
                    <span className="text-[13px] text-[#ccc] truncate">{p.name}</span>
                    {p.category && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-[rgba(255,255,255,0.04)] text-[#666] flex-shrink-0">
                        {p.category}
                      </span>
                    )}
                  </div>
                  <span className="text-[11px] text-[#555] tabular-nums flex-shrink-0">
                    {p.visit_count || 0} visite{(p.visit_count || 0) > 1 ? 's' : ''}
                  </span>
                </li>
              ))}
              {placeList.length > 10 && (
                <li className="text-[11px] text-[#555] pt-2 text-center">
                  +{placeList.length - 10} autres
                </li>
              )}
            </ul>
          )}
        </div>

        {/* Patterns detectes */}
        {patternList.length > 0 && (
          <div className="rounded-[20px] bg-[rgba(255,255,255,0.035)] border border-[rgba(255,255,255,0.07)] p-4">
            <div className="flex items-center justify-between mb-2">
              <div className="text-[12px] text-[#888] font-semibold uppercase tracking-wider">
                Patterns detectes
              </div>
              <span className="text-[11px] text-[#555] tabular-nums">{patternList.length}</span>
            </div>
            <ul className="space-y-2">
              {patternList.slice(0, 5).map((p) => (
                <li
                  key={p.id}
                  className="py-1 border-b border-[rgba(255,255,255,0.03)] last:border-b-0"
                >
                  <div className="text-[12px] text-[#aaa] leading-snug">{p.description}</div>
                  <div className="text-[10px] text-[#555] mt-0.5 tabular-nums flex items-center gap-1.5">
                    <span className="px-1.5 py-0.5 rounded-full bg-[rgba(255,255,255,0.04)] text-[#666]">
                      {p.pattern_type}
                    </span>
                    {p.occurrences > 0 && (
                      <span>
                        {p.occurrences}× — dernier{' '}
                        {p.last_seen
                          ? new Date(p.last_seen).toLocaleDateString('fr-FR')
                          : '?'}
                      </span>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
}

// ─────────────────────────────────────────────────────────────
// Sub-component
// ─────────────────────────────────────────────────────────────

function Row({
  label,
  value,
  mono,
  valueColor,
}: {
  label: string;
  value: string;
  mono?: boolean;
  valueColor?: string;
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-[12px] text-[#666] font-medium">{label}</span>
      <span
        className={`text-[12px] truncate text-right ${mono ? 'font-mono tabular-nums' : ''}`}
        style={{ color: valueColor ?? '#888' }}
      >
        {value}
      </span>
    </div>
  );
}
