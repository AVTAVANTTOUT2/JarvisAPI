/**
 * Transformations pures → GeoJSON pour MapLibre (couches / sources uniques).
 * Aucun accès réseau. Coordonnées invalides filtrées silencieusement.
 */

export interface CartographyPlace {
  id: number;
  name: string;
  category: string;
  latitude: number;
  longitude: number;
  radius_meters?: number;
  visit_count?: number;
  avg_duration_min?: number;
  last_visit?: string | null;
  address?: string | null;
  notes?: string | null;
}

export interface CartographyLocationPoint {
  id: number;
  latitude: number;
  longitude: number;
  accuracy?: number | null;
  source?: string | null;
  place_id?: number | null;
  place_name?: string | null;
  created_at: string;
}

export interface CartographyTrip {
  id: number;
  from_place_id?: number | null;
  to_place_id?: number | null;
  from_place?: string;
  to_place?: string;
  started_at?: string;
  ended_at?: string;
  duration_min?: number;
  distance_km?: number | null;
  transport_mode?: string | null;
  /** JSON string ou tableau déjà parsé de [lat, lng][] */
  route_points?: string | [number, number][] | null;
}

export type LonLat = [number, number];

export interface GeoJsonFeatureCollection {
  type: 'FeatureCollection';
  features: GeoJsonFeature[];
}

export interface GeoJsonFeature {
  type: 'Feature';
  id?: string | number;
  geometry: {
    type: 'Point' | 'LineString';
    coordinates: LonLat | LonLat[];
  };
  properties: Record<string, string | number | boolean | null>;
}

/** Couleurs alignées sur la PWA Leaflet (mode sombre JARVIS). */
export const PLACE_CATEGORY_COLORS: Record<string, string> = {
  home: '#FF9500',
  work: '#007AFF',
  school: '#9C59FF',
  gym: '#30D158',
  sport: '#30D158',
  restaurant: '#FFD60A',
  shop: '#FFD60A',
  commerce: '#FFD60A',
  transport: '#5AC8FA',
  leisure: '#AF52DE',
  medical: '#FF453A',
  friend: '#8E8E93',
  family: '#8E8E93',
  other: '#8E8E93',
};

export const TRANSPORT_MODE_COLORS: Record<string, string> = {
  pied: '#30D158',
  marche: '#30D158',
  'vélo': '#5AC8FA',
  velo: '#5AC8FA',
  voiture: '#FF9F0A',
  transport: '#BF5AF2',
  transports: '#BF5AF2',
  inconnu: '#8E8E93',
  unknown: '#8E8E93',
};

export const MAX_HISTORY_POINTS_ON_MAP = 500;

export function isValidCoordinate(lat: unknown, lng: unknown): boolean {
  const latitude = Number(lat);
  const longitude = Number(lng);
  return (
    Number.isFinite(latitude)
    && Number.isFinite(longitude)
    && latitude >= -90
    && latitude <= 90
    && longitude >= -180
    && longitude <= 180
  );
}

export function colorForPlaceCategory(category: string | null | undefined): string {
  if (!category) return PLACE_CATEGORY_COLORS.other;
  return PLACE_CATEGORY_COLORS[category.toLowerCase()] ?? PLACE_CATEGORY_COLORS.other;
}

export function colorForTransportMode(mode: string | null | undefined): string {
  if (!mode) return TRANSPORT_MODE_COLORS.inconnu;
  const key = mode.trim().toLowerCase();
  return TRANSPORT_MODE_COLORS[key] ?? TRANSPORT_MODE_COLORS.inconnu;
}

export function normalizeTransportMode(mode: string | null | undefined): string {
  if (!mode || !mode.trim()) return 'inconnu';
  const key = mode.trim().toLowerCase();
  if (key === 'marche') return 'pied';
  if (key === 'velo') return 'vélo';
  if (key === 'transports' || key === 'unknown') return key === 'unknown' ? 'inconnu' : 'transport';
  if (['pied', 'vélo', 'voiture', 'transport', 'inconnu'].includes(key)) return key;
  return 'inconnu';
}

function emptyCollection(): GeoJsonFeatureCollection {
  return { type: 'FeatureCollection', features: [] };
}

/** Échantillonne une série chronologique en conservant le premier et le dernier point. */
export function downsampleChronologicalPoints<T>(
  points: T[],
  maxPoints: number = MAX_HISTORY_POINTS_ON_MAP,
): T[] {
  if (points.length <= maxPoints) return points;
  if (maxPoints < 2) return points.slice(0, maxPoints);
  const step = (points.length - 1) / (maxPoints - 1);
  const sampled: T[] = [];
  const used = new Set<number>();
  for (let i = 0; i < maxPoints; i += 1) {
    const idx = Math.round(i * step);
    if (used.has(idx)) continue;
    used.add(idx);
    sampled.push(points[idx]);
  }
  return sampled;
}

export function placesToGeoJSON(places: CartographyPlace[]): GeoJsonFeatureCollection {
  const features: GeoJsonFeature[] = [];
  for (const place of places) {
    if (!isValidCoordinate(place.latitude, place.longitude)) continue;
    features.push({
      type: 'Feature',
      id: `place-${place.id}`,
      geometry: {
        type: 'Point',
        coordinates: [place.longitude, place.latitude],
      },
      properties: {
        kind: 'place',
        placeId: place.id,
        name: place.name,
        category: place.category || 'other',
        color: colorForPlaceCategory(place.category),
        visitCount: place.visit_count ?? 0,
        avgDurationMin: place.avg_duration_min ?? 0,
        lastVisit: place.last_visit ?? null,
        address: place.address ?? null,
      },
    });
  }
  return { type: 'FeatureCollection', features };
}

export function historyPointsToGeoJSON(
  points: CartographyLocationPoint[],
  options?: { latestId?: number | null; maxPoints?: number },
): GeoJsonFeatureCollection {
  const maxPoints = options?.maxPoints ?? MAX_HISTORY_POINTS_ON_MAP;
  const valid = points.filter((p) => isValidCoordinate(p.latitude, p.longitude));
  const sampled = downsampleChronologicalPoints(valid, maxPoints);
  const latestId = options?.latestId ?? (sampled.length ? sampled[sampled.length - 1].id : null);

  const features: GeoJsonFeature[] = sampled.map((point) => ({
    type: 'Feature',
    id: `gps-${point.id}`,
    geometry: {
      type: 'Point',
      coordinates: [point.longitude, point.latitude] as LonLat,
    },
    properties: {
      kind: 'gps',
      pointId: point.id,
      createdAt: point.created_at,
      accuracy: point.accuracy ?? null,
      placeName: point.place_name ?? null,
      isLatest: point.id === latestId,
    },
  }));

  return { type: 'FeatureCollection', features };
}

/** Trace chronologique (LineString) des points GPS échantillonnés. */
export function historyTrailToGeoJSON(
  points: CartographyLocationPoint[],
  maxPoints: number = MAX_HISTORY_POINTS_ON_MAP,
): GeoJsonFeatureCollection {
  const valid = points.filter((p) => isValidCoordinate(p.latitude, p.longitude));
  const sampled = downsampleChronologicalPoints(valid, maxPoints);
  if (sampled.length < 2) return emptyCollection();
  return {
    type: 'FeatureCollection',
    features: [
      {
        type: 'Feature',
        id: 'gps-trail',
        geometry: {
          type: 'LineString',
          coordinates: sampled.map((p) => [p.longitude, p.latitude] as LonLat),
        },
        properties: { kind: 'trail', pointCount: sampled.length },
      },
    ],
  };
}

export function parseRoutePoints(
  raw: CartographyTrip['route_points'],
): LonLat[] {
  if (!raw) return [];
  let parsed: unknown = raw;
  if (typeof raw === 'string') {
    const trimmed = raw.trim();
    if (!trimmed) return [];
    try {
      parsed = JSON.parse(trimmed);
    } catch {
      return [];
    }
  }
  if (!Array.isArray(parsed)) return [];
  const coords: LonLat[] = [];
  for (const entry of parsed) {
    if (!Array.isArray(entry) || entry.length < 2) continue;
    const lat = Number(entry[0]);
    const lng = Number(entry[1]);
    if (!isValidCoordinate(lat, lng)) continue;
    // API stocke [lat, lng] — GeoJSON attend [lng, lat]
    coords.push([lng, lat]);
  }
  return coords;
}

/**
 * Construit une LineString pour un trajet.
 * Priorité : route_points ; sinon segment entre lieux from/to si fournis.
 */
export function tripLineCoordinates(
  trip: CartographyTrip,
  placesById: Map<number, CartographyPlace>,
  placesByName?: Map<string, CartographyPlace>,
): LonLat[] {
  const fromRoute = parseRoutePoints(trip.route_points);
  if (fromRoute.length >= 2) return fromRoute;

  const from =
    (trip.from_place_id != null ? placesById.get(trip.from_place_id) : undefined)
    ?? (trip.from_place && placesByName ? placesByName.get(trip.from_place) : undefined);
  const to =
    (trip.to_place_id != null ? placesById.get(trip.to_place_id) : undefined)
    ?? (trip.to_place && placesByName ? placesByName.get(trip.to_place) : undefined);

  if (
    from
    && to
    && isValidCoordinate(from.latitude, from.longitude)
    && isValidCoordinate(to.latitude, to.longitude)
  ) {
    return [
      [from.longitude, from.latitude],
      [to.longitude, to.latitude],
    ];
  }
  return [];
}

export function tripsToGeoJSON(
  trips: CartographyTrip[],
  places: CartographyPlace[],
): GeoJsonFeatureCollection {
  const placesById = new Map(places.map((p) => [p.id, p]));
  const placesByName = new Map(places.map((p) => [p.name, p]));
  const features: GeoJsonFeature[] = [];

  for (const trip of trips) {
    const coordinates = tripLineCoordinates(trip, placesById, placesByName);
    if (coordinates.length < 2) continue;
    const mode = normalizeTransportMode(trip.transport_mode);
    features.push({
      type: 'Feature',
      id: `trip-${trip.id}`,
      geometry: { type: 'LineString', coordinates },
      properties: {
        kind: 'trip',
        tripId: trip.id,
        transportMode: mode,
        color: colorForTransportMode(mode),
        distanceKm: trip.distance_km ?? null,
        durationMin: trip.duration_min ?? null,
        startedAt: trip.started_at ?? null,
        endedAt: trip.ended_at ?? null,
        fromPlace: trip.from_place ?? null,
        toPlace: trip.to_place ?? null,
      },
    });
  }

  return { type: 'FeatureCollection', features };
}

/** Filtre les points dont `created_at` tombe sur le jour local YYYY-MM-DD. */
export function filterPointsByLocalDate(
  points: CartographyLocationPoint[],
  dateKey: string | null,
): CartographyLocationPoint[] {
  if (!dateKey) return points;
  return points.filter((p) => localDateKey(p.created_at) === dateKey);
}

export function filterTripsByLocalDate(
  trips: CartographyTrip[],
  dateKey: string | null,
): CartographyTrip[] {
  if (!dateKey) return trips;
  return trips.filter((t) => {
    const start = t.started_at ? localDateKey(t.started_at) : null;
    return start === dateKey;
  });
}

export function localDateKey(iso: string): string | null {
  const trimmed = iso.trim();
  if (!trimmed) return null;
  const d = new Date(trimmed);
  if (Number.isNaN(d.getTime())) {
    const m = trimmed.match(/^(\d{4}-\d{2}-\d{2})/);
    return m ? m[1] : null;
  }
  const y = d.getFullYear();
  const mo = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${mo}-${day}`;
}

export function collectBounds(
  places: CartographyPlace[],
  points: CartographyLocationPoint[],
  tripsGeo: GeoJsonFeatureCollection,
): [[number, number], [number, number]] | null {
  const lngs: number[] = [];
  const lats: number[] = [];

  for (const p of places) {
    if (!isValidCoordinate(p.latitude, p.longitude)) continue;
    lngs.push(p.longitude);
    lats.push(p.latitude);
  }
  for (const p of points) {
    if (!isValidCoordinate(p.latitude, p.longitude)) continue;
    lngs.push(p.longitude);
    lats.push(p.latitude);
  }
  for (const f of tripsGeo.features) {
    if (f.geometry.type !== 'LineString') continue;
    for (const [lng, lat] of f.geometry.coordinates as LonLat[]) {
      lngs.push(lng);
      lats.push(lat);
    }
  }

  if (lngs.length === 0 || lats.length === 0) return null;
  return [
    [Math.min(...lngs), Math.min(...lats)],
    [Math.max(...lngs), Math.max(...lats)],
  ];
}
