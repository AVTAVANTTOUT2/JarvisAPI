/* Coque, routage et cycle de vie.
 *
 * Routage par fragment (#/chat) : aucune route serveur supplémentaire, le
 * geste retour d'iOS fonctionne, et un rechargement dur ne peut pas produire
 * de 404 puisque le serveur ne voit jamais le fragment.
 */

import { api, onAuthLost, setCsrfToken } from './api.js';
import { requireSession, lock, watchIdle } from './auth.js';
import * as ws from './ws.js';
import { h, icon, banner } from './ui.js';

import chat from './views/chat.js';
import voice from './views/voice.js';
import today from './views/today.js';
import tasks from './views/tasks.js';
import mails from './views/mails.js';
import health from './views/health.js?v=20260803';

const ROUTES = [
  { id: 'chat',       label: 'Chat',        icon: 'chat',  view: chat },
  { id: 'voix',       label: 'Voix',        icon: 'mic',   view: voice },
  { id: 'aujourdhui', label: "Aujourd'hui", icon: 'home',  view: today },
  { id: 'taches',     label: 'Tâches',      icon: 'task',  view: tasks },
  { id: 'mails',      label: 'Mails',       icon: 'mail',  view: mails },
  { id: 'sante',      label: 'Fitness',      icon: 'pulse', view: health },
];
const DEFAULT_ROUTE = 'chat';

const el = {
  app:    document.getElementById('app'),
  topbar: document.getElementById('topbar'),
  banner: document.getElementById('banner'),
  view:   document.getElementById('view'),
  dock:   document.getElementById('dock'),
  tabs:   document.getElementById('tabs'),
};

let current = null;      // { id, teardown }
let stopIdle = () => {};
let offline = false;
let relockInFlight = null;

/* Fail-closed. `hidden` ne fait que masquer : sans ce drapeau, le
 * `hashchange` déclenché par la redirection initiale monterait une vue —
 * et donc ouvrirait le WebSocket et interrogerait l'API — avant même que
 * la session ne soit confirmée. Aucune vue ne se monte tant qu'il est faux. */
let sessionOpen = false;

// ── Coque ────────────────────────────────────────────────────────────

/** Contexte remis à chaque vue : elle ne touche jamais au DOM de la coque. */
function makeContext(route) {
  return {
    route: route.id,
    /** Remplit l'en-tête. `actions` : [{icon, label, onClick}] */
    setHeader(title, subtitle, actions = []) {
      const heading = h('h1', { class: subtitle ? 'sm' : '' }, title);
      if (subtitle) heading.append(h('span', { class: 'sub', text: subtitle }));
      el.topbar.replaceChildren(
        heading,
        ...actions.map((a) => h('button', {
          class: 'iconbtn', type: 'button', 'aria-label': a.label, onClick: a.onClick,
        }, icon(a.icon))),
      );
    },
    /** Contenu de l'écran. */
    setBody(...nodes) { el.view.replaceChildren(...nodes.flat().filter(Boolean)); },
    /** Barre ancrée au-dessus des onglets (composer, création rapide). */
    setDock(node) {
      el.dock.replaceChildren();
      el.dock.className = node ? 'dock' : '';
      if (node) el.dock.append(node);
    },
    navigate,
  };
}

function renderTabs(activeId) {
  el.tabs.replaceChildren(...ROUTES.map((r) => {
    const a = h('a', {
      class: 'tab' + (r.reserved ? ' reserved' : ''),
      href: `#/${r.id}`,
    }, icon(r.icon), h('span', { text: r.label }));
    if (r.id === activeId) a.setAttribute('aria-current', 'page');
    return a;
  }));
}

/** Bandeau réseau. Pousse le contenu vers le bas, ne le recouvre jamais. */
function setOffline(isOffline, message) {
  offline = isOffline;
  if (!isOffline) { el.banner.replaceChildren(); return; }
  el.banner.replaceChildren(banner(message || 'Serveur injoignable.'));
}
export { setOffline };

// ── Routage ──────────────────────────────────────────────────────────

function routeFromHash() {
  const id = (location.hash || '').replace(/^#\/?/, '').split(/[?/]/)[0];
  return ROUTES.find((r) => r.id === id) || null;
}

function navigate(id) {
  if (location.hash === `#/${id}`) render();
  else location.hash = `#/${id}`;
}

async function render() {
  if (!sessionOpen) return;
  const route = routeFromHash();
  if (!route) { location.replace(`#/${DEFAULT_ROUTE}`); return; }
  if (current && current.id === route.id) return;

  if (current && current.teardown) {
    try { current.teardown(); } catch (err) { console.error('[view] teardown', err); }
  }

  const ctx = makeContext(route);
  el.dock.replaceChildren();
  el.dock.className = '';
  el.view.replaceChildren();
  renderTabs(route.id);
  el.view.scrollTop = 0;

  let teardown = null;
  try {
    teardown = await route.view.mount(ctx);
  } catch (err) {
    console.error('[view] mount', route.id, err);
    ctx.setBody(h('div', { class: 'empty' },
      h('p', { text: 'Écran indisponible.' }),
      h('span', { text: "L'application a rencontré une erreur inattendue." })));
  }
  current = { id: route.id, teardown };
}

// ── Session ──────────────────────────────────────────────────────────

function relock(reason) {
  // Une expiration HTTP et la fermeture 4401 du WebSocket peuvent arriver au
  // même instant. Un seul verrou doit posséder la promesse de déverrouillage :
  // le second écraserait sinon son résolveur et figerait l'application.
  if (relockInFlight) return relockInFlight;

  relockInFlight = (async () => {
    sessionOpen = false;
    stopIdle();
    ws.disconnect();
    if (current && current.teardown) { try { current.teardown(); } catch { /* ignoré */ } }
    current = null;
    // Rien de la session précédente ne doit rester lisible derrière le verrou.
    el.topbar.replaceChildren();
    el.view.replaceChildren();
    el.dock.replaceChildren();
    el.tabs.replaceChildren();
    setOffline(false);
    setCsrfToken(null);

    await lock(reason);
    await start();
  })().finally(() => { relockInFlight = null; });

  return relockInFlight;
}

async function start() {
  sessionOpen = true;
  el.app.hidden = false;

  const status = await api.authStatus().catch(() => null);
  if (status && status.csrf_token) setCsrfToken(status.csrf_token);
  stopIdle = watchIdle(status && status.auto_lock_minutes, () => { void relock('idle'); });

  ws.onState((state) => {
    if (state === 'open') setOffline(false);
    else if (state === 'lost') setOffline(true, 'Serveur injoignable. Reconnexion en cours.');
    else if (state === 'auth') void relock('expired');
  });
  ws.connect();

  await render();
}

async function boot() {
  onAuthLost((reason) => { void relock(reason); });
  window.addEventListener('hashchange', () => { void render(); });
  window.addEventListener('online', () => { setOffline(false); ws.connect(); });
  window.addEventListener('offline', () => setOffline(true, 'Hors ligne.'));

  if (!location.hash) location.replace(`#/${DEFAULT_ROUTE}`);

  await requireSession();   // ne rend la main qu'une fois la session ouverte
  await start();
}

void boot();
