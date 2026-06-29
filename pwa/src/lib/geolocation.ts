/**
 * Service de geolocalisation continue pour la PWA.
 *
 * Strategie :
 *  - watchPosition au mount du layout client
 *  - throttle 5 min entre deux envois
 *  - distance minimum 30m pour ignorer le bruit GPS
 *  - source "pwa" pour distinguer des autres sources (raccourci iOS, app native)
 *
 * Contrainte iOS : Safari ne supporte PAS le background geolocation.
 * Le tracking ne fonctionne que tant que le PWA est au premier plan.
 * Un envoi immediat a chaque ouverture compense partiellement.
 *
 * Tous les appels passent par le proxy Next.js (/api/* -> backend FastAPI).
 */

const SEND_INTERVAL_MS = 5 * 60 * 1000; // 5 minutes entre deux envois
const FORCED_SEND_INTERVAL_MS = 2 * SEND_INTERVAL_MS; // envoi force au bout de 10 min
const MIN_DISTANCE_METERS = 30; // ignorer si deplacement < 30 m
const HIGH_ACCURACY = true;
const MAX_AGE_MS = 2 * 60 * 1000;
const TIMEOUT_MS = 15_000;

interface TrackingState {
  watchId: number | null;
  lastSentTimestamp: number;
  lastLat: number | null;
  lastLng: number | null;
  lastSentSuccess: number | null;
  lastError: string | null;
  failedAttempts: number;
}

const state: TrackingState = {
  watchId: null,
  lastSentTimestamp: 0,
  lastLat: null,
  lastLng: null,
  lastSentSuccess: null,
  lastError: null,
  failedAttempts: 0,
};

/** Distance Haversine en metres entre deux paires (lat, lng). */
function haversineDistance(lat1: number, lng1: number, lat2: number, lng2: number): number {
  const R = 6_371_000; // rayon Terre en metres
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLng = toRad(lng2 - lng1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLng / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

/**
 * POST /api/location avec garde-fous (throttle, distance, succes ou retry court).
 *
 * Retourne true si un envoi reseau a eu lieu (succes ou echec), false si
 * l'envoi a ete saute (throttle / distance / pas de mouvement).
 */
async function sendPosition(
  position: GeolocationPosition,
  options: { force?: boolean } = {}
): Promise<boolean> {
  const now = Date.now();
  const { latitude, longitude, altitude, accuracy, speed, heading } = position.coords;

  if (!options.force) {
    // Throttle dur : jamais plus d'un envoi par SEND_INTERVAL_MS, sauf force
    if (state.lastSentTimestamp && now - state.lastSentTimestamp < SEND_INTERVAL_MS) {
      // Cas particulier : si on a deja envoye recemment mais qu'on est bouge significativement,
      // on autorise quand meme un envoi (utile pour transitions rapides entre lieux).
      if (state.lastLat == null || state.lastLng == null) return false;
      const dist = haversineDistance(state.lastLat, state.lastLng, latitude, longitude);
      if (dist < MIN_DISTANCE_METERS) return false;
      // Mouvement significatif : on laisse passer
    }

    // Si vraiment pas bouge, on saute meme apres l'intervalle (sauf au bout de FORCED_SEND_INTERVAL_MS)
    if (state.lastLat != null && state.lastLng != null) {
      const dist = haversineDistance(state.lastLat, state.lastLng, latitude, longitude);
      const tooSoonForForcedSend = now - state.lastSentTimestamp < FORCED_SEND_INTERVAL_MS;
      if (dist < MIN_DISTANCE_METERS && tooSoonForForcedSend) return false;
    }
  }

  const body = {
    latitude,
    longitude,
    altitude: altitude ?? undefined,
    accuracy: accuracy ?? undefined,
    speed: speed ?? undefined,
    heading: heading ?? undefined,
    source: 'pwa',
    timestamp: new Date(position.timestamp).toISOString(),
  };

  state.lastSentTimestamp = now;

  try {
    const res = await fetch('/api/location', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      state.lastError = `HTTP ${res.status}`;
      state.failedAttempts += 1;
      console.warn('[geo] POST /api/location returned', res.status);
      return true;
    }
    state.lastLat = latitude;
    state.lastLng = longitude;
    state.lastSentSuccess = now;
    state.lastError = null;
    state.failedAttempts = 0;
    return true;
  } catch (err) {
    state.lastError = err instanceof Error ? err.message : String(err);
    state.failedAttempts += 1;
    console.warn('[geo] POST /api/location failed:', err);
    return true;
  }
}

function onError(err: GeolocationPositionError): void {
  state.lastError = `code ${err.code}: ${err.message}`;
  state.failedAttempts += 1;
  // Code 1 = PERMISSION_DENIED, 2 = POSITION_UNAVAILABLE, 3 = TIMEOUT
  if (err.code === 1) {
    console.warn('[geo] permission denied, stopping tracking');
    stopTracking();
  }
}

/** Demarre le tracking. Retourne true si l'API est disponible. */
export function startTracking(): boolean {
  if (typeof window === 'undefined' || !('geolocation' in navigator)) {
    console.warn('[geo] Geolocation API indisponible');
    return false;
  }

  if (state.watchId !== null) {
    return true; // deja actif
  }

  // 1) Envoi immediat
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      void sendPosition(pos, { force: true });
    },
    onError,
    {
      enableHighAccuracy: HIGH_ACCURACY,
      timeout: TIMEOUT_MS,
      maximumAge: MAX_AGE_MS,
    }
  );

  // 2) Surveillance continue
  state.watchId = navigator.geolocation.watchPosition(
    (pos) => {
      void sendPosition(pos);
    },
    onError,
    {
      enableHighAccuracy: HIGH_ACCURACY,
      timeout: TIMEOUT_MS,
      maximumAge: MAX_AGE_MS,
    }
  );

  console.log('[geo] tracking demarre, watchId:', state.watchId);
  return true;
}

/** Arrete le tracking et clear le watcher. */
export function stopTracking(): void {
  if (state.watchId !== null && typeof navigator !== 'undefined') {
    navigator.geolocation.clearWatch(state.watchId);
    state.watchId = null;
    console.log('[geo] tracking arrete');
  }
}

/**
 * Verifie si le contexte navigateur est securise (HTTPS ou localhost).
 *
 * Safari iOS bloque l'API Geolocation sur les pages HTTP non-localhost.
 * Sans contexte securise, getCurrentPosition/watchPosition echouent
 * silencieusement sans jamais afficher le prompt de permission.
 *
 * Retourne true si la geolocation peut fonctionner (HTTPS ou localhost/127.0.0.1).
 */
export function isSecureContextForGeo(): boolean {
  if (typeof window === 'undefined') return false;
  // window.isSecureContext === true si HTTPS ou localhost (W3C spec)
  return window.isSecureContext === true;
}

/** True si watchPosition est actif. */
export function isTracking(): boolean {
  return state.watchId !== null;
}

/** Recupere l'etat de la permission geolocation (granted / denied / prompt). */
export async function checkPermission(): Promise<PermissionState | 'unknown'> {
  if (typeof navigator === 'undefined' || !('permissions' in navigator)) {
    return 'unknown';
  }
  try {
    const result = await navigator.permissions.query({ name: 'geolocation' as PermissionName });
    return result.state;
  } catch {
    return 'unknown';
  }
}

/** Demande explicitement la permission via un appel getCurrentPosition. */
export async function requestPermission(): Promise<boolean> {
  if (typeof navigator === 'undefined' || !('geolocation' in navigator)) return false;
  return new Promise((resolve) => {
    navigator.geolocation.getCurrentPosition(
      () => resolve(true),
      () => resolve(false),
      { timeout: TIMEOUT_MS }
    );
  });
}

/** Force l'envoi immediat d'une position (bouton refresh). */
export async function sendCurrentPosition(): Promise<{
  latitude: number;
  longitude: number;
} | null> {
  if (typeof navigator === 'undefined' || !('geolocation' in navigator)) return null;
  return new Promise((resolve) => {
    navigator.geolocation.getCurrentPosition(
      async (pos) => {
        await sendPosition(pos, { force: true });
        resolve({ latitude: pos.coords.latitude, longitude: pos.coords.longitude });
      },
      () => resolve(null),
      { enableHighAccuracy: true, timeout: TIMEOUT_MS, maximumAge: 0 }
    );
  });
}

/** Etat instantane du tracking — pour affichage UI debug ou stats. */
export interface TrackingInfo {
  active: boolean;
  lastSentAt: number | null;
  lastError: string | null;
  failedAttempts: number;
  intervalMs: number;
  minDistanceMeters: number;
}

export function getTrackingInfo(): TrackingInfo {
  return {
    active: state.watchId !== null,
    lastSentAt: state.lastSentSuccess,
    lastError: state.lastError,
    failedAttempts: state.failedAttempts,
    intervalMs: SEND_INTERVAL_MS,
    minDistanceMeters: MIN_DISTANCE_METERS,
  };
}
