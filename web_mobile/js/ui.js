/* Fabrique d'éléments et formats partagés.
 *
 * Pas de moteur de gabarits : `h()` construit du DOM réel, ce qui évite
 * d'injecter du HTML issu du serveur (résumés de mails, titres de tâches,
 * réponses du modèle) dans innerHTML. Tout texte passe par textContent.
 */

/** h('div', {class:'card'}, ...enfants) — attributs et enfants optionnels. */
export function h(tag, attrs, ...kids) {
  const node = document.createElement(tag);
  if (attrs && (typeof attrs !== 'object' || Array.isArray(attrs) || attrs instanceof Node)) {
    kids.unshift(attrs);
    attrs = null;
  }
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v === null || v === undefined) continue;
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === 'dataset') Object.assign(node.dataset, v);
    else node.setAttribute(k, v === true ? '' : String(v));
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    node.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return node;
}

/** Icône du jeu défini une fois dans index.html. */
export function icon(name, cls = 'icon') {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('class', cls);
  svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttribute('href', `#i-${name}`);
  svg.append(use);
  return svg;
}

export function empty(message, detail) {
  return h('div', { class: 'empty' }, h('p', { text: message }), detail ? h('span', { text: detail }) : null);
}

export function skeleton(rows = 3) {
  return h('div', { class: 'card' },
    h('div', { style: 'display:flex;flex-direction:column;gap:9px' },
      ...Array.from({ length: rows }, (_, i) =>
        h('div', { class: 'skel', style: `width:${[62, 88, 40, 74][i % 4]}%` }))));
}

export function banner(message, kind = 'warn') {
  return h('div', { class: kind === 'err' ? 'banner err' : 'banner' }, icon('alert'), h('span', { text: message }));
}

// ── Dates ────────────────────────────────────────────────────────────

const JOURS = ['dimanche', 'lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi'];
const MOIS = ['janvier', 'février', 'mars', 'avril', 'mai', 'juin',
  'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre'];

export function parseDate(value) {
  if (!value) return null;
  // SQLite rend « 2026-07-30 14:30:00 » : Safari refuse l'espace, il veut un T.
  const d = new Date(typeof value === 'string' ? value.replace(' ', 'T') : value);
  return Number.isNaN(d.getTime()) ? null : d;
}

function midnight(d) { return new Date(d.getFullYear(), d.getMonth(), d.getDate()); }

/** Écart en jours pleins : négatif = passé. */
export function dayDelta(date, now = new Date()) {
  return Math.round((midnight(date) - midnight(now)) / 86400000);
}

export function longDate(date) {
  const jour = date.getDate();
  return `${JOURS[date.getDay()]} ${jour === 1 ? '1er' : jour} ${MOIS[date.getMonth()]}`;
}

export function hhmm(date) {
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
}

/** Échéance en clair. `late` indique s'il faut la traiter en retard. */
export function dueLabel(value, now = new Date()) {
  const d = parseDate(value);
  if (!d) return null;
  const delta = dayDelta(d, now);
  if (delta < -1) return { text: `${-delta} jours de retard`, late: true };
  if (delta === -1) return { text: 'Hier', late: true };
  if (delta === 0) return { text: "Aujourd'hui", late: false };
  if (delta === 1) return { text: 'Demain', late: false };
  return { text: longDate(d).replace(/^./, (c) => c.toUpperCase()), late: false };
}

export function relativeMinutes(from, now = new Date()) {
  const mins = Math.max(0, Math.round((now - from) / 60000));
  if (mins < 1) return "à l'instant";
  if (mins === 1) return 'il y a 1 minute';
  if (mins < 60) return `il y a ${mins} minutes`;
  const hours = Math.round(mins / 60);
  return hours === 1 ? 'il y a 1 heure' : `il y a ${hours} heures`;
}

const PRIORITES = { high: ['Haute', 'red'], medium: ['Moyenne', 'amber'], low: ['Basse', 'dim'] };

export function priorityPill(priority) {
  const [label, tone] = PRIORITES[priority] || PRIORITES.medium;
  return h('span', { class: `pill ${tone}`, text: label });
}

export function priorityColor(priority) {
  return { high: 'var(--red)', medium: 'var(--amber)', low: 'var(--fg-3)' }[priority] || 'var(--fg-3)';
}

/** Montant repéré dans un résumé d'email (« 412,00 € », « 19.99 EUR »). */
export function findAmount(...texts) {
  for (const t of texts) {
    if (!t) continue;
    const m = String(t).match(/(\d[\d\s ]*(?:[.,]\d{1,2})?)\s*(?:€|EUR\b)/i);
    if (m) return `${m[1].replace(/\s| /g, ' ').trim()} €`;
  }
  return null;
}
