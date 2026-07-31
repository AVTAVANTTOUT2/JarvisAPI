/* Fitness — programme, validation, nutrition, hydratation et conseil IA. */

import { api, ApiError } from '../api.js';
import { h, icon, skeleton, banner } from '../ui.js';

const DAYS = ['Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam', 'Dim'];
const MEALS = {
  petit_dej: 'Petit déjeuner', dejeuner: 'Déjeuner', diner: 'Dîner', collation: 'Collation',
};

function prescription(exercise) {
  const parts = [];
  if (exercise.sets) parts.push(`${exercise.sets} séries`);
  if (exercise.reps) parts.push(`${exercise.reps} reps`);
  if (exercise.duration_sec) parts.push(`${exercise.duration_sec} s`);
  return parts.join(' · ');
}

function progressResult(dashboard, exercise) {
  const list = (dashboard.progress && dashboard.progress.exercise_results) || [];
  return list.find((item) => item.name === exercise.name) || null;
}

export default {
  async mount(ctx) {
    let alive = true;
    let dashboard = null;
    let busy = false;
    let netError = null;
    let advice = null;
    let mealFormOpen = false;

    async function load() {
      try {
        dashboard = await api.get('/api/fitness/dashboard');
        netError = null;
      } catch (err) {
        netError = err instanceof ApiError && err.status === 0
          ? 'Serveur injoignable.' : 'Suivi fitness indisponible.';
      }
      if (alive) render();
    }

    async function setProgress(status, results) {
      if (!dashboard || !dashboard.scheduled_session || busy) return;
      busy = true;
      render();
      try {
        await api.put(
          `/api/fitness/sessions/${dashboard.scheduled_session.id}/progress`,
          {
            date: dashboard.date,
            status,
            exercise_results: results || ((dashboard.progress && dashboard.progress.exercise_results) || []),
          },
        );
      } catch { netError = 'Modification non enregistrée.'; }
      busy = false;
      await load();
    }

    async function toggleExercise(exercise) {
      const current = (dashboard.progress && dashboard.progress.exercise_results) || [];
      const other = current.filter((item) => item.name !== exercise.name);
      const existing = current.find((item) => item.name === exercise.name);
      const next = [...other, {
        name: exercise.name,
        completed: !(existing && existing.completed),
        sets_done: existing ? existing.sets_done : null,
        reps_done: existing ? existing.reps_done : null,
        duration_sec: existing ? existing.duration_sec : null,
        notes: existing ? existing.notes : null,
      }];
      await setProgress('in_progress', next);
    }

    async function addWater(amount) {
      if (busy || !dashboard) return;
      busy = true;
      try {
        await api.post('/api/fitness/water', { date: dashboard.date, amount_ml: amount, source: 'pwa' });
      } catch { netError = 'Hydratation non enregistrée.'; }
      busy = false;
      await load();
    }

    async function getAdvice() {
      if (busy) return;
      busy = true;
      render();
      try { advice = await api.post('/api/fitness/advice'); }
      catch { netError = 'Conseil indisponible.'; }
      busy = false;
      render();
    }

    function mealForm() {
      const type = h('select', { class: 'field fit-select', 'aria-label': 'Type de repas' },
        ...Object.entries(MEALS).map(([value, label]) => h('option', { value, text: label })));
      const description = h('textarea', { class: 'field fit-area', placeholder: 'Ce que vous avez mangé', 'aria-label': 'Description du repas' });
      const calories = h('input', { class: 'field', type: 'number', inputmode: 'numeric', placeholder: 'kcal', 'aria-label': 'Calories' });
      const protein = h('input', { class: 'field', type: 'number', inputmode: 'decimal', placeholder: 'protéines g', 'aria-label': 'Protéines' });
      const save = h('button', { class: 'btn primary block', type: 'button' }, 'Enregistrer');
      save.addEventListener('click', async () => {
        if (!description.value.trim() || busy) return;
        busy = true;
        try {
          await api.post('/api/fitness/meals', {
            date: dashboard.date,
            meal_type: type.value,
            description: description.value.trim(),
            calories_estimate: calories.value ? Number(calories.value) : null,
            protein_g: protein.value ? Number(protein.value) : null,
            source: 'pwa',
          });
          mealFormOpen = false;
        } catch { netError = 'Repas non enregistré.'; }
        busy = false;
        await load();
      });
      return h('div', { class: 'fit-form' }, type, description,
        h('div', { class: 'fit-two' }, calories, protein), save);
    }

    function metric(label, value, target, percent) {
      return h('div', { class: 'fit-metric' },
        h('span', { text: label }), h('strong', { class: 'num', text: value }),
        h('small', { text: target }),
        h('i', {}, h('b', { style: `width:${Math.max(0, Math.min(100, percent || 0))}%` })));
    }

    function sessionCard() {
      const session = dashboard.scheduled_session;
      if (!session) {
        return h('div', { class: 'card fit-rest' },
          h('p', { class: 'ct', text: 'Jour de récupération' }),
          h('p', { class: 'cs', text: `Prochaine séance : ${dashboard.next_session ? dashboard.next_session.title : 'à planifier'}.` }));
      }
      const status = dashboard.progress ? dashboard.progress.status : 'planned';
      const done = status === 'done';
      const exercises = session.exercises.map((exercise) => {
        const checked = Boolean(progressResult(dashboard, exercise)?.completed);
        const box = h('span', { class: `box${checked ? ' done' : ''}` }, icon('check'));
        const button = h('button', {
          class: 'fit-exercise', type: 'button', disabled: busy || done,
          'aria-pressed': checked ? 'true' : 'false',
        }, box, h('span', {},
          h('strong', { text: exercise.name }),
          h('small', { text: prescription(exercise) }),
          exercise.progression ? h('em', { text: exercise.progression }) : null));
        button.addEventListener('click', () => { void toggleExercise(exercise); });
        return button;
      });
      const doneButton = h('button', { class: 'btn primary', type: 'button', disabled: busy || done }, icon('check'), 'Fait');
      doneButton.addEventListener('click', () => { void setProgress('done'); });
      const skipButton = h('button', { class: 'btn ghost', type: 'button', disabled: busy || done }, icon('x'), 'Non fait');
      skipButton.addEventListener('click', () => { void setProgress('skipped'); });
      const warmup = h('details', { class: 'fit-details' }, h('summary', { text: 'Échauffement' }),
        ...session.warmup.map((item) => h('p', { text: `${item.name} · ${prescription(item)}` })));
      const stretch = h('details', { class: 'fit-details' }, h('summary', { text: 'Étirements de fin' }),
        ...session.stretches.map((item) => h('p', { text: `${item.name} · ${prescription(item)}` })));
      return h('div', { class: `card fit-session${done ? ' complete' : ''}` },
        h('div', { class: 'fit-session-head' }, h('div', {},
          h('span', { text: `Séance du jour · ${DAYS[session.day_of_week]}` }),
          h('p', { class: 'ct', text: session.title })),
          h('b', { class: `fit-status ${status}`, text: done ? 'FAIT' : status === 'skipped' ? 'NON FAIT' : status === 'in_progress' ? 'EN COURS' : 'À FAIRE' })),
        h('p', { class: 'cs', text: session.description || '' }), warmup,
        h('div', { class: 'fit-exercises' }, ...exercises), stretch,
        h('div', { class: 'fit-actions' }, doneButton, skipButton),
        session.notes ? h('p', { class: 'fit-note', text: session.notes }) : null);
    }

    function render() {
      ctx.setHeader('Fitness', dashboard ? `${dashboard.weekly_done}/${dashboard.weekly_target} séances cette semaine` : 'Programme personnel');
      ctx.setDock(null);
      if (!dashboard) {
        ctx.setBody(netError ? banner(netError, 'err') : skeleton(5));
        return;
      }
      const p = dashboard.program;
      const protein = dashboard.meals.reduce((sum, meal) => sum + (meal.protein_g || 0), 0);
      const mealAdd = h('button', { class: 'round', type: 'button', 'aria-label': 'Ajouter un repas' }, icon(mealFormOpen ? 'x' : 'plus'));
      mealAdd.addEventListener('click', () => { mealFormOpen = !mealFormOpen; render(); });
      const waterButtons = [250, 500, 750].map((amount) => {
        const button = h('button', { class: 'btn ghost', type: 'button', text: `+${amount} ml`, disabled: busy });
        button.addEventListener('click', () => { void addWater(amount); });
        return button;
      });
      const adviceButton = h('button', { class: 'btn block', type: 'button', disabled: busy },
        busy ? 'Analyse…' : 'Conseil de JARVIS');
      adviceButton.addEventListener('click', () => { void getAdvice(); });

      ctx.setBody(netError ? banner(netError, 'err') : null,
        h('div', { class: 'pad fit-pad' },
          h('div', { class: 'fit-metrics' },
            metric('Semaine', `${dashboard.weekly_done}/${dashboard.weekly_target}`, 'séances', dashboard.weekly_done / dashboard.weekly_target * 100),
            metric('Calories', String(dashboard.summary.calories_estimate), `${p.calories_min}–${p.calories_max} kcal`, dashboard.summary.calories_estimate / p.calories_min * 100),
            metric('Protéines', `${Math.round(protein)} g`, `${p.protein_min_g}–${p.protein_max_g} g`, protein / p.protein_min_g * 100),
            metric('Eau', `${(dashboard.summary.water_ml / 1000).toFixed(1)} L`, 'objectif 2 L', dashboard.summary.water_ml / 2000 * 100)),
          sessionCard(),
          h('div', { class: 'card' },
            h('div', { class: 'fit-card-head' }, h('div', {}, h('p', { class: 'ct', text: 'Alimentation' }), h('p', { class: 'cs', text: `${dashboard.meals.length} repas aujourd'hui` })), mealAdd),
            mealFormOpen ? mealForm() : null,
            h('div', { class: 'fit-meals' }, ...dashboard.meals.map((meal) => h('div', {},
              h('span', { text: MEALS[meal.meal_type] || 'Repas' }),
              h('strong', { text: `${meal.calories_estimate || '?'} kcal · ${meal.protein_g || '?'} g` }),
              h('p', { text: meal.description })))),
          h('div', { class: 'card' }, h('p', { class: 'ct', text: 'Hydratation' }), h('div', { class: 'fit-actions' }, ...waterButtons)),
          h('div', { class: 'card fit-advice' }, h('p', { class: 'ct', text: 'Conseil IA' }),
            h('p', { class: 'cs', text: advice ? advice.text : 'Analyse à la demande de votre séance, de vos repas et de votre récupération.' }), adviceButton),
          h('p', { class: 'seclabel', text: 'Programme de la semaine' }),
          ...p.sessions.map((item) => h('div', { class: 'card fit-program' },
            h('span', { text: DAYS[item.day_of_week] }), h('div', {}, h('p', { class: 'ct', text: item.title }), h('p', { class: 'cs', text: item.exercises.map((value) => value.name).join(' · ') }))))));
    }

    ctx.setHeader('Fitness');
    ctx.setBody(skeleton(5));
    await load();
    return () => { alive = false; };
  },
};
