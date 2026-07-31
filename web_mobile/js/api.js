/* Accès HTTP au backend JARVIS.
 *
 * Même origine que FastAPI : le cookie de session `jarvis_session`
 * (SameSite=Strict) part tout seul, à condition de le demander
 * explicitement avec credentials:'include'.
 *
 * Jeton synchronisé (CSRF) — api/middleware.py refuse en 403 toute méthode
 * POST/PATCH/DELETE portée par un cookie de session sans en-tête
 * `X-CSRF-Token` valide. Le jeton est fourni par GET /api/auth/status et
 * rafraîchi automatiquement ici : aucun appelant n'a à s'en occuper.
 *
 * Deux codes changent l'état de l'application plutôt que de signaler une
 * panne — 428 (verrou non configuré) et 401 (session absente ou expirée)
 * ramènent au verrou. Les routes /api/auth/* en sont exclues : pour elles,
 * un 401 est la réponse normale à un mauvais code.
 */

const listeners = new Set();
let csrfToken = null;

/** Prévient l'application qu'il faut repasser par le verrou. */
export function onAuthLost(fn) { listeners.add(fn); return () => listeners.delete(fn); }
function authLost(reason) { for (const fn of listeners) { try { fn(reason); } catch { /* isolé */ } } }

export class ApiError extends Error {
  constructor(status, message) { super(message); this.name = 'ApiError'; this.status = status; }
}

const UNSAFE = new Set(['POST', 'PATCH', 'PUT', 'DELETE']);

/** Récupère le jeton synchronisé courant. Silencieux si le serveur est muet. */
async function refreshCsrf() {
  try {
    const res = await fetch('/api/auth/status', {
      credentials: 'include',
      headers: { 'Accept': 'application/json' },
    });
    if (!res.ok) return null;
    const st = await res.json();
    csrfToken = st.csrf_token || null;
    return csrfToken;
  } catch {
    return null;
  }
}

export function setCsrfToken(token) { csrfToken = token || null; }

async function send(method, path, body) {
  const init = {
    method,
    credentials: 'include',
    headers: { 'Accept': 'application/json' },
  };
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  if (UNSAFE.has(method) && csrfToken) init.headers['X-CSRF-Token'] = csrfToken;
  return fetch(path, init);
}

async function request(method, path, body, { retried = false } = {}) {
  const isAuthRoute = path.startsWith('/api/auth/');

  // Une écriture sans jeton en main est vouée au 403 : on le cherche d'abord.
  if (UNSAFE.has(method) && !csrfToken && !isAuthRoute) await refreshCsrf();

  let res;
  try {
    res = await send(method, path, body);
  } catch {
    // Le serveur vit à la maison, le téléphone est souvent ailleurs.
    // La coupure est un cas courant, pas une anomalie.
    throw new ApiError(0, 'Serveur injoignable');
  }

  // Jeton périmé (session renouvelée ailleurs) : on le renouvelle, une fois.
  if (res.status === 403 && UNSAFE.has(method) && !retried && !isAuthRoute) {
    const fresh = await refreshCsrf();
    if (fresh) return request(method, path, body, { retried: true });
  }

  if (!isAuthRoute && (res.status === 401 || res.status === 428)) {
    csrfToken = null;
    authLost(res.status === 428 ? 'unconfigured' : 'expired');
    throw new ApiError(res.status, 'Session fermée');
  }

  if (!res.ok) {
    let detail = `Erreur ${res.status}`;
    try {
      const payload = await res.json();
      if (payload && (payload.detail || payload.error)) detail = String(payload.detail || payload.error);
    } catch { /* corps non-JSON : on garde le code */ }
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) return null;
  const data = await res.json();
  // Toute réponse d'authentification peut embarquer un jeton neuf.
  if (data && typeof data === 'object' && data.csrf_token) csrfToken = data.csrf_token;
  return data;
}

export const api = {
  get:   (p)    => request('GET', p),
  post:  (p, b) => request('POST', p, b === undefined ? {} : b),
  patch: (p, b) => request('PATCH', p, b),
  del:   (p)    => request('DELETE', p),

  // ── Verrou ──
  authStatus: ()  => request('GET', '/api/auth/status'),
  unlock:     (s) => request('POST', '/api/auth/unlock', { secret: s }),
  setup:      (s) => request('POST', '/api/auth/setup', { secret: s }),
  logout:     ()  => request('POST', '/api/auth/logout', {}),

  // ── Données ──
  notifications: ()      => request('GET', '/api/notifications'),
  markRead:      (id)    => request('POST', `/api/notifications/${id}/read`, {}),
  calendar:      (start, end) => request(
    'GET',
    `/api/calendar?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`,
  ),
  tasks:         (st)    => request('GET', st ? `/api/tasks?status=${encodeURIComponent(st)}` : '/api/tasks'),
  createTask:    (t)     => request('POST', '/api/tasks', t),
  updateTask:    (id, p) => request('PATCH', `/api/tasks/${id}`, p),
  deleteTask:    (id)    => request('DELETE', `/api/tasks/${id}`),
  emails:        (n = 20) => request('GET', `/api/emails?limit=${n}`),
  conversations: (n = 50) => request('GET', `/api/conversations?limit=${n}`),
  conversation:  (id)     => request('GET', `/api/conversations/${id}`),

  // ── Fitness ──
  fitnessSummaryToday: () => request('GET', '/api/fitness/summary/today'),
  createWorkout:       (b) => request('POST', '/api/fitness/workouts', b),
  createMeal:          (b) => request('POST', '/api/fitness/meals', b),
  addWater:            (b) => request('POST', '/api/fitness/water', b),
  createWellbeing:     (b) => request('POST', '/api/fitness/wellbeing', b),

  /** Appel LLM : plusieurs secondes et un coût réel. Jamais au montage d'un écran. */
  briefing: (kind = 'morning') => request('GET', `/api/briefing?kind=${encodeURIComponent(kind)}`),
};
