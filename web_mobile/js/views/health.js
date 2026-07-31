/* Santé — suivi fitness (séances, repas, eau, bien-être).
 *
 * Consomme /api/fitness/* déjà exposé par app/fitness/. Source « pwa » :
 * seule origine UI autorisée par le contrat backend (avec « voice »).
 */

import { api, ApiError } from '../api.js';
import { h, icon, skeleton, banner, empty } from '../ui.js';

function todayIso() {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

function waterLabel(ml) {
  if (ml >= 1000) return `${(ml / 1000).toLocaleString('fr-FR')} L`;
  return `${ml} ml`;
}

function field(attrs = {}, ...kids) {
  return h('input', { class: 'field fitness-field', ...attrs }, ...kids);
}

function select(attrs = {}, options) {
  return h('select', { class: 'field fitness-field', ...attrs },
    ...options.map(([value, label]) => h('option', { value, text: label })));
}

function textarea(attrs = {}) {
  return h('textarea', { class: 'field fitness-field fitness-area', ...attrs });
}

function feedbackLine(msg) {
  return msg ? h('p', { class: 'fitness-err', text: msg }) : null;
}

export default {
  async mount(ctx) {
    ctx.setHeader('Santé', 'Bouger, nourrir, hydrater, ressentir', [
      { icon: 'refresh', label: 'Actualiser', onClick: () => { void load(); } },
    ]);
    ctx.setDock(null);
    ctx.setBody(skeleton(4));

    let alive = true;
    let summary = null;

    async function load() {
      if (!alive) return;
      try {
        summary = await api.fitnessSummaryToday();
        render();
      } catch (err) {
        if (!alive) return;
        const msg = err instanceof ApiError ? err.message : 'Erreur';
        ctx.setBody(
          banner(msg === 'Serveur injoignable' ? 'Serveur injoignable.' : 'Résumé indisponible.', 'err'),
          empty('Section santé inaccessible.', 'Réessayez une fois le réseau rétabli.'),
        );
      }
    }

    function metric(label, value) {
      return h('div', { class: 'fitness-metric' },
        h('strong', { class: 'num', text: value }),
        h('span', { text: label }));
    }

    function summaryCard() {
      if (!summary) return null;
      const forme = summary.wellbeing && summary.wellbeing.rating != null
        ? `${summary.wellbeing.rating}/10`
        : '—';
      const seance = summary.workout_done
        ? `${summary.workout_count} faite${summary.workout_count > 1 ? 's' : ''}`
        : 'Aucune';
      return h('div', { class: 'card fitness-summary' },
        h('p', { class: 'seclabel', style: 'margin:0 0 10px' }, "Aujourd'hui",
          summary.calories_estimate > 0
            ? h('em', { class: 'num', text: `≈ ${summary.calories_estimate} kcal` })
            : null),
        h('div', { class: 'fitness-metrics' },
          metric('Séance', seance),
          metric('Repas', String(summary.meal_count || 0)),
          metric('Eau', waterLabel(summary.water_ml || 0)),
          metric('Forme', forme)));
    }

    function setBusy(btn, busy, ok) {
      if (!btn) return;
      btn.disabled = !!busy;
      if (busy) btn.textContent = '…';
      else if (ok) {
        btn.textContent = 'Enregistré';
        setTimeout(() => { if (alive) btn.textContent = btn.dataset.label || 'Enregistrer'; }, 1200);
      } else btn.textContent = btn.dataset.label || 'Enregistrer';
    }

    function workoutForm() {
      const type = select({}, [
        ['poussee', 'Poussée'],
        ['tirage', 'Tirage / dos'],
        ['jambes', 'Jambes'],
        ['full_body', 'Full body'],
        ['natation', 'Natation'],
        ['autre', 'Autre'],
      ]);
      const duration = field({ type: 'number', inputmode: 'numeric', min: '1', max: '1440', placeholder: 'Minutes' });
      const exercise = field({ type: 'text', maxlength: '160', placeholder: 'Exercice (optionnel)' });
      const sets = field({ type: 'number', inputmode: 'numeric', min: '1', placeholder: 'Séries', 'aria-label': 'Séries' });
      const reps = field({ type: 'number', inputmode: 'numeric', min: '1', placeholder: 'Reps', 'aria-label': 'Répétitions' });
      const err = h('div', {});
      const btn = h('button', { class: 'btn primary block', type: 'submit', text: 'Enregistrer', dataset: { label: 'Enregistrer' } });

      return h('form', {
        class: 'card fitness-form',
        onSubmit: async (event) => {
          event.preventDefault();
          err.replaceChildren();
          setBusy(btn, true);
          try {
            const name = exercise.value.trim();
            await api.createWorkout({
              date: todayIso(),
              type: type.value,
              duration_min: duration.value ? Number(duration.value) : null,
              exercises_json: name
                ? [{
                  name,
                  ...(sets.value ? { sets: Number(sets.value) } : {}),
                  ...(reps.value ? { reps: Number(reps.value) } : {}),
                }]
                : null,
              source: 'pwa',
            });
            exercise.value = '';
            sets.value = '';
            reps.value = '';
            setBusy(btn, false, true);
            await load();
          } catch {
            setBusy(btn, false);
            err.replaceChildren(feedbackLine('Enregistrement impossible. Réessaie.'));
          }
        },
      },
        h('p', { class: 'ct', text: 'Séance' }),
        h('p', { class: 'cm', text: 'Mouvement et durée' }),
        h('div', { class: 'fitness-row' }, type, duration),
        h('div', { class: 'fitness-row3' }, exercise, sets, reps),
        btn,
        err);
    }

    function mealForm() {
      const mealType = select({}, [
        ['petit_dej', 'Petit-déj.'],
        ['dejeuner', 'Déjeuner'],
        ['diner', 'Dîner'],
        ['collation', 'Collation'],
      ]);
      const calories = field({ type: 'number', inputmode: 'numeric', min: '0', max: '20000', placeholder: 'Kcal (optionnel)' });
      const description = textarea({ rows: '2', maxlength: '2000', placeholder: 'Qu’as-tu mangé ?' });
      const err = h('div', {});
      const btn = h('button', { class: 'btn primary block', type: 'submit', text: 'Enregistrer', dataset: { label: 'Enregistrer' } });

      return h('form', {
        class: 'card fitness-form',
        onSubmit: async (event) => {
          event.preventDefault();
          if (!description.value.trim()) return;
          err.replaceChildren();
          setBusy(btn, true);
          try {
            await api.createMeal({
              date: todayIso(),
              meal_type: mealType.value,
              description: description.value.trim(),
              calories_estimate: calories.value ? Number(calories.value) : null,
              source: 'pwa',
            });
            description.value = '';
            calories.value = '';
            setBusy(btn, false, true);
            await load();
          } catch {
            setBusy(btn, false);
            err.replaceChildren(feedbackLine('Enregistrement impossible. Réessaie.'));
          }
        },
      },
        h('p', { class: 'ct', text: 'Repas' }),
        h('p', { class: 'cm', text: 'Simple, sans comptage obligatoire' }),
        h('div', { class: 'fitness-row' }, mealType, calories),
        description,
        btn,
        err);
    }

    function waterForm() {
      const err = h('div', {});
      const buttons = [250, 500, 1000].map((amount) => {
        const label = amount === 1000 ? '+1 L' : `+${amount} ml`;
        const btn = h('button', {
          class: 'btn ghost fitness-water',
          type: 'button',
          text: label,
          dataset: { label },
          onClick: async () => {
            err.replaceChildren();
            setBusy(btn, true);
            try {
              await api.addWater({ date: todayIso(), amount_ml: amount, source: 'pwa' });
              setBusy(btn, false, true);
              await load();
            } catch {
              setBusy(btn, false);
              err.replaceChildren(feedbackLine('Enregistrement impossible. Réessaie.'));
            }
          },
        });
        return btn;
      });

      return h('div', { class: 'card fitness-form' },
        h('p', { class: 'ct', text: 'Eau' }),
        h('p', { class: 'cm', text: 'Ajout rapide' }),
        h('div', { class: 'fitness-water-row' }, ...buttons),
        err);
    }

    function wellbeingForm() {
      const ratingValue = h('strong', { class: 'num', text: '7/10' });
      const rating = h('input', {
        type: 'range', min: '1', max: '10', step: '1', value: '7',
        class: 'fitness-range', 'aria-label': 'Note de bien-être',
        onInput: (event) => { ratingValue.textContent = `${event.target.value}/10`; },
      });
      const journal = textarea({ rows: '3', maxlength: '2000', placeholder: 'Une pensée, une sensation… (optionnel)' });
      const err = h('div', {});
      const btn = h('button', { class: 'btn primary block', type: 'submit', text: 'Enregistrer', dataset: { label: 'Enregistrer' } });

      return h('form', {
        class: 'card fitness-form',
        onSubmit: async (event) => {
          event.preventDefault();
          err.replaceChildren();
          setBusy(btn, true);
          try {
            await api.createWellbeing({
              date: todayIso(),
              rating: Number(rating.value),
              journal_text: journal.value.trim() || null,
              source: 'pwa',
            });
            journal.value = '';
            setBusy(btn, false, true);
            await load();
          } catch {
            setBusy(btn, false);
            err.replaceChildren(feedbackLine('Enregistrement impossible. Réessaie.'));
          }
        },
      },
        h('p', { class: 'ct', text: 'Bien-être' }),
        h('p', { class: 'cm', text: 'Note rapide et journal libre' }),
        h('div', { class: 'fitness-rating' },
          h('div', { class: 'fitness-rating-head' },
            h('span', { class: 'cm', text: 'Ressenti global' }),
            ratingValue),
          rating),
        journal,
        btn,
        err);
    }

    function render() {
      if (!alive) return;
      ctx.setBody(
        summaryCard(),
        h('p', { class: 'seclabel', text: "Ajouter à aujourd'hui" }),
        workoutForm(),
        mealForm(),
        waterForm(),
        wellbeingForm(),
      );
    }

    await load();
    return () => { alive = false; };
  },
};
