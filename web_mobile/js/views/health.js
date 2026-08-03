/* Fitness mobile — miroir fonctionnel de la vue desktop, sans dépendance React. */

import { api, ApiError } from '../api.js';
import { h, icon, skeleton, banner } from '../ui.js';

const DAYS = ['Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam', 'Dim'];
const MEALS = {
  petit_dej: 'Petit déjeuner',
  dejeuner: 'Déjeuner',
  diner: 'Dîner',
  collation: 'Collation',
};
const PROGRAM_NUMBER_FIELDS = [
  ['calories_min', 'Calories min', 0, 20000],
  ['calories_max', 'Calories max', 0, 20000],
  ['protein_min_g', 'Protéines min', 0, 1000],
  ['protein_max_g', 'Protéines max', 0, 1000],
  ['weekly_min_sessions', 'Séances minimum', 1, 7],
  ['reminder_interval_min', 'Rappel toutes les min', 30, 720],
];

function validateProgramSettings(fields, reminderTime) {
  const payload = {};
  for (const [key, label, min, max] of PROGRAM_NUMBER_FIELDS) {
    const raw = fields.get(key).value.trim();
    if (!raw) return { error: `${label} est requis.` };
    const value = Number(raw);
    if (!Number.isInteger(value) || value < min || value > max) {
      return { error: `${label} doit être compris entre ${min} et ${max}.` };
    }
    payload[key] = value;
  }
  if (!/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(reminderTime.value)) {
    return { error: 'Premier rappel doit contenir une heure valide.' };
  }
  if (payload.calories_min > payload.calories_max) {
    return { error: 'Calories min doit être inférieur ou égal à Calories max.' };
  }
  if (payload.protein_min_g > payload.protein_max_g) {
    return { error: 'Protéines min doit être inférieur ou égal à Protéines max.' };
  }
  payload.reminder_time = reminderTime.value;
  return { payload };
}

function localIsoDate() {
  const now = new Date();
  const offset = now.getTimezoneOffset() * 60_000;
  return new Date(now.getTime() - offset).toISOString().slice(0, 10);
}

function prescription(exercise) {
  const parts = [];
  if (exercise.sets) parts.push(`${exercise.sets} séries`);
  if (exercise.reps) parts.push(`${exercise.reps} reps`);
  if (exercise.duration_sec) parts.push(`${exercise.duration_sec} s`);
  if (exercise.sides === 2) parts.push('de chaque côté');
  return parts.join(' · ') || 'À la sensation';
}

function ratio(value, target) {
  if (!target) return 0;
  return Math.max(0, Math.min(100, Math.round((value / target) * 100)));
}

function progressResult(dashboard, exercise) {
  const list = (dashboard.progress && dashboard.progress.exercise_results) || [];
  return list.find((item) => item.name === exercise.name) || null;
}

function input(attrs = {}) {
  return h('input', { class: 'field fit-input', ...attrs });
}

function textarea(attrs = {}, value = '') {
  const node = h('textarea', { class: 'field fit-area', ...attrs });
  node.value = value;
  return node;
}

function select(options, value, attrs = {}) {
  const node = h('select', { class: 'field fit-select', ...attrs },
    ...options.map(([key, label]) => h('option', { value: key, text: label })));
  node.value = String(value ?? '');
  return node;
}

function editorLines(items) {
  return (items || []).map((item) => (
    `${item.name} | ${item.sets ?? ''} | ${item.reps ?? ''} | ${item.duration_sec ?? ''} | ${item.sides ?? ''}`
  )).join('\n');
}

function parseEditorLines(value, previousItems) {
  return value.split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [name = '', sets = '', reps = '', duration = '', sides = ''] = line
        .split('|')
        .map((part) => part.trim());
      const previous = (previousItems || []).find((item) => item.name === name);
      return {
        name,
        sets: sets && Number(sets) > 0 ? Number(sets) : null,
        reps: reps || null,
        duration_sec: duration ? (/^\d+$/.test(duration) ? Number(duration) : duration) : null,
        sides: sides && Number(sides) > 0 ? Number(sides) : null,
        progression: previous ? previous.progression : null,
      };
    });
}

export default {
  async mount(ctx) {
    let alive = true;
    let dashboard = null;
    let busy = false;
    let networkError = null;
    let mutationError = null;
    let advice = null;
    let mealFormOpen = false;
    let activeTab = 'today';
    let settingsOpen = false;
    let editingSession = null;

    async function load() {
      try {
        dashboard = await api.get(`/api/fitness/dashboard?date=${encodeURIComponent(localIsoDate())}`);
        networkError = null;
      } catch (err) {
        networkError = err instanceof ApiError && err.status === 0
          ? 'Serveur injoignable.' : 'Suivi fitness indisponible.';
      }
      if (alive) render();
    }

    async function setProgress(status, results) {
      if (!dashboard || !dashboard.scheduled_session || busy) return;
      busy = true;
      mutationError = null;
      render();
      try {
        await api.put(
          `/api/fitness/sessions/${dashboard.scheduled_session.id}/progress`,
          {
            date: dashboard.date,
            status,
            exercise_results: results ?? ((dashboard.progress && dashboard.progress.exercise_results) || []),
          },
        );
      } catch {
        mutationError = 'Modification non enregistrée.';
      }
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
      mutationError = null;
      render();
      try {
        await api.post('/api/fitness/water', {
          date: dashboard.date,
          amount_ml: amount,
          source: 'pwa',
        });
      } catch {
        mutationError = 'Hydratation non enregistrée.';
      }
      busy = false;
      await load();
    }

    async function getAdvice() {
      if (busy || !dashboard) return;
      busy = true;
      mutationError = null;
      render();
      try {
        advice = await api.post(`/api/fitness/advice?date=${encodeURIComponent(dashboard.date)}`);
      } catch {
        mutationError = 'Conseil indisponible.';
      }
      busy = false;
      render();
    }

    function metric(label, value, detail, percent) {
      return h('div', { class: 'fit-metric' },
        h('span', { text: label }),
        h('strong', { class: 'num', text: value }),
        h('small', { text: detail }),
        h('i', {}, h('b', { style: `width:${ratio(percent, 100)}%` })));
    }

    function segmentSwitch() {
      return h('div', { class: 'fit-switch', role: 'tablist', 'aria-label': 'Vue fitness' },
        ...[
          ['today', "Aujourd'hui"],
          ['program', 'Programme'],
        ].map(([key, label]) => {
          const button = h('button', {
            class: `fit-switch-btn${activeTab === key ? ' active' : ''}`,
            type: 'button',
            role: 'tab',
            'aria-selected': activeTab === key ? 'true' : 'false',
            text: label,
          });
          button.addEventListener('click', () => { activeTab = key; render(); });
          return button;
        }));
    }

    function sessionCard() {
      const session = dashboard.scheduled_session;
      if (!session) {
        return h('div', { class: 'card fit-rest' },
          h('p', { class: 'fit-rest-mark', text: '✦' }),
          h('p', { class: 'ct', text: 'Jour de récupération' }),
          h('p', { class: 'cs', text: `Prochaine séance : ${dashboard.next_session ? dashboard.next_session.title : 'à planifier'}.` }));
      }

      const status = dashboard.progress ? dashboard.progress.status : 'planned';
      const workoutDone = status === 'done';
      const exercises = session.exercises.map((exercise) => {
        const checked = Boolean(progressResult(dashboard, exercise)?.completed);
        const box = h('span', { class: `box${checked ? ' done' : ''}` }, icon('check'));
        const button = h('button', {
          class: 'fit-exercise',
          type: 'button',
          disabled: busy || workoutDone,
          'aria-pressed': checked ? 'true' : 'false',
        }, box, h('span', {},
          h('strong', { text: exercise.name }),
          h('small', { text: prescription(exercise) }),
          exercise.progression ? h('em', { text: `Progression : ${exercise.progression}` }) : null));
        button.addEventListener('click', () => { void toggleExercise(exercise); });
        return button;
      });

      const doneButton = h('button', {
        class: 'btn primary', type: 'button', disabled: busy || workoutDone,
      }, icon('check'), 'Marquer comme fait');
      doneButton.addEventListener('click', () => { void setProgress('done'); });

      const skipButton = h('button', {
        class: 'btn ghost', type: 'button', disabled: busy || workoutDone,
      }, icon('x'), "Non fait aujourd'hui");
      skipButton.addEventListener('click', () => { void setProgress('skipped'); });

      let resetButton = null;
      if (dashboard.progress && status !== 'planned') {
        resetButton = h('button', { class: 'fit-reset', type: 'button', disabled: busy },
          icon('refresh'), 'Réinitialiser');
        resetButton.addEventListener('click', () => { void setProgress('planned', []); });
      }

      const completedCount = session.exercises.filter((exercise) => (
        Boolean(progressResult(dashboard, exercise)?.completed)
      )).length;
      const warmup = h('details', { class: 'fit-details' },
        h('summary', { text: `Échauffement · ${session.warmup.length} étapes` }),
        ...session.warmup.map((item) => h('p', { text: `${item.name} · ${prescription(item)}` })));
      const stretch = h('details', {
        class: 'fit-details',
        open: completedCount === session.exercises.length && session.exercises.length > 0,
      }, h('summary', { text: `Étirements de fin · ${session.stretches.length}` }),
      ...session.stretches.map((item) => h('p', { text: `${item.name} · ${prescription(item)}` })));

      return h('section', { class: `card fit-session${workoutDone ? ' complete' : ''}` },
        h('div', { class: 'fit-session-head' },
          h('div', {},
            h('span', { text: `Séance du jour · ${DAYS[session.day_of_week]}` }),
            h('p', { class: 'fit-session-title', text: session.title })),
          h('b', {
            class: `fit-status ${status}`,
            text: workoutDone ? 'FAIT' : status === 'skipped' ? 'NON FAIT' : status === 'in_progress' ? 'EN COURS' : 'À FAIRE',
          })),
        h('p', { class: 'cs', text: session.description || '' }),
        warmup,
        h('div', { class: 'fit-exercises' }, ...exercises),
        stretch,
        h('div', { class: 'fit-actions fit-session-actions' }, doneButton, skipButton),
        resetButton,
        session.notes ? h('p', { class: 'fit-note', text: session.notes }) : null);
    }

    function adviceCard() {
      const button = h('button', { class: 'btn block', type: 'button', disabled: busy },
        busy ? 'Analyse…' : 'Analyser ma journée');
      button.addEventListener('click', () => { void getAdvice(); });
      return h('section', { class: 'card fit-advice' },
        h('div', { class: 'fit-card-head' },
          h('p', { class: 'ct', text: 'Conseil JARVIS' }),
          advice ? h('span', {
            class: 'fit-source',
            text: advice.source === 'ai' ? 'IA' : 'hors ligne',
          }) : null),
        h('p', {
          class: 'cs fit-advice-copy',
          text: advice ? advice.text : 'Demandez une recommandation fondée sur la séance, l’alimentation, l’eau et votre progression du jour.',
        }),
        button);
    }

    function mealCard() {
      const addButton = h('button', {
        class: 'round fit-round',
        type: 'button',
        'aria-label': mealFormOpen ? 'Fermer le formulaire repas' : 'Ajouter un repas',
      }, icon(mealFormOpen ? 'x' : 'plus'));
      addButton.addEventListener('click', () => { mealFormOpen = !mealFormOpen; render(); });

      let form = null;
      if (mealFormOpen) {
        const type = select(Object.entries(MEALS), 'dejeuner', { 'aria-label': 'Type de repas' });
        const description = textarea({
          placeholder: 'Ce que vous avez mangé (texte libre ou détail manuel)',
          'aria-label': 'Description du repas',
        });
        const calories = input({
          type: 'number', inputmode: 'numeric', min: '0',
          placeholder: 'kcal (manuel)', 'aria-label': 'Calories',
        });
        const protein = input({
          type: 'number', inputmode: 'decimal', min: '0',
          placeholder: 'protéines g', 'aria-label': 'Protéines',
        });
        const analyze = h('button', { class: 'btn ghost', type: 'button' }, 'Analyser (IA)');
        const save = h('button', { class: 'btn primary', type: 'button' }, 'Manuel');

        async function submitMeal(mode) {
          const text = description.value.trim();
          if (!text || busy) return;
          busy = true;
          analyze.disabled = true;
          save.disabled = true;
          mutationError = null;
          try {
            if (mode === 'ai') {
              await api.createMealFromText({
                date: dashboard.date,
                text,
                meal_type: type.value,
                source: 'pwa',
                save: true,
              });
            } else {
              await api.createMeal({
                date: dashboard.date,
                meal_type: type.value,
                description: text,
                calories_estimate: calories.value ? Number(calories.value) : null,
                protein_g: protein.value ? Number(protein.value) : null,
                source: 'pwa',
              });
            }
            mealFormOpen = false;
          } catch {
            mutationError = mode === 'ai'
              ? 'Analyse alimentaire impossible.'
              : 'Repas non enregistré.';
          }
          busy = false;
          await load();
        }

        analyze.addEventListener('click', () => { void submitMeal('ai'); });
        save.addEventListener('click', () => { void submitMeal('manual'); });
        form = h('div', { class: 'fit-form' },
          type,
          description,
          h('div', { class: 'fit-two' }, calories, protein),
          h('div', { class: 'fit-actions' }, analyze, save));
      }

      const meals = dashboard.meals.length
        ? dashboard.meals.map((meal) => h('div', { class: 'fit-meal' },
          h('div', {},
            h('span', { text: MEALS[meal.meal_type] || 'Repas' }),
            h('strong', { text: `${meal.calories_estimate ?? '?'} kcal · ${meal.protein_g ?? '?'} g` })),
          h('p', { text: meal.description })))
        : [h('p', { class: 'fit-empty-copy', text: "Aucun repas noté aujourd'hui." })];

      return h('section', { class: 'card' },
        h('div', { class: 'fit-card-head' },
          h('div', {},
            h('p', { class: 'ct', text: 'Alimentation' }),
            h('p', { class: 'cs', text: `${dashboard.meals.length} repas aujourd'hui` })),
          addButton),
        form,
        h('div', { class: 'fit-meals' }, ...meals));
    }

    function waterCard() {
      const buttons = [250, 500, 750].map((amount) => {
        const button = h('button', {
          class: 'btn ghost', type: 'button', text: `+${amount} ml`, disabled: busy,
        });
        button.addEventListener('click', () => { void addWater(amount); });
        return button;
      });
      return h('section', { class: 'card fit-compact-card' },
        h('p', { class: 'ct', text: 'Eau' }),
        h('p', { class: 'cs', text: `${(dashboard.summary.water_ml / 1000).toFixed(1)} L aujourd'hui` }),
        h('div', { class: 'fit-actions' }, ...buttons));
    }

    function weightCard() {
      const latest = dashboard.latest_weight;
      const weight = input({
        type: 'number',
        inputmode: 'decimal',
        min: '20.1',
        max: '400',
        step: '0.1',
        placeholder: latest ? `${latest.weight_kg} kg` : 'Poids en kg',
        'aria-label': 'Poids en kilogrammes',
      });
      const feedback = h('p', { class: 'fit-inline-error' });
      const save = h('button', { class: 'btn fit-weight-save', type: 'button', disabled: busy }, 'Enregistrer');
      save.addEventListener('click', async () => {
        const value = Number(weight.value);
        if (!value || busy) return;
        busy = true;
        save.disabled = true;
        mutationError = null;
        try {
          await api.post('/api/fitness/weights', {
            date: dashboard.date,
            weight_kg: value,
            source: 'pwa',
          });
          weight.value = '';
        } catch {
          feedback.textContent = 'Pesée non enregistrée.';
        }
        busy = false;
        await load();
      });
      return h('section', { class: 'card fit-compact-card' },
        h('p', { class: 'ct', text: 'Pesée hebdomadaire' }),
        h('p', {
          class: 'cs',
          text: latest ? `Dernière mesure : ${latest.weight_kg} kg le ${latest.date}` : 'Aucune pesée enregistrée.',
        }),
        h('div', { class: 'fit-weight-row' }, weight, save),
        feedback);
    }

    function todayView() {
      const program = dashboard.program;
      const summary = dashboard.summary;
      const protein = dashboard.meals.reduce((sum, meal) => sum + (meal.protein_g || 0), 0);
      return [
        h('div', { class: 'fit-metrics' },
          metric('Semaine', `${dashboard.weekly_done}/${dashboard.weekly_target}`, `${dashboard.current_streak_weeks} semaine(s) de série`, ratio(dashboard.weekly_done, dashboard.weekly_target)),
          metric('Calories', summary.calories_estimate.toLocaleString('fr-FR'), `Cible ${program.calories_min}–${program.calories_max} kcal`, ratio(summary.calories_estimate, program.calories_min)),
          metric('Protéines', `${Math.round(protein)} g`, `Cible ${program.protein_min_g}–${program.protein_max_g} g`, ratio(protein, program.protein_min_g)),
          metric('Hydratation', `${(summary.water_ml / 1000).toFixed(1)} L`, `${summary.meal_count} repas journalisé(s)`, ratio(summary.water_ml, 2000))),
        sessionCard(),
        adviceCard(),
        mealCard(),
        h('div', { class: 'fit-compact-grid' }, waterCard(), weightCard()),
      ];
    }

    function settingsModal() {
      if (!settingsOpen) return null;
      const program = dashboard.program;
      const fields = new Map();
      const fieldNodes = PROGRAM_NUMBER_FIELDS.map(([key, label, min, max]) => {
        const node = input({
          type: 'number', value: String(program[key] ?? ''), min, max, 'aria-label': label,
        });
        fields.set(key, node);
        return h('label', { class: 'fit-label' }, h('span', { text: label }), node);
      });
      const reminderTime = input({ type: 'time', value: program.reminder_time || '', 'aria-label': 'Premier rappel' });
      const reminders = input({ type: 'checkbox', checked: Boolean(program.reminders_enabled) });
      const mealTracking = input({ type: 'checkbox', checked: Boolean(program.meal_tracking_enabled) });
      const feedback = h('p', { class: 'fit-inline-error' });
      const save = h('button', { class: 'btn primary block', type: 'button' }, 'Enregistrer les réglages');
      save.addEventListener('click', async () => {
        if (busy) return;
        const validation = validateProgramSettings(fields, reminderTime);
        if (validation.error) {
          feedback.textContent = validation.error;
          return;
        }
        busy = true;
        save.disabled = true;
        try {
          const payload = validation.payload;
          payload.reminders_enabled = reminders.checked;
          payload.meal_tracking_enabled = mealTracking.checked;
          await api.patch('/api/fitness/program', payload);
          settingsOpen = false;
          mutationError = null;
          busy = false;
          await load();
        } catch {
          busy = false;
          save.disabled = false;
          feedback.textContent = 'Réglages non enregistrés.';
        }
      });
      const close = h('button', { class: 'round fit-modal-close', type: 'button', 'aria-label': 'Fermer' }, icon('x'));
      close.addEventListener('click', () => { settingsOpen = false; render(); });
      return h('div', { class: 'fit-modal', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Objectifs et rappels' },
        h('div', { class: 'fit-modal-sheet' },
          h('div', { class: 'fit-modal-head' }, h('h2', { text: 'Objectifs et rappels' }), close),
          h('div', { class: 'fit-settings-grid' }, ...fieldNodes,
            h('label', { class: 'fit-label' }, h('span', { text: 'Premier rappel' }), reminderTime)),
          h('label', { class: 'fit-toggle' }, h('span', { text: 'Relances séance à voix haute' }), reminders),
          h('label', { class: 'fit-toggle' }, h('span', { text: 'Questions sur les repas' }), mealTracking),
          feedback,
          save));
    }

    function sessionEditorModal() {
      if (!editingSession) return null;
      const session = editingSession;
      const title = input({ value: session.title, 'aria-label': 'Titre de la séance' });
      const day = select(DAYS.map((label, index) => [String(index), label]), session.day_of_week, { 'aria-label': 'Jour de la séance' });
      const description = input({ value: session.description || '', 'aria-label': 'Description de la séance' });
      const warmup = textarea({ 'aria-label': 'Échauffement' }, editorLines(session.warmup));
      const exercises = textarea({ 'aria-label': 'Exercices' }, editorLines(session.exercises));
      const stretches = textarea({ 'aria-label': 'Étirements' }, editorLines(session.stretches));
      const notes = textarea({ 'aria-label': 'Notes' }, session.notes || '');
      exercises.classList.add('fit-area-tall');
      const feedback = h('p', { class: 'fit-inline-error' });
      const save = h('button', { class: 'btn primary block', type: 'button' }, 'Enregistrer la séance');
      save.addEventListener('click', async () => {
        if (!title.value.trim() || busy) return;
        const parsedWarmup = parseEditorLines(warmup.value, session.warmup);
        const parsedExercises = parseEditorLines(exercises.value, session.exercises);
        const parsedStretches = parseEditorLines(stretches.value, session.stretches);
        if ([...parsedWarmup, ...parsedExercises, ...parsedStretches].some((item) => !item.name)) {
          feedback.textContent = 'Chaque ligne doit commencer par un nom.';
          return;
        }
        busy = true;
        save.disabled = true;
        try {
          await api.patch(`/api/fitness/program/sessions/${session.id}`, {
            title: title.value.trim(),
            description: description.value.trim() || null,
            day_of_week: Number(day.value),
            notes: notes.value.trim() || null,
            warmup: parsedWarmup,
            exercises: parsedExercises,
            stretches: parsedStretches,
          });
          editingSession = null;
          mutationError = null;
          busy = false;
          await load();
        } catch {
          busy = false;
          save.disabled = false;
          feedback.textContent = 'Séance non enregistrée.';
        }
      });
      const close = h('button', { class: 'round fit-modal-close', type: 'button', 'aria-label': 'Fermer' }, icon('x'));
      close.addEventListener('click', () => { editingSession = null; render(); });
      return h('div', { class: 'fit-modal', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Modifier la séance' },
        h('div', { class: 'fit-modal-sheet fit-editor-sheet' },
          h('div', { class: 'fit-modal-head' }, h('h2', { text: 'Modifier la séance' }), close),
          h('div', { class: 'fit-two' },
            h('label', { class: 'fit-label' }, h('span', { text: 'Titre' }), title),
            h('label', { class: 'fit-label' }, h('span', { text: 'Jour' }), day)),
          h('label', { class: 'fit-label' }, h('span', { text: 'Description' }), description),
          h('p', { class: 'fit-format-help', text: 'Format : nom | séries | répétitions | secondes | côtés.' }),
          h('label', { class: 'fit-label' }, h('span', { text: 'Échauffement' }), warmup),
          h('label', { class: 'fit-label' }, h('span', { text: 'Exercices' }), exercises),
          h('label', { class: 'fit-label' }, h('span', { text: 'Étirements' }), stretches),
          h('label', { class: 'fit-label' }, h('span', { text: 'Notes' }), notes),
          feedback,
          save));
    }

    function programView() {
      const program = dashboard.program;
      const settings = h('button', { class: 'btn ghost block', type: 'button' }, 'Objectifs et rappels');
      settings.addEventListener('click', () => { settingsOpen = true; render(); });
      const cards = program.sessions.map((session) => {
        const edit = h('button', { class: 'round fit-edit', type: 'button', 'aria-label': `Modifier ${session.title}` }, '✎');
        edit.addEventListener('click', () => { editingSession = session; render(); });
        return h('article', { class: 'card fit-program-card' },
          h('div', { class: 'fit-card-head fit-program-head' },
            h('div', {},
              h('span', { class: 'fit-program-day', text: `${DAYS[session.day_of_week]} · Séance ${session.position}` }),
              h('p', { class: 'fit-program-title', text: session.title }),
              h('p', { class: 'cs', text: session.description || '' })),
            edit),
          h('div', { class: 'fit-program-exercises' },
            ...session.exercises.map((exercise) => h('div', {},
              h('span', { text: exercise.name }),
              h('strong', { text: prescription(exercise) })))),
          h('details', { class: 'fit-program-details' },
            h('summary', { text: 'Échauffement et étirements' }),
            h('p', { text: `Échauffement : ${session.warmup.map((item) => item.name).join(', ') || '—'}` }),
            h('p', { text: `Étirements : ${session.stretches.map((item) => item.name).join(', ') || '—'}` })));
      });
      return [
        h('section', { class: 'card fit-program-intro' },
          h('p', { class: 'fit-program-name', text: program.name }),
          h('p', { class: 'cs', text: `${program.sessions.length} séances prévues · minimum ${program.weekly_min_sessions} · natation occasionnelle compatible` }),
          settings),
        ...cards,
        h('section', { class: 'card fit-swim-note' },
          h('strong', { text: 'Natation. ' }),
          h('span', { text: 'Évitez-la juste avant la séance jambes et compensez toujours la dépense cardio par un repas complet après la nage.' })),
      ];
    }

    function render() {
      if (!alive) return;
      ctx.setHeader('Fitness', dashboard ? `${dashboard.weekly_done}/${dashboard.weekly_target} séances cette semaine` : 'Programme personnel', [
        { icon: 'refresh', label: 'Actualiser', onClick: () => { void load(); } },
      ]);
      ctx.setDock(null);
      if (!dashboard) {
        ctx.setBody(networkError ? banner(networkError, 'err') : skeleton(5));
        return;
      }
      const error = mutationError || networkError;
      ctx.setBody(
        error ? banner(error, 'err') : null,
        h('div', { class: 'pad fit-pad' },
          h('section', { class: 'fit-hero' },
            h('p', { class: 'fit-kicker', text: 'Fitness connecté' }),
            h('h2', { text: 'Votre entraînement, suivi par JARVIS' }),
            h('p', { text: dashboard.program.goal })),
          segmentSwitch(),
          ...(activeTab === 'today' ? todayView() : programView())),
        settingsModal(),
        sessionEditorModal(),
      );
    }

    ctx.setHeader('Fitness');
    ctx.setBody(skeleton(5));
    await load();
    return () => { alive = false; };
  },
};
