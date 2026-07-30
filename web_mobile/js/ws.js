/* Canal temps réel avec JARVIS.
 *
 * Le chat et la voix passent par /ws et non par /api/mobile/*, dont les
 * points d'entrée exigent un Bearer réservé au Companion Android natif —
 * inutilisable depuis un navigateur. /ws accepte le cookie de session
 * (api/ws_session.py : resolve_websocket_auth), envoyé automatiquement
 * puisque nous sommes sur la même origine.
 *
 * Fermetures signifiantes émises par le serveur :
 *   4428 — aucun secret configuré
 *   4401 — session absente ou expirée
 * Toute autre fermeture est traitée comme un incident réseau : on reconnecte
 * avec un délai croissant plutôt que de marteler le serveur.
 */

const handlers = new Map();
let socket = null;
let attempt = 0;
let timer = null;
let closedByUs = false;
let stateFn = () => {};

function emit(type, payload) {
  const set = handlers.get(type);
  if (!set) return;
  for (const fn of set) {
    try { fn(payload); } catch (err) { console.error('[ws] handler', type, err); }
  }
}

/** Abonne à un type de message serveur. Retourne la fonction de désabonnement. */
export function on(type, fn) {
  if (!handlers.has(type)) handlers.set(type, new Set());
  handlers.get(type).add(fn);
  return () => handlers.get(type).delete(fn);
}

/** Notifie l'application des changements de connexion : 'open' | 'lost' | 'auth'. */
export function onState(fn) { stateFn = fn; }

export function isOpen() { return socket && socket.readyState === WebSocket.OPEN; }

function url() {
  const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${scheme}//${location.host}/ws`;
}

export function connect() {
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) return;
  closedByUs = false;

  let ws;
  try {
    ws = new WebSocket(url());
  } catch {
    scheduleReconnect();
    return;
  }
  socket = ws;
  ws.binaryType = 'arraybuffer';

  ws.addEventListener('open', () => {
    attempt = 0;
    stateFn('open');
  });

  ws.addEventListener('message', (event) => {
    if (typeof event.data !== 'string') { emit('binary', event.data); return; }
    let msg;
    try { msg = JSON.parse(event.data); } catch { return; }
    if (msg && msg.type) emit(msg.type, msg);
  });

  ws.addEventListener('close', (event) => {
    socket = null;
    if (closedByUs) return;
    // 4428 / 4401 : ce n'est pas le réseau, c'est la session. Reconnecter
    // en boucle ne servirait qu'à consommer de la batterie.
    if (event.code === 4401 || event.code === 4428) { stateFn('auth'); return; }
    stateFn('lost');
    scheduleReconnect();
  });

  ws.addEventListener('error', () => { /* 'close' suit toujours : traité là. */ });
}

function scheduleReconnect() {
  clearTimeout(timer);
  attempt += 1;
  const delay = Math.min(30000, 800 * 2 ** Math.min(attempt, 5));
  timer = setTimeout(connect, delay);
}

export function disconnect() {
  closedByUs = true;
  clearTimeout(timer);
  if (socket) { try { socket.close(); } catch { /* déjà fermée */ } }
  socket = null;
}

/** Envoie un objet JSON. Retourne false si le canal n'est pas ouvert. */
export function send(payload) {
  if (!isOpen()) return false;
  socket.send(JSON.stringify(payload));
  return true;
}

/** Envoie un blob audio brut (mode vocal). */
export function sendBinary(blob) {
  if (!isOpen()) return false;
  socket.send(blob);
  return true;
}

export const sendText = (content, opts = {}) =>
  send({ type: 'text', content, stream: opts.stream !== false, tts: !!opts.tts });

export const confirmAction = (action) => send({ type: 'action_confirm', action });
export const newConversation = () => send({ type: 'new_conversation' });
export const switchConversation = (id) => send({ type: 'switch_conversation', conversation_id: id });
export const donePlaying = () => send({ type: 'done_playing' });
