/* Tâches.
 *
 * Le retard est une section, pas une couleur : plutôt que de teindre des
 * lignes dispersées dans une liste unique, les tâches en retard sont
 * regroupées en tête sous leur propre titre. On voit le nombre avant de lire
 * les libellés.
 */

import { api, ApiError } from '../api.js';
import { h, icon, skeleton, banner, dueLabel, priorityPill, priorityColor } from '../ui.js';

export default {
  async mount(ctx) {
    let items = [];
    let alive = true;
    let netError = null;

    const field = h('input', {
      class: 'field', type: 'text', placeholder: 'Nouvelle tâche',
      enterkeyhint: 'done', autocomplete: 'off', 'aria-label': 'Titre de la nouvelle tâche',
    });
    const addBtn = h('button', { class: 'round primary', type: 'button', 'aria-label': 'Ajouter' }, icon('plus'));

    async function add() {
      const title = field.value.trim();
      if (!title) return;
      field.value = '';
      field.blur();
      try {
        await api.createTask({ title, priority: 'medium' });
        await load();
      } catch (err) {
        netError = err instanceof ApiError && err.status === 0
          ? 'Serveur injoignable. La tâche n’a pas été créée.'
          : 'La tâche n’a pas pu être créée.';
        field.value = title;   // on ne perd pas ce qui a été tapé
        render();
      }
    }

    field.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); void add(); } });
    addBtn.addEventListener('click', () => { void add(); });

    async function load() {
      try {
        const res = await api.tasks('all');
        items = (res && res.tasks) || [];
        netError = null;
      } catch (err) {
        if (err instanceof ApiError && err.status === 0) netError = 'Serveur injoignable.';
        else netError = 'Tâches indisponibles.';
      }
      if (alive) render();
    }

    async function toggle(task, boxEl) {
      const done = task.status === 'done';
      const next = done ? 'todo' : 'done';
      // Optimiste : la coche répond au doigt, pas au réseau.
      boxEl.classList.toggle('done', !done);
      boxEl.parentElement?.setAttribute('aria-pressed', !done ? 'true' : 'false');
      task.status = next;
      try {
        await api.updateTask(task.id, { status: next });
        await load();
      } catch {
        boxEl.classList.toggle('done', done);
        boxEl.parentElement?.setAttribute('aria-pressed', done ? 'true' : 'false');
        task.status = done ? 'done' : 'todo';
        netError = 'Modification non enregistrée. Serveur injoignable.';
        render();
      }
    }

    function row(task) {
      const now = new Date();
      const due = dueLabel(task.due_date, now);
      const done = task.status === 'done';
      const box = h('span', {
        class: 'box' + (done ? ' done' : ''),
      }, icon('check'));
      const toggleButton = h('button', {
        class: 'task-toggle', type: 'button',
        'aria-label': done ? 'Rouvrir la tâche' : 'Terminer la tâche',
        'aria-pressed': done ? 'true' : 'false',
      }, box);
      toggleButton.addEventListener('click', () => { void toggle(task, box); });

      const meta = [];
      if (due) meta.push(h('span', { class: 'cm' + (due.late && !done ? ' late' : ''), text: due.text }));
      if (!done) meta.push(priorityPill(task.priority));

      return h('div', { class: 'task' },
        h('span', { class: 'stripe', style: `background:${done ? 'var(--green)' : priorityColor(task.priority)}` }),
        toggleButton,
        h('div', { style: 'flex:1;min-width:0' },
          h('p', { class: 'ct' + (done ? ' strike' : ''), text: task.title }),
          meta.length ? h('div', { class: 'tmeta' }, ...meta) : null));
    }

    function section(label, list, extraClass = '') {
      if (!list.length) return [];
      return [
        h('p', { class: 'seclabel' }, label, h('em', { class: 'num', text: String(list.length) })),
        h('div', { class: `card flush ${extraClass}`.trim() }, ...list.map(row)),
      ];
    }

    function render() {
      const now = new Date();
      const open = items.filter((t) => t.status !== 'done');
      const late = open.filter((t) => { const d = dueLabel(t.due_date, now); return d && d.late; });
      const rest = open.filter((t) => !late.includes(t));
      // Terminées du jour seulement : la liste ne devient pas un cimetière.
      const doneToday = items.filter((t) => {
        if (t.status !== 'done' || !t.completed_at) return false;
        const d = new Date(String(t.completed_at).replace(' ', 'T'));
        return !Number.isNaN(d.getTime()) && d.toDateString() === now.toDateString();
      });

      ctx.setHeader('Tâches',
        `${open.length} en cours${late.length ? `, ${late.length} en retard` : ''}`);

      if (!items.length && netError) {
        ctx.setBody(banner(netError, 'err'),
          h('div', { class: 'empty' }, h('p', { text: 'Aucune donnée.' })));
        return;
      }
      if (!items.length) {
        ctx.setBody(h('div', { class: 'empty' }, h('p', { text: 'Aucune tâche.' })));
        return;
      }

      ctx.setBody(
        netError ? banner(netError) : null,
        h('div', { class: 'pad' },
          ...section('En retard', late),
          ...section('À faire', rest),
          ...section("Terminé aujourd'hui", doneToday, 'doneset')),
      );
    }

    ctx.setHeader('Tâches');
    ctx.setBody(skeleton(3));
    ctx.setDock(h('div', { style: 'display:flex;align-items:center;gap:9px;width:100%' }, field, addBtn));

    await load();
    return () => { alive = false; };
  },
};
