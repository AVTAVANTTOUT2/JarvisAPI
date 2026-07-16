export interface LocationPoint {
  id: number;
  latitude: number;
  longitude: number;
  accuracy?: number | null;
  source?: string | null;
  place_id?: number | null;
  place_name?: string | null;
  created_at: string;
}

export type LocationFreshness = 'empty' | 'recent' | 'stale';

export interface LocationDisplayStatus {
  freshness: LocationFreshness;
  label: string;
}

const RECENT_LOCATION_MS = 5 * 60_000;

function elapsedLabel(elapsedMs: number): string {
  const seconds = Math.max(0, Math.floor(elapsedMs / 1_000));
  if (seconds < 60) return `${seconds} seconde${seconds > 1 ? 's' : ''}`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} minute${minutes > 1 ? 's' : ''}`;
  const hours = Math.floor(minutes / 60);
  return `${hours} heure${hours > 1 ? 's' : ''}`;
}

export function getLocationDisplayStatus(
  latestPoint: LocationPoint | undefined,
  nowMs: number = Date.now(),
): LocationDisplayStatus {
  if (!latestPoint) {
    return { freshness: 'empty', label: 'Aucune position reçue depuis le téléphone' };
  }
  const capturedAt = Date.parse(latestPoint.created_at);
  if (!Number.isFinite(capturedAt)) {
    return { freshness: 'stale', label: 'Dernière position reçue — heure inconnue' };
  }
  const elapsed = Math.max(0, nowMs - capturedAt);
  const age = elapsedLabel(elapsed);
  if (elapsed <= RECENT_LOCATION_MS) {
    return { freshness: 'recent', label: `Position reçue il y a ${age}` };
  }
  return { freshness: 'stale', label: `Dernière position trop ancienne — il y a ${age}` };
}

export function mapLocationHistory(payload: unknown): LocationPoint[] {
  if (!payload || typeof payload !== 'object') return [];
  const points = (payload as { points?: unknown }).points;
  if (!Array.isArray(points)) return [];
  return points.flatMap((raw) => {
    if (!raw || typeof raw !== 'object') return [];
    const point = raw as Record<string, unknown>;
    if (point.latitude == null || point.longitude == null) return [];
    const latitude = Number(point.latitude);
    const longitude = Number(point.longitude);
    const createdAt = typeof point.created_at === 'string' ? point.created_at : '';
    if (!Number.isFinite(latitude) || !Number.isFinite(longitude) || !createdAt) return [];
    return [{
      id: Number(point.id),
      latitude,
      longitude,
      accuracy: point.accuracy == null ? null : Number(point.accuracy),
      source: typeof point.source === 'string' ? point.source : null,
      place_id: point.place_id == null ? null : Number(point.place_id),
      place_name: typeof point.place_name === 'string' ? point.place_name : null,
      created_at: createdAt,
    }];
  });
}
