/** Types partages pour la page /map — mapping direct des retours API backend. */

// ── GET /api/location/history ──────────────────────────────
export interface LocationPoint {
  id: number;
  latitude: number;
  longitude: number;
  altitude: number | null;
  accuracy: number | null;
  speed: number | null;
  heading: number | null;
  source: string | null;
  place_id: number | null;
  place_name: string | null;
  created_at: string; // ISO 8601
}

export interface LocationHistoryResponse {
  points: LocationPoint[];
}

// ── GET /api/places ────────────────────────────────────────
export interface Place {
  id: number;
  name: string;
  category: string | null;
  latitude: number;
  longitude: number;
  radius_meters: number;
  address: string | null;
  notes: string | null;
  visit_count: number;
  avg_duration_min: number | null;
  last_visit: string | null;
}

export interface PlacesResponse {
  places: Place[];
}

// ── GET /api/visits ────────────────────────────────────────
export interface Visit {
  id: number;
  place_id: number;
  place_name: string;
  arrived_at: string;
  departed_at: string | null;
  duration_minutes: number | null;
  day_of_week: number | null;
}

export interface VisitsResponse {
  visits: Visit[];
}

// ── GET /api/trips ─────────────────────────────────────────
export interface Trip {
  id: number;
  from_place_id: number | null;
  to_place_id: number | null;
  started_at: string;
  ended_at: string;
  duration_min: number;
  distance_km: number | null;
  transport_mode: string | null;
  route_points: string | null; // JSON array of [lat, lng]
}

export interface TripsResponse {
  trips: Trip[];
}

// ── GET /api/location/patterns ─────────────────────────────
export interface LocationPattern {
  id: number;
  pattern_type: string;
  description: string;
  place_id: number | null;
  occurrences: number;
  first_seen: string;
  last_seen: string;
  status: string;
}

export interface PatternsResponse {
  patterns: LocationPattern[];
}

// ── Direction de la timeline ──────────────────────────────
export type TimelineDirection = 'future' | 'past';

// ── Marqueur de lieu sur la carte ─────────────────────────
export interface PlaceMarker extends Place {
  /** Isochron du dernier passage (visite ou point de passage) */
  lastSeenIso: string | null;
}
