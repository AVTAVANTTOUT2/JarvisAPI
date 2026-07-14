'use client';

import { useState, useEffect, useCallback } from 'react';
import { X, Clock, Navigation, MapPin, Hash } from 'lucide-react';

import type { Place, LocationPoint, Trip, Visit } from '@mobile/lib/map-types';
import { jarvisFetch } from '@unified/lib/api';

// ─────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────

export interface DetailSheetProps {
  selectedPlace: Place | null;
  selectedPoint: LocationPoint | null;
  selectedTrip: Trip | null;
  visits: Visit[];
  trips: Trip[];
  selectedDate: string | null;
  onClose: () => void;
  onTripHighlight?: (tripId: number | null) => void;
}

interface PlaceStats {
  visit_count: number;
  avg_duration_min: number | null;
  first_visit: string | null;
  last_visit: string | null;
  total_duration_min: number | null;
  day_distribution: Record<string, number> | null;
}

// ─────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────

function fmtTime(iso: string | null): string {
  if (!iso) return '-';
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
  } catch {
    return iso;
  }
}

function fmtDate(iso: string | null): string {
  if (!iso) return '-';
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleDateString('fr-FR', { day: 'numeric', month: 'long', year: 'numeric' });
  } catch {
    return iso;
  }
}

function fmtDuration(min: number | null): string {
  if (min == null) return '-';
  const m = Math.round(min);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  const rest = m % 60;
  return rest > 0 ? `${h}h${String(rest).padStart(2, '0')}` : `${h}h`;
}

function fmtDistance(km: number | null): string {
  if (km == null) return '-';
  if (km < 1) return `${Math.round(km * 1000)} m`;
  return `${km.toFixed(1)} km`;
}

function transportLabel(mode: string | null): string {
  switch (mode) {
    case 'pied':
      return 'A pied';
    case 'vélo':
      return 'Velo';
    case 'voiture':
      return 'Voiture';
    case 'transport':
      return 'Transport en commun';
    default:
      return mode || 'Inconnu';
  }
}

// ─────────────────────────────────────────────────────────────
// Composant
// ─────────────────────────────────────────────────────────────

export default function DetailSheet({
  selectedPlace,
  selectedPoint,
  selectedTrip,
  visits,
  trips,
  selectedDate,
  onClose,
  onTripHighlight,
}: DetailSheetProps) {
  const [placeStats, setPlaceStats] = useState<PlaceStats | null>(null);
  const [statsLoading, setStatsLoading] = useState(false);

  const open = selectedPlace !== null || selectedTrip !== null || selectedPoint !== null;

  // Charger les stats du lieu selectionne
  useEffect(() => {
    if (!selectedPlace) {
      setPlaceStats(null);
      return;
    }
    let cancelled = false;
    setStatsLoading(true);
    jarvisFetch<PlaceStats>(`/api/places/${selectedPlace.id}/stats`)
      .then((data) => {
        if (!cancelled) setPlaceStats(data);
      })
      .catch(() => {
        if (!cancelled) setPlaceStats(null);
      })
      .finally(() => {
        if (!cancelled) setStatsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedPlace]);

  // Filtrer visites et trajets pour le lieu selectionne
  const relatedVisits = selectedPlace
    ? visits.filter((v) => v.place_id === selectedPlace.id)
    : [];

  const relatedTrips = selectedPlace
    ? trips.filter(
        (t) => t.from_place_id === selectedPlace.id || t.to_place_id === selectedPlace.id
      )
    : [];

  // Gestuelle de fermeture
  const handleBackdrop = useCallback(() => onClose(), [onClose]);

  if (!open) return null;

  return (
    <>
      {/* Overlay floute */}
      <div
        className="fixed inset-0 z-40 bg-black/40 backdrop-blur-sm"
        onClick={handleBackdrop}
      />

      {/* Sheet */}
      <div className="fixed bottom-0 left-0 right-0 z-50 max-h-[58vh] overflow-y-auto bg-[#111118] border-t border-[rgba(255,255,255,0.08)] rounded-t-[20px] shadow-[0_-8px_40px_rgba(0,0,0,0.5)] animate-slide-up">
        {/* Poignee */}
        <div className="flex justify-center pt-3 pb-1 sticky top-0 bg-[#111118] rounded-t-[20px]">
          <div className="w-8 h-1 rounded-full bg-[#333]" />
          <button
            type="button"
            onClick={onClose}
            className="absolute right-4 top-3 w-7 h-7 rounded-full bg-[rgba(255,255,255,0.06)] flex items-center justify-center active:scale-90"
            aria-label="Fermer"
          >
            <X size={14} className="text-[#888]" />
          </button>
        </div>

        <div className="px-4 pb-8 space-y-4">
          {/* ── Lieu nomme ── */}
          {selectedPlace && (
            <>
              <div className="flex items-center gap-2.5">
                <div className="w-10 h-10 rounded-xl bg-[rgba(74,158,255,0.12)] flex items-center justify-center flex-shrink-0">
                  <MapPin size={18} className="text-[#4A9EFF]" />
                </div>
                <div className="min-w-0">
                  <h2 className="text-[17px] font-bold text-white truncate">
                    {selectedPlace.name}
                  </h2>
                  {selectedPlace.category && (
                    <span className="text-[11px] text-[#666] capitalize">
                      {selectedPlace.category}
                    </span>
                  )}
                </div>
              </div>

              {/* Stats */}
              <div className="grid grid-cols-3 gap-2">
                <StatChip
                  label="Visites"
                  value={String(selectedPlace.visit_count)}
                  loading={false}
                />
                <StatChip
                  label="Duree moy."
                  value={fmtDuration(placeStats?.avg_duration_min ?? selectedPlace.avg_duration_min)}
                  loading={statsLoading}
                />
                <StatChip
                  label="Derniere visite"
                  value={
                    placeStats?.last_visit
                      ? fmtDate(placeStats.last_visit)
                      : selectedPlace.last_visit
                        ? fmtDate(selectedPlace.last_visit)
                        : '-'
                  }
                  loading={false}
                />
              </div>

              {/* Dernieres visites */}
              {relatedVisits.length > 0 && (
                <div>
                  <h3 className="text-[11px] font-bold tracking-[0.12em] uppercase text-[#555] mb-2">
                    Dernieres visites
                  </h3>
                  <div className="space-y-1.5">
                    {relatedVisits.slice(0, 5).map((v) => (
                      <div
                        key={v.id}
                        className="flex items-center justify-between text-[13px] py-1 px-2 rounded-lg bg-[rgba(255,255,255,0.02)]"
                      >
                        <span className="text-[#ccc]">{fmtDate(v.arrived_at)}</span>
                        <span className="text-[#888] tabular-nums">
                          {v.departed_at
                            ? `${fmtTime(v.arrived_at)} - ${fmtTime(v.departed_at)}`
                            : `Depuis ${fmtTime(v.arrived_at)}`}
                        </span>
                        <span className="text-[#666] tabular-nums w-14 text-right">
                          {fmtDuration(v.duration_minutes ?? null)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Trajets associes */}
              {relatedTrips.length > 0 && (
                <div>
                  <h3 className="text-[11px] font-bold tracking-[0.12em] uppercase text-[#555] mb-2">
                    Trajets
                  </h3>
                  <div className="space-y-1.5">
                    {relatedTrips.slice(0, 5).map((t) => (
                      <TripRow key={t.id} trip={t} />
                    ))}
                  </div>
                </div>
              )}
            </>
          )}

          {/* ── Trajet selectionne ── */}
          {selectedTrip && !selectedPlace && (
            <>
              <div className="flex items-center gap-2.5">
                <div className="w-10 h-10 rounded-xl bg-[rgba(255,149,0,0.12)] flex items-center justify-center flex-shrink-0">
                  <Navigation size={18} className="text-[#FF9500]" />
                </div>
                <div className="min-w-0">
                  <h2 className="text-[17px] font-bold text-white truncate">
                    {transportLabel(selectedTrip.transport_mode)}
                  </h2>
                  <span className="text-[11px] text-[#666]">
                    {fmtDate(selectedTrip.started_at)}
                  </span>
                </div>
              </div>
              <div className="grid grid-cols-3 gap-2">
                <StatChip label="Distance" value={fmtDistance(selectedTrip.distance_km)} loading={false} />
                <StatChip label="Duree" value={fmtDuration(selectedTrip.duration_min)} loading={false} />
                <StatChip
                  label="Vitesse"
                  value={
                    selectedTrip.distance_km != null && selectedTrip.duration_min > 0
                      ? `${Math.round((selectedTrip.distance_km / (selectedTrip.duration_min / 60)))} km/h`
                      : '-'
                  }
                  loading={false}
                />
              </div>
              <div className="text-[13px] text-[#aaa] py-2 px-3 rounded-lg bg-[rgba(255,255,255,0.02)]">
                {fmtTime(selectedTrip.started_at)} → {fmtTime(selectedTrip.ended_at)}
              </div>
            </>
          )}

          {/* ── Point GPS ── */}
          {selectedPoint && !selectedPlace && !selectedTrip && (
            <>
              <div className="flex items-center gap-2.5">
                <div className="w-10 h-10 rounded-xl bg-[rgba(255,255,255,0.06)] flex items-center justify-center flex-shrink-0">
                  <Hash size={18} className="text-[#888]" />
                </div>
                <div className="min-w-0">
                  <h2 className="text-[15px] font-semibold text-white truncate">
                    {selectedPoint.place_name || 'Point de passage'}
                  </h2>
                  <span className="text-[11px] text-[#666]">
                    {fmtDate(selectedPoint.created_at)} {fmtTime(selectedPoint.created_at)}
                  </span>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <StatChip
                  label="Precision"
                  value={selectedPoint.accuracy != null ? `±${Math.round(selectedPoint.accuracy)} m` : '-'}
                  loading={false}
                />
                <StatChip
                  label="Vitesse"
                  value={selectedPoint.speed != null ? `${Math.round(selectedPoint.speed * 3.6)} km/h` : '-'}
                  loading={false}
                />
              </div>
              <div className="text-[11px] text-[#666] font-mono">
                {selectedPoint.latitude.toFixed(6)}, {selectedPoint.longitude.toFixed(6)}
              </div>
            </>
          )}
        </div>
      </div>
    </>
  );
}

// ─────────────────────────────────────────────────────────────
// Sub-components
// ─────────────────────────────────────────────────────────────

function StatChip({
  label,
  value,
  loading,
}: {
  label: string;
  value: string;
  loading: boolean;
}) {
  return (
    <div className="rounded-xl bg-[rgba(255,255,255,0.04)] border border-[rgba(255,255,255,0.04)] p-3 text-center">
      {loading ? (
        <div className="h-5 w-12 mx-auto rounded bg-[rgba(255,255,255,0.05)] animate-pulse" />
      ) : (
        <div className="text-[17px] font-bold text-white tabular-nums">{value}</div>
      )}
      <div className="text-[10px] text-[#666] mt-0.5 font-medium uppercase tracking-wider">
        {label}
      </div>
    </div>
  );
}

function TripRow({ trip }: { trip: Trip }) {
  return (
    <div className="flex items-center justify-between text-[13px] py-1.5 px-2 rounded-lg bg-[rgba(255,255,255,0.02)]">
      <div className="flex items-center gap-1.5 min-w-0">
        <Navigation size={11} className="text-[#FF9500] flex-shrink-0" />
        <span className="text-[#ccc] truncate">{transportLabel(trip.transport_mode)}</span>
      </div>
      <span className="text-[#666] tabular-nums">{fmtDistance(trip.distance_km)}</span>
      <span className="text-[#666] tabular-nums w-14 text-right">
        {fmtDuration(trip.duration_min)}
      </span>
    </div>
  );
}
