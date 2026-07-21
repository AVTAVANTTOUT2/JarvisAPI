import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import {
  Layers,
  Navigation,
  MapPin,
  X,
  Plus,
  Loader2,
  Crosshair,
  LocateFixed,
} from 'lucide-react';
import { api } from '@unified/lib/api';
import {
  getLocationDisplayStatus,
  mapLocationHistory,
  mapLocationPoint,
  resolveDisplayLocationPoint,
  type LocationPoint,
} from '@unified/lib/locationDisplay';
import { timeAgo, formatDurationMin } from '@desktop/app/lib/timeFormat';
import {
  filterPointsByLocalDate,
  filterTripsByLocalDate,
  localDateKey,
  type CartographyPlace,
  type CartographyTrip,
} from '@desktop/app/lib/cartographyGeojson';
import type { CartographySelection } from '@desktop/app/components/map/CartographyMap';

// MapLibre touche window/WebGL — chargement différé (SSR Next via DesktopApp ssr:false).
const CartographyMap = lazy(() =>
  import('@desktop/app/components/map/CartographyMap').then((m) => ({ default: m.CartographyMap })),
);

// ── Types ────────────────────────────────────────────────────

interface Place extends CartographyPlace {}

interface Visit {
  place_id?: number;
  place_name?: string;
  arrived_at: string;
  departed_at?: string;
  duration_min?: number;
}

interface Trip extends CartographyTrip {}

interface Pattern {
  pattern_type?: string;
  description?: string;
  place_id?: number;
  occurrences?: number;
  status?: string;
}

interface LocationStatus {
  tracking_enabled?: boolean;
  current_location?: LocationPoint | null;
  current_visit?: { place_name?: string } | null;
  place_name?: string | null;
  points_24h?: number;
}

// ── Helpers ──────────────────────────────────────────────────

function categoryEmoji(cat: string): string {
  const map: Record<string, string> = {
    home: '🏠', work: '💼', school: '📚', gym: '💪',
    restaurant: '🍽️', shop: '🛍️', friend: '👤', family: '👨‍👩‍👧',
    medical: '🏥', transport: '🚆', leisure: '🎮', other: '📍',
    social: '🎉', health: '🏥', sport: '💪', commerce: '🛍️',
  };
  return map[cat] ?? '📍';
}

function patternIcon(type: string): string {
  const map: Record<string, string> = {
    routine: '🔄', absence: '✈️', new_place: '🆕', frequency: '📊',
    anomaly: '⚠️', habit: '🕐',
  };
  return map[type] ?? '📌';
}

function intensityDot(count: number): string {
  if (count > 20) return 'bg-white';
  if (count > 10) return 'bg-white/50';
  return 'bg-white/20';
}

function groupVisitsByDay(visits: Visit[]): { day: string; count: number; totalMin: number }[] {
  const days = ['Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam', 'Dim'];
  const groups = days.map((d) => ({ day: d, count: 0, totalMin: 0 }));
  visits.forEach((v) => {
    const dayIdx = new Date(v.arrived_at).getDay();
    const adjusted = dayIdx === 0 ? 6 : dayIdx - 1;
    groups[adjusted].count++;
    groups[adjusted].totalMin += v.duration_min ?? 0;
  });
  return groups;
}

function todayLocalKey(): string {
  const d = new Date();
  const y = d.getFullYear();
  const mo = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${mo}-${day}`;
}

function formatAccuracy(meters: number | null | undefined): string | null {
  if (meters == null || !Number.isFinite(meters)) return null;
  if (meters < 1000) return `±${Math.round(meters)} m`;
  return `±${(meters / 1000).toFixed(1)} km`;
}

function mapApiTrip(raw: Record<string, unknown>): Trip {
  return {
    id: Number(raw.id) || 0,
    from_place_id: raw.from_place_id == null ? null : Number(raw.from_place_id),
    to_place_id: raw.to_place_id == null ? null : Number(raw.to_place_id),
    from_place: typeof raw.from_place === 'string' ? raw.from_place : undefined,
    to_place: typeof raw.to_place === 'string' ? raw.to_place : undefined,
    started_at: typeof raw.started_at === 'string' ? raw.started_at : undefined,
    ended_at: typeof raw.ended_at === 'string' ? raw.ended_at : undefined,
    duration_min: raw.duration_min == null ? undefined : Number(raw.duration_min),
    distance_km: raw.distance_km == null ? null : Number(raw.distance_km),
    transport_mode: typeof raw.transport_mode === 'string' ? raw.transport_mode : null,
    route_points:
      typeof raw.route_points === 'string' || Array.isArray(raw.route_points)
        ? (raw.route_points as Trip['route_points'])
        : null,
  };
}

// ── Composant principal ──────────────────────────────────────

export function MapView() {
  const [places, setPlaces] = useState<Place[]>([]);
  const [todayVisits, setTodayVisits] = useState<Visit[]>([]);
  const [weekVisits, setWeekVisits] = useState<Visit[]>([]);
  const [trips, setTrips] = useState<Trip[]>([]);
  const [patterns, setPatterns] = useState<Pattern[]>([]);
  const [locationStatus, setLocationStatus] = useState<LocationStatus | null>(null);
  const [historyPoints, setHistoryPoints] = useState<LocationPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [locationUnavailable, setLocationUnavailable] = useState(false);

  const [selectedPlace, setSelectedPlace] = useState<Place | null>(null);
  const [selectedPoint, setSelectedPoint] = useState<LocationPoint | null>(null);
  const [selectedTrip, setSelectedTrip] = useState<Trip | null>(null);

  const [showPlaces, setShowPlaces] = useState(true);
  const [showRoutes, setShowRoutes] = useState(true);
  const [showGps, setShowGps] = useState(true);
  const [selectedDate, setSelectedDate] = useState<string>('');
  const [fitToken, setFitToken] = useState(0);
  const [recenterToken, setRecenterToken] = useState(0);
  const [mapTileError, setMapTileError] = useState<string | null>(null);

  const [showAddForm, setShowAddForm] = useState(false);
  const [addName, setAddName] = useState('');
  const [addCategory, setAddCategory] = useState('other');
  const [addLat, setAddLat] = useState('');
  const [addLng, setAddLng] = useState('');
  const [addSaving, setAddSaving] = useState(false);

  const loadAll = useCallback(async () => {
    try {
      const [p, tv, wv, tr, pat, loc, history] = await Promise.all([
        api.getPlaces() as Promise<{ places: Place[] }>,
        api.getTodayVisits() as Promise<{ visits: Visit[] }>,
        api.getVisits(7) as Promise<{ visits: Visit[] }>,
        api.getTrips(30) as Promise<{ trips: Record<string, unknown>[] }>,
        api.getLocationPatterns() as Promise<{ patterns: Pattern[] }>,
        api.getLocationStatus() as Promise<LocationStatus>,
        api.getLocationHistory(24) as Promise<unknown>,
      ]);
      setPlaces(p.places ?? []);
      setTodayVisits(tv.visits ?? []);
      setWeekVisits(wv.visits ?? []);
      setTrips((tr.trips ?? []).map((row) => mapApiTrip(row)));
      setPatterns(pat.patterns ?? []);
      setLocationStatus(loc);
      setHistoryPoints(mapLocationHistory(history));
      setLocationUnavailable(false);
    } catch (e) {
      console.error('[MapView] loadAll:', e);
      setLocationUnavailable(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  // Rafraîchissement existant (60 s) — pas de polling supplémentaire.
  useEffect(() => {
    const iv = setInterval(() => {
      Promise.all([
        api.getLocationStatus() as Promise<LocationStatus>,
        api.getLocationHistory(24) as Promise<unknown>,
      ]).then(([status, history]) => {
        setLocationStatus(status);
        setHistoryPoints(mapLocationHistory(history));
        setLocationUnavailable(false);
      }).catch(() => setLocationUnavailable(true));
    }, 60_000);
    return () => clearInterval(iv);
  }, []);

  const statusPoint = mapLocationPoint(locationStatus?.current_location ?? null) ?? undefined;
  const latestPoint = resolveDisplayLocationPoint(historyPoints, statusPoint);
  const locationDisplay = getLocationDisplayStatus(latestPoint);

  const dateFilter = selectedDate.trim() || null;
  const filteredPoints = useMemo(
    () => filterPointsByLocalDate(historyPoints, dateFilter),
    [historyPoints, dateFilter],
  );
  const filteredTrips = useMemo(
    () => filterTripsByLocalDate(trips, dateFilter),
    [trips, dateFilter],
  );

  const hasLocationData =
    places.length > 0
    || filteredPoints.length > 0
    || filteredTrips.length > 0
    || Boolean(latestPoint);

  const historyCount = Math.max(historyPoints.length, locationStatus?.points_24h ?? 0);
  const sortedByVisit = [...places].sort((a, b) => (b.visit_count ?? 0) - (a.visit_count ?? 0));
  const weekGroups = groupVisitsByDay(weekVisits);
  const maxWeekCount = Math.max(1, ...weekGroups.map((g) => g.count));

  const placeNameById = useMemo(() => {
    const m = new Map<number, string>();
    for (const p of places) m.set(p.id, p.name);
    return m;
  }, [places]);

  function clearSelections(): void {
    setSelectedPlace(null);
    setSelectedPoint(null);
    setSelectedTrip(null);
  }

  function handleMapSelect(selection: CartographySelection): void {
    if (!selection) {
      clearSelections();
      return;
    }
    if (selection.kind === 'place') {
      setSelectedPlace(selection.place);
      setSelectedPoint(null);
      setSelectedTrip(null);
      return;
    }
    if (selection.kind === 'gps') {
      setSelectedPoint(selection.point);
      setSelectedPlace(null);
      setSelectedTrip(null);
      return;
    }
    setSelectedTrip(selection.trip);
    setSelectedPlace(null);
    setSelectedPoint(null);
  }

  async function handleRename(place: Place) {
    const next = window.prompt('Nouveau nom :', place.name);
    if (!next || next.trim() === place.name) return;
    try {
      await api.updatePlace(place.id, { name: next.trim() });
      await loadAll();
      setSelectedPlace((prev) => (prev?.id === place.id ? { ...prev, name: next.trim() } : prev));
    } catch (e) {
      console.error(e);
    }
  }

  async function handleDelete(place: Place) {
    if (!window.confirm(`Supprimer "${place.name}" ?`)) return;
    try {
      await api.deletePlace(place.id);
      setSelectedPlace(null);
      await loadAll();
    } catch (e) {
      console.error(e);
    }
  }

  async function handleAddPlace() {
    const lat = parseFloat(addLat);
    const lng = parseFloat(addLng);
    if (!addName.trim() || isNaN(lat) || isNaN(lng)) return;
    setAddSaving(true);
    try {
      await api.createPlace({ name: addName.trim(), category: addCategory, latitude: lat, longitude: lng });
      setAddName('');
      setAddCategory('other');
      setAddLat('');
      setAddLng('');
      setShowAddForm(false);
      await loadAll();
    } catch (e) {
      console.error(e);
    } finally {
      setAddSaving(false);
    }
  }

  async function handleNameCurrent() {
    const name = window.prompt('Nom de cet endroit :');
    if (!name?.trim()) return;
    const category = window.prompt('Catégorie (home/work/school/restaurant/shop/leisure/other) :', 'other') ?? 'other';
    try {
      await api.nameCurrentLocation(name.trim(), category.trim());
      await loadAll();
    } catch (e) {
      console.error(e);
    }
  }

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center" data-testid="cartography-page-loading">
        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="flex flex-1 min-h-0 h-full map-layout">
      <aside className="w-80 shrink-0 border-r border-border glass-panel overflow-y-auto flex flex-col map-sidebar">
        <div className="p-5 border-b border-white/10">
          <h1 className="text-sm font-bold tracking-widest uppercase">Cartographie</h1>
          <p className="font-mono text-xs text-muted-foreground mt-0.5">Surveillance des déplacements</p>
        </div>

        <div className="p-4 space-y-5 flex-1">
          <div className="space-y-2">
            <label className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground" htmlFor="cartography-date">
              Date
            </label>
            <div className="flex gap-2">
              <input
                id="cartography-date"
                type="date"
                value={selectedDate}
                max={todayLocalKey()}
                onChange={(e) => setSelectedDate(e.target.value)}
                className="flex-1 h-9 px-3 rounded-xl bg-white/5 border border-white/10 text-xs font-mono focus:outline-none focus:border-white/30"
              />
              {selectedDate && (
                <button
                  type="button"
                  onClick={() => setSelectedDate('')}
                  className="h-9 px-3 rounded-xl bg-white/5 border border-white/10 text-xs text-muted-foreground hover:bg-white/10"
                >
                  Tout
                </button>
              )}
            </div>
          </div>

          <div className="grid grid-cols-3 gap-2">
            <button
              type="button"
              onClick={() => setShowPlaces((v) => !v)}
              className={`flex flex-col items-center justify-center gap-1 px-2 py-2 rounded-xl text-[10px] transition-all border ${
                showPlaces ? 'bg-white text-black border-white' : 'bg-white/5 text-muted-foreground border-white/10 hover:bg-white/10'
              }`}
              aria-pressed={showPlaces}
            >
              <MapPin className="w-3.5 h-3.5" />
              Lieux
            </button>
            <button
              type="button"
              onClick={() => setShowRoutes((v) => !v)}
              className={`flex flex-col items-center justify-center gap-1 px-2 py-2 rounded-xl text-[10px] transition-all border ${
                showRoutes ? 'bg-white text-black border-white' : 'bg-white/5 text-muted-foreground border-white/10 hover:bg-white/10'
              }`}
              aria-pressed={showRoutes}
            >
              <Navigation className="w-3.5 h-3.5" />
              Trajets
            </button>
            <button
              type="button"
              onClick={() => setShowGps((v) => !v)}
              className={`flex flex-col items-center justify-center gap-1 px-2 py-2 rounded-xl text-[10px] transition-all border ${
                showGps ? 'bg-white text-black border-white' : 'bg-white/5 text-muted-foreground border-white/10 hover:bg-white/10'
              }`}
              aria-pressed={showGps}
            >
              <Layers className="w-3.5 h-3.5" />
              GPS
            </button>
          </div>

          <div className="grid grid-cols-3 gap-2">
            <div className="glass-panel rounded-xl p-3 border border-white/10">
              <div className="flex items-center gap-2 mb-1">
                <Navigation className="w-3.5 h-3.5 text-muted-foreground" />
                <span className="font-mono text-xs text-muted-foreground">Points</span>
              </div>
              <p className="text-xl font-bold">{historyCount}</p>
            </div>
            <div className="glass-panel rounded-xl p-3 border border-white/10">
              <div className="flex items-center gap-2 mb-1">
                <MapPin className="w-3.5 h-3.5 text-muted-foreground" />
                <span className="font-mono text-xs text-muted-foreground">Lieux</span>
              </div>
              <p className="text-xl font-bold">{places.length}</p>
            </div>
            <div className="glass-panel rounded-xl p-3 border border-white/10">
              <div className="flex items-center gap-2 mb-1">
                <Layers className="w-3.5 h-3.5 text-muted-foreground" />
                <span className="font-mono text-xs text-muted-foreground">Visites</span>
              </div>
              <p className="text-xl font-bold">{todayVisits.length}</p>
            </div>
          </div>

          {historyCount > 0 && places.length === 0 && (
            <p className="font-mono text-[10px] text-muted-foreground leading-relaxed">
              Historique téléphone reçu — aucun lieu nommé pour l’instant.
            </p>
          )}

          <div className="glass-panel rounded-xl p-4 border border-white/10">
            <h3 className="font-mono text-xs text-muted-foreground uppercase tracking-wider mb-3">
              Activité 7 jours
            </h3>
            <div className="space-y-2">
              {weekGroups.map((g) => (
                <div key={g.day} className="flex items-center gap-2 group relative">
                  <span className="font-mono text-xs text-muted-foreground w-7 shrink-0">{g.day}</span>
                  <div className="flex-1 h-1.5 bg-white/5 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-white rounded-full transition-all"
                      style={{ width: `${(g.count / maxWeekCount) * 100}%` }}
                    />
                  </div>
                  <span className="font-mono text-xs text-muted-foreground w-4 text-right">{g.count}</span>
                  {g.count > 0 && (
                    <div className="absolute left-10 -top-8 hidden group-hover:block z-10 bg-black/90 border border-white/10 rounded-lg px-2 py-1 text-xs font-mono whitespace-nowrap pointer-events-none">
                      {g.count} visite{g.count > 1 ? 's' : ''} · {formatDurationMin(g.totalMin)}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>

          <div>
            <h3 className="font-mono text-xs text-muted-foreground uppercase tracking-wider mb-2">
              Lieux fréquents
            </h3>
            {places.length === 0 ? (
              <p className="text-sm text-muted-foreground py-2">Aucun lieu enregistré.</p>
            ) : (
              <div className="space-y-1.5">
                {sortedByVisit.slice(0, 8).map((place) => (
                  <button
                    key={place.id}
                    type="button"
                    onClick={() => {
                      setSelectedPlace(place);
                      setSelectedPoint(null);
                      setSelectedTrip(null);
                    }}
                    className={`w-full text-left p-2.5 rounded-xl transition-all border ${
                      selectedPlace?.id === place.id
                        ? 'bg-white/10 border-white/30'
                        : 'hover:bg-white/5 border-transparent hover:border-white/10'
                    }`}
                  >
                    <div className="flex items-center gap-2.5">
                      <div className="w-8 h-8 rounded-lg bg-white/5 border border-white/10 flex items-center justify-center text-sm shrink-0">
                        {categoryEmoji(place.category)}
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-1.5">
                          <p className="text-sm truncate">{place.name}</p>
                          <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${intensityDot(place.visit_count ?? 0)}`} />
                        </div>
                        <div className="flex items-center gap-2 mt-0.5">
                          <span className="font-mono text-xs text-muted-foreground">
                            {place.visit_count ?? 0} visites
                          </span>
                          {place.avg_duration_min !== undefined && place.avg_duration_min > 0 && (
                            <span className="font-mono text-xs text-muted-foreground">
                              · {formatDurationMin(place.avg_duration_min)}
                            </span>
                          )}
                        </div>
                        <p className="font-mono text-xs text-muted-foreground/60 mt-0.5">
                          {timeAgo(place.last_visit ?? undefined)}
                        </p>
                      </div>
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>

          <div>
            <h3 className="font-mono text-xs text-muted-foreground uppercase tracking-wider mb-2">
              Patterns détectés
            </h3>
            {patterns.length === 0 ? (
              <p className="text-sm text-muted-foreground">Aucun pattern détecté.</p>
            ) : (
              <div className="space-y-1.5">
                {patterns.slice(0, 5).map((pat, i) => (
                  <div key={i} className="p-2.5 rounded-xl bg-white/3 border border-white/8 glass-panel">
                    <div className="flex items-start gap-2">
                      <span className="text-sm shrink-0 mt-0.5">{patternIcon(pat.pattern_type ?? '')}</span>
                      <div className="min-w-0">
                        <p className="text-xs leading-snug">{pat.description ?? pat.pattern_type}</p>
                        {pat.occurrences !== undefined && (
                          <span className="font-mono text-xs text-muted-foreground">{pat.occurrences}×</span>
                        )}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </aside>

      <div className="flex-1 flex flex-col min-w-0 relative">
        <div className="shrink-0 flex items-center justify-between px-5 py-3 border-b border-white/10 glass-panel gap-3 flex-wrap">
          <div className="min-w-0">
            <h2 className="text-sm font-semibold">Carte Interactive</h2>
            <p className={`font-mono text-xs ${
              locationUnavailable
                ? 'text-red-400'
                : locationDisplay.freshness === 'recent'
                  ? 'text-emerald-400'
                  : 'text-muted-foreground'
            }`}>
              {locationUnavailable ? 'Serveur de localisation indisponible' : locationDisplay.label}
              {latestPoint?.accuracy != null && Number.isFinite(latestPoint.accuracy) && (
                <span className="text-muted-foreground"> · {formatAccuracy(latestPoint.accuracy)}</span>
              )}
            </p>
            {locationStatus?.tracking_enabled === false && (
              <p className="font-mono text-[10px] text-amber-400">
                Enrichissement des lieux désactivé — historique brut visible
              </p>
            )}
            {mapTileError && (
              <p className="font-mono text-[10px] text-amber-400">
                Tuiles OpenFreeMap indisponibles — données locales affichables dès rétablissement
              </p>
            )}
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              type="button"
              onClick={() => setFitToken((n) => n + 1)}
              className="h-8 px-3 rounded-lg bg-white/5 border border-white/10 hover:bg-white/10 flex items-center gap-1.5 transition-colors font-mono text-xs"
              aria-label="Ajuster la vue aux données"
            >
              <LocateFixed className="w-3.5 h-3.5" />
              Ajuster
            </button>
            <button
              type="button"
              onClick={() => setRecenterToken((n) => n + 1)}
              disabled={!latestPoint}
              className="h-8 px-3 rounded-lg bg-white/5 border border-white/10 hover:bg-white/10 disabled:opacity-40 flex items-center gap-1.5 transition-colors font-mono text-xs"
              aria-label="Recentrer sur la dernière position"
            >
              <Crosshair className="w-3.5 h-3.5" />
              Recentrer
            </button>
          </div>
        </div>

        <div className="flex-1 relative overflow-hidden bg-black" style={{ minHeight: 0 }}>
          {hasLocationData ? (
            <Suspense
              fallback={(
                <div className="absolute inset-0 flex items-center justify-center" data-testid="cartography-map-suspense">
                  <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
                </div>
              )}
            >
              <CartographyMap
                places={places}
                historyPoints={filteredPoints}
                trips={filteredTrips}
                latestPointId={latestPoint?.id ?? null}
                showPlaces={showPlaces}
                showGps={showGps}
                showTrips={showRoutes}
                selectedPlaceId={selectedPlace?.id ?? null}
                fitToken={fitToken}
                recenterToken={recenterToken}
                onSelect={handleMapSelect}
                onErrorChange={setMapTileError}
              />
            </Suspense>
          ) : (
            <div
              className="absolute inset-0 flex flex-col items-center justify-center gap-4 text-center px-8"
              data-testid="cartography-empty"
            >
              <MapPin className="w-12 h-12 text-white/20" />
              <div>
                <p className="text-sm text-muted-foreground max-w-xs">
                  {locationUnavailable
                    ? 'Serveur de localisation indisponible.'
                    : selectedDate
                      ? `Aucune donnée pour le ${selectedDate}.`
                      : 'Aucune position reçue depuis le téléphone.'}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setShowAddForm(true)}
                className="px-4 py-2 rounded-xl bg-white/5 border border-white/20 hover:bg-white/10 text-sm transition-colors flex items-center gap-2"
              >
                <Plus className="w-4 h-4" />
                Ajouter un lieu manuellement
              </button>
            </div>
          )}

          {selectedPlace && (
            <div className="absolute top-4 right-4 w-72 max-h-[min(70vh,28rem)] overflow-y-auto glass-panel rounded-2xl border border-white/20 p-4 shadow-2xl map-info-panel z-20">
              <div className="flex items-start justify-between mb-3">
                <div className="flex items-center gap-2">
                  <div className="w-10 h-10 rounded-xl bg-white/10 border border-white/20 flex items-center justify-center text-lg">
                    {categoryEmoji(selectedPlace.category)}
                  </div>
                  <div>
                    <p className="font-semibold text-sm leading-tight">{selectedPlace.name}</p>
                    <p className="font-mono text-xs text-muted-foreground">#{String(selectedPlace.id).padStart(4, '0')}</p>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => setSelectedPlace(null)}
                  className="w-7 h-7 rounded-lg bg-white/5 hover:bg-white/10 flex items-center justify-center transition-colors shrink-0"
                  aria-label="Fermer"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
              <div className="grid grid-cols-2 gap-2 mb-3">
                <div className="p-2 rounded-lg bg-white/5 border border-white/8">
                  <p className="font-mono text-xs text-muted-foreground">Visites</p>
                  <p className="font-bold text-base">{selectedPlace.visit_count ?? 0}</p>
                </div>
                <div className="p-2 rounded-lg bg-white/5 border border-white/8">
                  <p className="font-mono text-xs text-muted-foreground">Durée moy.</p>
                  <p className="font-bold text-base">{formatDurationMin(selectedPlace.avg_duration_min ?? 0)}</p>
                </div>
              </div>
              <div className="space-y-1 mb-3 text-xs">
                <div className="flex justify-between">
                  <span className="text-muted-foreground font-mono">Catégorie</span>
                  <span className="font-mono capitalize">{selectedPlace.category}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground font-mono">Dernière visite</span>
                  <span className="font-mono">{timeAgo(selectedPlace.last_visit ?? undefined)}</span>
                </div>
                {selectedPlace.address && (
                  <div className="flex justify-between gap-2">
                    <span className="text-muted-foreground font-mono shrink-0">Adresse</span>
                    <span className="font-mono text-right truncate">{selectedPlace.address}</span>
                  </div>
                )}
                <div className="flex justify-between">
                  <span className="text-muted-foreground font-mono">Coords</span>
                  <span className="font-mono text-right">
                    {selectedPlace.latitude.toFixed(4)}, {selectedPlace.longitude.toFixed(4)}
                  </span>
                </div>
              </div>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => void handleRename(selectedPlace)}
                  className="flex-1 px-3 py-1.5 rounded-lg bg-white/5 border border-white/10 hover:bg-white/10 text-xs transition-colors"
                >
                  Renommer
                </button>
                <button
                  type="button"
                  onClick={() => void handleDelete(selectedPlace)}
                  className="flex-1 px-3 py-1.5 rounded-lg bg-red-500/10 border border-red-500/20 hover:bg-red-500/20 text-xs text-red-400 transition-colors"
                >
                  Supprimer
                </button>
              </div>
            </div>
          )}

          {selectedPoint && !selectedPlace && (
            <div className="absolute top-4 right-4 w-72 glass-panel rounded-2xl border border-white/20 p-4 shadow-2xl map-info-panel z-20">
              <div className="flex items-start justify-between mb-3">
                <div>
                  <p className="font-semibold text-sm">Point GPS</p>
                  <p className="font-mono text-xs text-muted-foreground">
                    {selectedPoint.created_at}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setSelectedPoint(null)}
                  className="w-7 h-7 rounded-lg bg-white/5 hover:bg-white/10 flex items-center justify-center"
                  aria-label="Fermer"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
              <div className="space-y-1 text-xs font-mono">
                <div className="flex justify-between gap-2">
                  <span className="text-muted-foreground">Coords</span>
                  <span>{selectedPoint.latitude.toFixed(5)}, {selectedPoint.longitude.toFixed(5)}</span>
                </div>
                {formatAccuracy(selectedPoint.accuracy) && (
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Précision</span>
                    <span>{formatAccuracy(selectedPoint.accuracy)}</span>
                  </div>
                )}
                {selectedPoint.place_name && (
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Lieu</span>
                    <span>{selectedPoint.place_name}</span>
                  </div>
                )}
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Jour</span>
                  <span>{localDateKey(selectedPoint.created_at) ?? '—'}</span>
                </div>
              </div>
            </div>
          )}

          {selectedTrip && !selectedPlace && !selectedPoint && (
            <div className="absolute top-4 right-4 w-72 glass-panel rounded-2xl border border-white/20 p-4 shadow-2xl map-info-panel z-20">
              <div className="flex items-start justify-between mb-3">
                <div>
                  <p className="font-semibold text-sm">Trajet</p>
                  <p className="font-mono text-xs text-muted-foreground capitalize">
                    {selectedTrip.transport_mode ?? 'inconnu'}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setSelectedTrip(null)}
                  className="w-7 h-7 rounded-lg bg-white/5 hover:bg-white/10 flex items-center justify-center"
                  aria-label="Fermer"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
              <div className="space-y-1 text-xs font-mono">
                <div className="flex justify-between gap-2">
                  <span className="text-muted-foreground">De</span>
                  <span className="truncate text-right">
                    {selectedTrip.from_place
                      ?? (selectedTrip.from_place_id != null
                        ? placeNameById.get(selectedTrip.from_place_id)
                        : null)
                      ?? '—'}
                  </span>
                </div>
                <div className="flex justify-between gap-2">
                  <span className="text-muted-foreground">Vers</span>
                  <span className="truncate text-right">
                    {selectedTrip.to_place
                      ?? (selectedTrip.to_place_id != null
                        ? placeNameById.get(selectedTrip.to_place_id)
                        : null)
                      ?? '—'}
                  </span>
                </div>
                {selectedTrip.distance_km != null && (
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Distance</span>
                    <span>{selectedTrip.distance_km.toFixed(1)} km</span>
                  </div>
                )}
                {selectedTrip.duration_min != null && (
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Durée</span>
                    <span>{formatDurationMin(selectedTrip.duration_min)}</span>
                  </div>
                )}
                {selectedTrip.started_at && (
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Début</span>
                    <span className="truncate">{selectedTrip.started_at}</span>
                  </div>
                )}
              </div>
            </div>
          )}

          {latestPoint && !locationStatus?.current_visit && (
            <button
              type="button"
              onClick={() => void handleNameCurrent()}
              className="absolute bottom-4 left-1/2 -translate-x-1/2 px-4 py-2 rounded-xl bg-white/10 border border-white/20 hover:bg-white/15 text-sm transition-colors backdrop-blur-sm flex items-center gap-2 z-20"
            >
              <MapPin className="w-4 h-4" />
              Nommer cet endroit
            </button>
          )}

          {places.length > 0 && (
            <button
              type="button"
              onClick={() => setShowAddForm((v) => !v)}
              className="absolute bottom-4 right-4 w-10 h-10 rounded-xl bg-white/10 border border-white/20 hover:bg-white/15 flex items-center justify-center transition-colors backdrop-blur-sm z-20"
              aria-label="Ajouter un lieu"
              title="Ajouter un lieu"
            >
              <Plus className="w-5 h-5" />
            </button>
          )}
        </div>

        {showAddForm && (
          <div className="shrink-0 border-t border-white/10 glass-panel p-4">
            <div className="flex items-center justify-between mb-3">
              <h4 className="font-mono text-xs uppercase tracking-wider">Ajouter un lieu</h4>
              <button type="button" onClick={() => setShowAddForm(false)} className="text-muted-foreground hover:text-white">
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <input
                className="col-span-2 h-9 px-3 rounded-xl bg-white/5 border border-white/10 text-sm focus:outline-none focus:border-white/30 font-mono placeholder:text-muted-foreground/50"
                placeholder="Nom du lieu"
                value={addName}
                onChange={(e) => setAddName(e.target.value)}
              />
              <select
                className="h-9 px-3 rounded-xl bg-white/5 border border-white/10 text-sm focus:outline-none focus:border-white/30 font-mono text-muted-foreground"
                value={addCategory}
                onChange={(e) => setAddCategory(e.target.value)}
              >
                {['home','work','school','restaurant','shop','gym','leisure','social','medical','transport','other'].map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
              <div className="flex gap-2">
                <input
                  className="flex-1 h-9 px-3 rounded-xl bg-white/5 border border-white/10 text-sm focus:outline-none focus:border-white/30 font-mono placeholder:text-muted-foreground/50"
                  placeholder="Lat"
                  value={addLat}
                  onChange={(e) => setAddLat(e.target.value)}
                />
                <input
                  className="flex-1 h-9 px-3 rounded-xl bg-white/5 border border-white/10 text-sm focus:outline-none focus:border-white/30 font-mono placeholder:text-muted-foreground/50"
                  placeholder="Lng"
                  value={addLng}
                  onChange={(e) => setAddLng(e.target.value)}
                />
              </div>
              <button
                type="button"
                onClick={() => void handleAddPlace()}
                disabled={addSaving || !addName.trim() || !addLat || !addLng}
                className="col-span-2 h-9 rounded-xl bg-white text-black text-sm font-medium hover:bg-white/90 disabled:opacity-40 disabled:cursor-not-allowed transition-colors flex items-center justify-center gap-2"
              >
                {addSaving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
                Enregistrer
              </button>
            </div>
          </div>
        )}
      </div>

      <style>{`
        @media (max-width: 720px) {
          .map-layout { flex-direction: column !important; }
          .map-sidebar { width: 100% !important; max-height: 40vh; border-right: none !important; border-bottom: 1px solid rgba(255,255,255,0.1); }
          .map-info-panel { top: auto !important; right: 0.5rem !important; bottom: 0.5rem !important; left: 0.5rem !important; width: auto !important; }
        }
        .maplibre-ctrl-attrib {
          font-size: 10px;
          background: rgba(0,0,0,0.55) !important;
          color: rgba(255,255,255,0.75) !important;
        }
        .maplibre-ctrl-attrib a {
          color: rgba(255,255,255,0.9) !important;
        }
      `}</style>
    </div>
  );
}
