'use client';

import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { MapPin, Navigation, RefreshCw, Tag, X } from 'lucide-react';

import { jarvisFetch } from '@/lib/api';
import { isSecureContextForGeo, sendCurrentPosition } from '@/lib/geolocation';

// ─────────────────────────────────────────────────────────────
// Types backend
// ─────────────────────────────────────────────────────────────

interface LocationStatus {
  tracking_enabled?: boolean;
  default_radius_m?: number;
  current_location?: {
    id: number;
    latitude: number;
    longitude: number;
    accuracy?: number | null;
    source?: string | null;
    place_id?: number | null;
    place_name?: string | null;
    created_at: string;
  } | null;
  current_visit?: {
    id: number;
    place_id: number;
    place_name?: string | null;
    arrived_at: string;
  } | null;
  minutes_at_place?: number | null;
}

interface Visit {
  id: number;
  place_name?: string | null;
  arrived_at: string | null;
  departed_at: string | null;
  duration_minutes?: number | null;
  duration_min?: number | null;
}

interface VisitsResponse {
  visits: Visit[];
}

// ─────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────

function formatTime(iso: string | null | undefined): string {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    return d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
  } catch {
    return '';
  }
}

function formatDuration(minutes: number | null | undefined): string {
  if (minutes == null || Number.isNaN(minutes)) return 'en cours';
  const m = Math.round(minutes);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  const rest = m % 60;
  return rest > 0 ? `${h}h${String(rest).padStart(2, '0')}` : `${h}h`;
}

// ─────────────────────────────────────────────────────────────
// Composant
// ─────────────────────────────────────────────────────────────

export function LocationWidget() {
  const qc = useQueryClient();
  const [refreshing, setRefreshing] = useState(false);
  const [naming, setNaming] = useState(false);
  const [newName, setNewName] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const location = useQuery<LocationStatus>({
    queryKey: ['location-status'],
    queryFn: () => jarvisFetch<LocationStatus>('/api/location/status'),
    refetchInterval: 60_000,
    retry: 0,
  });

  const visits = useQuery<VisitsResponse>({
    queryKey: ['visits-today'],
    queryFn: () => jarvisFetch<VisitsResponse>('/api/visits/today'),
    refetchInterval: 5 * 60_000,
    retry: 0,
  });

  const loc = location.data;
  const visitList = visits.data?.visits ?? [];
  const currentPlace =
    loc?.current_visit?.place_name ||
    loc?.current_location?.place_name ||
    null;
  const lastUpdate = formatTime(loc?.current_location?.created_at ?? null);
  const minutesHere = loc?.minutes_at_place;
  const trackingActive = !!loc?.tracking_enabled;
  const hasCoords = !!loc?.current_location;

  async function handleRefresh() {
    setRefreshing(true);
    try {
      await sendCurrentPosition();
      await location.refetch();
    } finally {
      setRefreshing(false);
    }
  }

  async function nameCurrentPlace() {
    const trimmed = newName.trim();
    if (!trimmed) return;
    setSubmitting(true);
    try {
      await jarvisFetch('/api/location/name-current', {
        method: 'POST',
        body: JSON.stringify({ name: trimmed, category: 'other' }),
      });
      setNewName('');
      setNaming(false);
      qc.invalidateQueries({ queryKey: ['location-status'] });
      qc.invalidateQueries({ queryKey: ['places'] });
    } catch (err) {
      console.warn('[LocationWidget] name-current failed:', err);
    } finally {
      setSubmitting(false);
    }
  }

  // Etat d'erreur / indisponible
  if (location.isError) {
    return (
      <div className="rounded-[20px] bg-[rgba(255,69,58,0.06)] border border-[rgba(255,69,58,0.18)] p-4">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-[rgba(255,69,58,0.12)] flex items-center justify-center">
            <MapPin size={16} className="text-[#FF453A]" />
          </div>
          <div>
            <div className="text-[13px] font-semibold text-[#FF453A]">Localisation indisponible</div>
            <div className="text-[11px] text-[#FF453A]/70">Backend injoignable</div>
          </div>
        </div>
      </div>
    );
  }

  // Contexte non securise (HTTP non-localhost) — Safari bloque la geolocation
  if (!isSecureContextForGeo()) {
    return (
      <div className="rounded-[20px] bg-[rgba(255,214,10,0.06)] border border-[rgba(255,214,10,0.18)] p-4">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-[rgba(255,214,10,0.12)] flex items-center justify-center flex-shrink-0">
            <Navigation size={16} className="text-[#FFD60A]" />
          </div>
          <div className="min-w-0">
            <div className="text-[13px] font-semibold text-[#FFD60A]">
              Geolocalisation bloquee
            </div>
            <div className="text-[11px] text-[#FFD60A]/70 mt-0.5">
              Safari exige le HTTPS pour la geolocalisation.
              Le serveur PWA doit etre lance avec <code className="text-[#FFD60A]/90 bg-[rgba(255,214,10,0.08)] px-1 rounded text-[10px]">--experimental-https</code>.
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (location.isLoading) {
    return (
      <div className="rounded-[20px] bg-[rgba(255,255,255,0.035)] border border-[rgba(255,255,255,0.07)] p-4">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-[rgba(255,255,255,0.05)] animate-pulse" />
          <div className="flex-1 space-y-1.5">
            <div className="h-3 w-32 rounded bg-[rgba(255,255,255,0.05)] animate-pulse" />
            <div className="h-2.5 w-20 rounded bg-[rgba(255,255,255,0.04)] animate-pulse" />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-[20px] bg-[rgba(255,255,255,0.035)] border border-[rgba(255,255,255,0.07)] p-4 space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2.5 min-w-0 flex-1">
          <div className="w-8 h-8 rounded-lg bg-[rgba(74,158,255,0.12)] flex items-center justify-center flex-shrink-0">
            <MapPin size={16} className="text-[#4A9EFF]" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="text-[14px] font-semibold text-white truncate">
              {currentPlace || (hasCoords ? 'Position non nommee' : 'Position inconnue')}
            </div>
            <div className="text-[11px] text-[#555] flex items-center gap-2">
              {lastUpdate && <span className="tabular-nums">Maj {lastUpdate}</span>}
              {minutesHere != null && currentPlace && (
                <>
                  <span className="text-[#333]">•</span>
                  <span className="tabular-nums">{formatDuration(minutesHere)} ici</span>
                </>
              )}
            </div>
          </div>
        </div>
        <button
          type="button"
          onClick={handleRefresh}
          disabled={refreshing}
          className="w-8 h-8 rounded-lg bg-[rgba(255,255,255,0.05)] border border-[rgba(255,255,255,0.05)] flex items-center justify-center flex-shrink-0 active:scale-90 transition-transform"
          aria-label="Actualiser la position"
        >
          <RefreshCw size={14} className={`text-[#888] ${refreshing ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {/* Avertissement tracking off */}
      {!trackingActive && (
        <div className="text-[11px] text-[#FFD60A] bg-[rgba(255,214,10,0.06)] border border-[rgba(255,214,10,0.15)] rounded-lg px-2.5 py-1.5">
          Tracking desactive cote backend. Active LOCATION_TRACKING dans .env.
        </div>
      )}

      {/* Nommer cet endroit */}
      {hasCoords && !currentPlace && (
        <div>
          {!naming ? (
            <button
              type="button"
              onClick={() => setNaming(true)}
              className="text-[12px] text-[#4A9EFF] inline-flex items-center gap-1.5 active:opacity-60"
            >
              <Tag size={12} /> Nommer cet endroit
            </button>
          ) : (
            <div className="flex gap-2 items-center">
              <input
                type="text"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="Maison, Bureau..."
                maxLength={50}
                autoFocus
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    void nameCurrentPlace();
                  } else if (e.key === 'Escape') {
                    setNaming(false);
                    setNewName('');
                  }
                }}
                className="flex-1 bg-[rgba(255,255,255,0.05)] border border-[rgba(255,255,255,0.1)] rounded-lg px-3 py-1.5 text-[13px] text-white placeholder:text-[#444] outline-none focus:border-[#4A9EFF]"
                disabled={submitting}
              />
              <button
                type="button"
                onClick={nameCurrentPlace}
                disabled={!newName.trim() || submitting}
                className="px-3 py-1.5 rounded-lg bg-[rgba(74,158,255,0.15)] border border-[rgba(74,158,255,0.25)] text-[#4A9EFF] text-[12px] font-semibold active:scale-95 transition-transform disabled:opacity-40"
              >
                OK
              </button>
              <button
                type="button"
                onClick={() => {
                  setNaming(false);
                  setNewName('');
                }}
                className="w-8 h-8 rounded-lg bg-[rgba(255,255,255,0.05)] flex items-center justify-center active:scale-90 transition-transform"
                aria-label="Annuler"
              >
                <X size={14} className="text-[#888]" />
              </button>
            </div>
          )}
        </div>
      )}

      {/* Visites du jour */}
      {visitList.length > 0 && (
        <div className="space-y-1.5 pt-3 border-t border-[rgba(255,255,255,0.04)]">
          <div className="text-[10px] font-bold tracking-[0.15em] uppercase text-[#555] mb-1">
            Aujourd&rsquo;hui
          </div>
          {visitList.slice(0, 4).map((v) => {
            const arrived = formatTime(v.arrived_at);
            const duration = formatDuration(v.duration_minutes ?? v.duration_min);
            return (
              <div
                key={v.id}
                className="flex items-center gap-2 text-[12px] py-0.5"
              >
                <Navigation size={10} className="text-[#444] flex-shrink-0" />
                <span className="text-[#aaa] flex-1 truncate">
                  {v.place_name || 'Lieu inconnu'}
                </span>
                {arrived && (
                  <span className="text-[#555] tabular-nums">{arrived}</span>
                )}
                <span className="text-[#666] tabular-nums">{duration}</span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
