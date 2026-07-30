/* Mails — lecture seule.
 *
 * Ce n'est pas une boîte de réception. Rien n'est listé qui n'ait déjà passé
 * le filtre de JARVIS en amont : ce qui coûte de l'argent, ce qu'une vraie
 * personne attend. Tout le reste a été lu, jugé sans intérêt, et oublié.
 * Le sous-titre le dit franchement, pour que l'absence de messages ne soit
 * jamais prise pour une panne.
 */

import { api, ApiError } from '../api.js';
import { h, skeleton, banner, findAmount } from '../ui.js';

const FILTERS = [
  { id: 'all', label: 'Tout' },
  { id: 'payment', label: 'Paiements' },
  { id: 'request', label: 'Demandes' },
];

/** Le backend ne catégorise pas explicitement : on déduit du montant. */
function kindOf(mail) {
  return findAmount(mail.summary, mail.subject, mail.action_needed) ? 'payment' : 'request';
}

export default {
  async mount(ctx) {
    let mails = [];
    let filter = 'all';
    let netError = null;
    let alive = true;

    ctx.setHeader('Mails', 'Retenus par JARVIS, pas votre boîte');
    ctx.setDock(null);
    ctx.setBody(skeleton(3));

    async function load() {
      try {
        const res = await api.emails(30);
        mails = (res && res.emails) || [];
        netError = null;
      } catch (err) {
        netError = err instanceof ApiError && err.status === 0
          ? 'Serveur injoignable.' : 'Mails indisponibles.';
      }
      if (alive) render();
    }

    function card(mail) {
      const kind = kindOf(mail);
      const amount = kind === 'payment' ? findAmount(mail.summary, mail.subject, mail.action_needed) : null;
      const urgent = mail.priority === 'urgent' || mail.priority === 'high';

      const left = h('div', { style: 'flex:1;min-width:0' },
        h('span', { class: kind === 'payment' ? 'pill blue' : 'pill amber', text: kind === 'payment' ? 'Paiement' : 'Demande' }),
        h('p', { class: 'ct', style: 'margin-top:9px', text: mail.sender || 'Expéditeur inconnu' }),
        mail.subject ? h('p', { class: 'cs', style: 'margin-top:1px', text: mail.subject }) : null);

      const head = amount
        ? h('div', { class: 'mrow' }, left,
            h('span', { class: 'amount num', style: urgent ? 'color:var(--red)' : '', text: amount }))
        : left;

      // Le verdict de JARVIS passe devant l'objet : ce n'est pas un extrait
      // du message, c'est sa conclusion.
      const verdict = mail.action_needed || mail.summary;

      return h('div', { class: 'card' }, head,
        verdict ? h('p', { class: 'mverdict', text: verdict }) : null);
    }

    function render() {
      const counts = {
        all: mails.length,
        payment: mails.filter((m) => kindOf(m) === 'payment').length,
        request: mails.filter((m) => kindOf(m) === 'request').length,
      };
      const shown = filter === 'all' ? mails : mails.filter((m) => kindOf(m) === filter);

      const pills = h('div', { class: 'filters' }, ...FILTERS.map((f) => {
        const b = h('button', {
          class: 'fpill', type: 'button', 'aria-pressed': filter === f.id ? 'true' : 'false',
          onClick: () => { filter = f.id; render(); },
        }, f.label, h('span', { class: 'n num', text: String(counts[f.id]) }));
        return b;
      }));

      if (!mails.length) {
        ctx.setBody(
          netError ? banner(netError, 'err') : null,
          h('div', { class: 'empty' },
            h('p', { text: netError ? 'Aucune donnée.' : 'Aucun mail retenu.' }),
            netError ? null : h('span', { text: 'Rien qui demande votre attention.' })),
        );
        return;
      }

      ctx.setBody(
        netError ? banner(netError) : null,
        pills,
        shown.length
          ? h('div', { class: 'pad', style: 'padding-top:12px' }, ...shown.map(card))
          : h('div', { class: 'empty' }, h('p', { text: 'Rien dans cette catégorie.' })),
      );
    }

    await load();
    return () => { alive = false; };
  },
};
