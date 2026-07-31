/* Écran de verrouillage.
 *
 * Fail-closed : `mount()` ne résout que lorsque la session est confirmée.
 * Rien de l'application n'est construit avant, pas même en arrière-plan.
 *
 * Le pavé numérique est dessiné plutôt que délégué à un <input>. Le clavier
 * iOS recouvrirait la moitié de l'écran, imposerait 16 px sous peine de zoom,
 * et offrirait des touches deux fois trop petites. Des cibles de 78 px dans
 * le tiers bas règlent les trois d'un coup.
 *
 * Longueur variable : le bureau accepte un PIN à 4 chiffres (ou plus). Le
 * déverrouillage mobile soumet via « OK » dès MIN_PIN chiffres ; la création
 * exige aussi MIN_PIN, sans imposer une longueur fixe de 6.
 */

import { api, ApiError } from './api.js';

const MIN_PIN = 4;
const MAX_PIN = 12;

const el = {
  root: document.getElementById('lock'),
  msg:  document.getElementById('lock-msg'),
  dots: document.getElementById('lock-dots'),
  keys: document.getElementById('lock-keys'),
};

let code = '';
let mode = 'unlock';   // 'unlock' | 'setup' | 'confirm'
let firstEntry = '';   // première saisie en mode création
let busy = false;
let countdown = null;
let resolveUnlocked = null;

function dotsCount() {
  return Math.min(MAX_PIN, Math.max(MIN_PIN, code.length || MIN_PIN));
}

function renderDots(error = false) {
  el.dots.className = 'lock-dots' + (error ? ' err' : '');
  el.dots.replaceChildren(...Array.from({ length: dotsCount() }, (_, i) => {
    const d = document.createElement('span');
    d.className = 'lock-dot' + (i < code.length ? ' set' : '');
    return d;
  }));
  syncOkButton();
}

function say(text, error = false) {
  el.msg.textContent = text;
  el.msg.className = 'lock-msg' + (error ? ' err' : '');
}

function canSubmit() {
  return code.length >= MIN_PIN && code.length <= MAX_PIN;
}

function syncOkButton() {
  const ok = el.keys.querySelector('[data-action="ok"]');
  if (ok) ok.disabled = busy || !canSubmit();
}

function buildKeys() {
  const layout = ['1','2','3','4','5','6','7','8','9','OK','0','⌫'];
  el.keys.replaceChildren(...layout.map((label) => {
    const b = document.createElement('button');
    b.type = 'button';
    if (label === 'OK') {
      b.className = 'lock-key bare';
      b.textContent = 'OK';
      b.dataset.action = 'ok';
      b.setAttribute('aria-label', 'Valider le code');
      b.addEventListener('click', () => {
        if (busy || !canSubmit()) return;
        submit();
      });
      return b;
    }
    if (label === '⌫') {
      b.className = 'lock-key bare'; b.textContent = 'Effacer';
      b.setAttribute('aria-label', 'Effacer le dernier chiffre');
      b.addEventListener('click', () => { if (busy) return; code = code.slice(0, -1); renderDots(); });
      return b;
    }
    b.className = 'lock-key';
    b.textContent = label;
    b.addEventListener('click', () => press(label));
    return b;
  }));
  syncOkButton();
}

function setKeysDisabled(disabled) {
  for (const b of el.keys.querySelectorAll('.lock-key')) {
    if (b.dataset.action === 'ok') {
      b.disabled = disabled || !canSubmit();
      continue;
    }
    if (!b.classList.contains('bare') || b.textContent === 'Effacer') b.disabled = disabled;
  }
}

function press(digit) {
  if (busy || code.length >= MAX_PIN) return;
  code += digit;
  renderDots();
  // Auto-soumission uniquement au plafond : en dessous, « OK » laisse
  // valider un PIN à 4 chiffres comme sur le bureau.
  if (code.length === MAX_PIN) submit();
}

function buzz() {
  // Retour haptique quand la plateforme le propose. iOS ne l'expose pas :
  // l'absence de vibration ne doit donc jamais être le seul signal d'erreur.
  if (navigator.vibrate) { try { navigator.vibrate(60); } catch { /* ignoré */ } }
}

async function submit() {
  if (!canSubmit()) return;
  busy = true;
  setKeysDisabled(true);

  const entered = code;
  code = '';

  try {
    if (mode === 'setup') {
      firstEntry = entered;
      mode = 'confirm';
      say('Confirmez le code.');
      renderDots();
      return;
    }

    if (mode === 'confirm') {
      if (entered !== firstEntry) {
        mode = 'setup';
        firstEntry = '';
        renderDots(true);
        buzz();
        say('Les deux saisies diffèrent. Recommencez.', true);
        return;
      }
      await api.setup(entered);
      unlocked();
      return;
    }

    await api.unlock(entered);
    unlocked();
  } catch (err) {
    renderDots(true);
    buzz();
    if (err instanceof ApiError && err.status === 0) {
      say('Serveur injoignable. Vérifiez le réseau.', true);
    } else {
      await refreshLockoutMessage();
    }
  } finally {
    busy = false;
    setKeysDisabled(false);
    if (mode !== 'confirm') renderDots(el.dots.classList.contains('err'));
  }
}

/* Après un échec, seul le serveur sait où en est la limitation de débit.
 * /api/auth/status n'expose pas de compteur de tentatives — on n'en invente
 * donc pas : soit le verrouillage est en cours et on affiche le décompte
 * exact, soit on se contente de constater l'échec. */
async function refreshLockoutMessage() {
  let st = null;
  try { st = await api.authStatus(); } catch { /* statut indisponible */ }

  if (st && st.locked_out) { startCountdown(st.lockout_seconds || 0); return; }
  say('Code incorrect.', true);
}

function startCountdown(seconds) {
  stopCountdown();
  setKeysDisabled(true);
  let left = Math.max(0, Math.ceil(seconds));

  const tick = () => {
    if (left <= 0) {
      stopCountdown();
      setKeysDisabled(false);
      say('Réessayez.');
      return;
    }
    const m = String(Math.floor(left / 60)).padStart(2, '0');
    const s = String(left % 60).padStart(2, '0');
    say(`Verrouillé. Réessayez dans ${m}:${s}.`, true);
    left -= 1;
  };
  tick();
  countdown = setInterval(tick, 1000);
}

function stopCountdown() {
  if (countdown) { clearInterval(countdown); countdown = null; }
}

function unlocked() {
  stopCountdown();
  code = '';
  firstEntry = '';
  // Après la création initiale, le prochain verrou doit vérifier le code au
  // lieu de rester dans l'étape de confirmation de la première saisie.
  mode = 'unlock';
  el.root.hidden = true;
  const done = resolveUnlocked;
  resolveUnlocked = null;
  if (done) done();
}

/** Affiche le verrou et ne rend la main qu'une fois la session ouverte. */
export function lock(reason) {
  document.getElementById('app').hidden = true;
  el.root.hidden = false;
  code = '';
  firstEntry = '';
  mode = reason === 'unconfigured' ? 'setup' : 'unlock';
  renderDots();

  if (reason === 'unconfigured') say('Aucun code défini. Choisissez-en un (4 chiffres min.), puis OK.');
  else if (reason === 'expired') say('Session expirée. Entrez votre code, puis OK.');
  else if (reason === 'idle') say('Verrouillé par inactivité. Entrez votre code, puis OK.');
  else say('Entrez votre code, puis OK.');

  return new Promise((resolve) => { resolveUnlocked = resolve; });
}

/**
 * Point d'entrée. Résout quand la session est confirmée — jamais avant.
 * @returns {Promise<void>}
 */
export async function requireSession() {
  buildKeys();
  renderDots();

  let st;
  while (!st) {
    try {
      st = await api.authStatus();
    } catch {
      el.root.hidden = false;
      say('Serveur injoignable. Vérifiez le réseau.', true);
      setKeysDisabled(true);
      // Sans statut, impossible de savoir si l'application a le droit de
      // s'afficher. Une boucle évite d'empiler une promesse récursive toutes
      // les trois secondes pendant une longue coupure réseau.
      await new Promise((r) => setTimeout(r, 3000));
    }
  }
  setKeysDisabled(false);

  if (st.authenticated) { el.root.hidden = true; return; }

  const waiting = lock(st.configured ? undefined : 'unconfigured');
  if (st.configured && st.locked_out) startCountdown(st.lockout_seconds || 0);
  return waiting;
}

/** Verrouillage automatique après inactivité prolongée. */
export function watchIdle(minutes, onIdle) {
  if (!minutes || minutes <= 0) return () => {};
  const limit = minutes * 60 * 1000;
  let timer = null;
  let lastActivity = Date.now();
  let fired = false;

  const expire = () => {
    if (fired) return;
    fired = true;
    clearTimeout(timer);
    onIdle();
  };
  const arm = () => {
    clearTimeout(timer);
    if (document.hidden || fired) return;
    const remaining = limit - (Date.now() - lastActivity);
    if (remaining <= 0) { expire(); return; }
    timer = setTimeout(expire, remaining);
  };
  const activity = () => {
    if (fired) return;
    lastActivity = Date.now();
    arm();
  };
  const visibility = () => {
    // Les minuteurs sont gelés quand Safari passe en arrière-plan. Ne surtout
    // pas considérer le retour au premier plan comme une activité : on mesure
    // le temps réellement écoulé et on verrouille immédiatement si nécessaire.
    arm();
  };
  const events = ['pointerdown', 'keydown'];
  for (const e of events) document.addEventListener(e, activity, { passive: true });
  document.addEventListener('visibilitychange', visibility, { passive: true });
  arm();

  return () => {
    clearTimeout(timer);
    for (const e of events) document.removeEventListener(e, activity);
    document.removeEventListener('visibilitychange', visibility);
  };
}
