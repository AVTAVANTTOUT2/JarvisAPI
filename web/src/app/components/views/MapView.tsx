import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Layers,
  Navigation,
  MapPin,
  X,
  Plus,
  Minus,
  Loader2,
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

// ── Types ────────────────────────────────────────────────────

interface Place {
  id: number;
  name: string;
  category: string;
  latitude: number;
  longitude: number;
  radius_meters?: number;
  visit_count?: number;
  avg_duration_min?: number;
  last_visit?: string;
  address?: string;
  notes?: string;
}

interface Visit {
  place_id?: number;
  place_name?: string;
  arrived_at: string;
  departed_at?: string;
  duration_min?: number;
}

interface Trip {
  from_place?: string;
  to_place?: string;
  distance_km?: number;
  duration_min?: number;
  transport_mode?: string;
  started_at?: string;
}

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

interface GeoCoordinate {
  latitude: number;
  longitude: number;
}

// ── Helpers ──────────────────────────────────────────────────


function categoryEmoji(cat: string): string {
  const map: Record<string, string> = {
    home: '🏠', work: '💼', school: '📚', gym: '💪',
    restaurant: '🍽️', shop: '🛍️', friend: '👤', family: '👨‍👩‍👧',
    medical: '🏥', transport: '🚆', leisure: '🎮', other: '📍',
    social: '🎉', health: '🏥',
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

function markerColor(category: string): string {
  switch (category) {
    case 'home': return 'rgba(255,255,255,1)';
    case 'work':
    case 'school': return 'rgba(255,255,255,0.9)';
    case 'gym':
    case 'health':
    case 'medical': return 'rgba(200,200,200,0.85)';
    case 'restaurant':
    case 'shop':
    case 'social':
    case 'friend':
    case 'family': return 'rgba(170,170,170,0.85)';
    case 'leisure': return 'rgba(150,150,150,0.85)';
    default: return 'rgba(120,120,120,0.85)';
  }
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

// ── SVG projection ───────────────────────────────────────────

function projectToSVG(
  lat: number,
  lng: number,
  coordinates: GeoCoordinate[],
  width: number,
  height: number,
): { x: number; y: number } {
  if (coordinates.length <= 1) return { x: width / 2, y: height / 2 };
  const padding = 0.12;
  const lats = coordinates.map((p) => p.latitude);
  const lngs = coordinates.map((p) => p.longitude);
  const minLat = Math.min(...lats);
  const maxLat = Math.max(...lats);
  const minLng = Math.min(...lngs);
  const maxLng = Math.max(...lngs);
  const latRange = maxLat - minLat || 0.01;
  const lngRange = maxLng - minLng || 0.01;
  const x = padding * width + ((lng - minLng) / lngRange) * width * (1 - 2 * padding);
  const y = padding * height + ((maxLat - lat) / latRange) * height * (1 - 2 * padding);
  return { x, y };
}

// ── Composant principal ──────────────────────────────────────

export function MapView() {
  const svgRef = useRef<SVGSVGElement>(null);
  const [svgSize, setSvgSize] = useState({ w: 800, h: 600 });

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
  const [hoveredPlace, setHoveredPlace] = useState<Place | null>(null);
  const [showHeatmap, setShowHeatmap] = useState(true);
  const [showRoutes, setShowRoutes] = useState(true);
  const [zoomLevel, setZoomLevel] = useState(1);

  // Form "ajouter lieu"
  const [showAddForm, setShowAddForm] = useState(false);
  const [addName, setAddName] = useState('');
  const [addCategory, setAddCategory] = useState('other');
  const [addLat, setAddLat] = useState('');
  const [addLng, setAddLng] = useState('');
  const [addSaving, setAddSaving] = useState(false);

  // ── Chargement des données ────────────────────────────────

  const loadAll = useCallback(async () => {
    try {
      const [p, tv, wv, tr, pat, loc, history] = await Promise.all([
        api.getPlaces() as Promise<{ places: Place[] }>,
        api.getTodayVisits() as Promise<{ visits: Visit[] }>,
        api.getVisits(7) as Promise<{ visits: Visit[] }>,
        api.getTrips(30) as Promise<{ trips: Trip[] }>,
        api.getLocationPatterns() as Promise<{ patterns: Pattern[] }>,
        api.getLocationStatus() as Promise<LocationStatus>,
        api.getLocationHistory(24) as Promise<unknown>,
      ]);
      setPlaces(p.places ?? []);
      setTodayVisits(tv.visits ?? []);
      setWeekVisits(wv.visits ?? []);
      setTrips(tr.trips ?? []);
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

  // Rafraîchissement Live sans polling agressif.
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

  // Mesure du SVG
  useEffect(() => {
    const el = svgRef.current?.parentElement;
    if (!el) return;
    const obs = new ResizeObserver(() => {
      setSvgSize({ w: el.clientWidth, h: el.clientHeight });
    });
    obs.observe(el);
    setSvgSize({ w: el.clientWidth, h: el.clientHeight });
    return () => obs.disconnect();
  }, []);

  // ── Données dérivées ──────────────────────────────────────

  const maxVisitCount = Math.max(1, ...places.map((p) => p.visit_count ?? 0));
  const sortedByVisit = [...places].sort((a, b) => (b.visit_count ?? 0) - (a.visit_count ?? 0));
  const weekGroups = groupVisitsByDay(weekVisits);
  const maxWeekCount = Math.max(1, ...weekGroups.map((g) => g.count));
  const statusPoint = mapLocationPoint(locationStatus?.current_location ?? null) ?? undefined;
  const latestPoint = resolveDisplayLocationPoint(historyPoints, statusPoint);
  const locationDisplay = getLocationDisplayStatus(latestPoint);
  const mapCoordinates: GeoCoordinate[] = [
    ...places,
    ...historyPoints,
    ...(latestPoint ? [latestPoint] : []),
  ];
  const hasLocationData = mapCoordinates.length > 0;
  const historyCount = Math.max(historyPoints.length, locationStatus?.points_24h ?? 0);

  // Trajets uniques (from → to)
  type TripKey = string;
  const tripMap = new Map<TripKey, { from: string; to: string; count: number }>();
  trips.forEach((t) => {
    if (!t.from_place || !t.to_place) return;
    const key = [t.from_place, t.to_place].sort().join('|||');
    const existing = tripMap.get(key);
    if (existing) existing.count++;
    else tripMap.set(key, { from: t.from_place, to: t.to_place, count: 1 });
  });

  // Trouver les coordonnées des lieux pour les trajets
  const placeByName = new Map(places.map((p) => [p.name, p]));

  // ── Actions ───────────────────────────────────────────────

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

  // ── Carte SVG ─────────────────────────────────────────────

  const { w, h } = svgSize;

  function renderMap() {
    // Historique GPS brut (Android) doit s'afficher même sans lieux nommés.
    if (!hasLocationData) return null;

    return (
      <g transform={`scale(${zoomLevel}) translate(${(w * (1 - zoomLevel)) / (2 * zoomLevel)}, ${(h * (1 - zoomLevel)) / (2 * zoomLevel)})`}>
        {/* Couche 2 — Heatmap */}
        {showHeatmap &&
          places.map((place) => {
            const { x, y } = projectToSVG(place.latitude, place.longitude, mapCoordinates, w, h);
            const vc = place.visit_count ?? 0;
            const baseR = 20 + vc * 2;
            const opacity = 0.04 + (vc / maxVisitCount) * 0.14;
            return (
              <g key={`heat-${place.id}`}>
                <circle cx={x} cy={y} r={baseR * 1.5} fill="white" opacity={opacity * 0.4} />
                <circle cx={x} cy={y} r={baseR} fill="white" opacity={opacity} />
              </g>
            );
          })}

        {/* Couche 3 — Routes */}
        {showRoutes &&
          Array.from(tripMap.values()).map(({ from, to, count }) => {
            const pFrom = placeByName.get(from);
            const pTo = placeByName.get(to);
            if (!pFrom || !pTo) return null;
            const a = projectToSVG(pFrom.latitude, pFrom.longitude, places, w, h);
            const b = projectToSVG(pTo.latitude, pTo.longitude, places, w, h);
            const isActive =
              selectedPlace?.name === from || selectedPlace?.name === to;
            const strokeW = Math.min(3, 0.5 + count * 0.4);
            return (
              <line
                key={`route-${from}-${to}`}
                x1={a.x} y1={a.y}
                x2={b.x} y2={b.y}
                stroke="white"
                strokeWidth={strokeW}
                strokeOpacity={isActive ? 0.7 : 0.2}
                strokeDasharray="8 4"
                className="route-dash"
              />
            );
          })}

        {/* Couche 4 — Marqueurs */}
        {places.map((place) => {
          const { x, y } = projectToSVG(place.latitude, place.longitude, places, w, h);
          const isSelected = selectedPlace?.id === place.id;
          const isHovered = hoveredPlace?.id === place.id;
          const color = markerColor(place.category);
          const r = isSelected ? 12 : isHovered ? 11 : 8;
          return (
            <g
              key={`marker-${place.id}`}
              style={{ cursor: 'pointer' }}
              onClick={() => setSelectedPlace(place)}
              onMouseEnter={() => setHoveredPlace(place)}
              onMouseLeave={() => setHoveredPlace(null)}
            >
              {/* Glow */}
              <circle
                cx={x} cy={y}
                r={isHovered || isSelected ? 28 : 18}
                fill="white"
                opacity={isSelected ? 0.12 : isHovered ? 0.08 : 0.04}
              />
              {/* Anneaux pulsants si sélectionné */}
              {isSelected && (
                <>
                  <circle cx={x} cy={y} r={18} fill="none" stroke="white" strokeWidth={1} opacity={0.3} className="ping-ring" />
                  <circle cx={x} cy={y} r={24} fill="none" stroke="white" strokeWidth={0.5} opacity={0.15} className="ping-ring-2" />
                </>
              )}
              {/* Cercle principal */}
              <circle cx={x} cy={y} r={r} fill={color} />
              {/* Point central */}
              <circle cx={x} cy={y} r={3} fill="black" opacity={0.6} />
              {/* Label hover/selected */}
              {(isHovered || isSelected) && (
                <g>
                  <rect
                    x={x + 14} y={y - 14}
                    width={Math.min(place.name.length * 7.5 + 16, 180)} height={24}
                    rx={4}
                    fill="rgba(0,0,0,0.85)"
                    stroke="rgba(255,255,255,0.2)"
                    strokeWidth={0.5}
                  />
                  <text
                    x={x + 22} y={y + 2}
                    fill="white"
                    fontSize={11}
                    fontFamily="'JetBrains Mono', monospace"
                    dominantBaseline="middle"
                  >
                    {place.name.slice(0, 20)}
                  </text>
                </g>
              )}
            </g>
          );
        })}

        {/* Couche 5 — Historique brut reçu des téléphones */}
        {historyPoints.slice(-200).map((point) => {
          const { x, y } = projectToSVG(
            point.latitude,
            point.longitude,
            mapCoordinates,
            w,
            h,
          );
          const isLatest = point.id === latestPoint?.id;
          return (
            <g key={`location-${point.id}`}>
              {isLatest && <circle cx={x} cy={y} r={18} fill="rgba(59,130,246,0.12)" />}
              <circle
                cx={x}
                cy={y}
                r={isLatest ? 6 : 2.5}
                fill={isLatest ? 'rgba(59,130,246,0.95)' : 'rgba(148,163,184,0.55)'}
                stroke={isLatest ? 'white' : 'none'}
                strokeWidth={isLatest ? 1.5 : 0}
              />
            </g>
          );
        })}

        {/* Couche 6 — Dernière position connue (historique ou status) */}
        {latestPoint && (() => {
          const loc = latestPoint;
          const { x, y } = projectToSVG(loc.latitude, loc.longitude, mapCoordinates, w, h);
          return (
            <g>
              <circle cx={x} cy={y} r={20} fill="rgba(59,130,246,0.1)" />
              <circle cx={x} cy={y} r={8} fill="rgba(59,130,246,0.9)" />
              <circle cx={x} cy={y} r={3} fill="white" />
              <rect x={x + 12} y={y - 12} width={90} height={22} rx={4} fill="rgba(0,0,0,0.85)" stroke="rgba(59,130,246,0.4)" strokeWidth={1} />
              <text x={x + 20} y={y + 1} fill="white" fontSize={10} fontFamily="'JetBrains Mono', monospace" dominantBaseline="middle">
                Vous êtes ici
              </text>
            </g>
          );
        })()}
      </g>
    );
  }

  // ── Rendu ─────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="flex flex-1 min-h-0 h-full">
      {/* ── Sidebar gauche ── */}
      <aside className="w-80 shrink-0 border-r border-border glass-panel overflow-y-auto flex flex-col">
        {/* Header */}
        <div className="p-5 border-b border-white/10">
          <h1 className="text-sm font-bold tracking-widest uppercase">Cartographie</h1>
          <p className="font-mono text-xs text-muted-foreground mt-0.5">Surveillance des déplacements</p>
        </div>

        <div className="p-4 space-y-5 flex-1">
          {/* Contrôles toggle */}
          <div className="flex gap-2">
            <button
              onClick={() => setShowHeatmap((v) => !v)}
              className={`flex-1 flex items-center justify-center gap-2 px-3 py-2 rounded-xl text-xs transition-all border ${
                showHeatmap ? 'bg-white text-black border-white' : 'bg-white/5 text-muted-foreground border-white/10 hover:bg-white/10'
              }`}
            >
              <Layers className="w-3.5 h-3.5" />
              Heatmap
            </button>
            <button
              onClick={() => setShowRoutes((v) => !v)}
              className={`flex-1 flex items-center justify-center gap-2 px-3 py-2 rounded-xl text-xs transition-all border ${
                showRoutes ? 'bg-white text-black border-white' : 'bg-white/5 text-muted-foreground border-white/10 hover:bg-white/10'
              }`}
            >
              <Navigation className="w-3.5 h-3.5" />
              Routes
            </button>
          </div>

          {/* Stats rapides — points GPS ≠ lieux nommés */}
          <div className="grid grid-cols-2 gap-2">
            <div className="glass-panel rounded-xl p-3 border border-white/10">
              <div className="flex items-center gap-2 mb-1">
                <Navigation className="w-3.5 h-3.5 text-muted-foreground" />
                <span className="font-mono text-xs text-muted-foreground">Points GPS</span>
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
          </div>
          {historyCount > 0 && places.length === 0 && (
            <p className="font-mono text-[10px] text-muted-foreground leading-relaxed">
              Historique téléphone reçu — aucun lieu nommé pour l’instant.
            </p>
          )}

          {/* Activité hebdomadaire */}
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
                  {/* Tooltip */}
                  {g.count > 0 && (
                    <div className="absolute left-10 -top-8 hidden group-hover:block z-10 bg-black/90 border border-white/10 rounded-lg px-2 py-1 text-xs font-mono whitespace-nowrap pointer-events-none">
                      {g.count} visite{g.count > 1 ? 's' : ''} · {formatDurationMin(g.totalMin)}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* Lieux fréquents */}
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
                    onClick={() => setSelectedPlace(place)}
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
                          {timeAgo(place.last_visit)}
                        </p>
                      </div>
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Patterns */}
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

      {/* ── Zone carte ── */}
      <div className="flex-1 flex flex-col min-w-0 relative">
        {/* Header carte */}
        <div className="shrink-0 flex items-center justify-between px-5 py-3 border-b border-white/10 glass-panel">
          <div>
            <h2 className="text-sm font-semibold">Carte Interactive</h2>
            <p className={`font-mono text-xs ${
              locationUnavailable
                ? 'text-red-400'
                : locationDisplay.freshness === 'recent'
                  ? 'text-emerald-400'
                  : 'text-muted-foreground'
            }`}>
              {locationUnavailable ? 'Serveur de localisation indisponible' : locationDisplay.label}
            </p>
            {locationStatus?.tracking_enabled === false && (
              <p className="font-mono text-[10px] text-amber-400">
                Enrichissement des lieux désactivé — historique brut visible
              </p>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setZoomLevel((z) => Math.min(3, +(z + 0.2).toFixed(1)))}
              className="w-8 h-8 rounded-lg bg-white/5 border border-white/10 hover:bg-white/10 flex items-center justify-center transition-colors"
              aria-label="Zoom +"
            >
              <Plus className="w-4 h-4" />
            </button>
            <span className="font-mono text-xs text-muted-foreground w-10 text-center">
              {Math.round(zoomLevel * 100)}%
            </span>
            <button
              onClick={() => setZoomLevel((z) => Math.max(0.4, +(z - 0.2).toFixed(1)))}
              className="w-8 h-8 rounded-lg bg-white/5 border border-white/10 hover:bg-white/10 flex items-center justify-center transition-colors"
              aria-label="Zoom -"
            >
              <Minus className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* SVG + overlays */}
        <div className="flex-1 relative overflow-hidden bg-black" style={{ minHeight: 0 }}>
          {/* Grille de fond */}
          <svg
            ref={svgRef}
            width="100%"
            height="100%"
            className="absolute inset-0"
            style={{ display: 'block' }}
          >
            <defs>
              <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
                <path d="M 40 0 L 0 0 0 40" fill="none" stroke="white" strokeWidth="0.5" opacity="0.03" />
              </pattern>
              <style>{`
                @keyframes ping-ring {
                  0% { r: 18; opacity: 0.3; }
                  100% { r: 32; opacity: 0; }
                }
                @keyframes ping-ring-2 {
                  0% { r: 24; opacity: 0.15; }
                  100% { r: 40; opacity: 0; }
                }
                @keyframes route-dash {
                  to { stroke-dashoffset: -24; }
                }
                .ping-ring { animation: ping-ring 1.8s ease-out infinite; }
                .ping-ring-2 { animation: ping-ring-2 1.8s ease-out infinite 0.6s; }
                .route-dash { animation: route-dash 2s linear infinite; }
              `}</style>
            </defs>
            <rect width="100%" height="100%" fill="url(#grid)" />

            {hasLocationData ? (
              renderMap()
            ) : null}
          </svg>

          {/* État vide */}
          {!hasLocationData && !loading && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-4 text-center px-8">
              <MapPin className="w-12 h-12 text-white/20" />
              <div>
                <p className="text-sm text-muted-foreground max-w-xs">
                  {locationUnavailable
                    ? 'Serveur de localisation indisponible.'
                    : 'Aucune position reçue depuis le téléphone.'}
                </p>
              </div>
              <button
                onClick={() => setShowAddForm(true)}
                className="px-4 py-2 rounded-xl bg-white/5 border border-white/20 hover:bg-white/10 text-sm transition-colors flex items-center gap-2"
              >
                <Plus className="w-4 h-4" />
                Ajouter un lieu manuellement
              </button>
            </div>
          )}

          {/* Panel info lieu (coin supérieur droit) */}
          {selectedPlace && (
            <div className="absolute top-4 right-4 w-72 glass-panel rounded-2xl border border-white/20 p-4 shadow-2xl">
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
                  onClick={() => setSelectedPlace(null)}
                  className="w-7 h-7 rounded-lg bg-white/5 hover:bg-white/10 flex items-center justify-center transition-colors shrink-0"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>

              {/* Stats */}
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

              {/* Détails */}
              <div className="space-y-1 mb-3 text-xs">
                <div className="flex justify-between">
                  <span className="text-muted-foreground font-mono">Catégorie</span>
                  <span className="font-mono capitalize">{selectedPlace.category}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground font-mono">Dernière visite</span>
                  <span className="font-mono">{timeAgo(selectedPlace.last_visit)}</span>
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

              {/* Actions */}
              <div className="flex gap-2">
                <button
                  onClick={() => void handleRename(selectedPlace)}
                  className="flex-1 px-3 py-1.5 rounded-lg bg-white/5 border border-white/10 hover:bg-white/10 text-xs transition-colors"
                >
                  Renommer
                </button>
                <button
                  onClick={() => void handleDelete(selectedPlace)}
                  className="flex-1 px-3 py-1.5 rounded-lg bg-red-500/10 border border-red-500/20 hover:bg-red-500/20 text-xs text-red-400 transition-colors"
                >
                  Supprimer
                </button>
              </div>
            </div>
          )}

          {/* Bouton "Nommer cet endroit" — nécessite une position affichable */}
          {latestPoint && !locationStatus?.current_visit && (
            <button
              onClick={() => void handleNameCurrent()}
              className="absolute bottom-4 left-1/2 -translate-x-1/2 px-4 py-2 rounded-xl bg-white/10 border border-white/20 hover:bg-white/15 text-sm transition-colors backdrop-blur-sm flex items-center gap-2"
            >
              <MapPin className="w-4 h-4" />
              Nommer cet endroit
            </button>
          )}

          {/* Bouton ajouter (si places > 0) */}
          {places.length > 0 && (
            <button
              onClick={() => setShowAddForm((v) => !v)}
              className="absolute bottom-4 right-4 w-10 h-10 rounded-xl bg-white/10 border border-white/20 hover:bg-white/15 flex items-center justify-center transition-colors backdrop-blur-sm"
              aria-label="Ajouter un lieu"
              title="Ajouter un lieu"
            >
              <Plus className="w-5 h-5" />
            </button>
          )}
        </div>

        {/* Formulaire ajout lieu (slide-down en bas) */}
        {showAddForm && (
          <div className="shrink-0 border-t border-white/10 glass-panel p-4">
            <div className="flex items-center justify-between mb-3">
              <h4 className="font-mono text-xs uppercase tracking-wider">Ajouter un lieu</h4>
              <button onClick={() => setShowAddForm(false)} className="text-muted-foreground hover:text-white">
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

      {/* CSS responsive */}
      <style>{`
        @media (max-width: 720px) {
          /* Layout vertical sous 720px */
          .map-layout { flex-direction: column !important; }
          .map-sidebar { width: 100% !important; max-height: 40vh; border-right: none !important; border-bottom: 1px solid rgba(255,255,255,0.1); }
          .map-info-panel { top: auto !important; right: 0.5rem !important; bottom: 0.5rem !important; left: 0.5rem !important; width: auto !important; }
        }
      `}</style>
    </div>
  );
}
