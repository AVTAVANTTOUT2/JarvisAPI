/* Aujourd'hui — l'écran du réveil.
 *
 * Ordre imposé et invariable : ce qui coûte de l'argent, ce qui prend du
 * temps, ce qui est en retard, puis le commentaire. Un ordre fixe s'apprend
 * en trois jours et se lit ensuite sans réfléchir ; un ordre adaptatif
 * oblige à relire chaque matin.
 */

import { api, ApiError } from '../api.js';
import { h, icon, skeleton, banner, parseDate, longDate, hhmm, dueLabel } from '../ui.js';

const URGENT = new Set(['urgent', 'high']);

export default {
  async mount(ctx) {
    const now = new Date();
    ctx.setHeader("Aujourd'hui", longDate(now).replace(/^./, (c) => c.toUpperCase()), [
      { icon: 'refresh', label: 'Actualiser', onClick: () => { void load(); } },
    ]);
    ctx.setDock(null);
    ctx.setBody(skeleton(3));

    let briefingText = null;
    let alive = true;

    function localIso(date) {
      const pad = (value) => String(value).padStart(2, '0');
      return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
        + `T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
    }

    async function load() {
      if (!alive) return;
      const dayStart = new Date(now);
      dayStart.setHours(0, 0, 0, 0);
      const dayEnd = new Date(dayStart);
      dayEnd.setDate(dayEnd.getDate() + 1);
      const [notifs, events, tasks] = await Promise.all([
        api.notifications().catch(errorOf),
        api.calendar(localIso(dayStart), localIso(dayEnd)).catch(errorOf),
        api.tasks().catch(errorOf),
      ]);
      if (!alive) return;
      render(notifs, events, tasks);
    }

    function errorOf(err) { return { __error: err instanceof ApiError ? err : new ApiError(0, 'Erreur') }; }

    function render(notifs, events, tasks) {
      const blocks = [];

      const failed = [notifs, events, tasks].filter((r) => r && r.__error);
      if (failed.length === 3) {
        ctx.setBody(banner('Serveur injoignable. Aucune donnée disponible.', 'err'),
          h('div', { class: 'empty' }, h('p', { text: 'Rien à afficher.' }),
            h('span', { text: 'Réessayez une fois le réseau rétabli.' })));
        return;
      }
      if (failed.length) blocks.push(banner('Certaines données n’ont pas pu être chargées.'));

      // 1 — Ce qui coûte de l'argent, ou qui ne peut pas attendre.
      const urgent = ((notifs && notifs.notifications) || []).filter((n) => URGENT.has(n.priority));
      for (const n of urgent) blocks.push(alertCard(n));

      // 2 — Ce qui prend du temps.
      const list = (events && (events.events || events.calendar)) || [];
      const todays = list.map((e) => ({ ...e, at: parseDate(e.start) }))
        .filter((e) => e.at && e.at.toDateString() === now.toDateString())
        .sort((a, b) => a.at - b.at);
      if (todays.length) {
        const next = todays.find((e) => e.at >= now);
        blocks.push(h('p', { class: 'seclabel' }, 'Agenda', h('em', { class: 'num', text: String(todays.length) })));
        blocks.push(h('div', { class: 'card flush' }, ...todays.map((e) => h('div', {
          class: 'evt' + (e === next ? ' next' : ''),
        },
          h('span', { class: 'h num', text: hhmm(e.at) }),
          h('div', {},
            h('p', { class: 'ct', style: 'font-size:14.5px', text: e.summary || e.title || 'Sans titre' }),
            e.location ? h('p', { class: 'cm', text: e.location }) : null),
        ))));
      }

      // 3 — Ce qui est en retard.
      const late = ((tasks && tasks.tasks) || []).filter((t) => {
        const d = dueLabel(t.due_date, now);
        return t.status !== 'done' && d && d.late;
      });
      if (late.length) {
        blocks.push(h('p', { class: 'seclabel' }, 'En retard', h('em', { class: 'num', text: String(late.length) })));
        blocks.push(h('div', { class: 'card flush' }, ...late.map((t) => {
          const d = dueLabel(t.due_date, now);
          return h('div', { class: 'task' },
            h('span', { class: 'stripe', style: 'background:var(--red)' }),
            h('span', { class: 'box' }, icon('check')),
            h('div', { style: 'flex:1' },
              h('p', { class: 'ct', style: 'font-size:14.5px', text: t.title }),
              h('div', { class: 'tmeta' }, h('span', { class: 'cm late', text: d.text }))));
        })));
      }

      // 4 — Le commentaire, jamais avant.
      blocks.push(h('p', { class: 'seclabel' }, 'Briefing'));
      blocks.push(briefingCard());

      if (!urgent.length && !todays.length && !late.length) {
        blocks.splice(failed.length ? 1 : 0, 0,
          h('div', { class: 'card' }, h('p', { class: 'cs', text: 'Rien d’urgent, rien au programme, rien en retard.' })));
      }

      ctx.setBody(h('div', { class: 'pad' }, ...blocks));
    }

    function alertCard(n) {
      return h('div', { class: 'card alert' },
        h('div', { style: 'display:flex;gap:11px;align-items:flex-start' },
          icon('alert', 'icon'),
          h('div', { style: 'flex:1;min-width:0' },
            h('p', { class: 'ct', text: n.title || 'Notification' }),
            n.content ? h('p', { class: 'cs', style: 'margin-top:2px', text: n.content }) : null),
          h('button', {
            class: 'iconbtn', type: 'button', 'aria-label': 'Marquer comme lu',
            onClick: async (e) => {
              const card = e.currentTarget.closest('.card');
              try { await api.markRead(n.id); card.remove(); } catch { /* réseau : on garde la carte */ }
            },
          }, icon('check'))));
    }

    /* Le briefing est un appel LLM : plusieurs secondes et un coût réel à
     * chaque fois. En faire un chargement automatique reviendrait à payer à
     * chaque ouverture de l'application. C'est donc un bouton, la durée
     * annoncée d'avance. */
    function briefingCard() {
      if (briefingText) {
        return h('div', { class: 'card' },
          h('p', { class: 'briefing', text: briefingText }),
          h('button', {
            class: 'btn block', type: 'button', style: 'margin-top:13px',
            onClick: (e) => { void generate(e.currentTarget); },
          }, 'Régénérer'));
      }
      const btn = h('button', {
        class: 'btn block', type: 'button',
        onClick: (e) => { void generate(e.currentTarget); },
      }, 'Générer le briefing du matin');
      return h('div', { class: 'card' },
        h('p', { class: 'cs', text: 'Aucun briefing généré aujourd’hui.' }),
        h('div', { style: 'margin-top:13px' }, btn),
        h('p', { class: 'cm', style: 'margin-top:9px;text-align:center', text: 'Environ 8 secondes' }));
    }

    async function generate(btn) {
      btn.disabled = true;
      btn.textContent = 'Génération…';
      try {
        const res = await api.briefing('morning');
        briefingText = (res && res.content) || 'Briefing vide.';
      } catch (err) {
        briefingText = err instanceof ApiError && err.status === 0
          ? 'Serveur injoignable. Briefing non généré.'
          : 'Briefing indisponible pour le moment.';
      }
      if (alive) await load();
    }

    await load();
    return () => { alive = false; };
  },
};
