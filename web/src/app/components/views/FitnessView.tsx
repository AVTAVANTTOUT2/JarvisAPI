import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Apple,
  BellRing,
  BrainCircuit,
  Check,
  ChevronDown,
  Dumbbell,
  Droplets,
  Flame,
  Gauge,
  Loader2,
  Pencil,
  Plus,
  RotateCcw,
  Save,
  Scale,
  Sparkles,
  Target,
  X,
} from 'lucide-react';
import { api } from '@unified/lib/api';

type SessionStatus = 'planned' | 'in_progress' | 'done' | 'skipped';

interface ProgramExercise {
  name: string;
  sets: number | null;
  reps: string | null;
  duration_sec: number | string | null;
  sides: number | null;
  progression: string | null;
}

interface ExerciseResult {
  name: string;
  completed: boolean;
  sets_done: number | null;
  reps_done: string | null;
  duration_sec: number | null;
  notes: string | null;
}

interface ProgramSession {
  id: number;
  position: number;
  day_of_week: number;
  type: string;
  title: string;
  description: string | null;
  warmup: ProgramExercise[];
  exercises: ProgramExercise[];
  stretches: ProgramExercise[];
  notes: string | null;
  active: boolean;
}

interface FitnessProgram {
  id: number;
  name: string;
  goal: string;
  weekly_min_sessions: number;
  calories_min: number;
  calories_max: number;
  protein_min_g: number;
  protein_max_g: number;
  reminders_enabled: boolean;
  reminder_time: string;
  reminder_interval_min: number;
  meal_tracking_enabled: boolean;
  sessions: ProgramSession[];
}

interface SessionProgress {
  id: number;
  status: SessionStatus;
  exercise_results: ExerciseResult[];
  duration_min: number | null;
  perceived_effort: number | null;
}

interface Meal {
  id: number;
  meal_type: string | null;
  description: string;
  calories_estimate: number | null;
  protein_g: number | null;
}

interface FitnessDashboard {
  date: string;
  program: FitnessProgram;
  scheduled_session: ProgramSession | null;
  progress: SessionProgress | null;
  summary: {
    workout_done: boolean;
    meal_count: number;
    calories_estimate: number;
    water_ml: number;
  };
  weekly_done: number;
  weekly_target: number;
  current_streak_weeks: number;
  next_session: ProgramSession | null;
  meals: Meal[];
  latest_weight: { weight_kg: number; date: string } | null;
}

const DAYS = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche'];
const MEAL_LABELS: Record<string, string> = {
  petit_dej: 'Petit déjeuner',
  dejeuner: 'Déjeuner',
  diner: 'Dîner',
  collation: 'Collation',
};

function localIsoDate(): string {
  const now = new Date();
  const offset = now.getTimezoneOffset() * 60_000;
  return new Date(now.getTime() - offset).toISOString().slice(0, 10);
}

function prescription(exercise: ProgramExercise): string {
  const parts: string[] = [];
  if (exercise.sets) parts.push(`${exercise.sets} séries`);
  if (exercise.reps) parts.push(`${exercise.reps} reps`);
  if (exercise.duration_sec) parts.push(`${exercise.duration_sec} s`);
  if (exercise.sides === 2) parts.push('de chaque côté');
  return parts.join(' · ') || 'À la sensation';
}

function ratio(value: number, target: number): number {
  if (!target) return 0;
  return Math.max(0, Math.min(100, Math.round((value / target) * 100)));
}

function Metric({
  icon: Icon,
  label,
  value,
  detail,
  progress,
}: {
  icon: typeof Target;
  label: string;
  value: string;
  detail: string;
  progress?: number;
}) {
  return (
    <div className="glass-panel rounded-2xl p-4">
      <div className="flex items-center justify-between text-white/50">
        <span className="text-xs font-mono uppercase tracking-wider">{label}</span>
        <Icon size={16} />
      </div>
      <p className="mt-3 text-2xl font-mono tabular-nums">{value}</p>
      <p className="mt-1 text-xs text-white/40">{detail}</p>
      {progress !== undefined && (
        <div className="mt-3 h-1.5 rounded-full bg-white/5 overflow-hidden">
          <div className="h-full rounded-full bg-white transition-all" style={{ width: `${progress}%` }} />
        </div>
      )}
    </div>
  );
}

export function FitnessView() {
  const [dashboard, setDashboard] = useState<FitnessDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<'today' | 'program'>('today');
  const [advice, setAdvice] = useState<{ text: string; source: string } | null>(null);
  const [adviceLoading, setAdviceLoading] = useState(false);
  const [mealOpen, setMealOpen] = useState(false);
  const [mealType, setMealType] = useState('dejeuner');
  const [mealDescription, setMealDescription] = useState('');
  const [mealCalories, setMealCalories] = useState('');
  const [mealProtein, setMealProtein] = useState('');
  const [weight, setWeight] = useState('');
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settings, setSettings] = useState<Partial<FitnessProgram>>({});
  const [editSession, setEditSession] = useState<ProgramSession | null>(null);
  const [editTitle, setEditTitle] = useState('');
  const [editDescription, setEditDescription] = useState('');
  const [editDay, setEditDay] = useState(0);
  const [editNotes, setEditNotes] = useState('');
  const [editWarmup, setEditWarmup] = useState('');
  const [editExercises, setEditExercises] = useState('');
  const [editStretches, setEditStretches] = useState('');

  const load = useCallback(async () => {
    setError(null);
    try {
      const data = await api.getFitnessDashboard(localIsoDate()) as FitnessDashboard;
      setDashboard(data);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Fitness indisponible');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const results = useMemo(() => {
    const map = new Map<string, ExerciseResult>();
    for (const item of dashboard?.progress?.exercise_results ?? []) map.set(item.name, item);
    return map;
  }, [dashboard]);

  const proteinTotal = useMemo(
    () => dashboard?.meals.reduce((sum, meal) => sum + (meal.protein_g ?? 0), 0) ?? 0,
    [dashboard],
  );

  async function saveProgress(status: SessionStatus, nextResults?: ExerciseResult[]) {
    const session = dashboard?.scheduled_session;
    if (!session) return;
    setSaving(true);
    setError(null);
    try {
      await api.updateFitnessSession(session.id, {
        date: dashboard.date,
        status,
        exercise_results: nextResults ?? Array.from(results.values()),
      });
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Modification non enregistrée');
    } finally {
      setSaving(false);
    }
  }

  function toggleExercise(exercise: ProgramExercise) {
    const current = results.get(exercise.name);
    const next = new Map(results);
    next.set(exercise.name, {
      name: exercise.name,
      completed: !(current?.completed ?? false),
      sets_done: current?.sets_done ?? null,
      reps_done: current?.reps_done ?? null,
      duration_sec: current?.duration_sec ?? null,
      notes: current?.notes ?? null,
    });
    void saveProgress('in_progress', Array.from(next.values()));
  }

  async function addMeal() {
    if (!mealDescription.trim()) return;
    setSaving(true);
    try {
      await api.addFitnessMeal({
        date: dashboard?.date ?? localIsoDate(),
        meal_type: mealType,
        description: mealDescription.trim(),
        calories_estimate: mealCalories ? Number(mealCalories) : null,
        protein_g: mealProtein ? Number(mealProtein) : null,
        source: 'pwa',
      });
      setMealDescription('');
      setMealCalories('');
      setMealProtein('');
      setMealOpen(false);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Repas non enregistré');
    } finally {
      setSaving(false);
    }
  }

  async function addWater(amount: number) {
    setSaving(true);
    try {
      await api.addFitnessWater({ date: dashboard?.date ?? localIsoDate(), amount_ml: amount, source: 'pwa' });
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Eau non enregistrée');
    } finally {
      setSaving(false);
    }
  }

  async function addWeight() {
    const value = Number(weight);
    if (!value) return;
    setSaving(true);
    try {
      await api.addFitnessWeight({ date: dashboard?.date ?? localIsoDate(), weight_kg: value, source: 'pwa' });
      setWeight('');
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Pesée non enregistrée');
    } finally {
      setSaving(false);
    }
  }

  async function generateAdvice() {
    setAdviceLoading(true);
    try {
      const result = await api.getFitnessAdvice(dashboard?.date) as { text: string; source: string };
      setAdvice(result);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Conseil indisponible');
    } finally {
      setAdviceLoading(false);
    }
  }

  function openSettings() {
    if (!dashboard) return;
    setSettings({
      weekly_min_sessions: dashboard.program.weekly_min_sessions,
      calories_min: dashboard.program.calories_min,
      calories_max: dashboard.program.calories_max,
      protein_min_g: dashboard.program.protein_min_g,
      protein_max_g: dashboard.program.protein_max_g,
      reminders_enabled: dashboard.program.reminders_enabled,
      reminder_time: dashboard.program.reminder_time,
      reminder_interval_min: dashboard.program.reminder_interval_min,
      meal_tracking_enabled: dashboard.program.meal_tracking_enabled,
    });
    setSettingsOpen(true);
  }

  async function saveSettings() {
    setSaving(true);
    try {
      await api.updateFitnessProgram(settings as Record<string, unknown>);
      setSettingsOpen(false);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Réglages non enregistrés');
    } finally {
      setSaving(false);
    }
  }

  function openSessionEditor(session: ProgramSession) {
    const serialize = (items: ProgramExercise[]) => items.map(item => (
      `${item.name} | ${item.sets ?? ''} | ${item.reps ?? ''} | ${item.duration_sec ?? ''} | ${item.sides ?? ''}`
    )).join('\n');
    setEditSession(session);
    setEditTitle(session.title);
    setEditDescription(session.description ?? '');
    setEditDay(session.day_of_week);
    setEditNotes(session.notes ?? '');
    setEditWarmup(serialize(session.warmup));
    setEditExercises(serialize(session.exercises));
    setEditStretches(serialize(session.stretches));
  }

  async function saveSessionEditor() {
    if (!editSession) return;
    const parse = (text: string, previousItems: ProgramExercise[]) => text
      .split('\n')
      .map(line => line.trim())
      .filter(Boolean)
      .map(line => {
        const [name = '', sets = '', reps = '', duration = '', sides = ''] = line
          .split('|')
          .map(part => part.trim());
        const previous = previousItems.find(item => item.name === name);
        return {
          name,
          sets: sets && Number(sets) > 0 ? Number(sets) : null,
          reps: reps || null,
          duration_sec: duration ? (/^\d+$/.test(duration) ? Number(duration) : duration) : null,
          sides: sides && Number(sides) > 0 ? Number(sides) : null,
          progression: previous?.progression ?? null,
        };
      });
    const warmup = parse(editWarmup, editSession.warmup);
    const exercises = parse(editExercises, editSession.exercises);
    const stretches = parse(editStretches, editSession.stretches);
    if ([...warmup, ...exercises, ...stretches].some(item => !item.name)) return;
    setSaving(true);
    try {
      await api.updateFitnessProgramSession(editSession.id, {
        title: editTitle,
        description: editDescription || undefined,
        day_of_week: editDay,
        notes: editNotes || undefined,
        warmup,
        exercises,
        stretches,
      });
      setEditSession(null);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Séance non enregistrée');
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return <div className="min-h-[60vh] grid place-items-center text-white/40"><Loader2 className="animate-spin" /></div>;
  }
  if (!dashboard) {
    return <div className="p-8 text-red-300">{error ?? 'Fitness indisponible.'}</div>;
  }

  const { program, scheduled_session: session, progress, summary } = dashboard;
  const exerciseDone = session?.exercises.filter(item => results.get(item.name)?.completed).length ?? 0;
  const workoutDone = progress?.status === 'done';

  return (
    <div className="p-4 sm:p-6 lg:p-8 space-y-6 bg-grid-pattern min-h-full">
      <header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <div className="flex items-center gap-2 text-white/50 text-xs font-mono uppercase tracking-[0.18em]">
            <Dumbbell size={15} /> Fitness connecté
          </div>
          <h1 className="mt-2 text-3xl tracking-tight">Votre entraînement, suivi par JARVIS</h1>
          <p className="mt-1 text-sm text-white/45">{program.goal}</p>
        </div>
        <div className="flex items-center rounded-xl border border-white/10 bg-black/30 p-1">
          {([['today', "Aujourd'hui"], ['program', 'Programme']] as const).map(([key, label]) => (
            <button key={key} onClick={() => setTab(key)} className={`px-4 py-2 rounded-lg text-xs font-mono transition ${tab === key ? 'bg-white text-black' : 'text-white/50 hover:text-white'}`}>
              {label}
            </button>
          ))}
        </div>
      </header>

      {error && <div className="rounded-xl border border-red-400/30 bg-red-400/10 px-4 py-3 text-sm text-red-200">{error}</div>}

      {tab === 'today' ? (
        <>
          <section className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <Metric icon={Target} label="Semaine" value={`${dashboard.weekly_done}/${dashboard.weekly_target}`} detail={`${dashboard.current_streak_weeks} semaine(s) de série`} progress={ratio(dashboard.weekly_done, dashboard.weekly_target)} />
            <Metric icon={Flame} label="Calories" value={summary.calories_estimate.toLocaleString('fr-FR')} detail={`Cible ${program.calories_min}–${program.calories_max} kcal`} progress={ratio(summary.calories_estimate, program.calories_min)} />
            <Metric icon={Gauge} label="Protéines" value={`${Math.round(proteinTotal)} g`} detail={`Cible ${program.protein_min_g}–${program.protein_max_g} g`} progress={ratio(proteinTotal, program.protein_min_g)} />
            <Metric icon={Droplets} label="Hydratation" value={`${(summary.water_ml / 1000).toFixed(1)} L`} detail={`${summary.meal_count} repas journalisé(s)`} progress={ratio(summary.water_ml, 2000)} />
          </section>

          <section className="grid grid-cols-1 xl:grid-cols-[minmax(0,1.55fr)_minmax(320px,.75fr)] gap-5">
            <div className={`rounded-3xl border p-5 sm:p-6 ${workoutDone ? 'border-emerald-400/30 bg-emerald-400/[0.06]' : 'border-white/10 bg-white/[0.035]'}`}>
              {session ? (
                <>
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <p className="text-xs font-mono uppercase tracking-wider text-white/40">Séance du jour · {DAYS[session.day_of_week]}</p>
                      <h2 className="mt-2 text-2xl">{session.title}</h2>
                      <p className="mt-1 text-sm text-white/45">{session.description}</p>
                    </div>
                    <span className={`shrink-0 rounded-full px-3 py-1 text-xs font-mono ${workoutDone ? 'bg-emerald-400/15 text-emerald-300' : progress?.status === 'skipped' ? 'bg-red-400/15 text-red-300' : 'bg-amber-400/15 text-amber-200'}`}>
                      {workoutDone ? 'FAIT' : progress?.status === 'skipped' ? 'NON FAIT' : progress?.status === 'in_progress' ? 'EN COURS' : 'À FAIRE'}
                    </span>
                  </div>

                  <details className="mt-5 rounded-xl border border-white/8 bg-black/20 group">
                    <summary className="cursor-pointer list-none flex items-center justify-between px-4 py-3 text-sm text-white/65">
                      Échauffement · {session.warmup.length} étapes <ChevronDown size={15} className="group-open:rotate-180 transition" />
                    </summary>
                    <div className="px-4 pb-4 space-y-2">{session.warmup.map(item => <p key={item.name} className="text-sm text-white/45">{item.name} <span className="font-mono text-white/25">{prescription(item)}</span></p>)}</div>
                  </details>

                  <div className="mt-4 space-y-2">
                    {session.exercises.map(exercise => {
                      const done = results.get(exercise.name)?.completed ?? false;
                      return (
                        <button key={exercise.name} disabled={saving || workoutDone} onClick={() => toggleExercise(exercise)} className={`w-full text-left rounded-xl border px-4 py-3.5 flex items-start gap-3 transition ${done ? 'border-emerald-400/25 bg-emerald-400/[0.07]' : 'border-white/8 bg-black/15 hover:border-white/20'}`}>
                          <span className={`mt-0.5 w-6 h-6 rounded-lg border grid place-items-center shrink-0 ${done ? 'bg-emerald-400 border-emerald-400 text-black' : 'border-white/25'}`}>{done && <Check size={15} strokeWidth={3} />}</span>
                          <span className="min-w-0 flex-1">
                            <span className={`block text-sm font-medium ${done ? 'text-white/45 line-through' : ''}`}>{exercise.name}</span>
                            <span className="block mt-0.5 text-xs font-mono text-white/35">{prescription(exercise)}</span>
                            {exercise.progression && <span className="block mt-1 text-xs text-white/30">Progression : {exercise.progression}</span>}
                          </span>
                        </button>
                      );
                    })}
                  </div>

                  <details className="mt-4 rounded-xl border border-white/8 bg-black/20 group" open={exerciseDone === session.exercises.length}>
                    <summary className="cursor-pointer list-none flex items-center justify-between px-4 py-3 text-sm text-white/65">
                      Étirements de fin · {session.stretches.length} <ChevronDown size={15} className="group-open:rotate-180 transition" />
                    </summary>
                    <div className="px-4 pb-4 space-y-2">{session.stretches.map(item => <p key={item.name} className="text-sm text-white/45">{item.name} <span className="font-mono text-white/25">{prescription(item)}</span></p>)}</div>
                  </details>

                  <div className="mt-5 flex flex-wrap gap-2">
                    <button disabled={saving || workoutDone} onClick={() => void saveProgress('done')} className="inline-flex items-center gap-2 rounded-xl bg-white text-black px-4 py-2.5 text-sm font-medium disabled:opacity-40">
                      {saving ? <Loader2 size={16} className="animate-spin" /> : <Check size={16} />} Marquer comme fait
                    </button>
                    <button disabled={saving || workoutDone} onClick={() => void saveProgress('skipped')} className="inline-flex items-center gap-2 rounded-xl border border-white/12 px-4 py-2.5 text-sm text-white/50 hover:text-white disabled:opacity-40">
                      <X size={16} /> Non fait aujourd'hui
                    </button>
                    {progress && progress.status !== 'planned' && (
                      <button disabled={saving} onClick={() => void saveProgress('planned', [])} className="inline-flex items-center gap-2 rounded-xl px-3 py-2.5 text-xs text-white/35 hover:text-white"><RotateCcw size={14} /> Réinitialiser</button>
                    )}
                  </div>
                  {session.notes && <p className="mt-4 border-l-2 border-white/15 pl-3 text-xs text-white/35">{session.notes}</p>}
                </>
              ) : (
                <div className="py-12 text-center">
                  <Sparkles className="mx-auto text-white/25" />
                  <h2 className="mt-3 text-xl">Jour de récupération</h2>
                  <p className="mt-1 text-sm text-white/40">Prochaine séance : {dashboard.next_session?.title ?? 'à planifier'}.</p>
                </div>
              )}
            </div>

            <aside className="space-y-4">
              <div className="glass-panel rounded-2xl p-5">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2"><BrainCircuit size={17} /><h3>Conseil JARVIS</h3></div>
                  {advice && <span className="text-[10px] font-mono uppercase text-white/30">{advice.source === 'ai' ? 'IA' : 'hors ligne'}</span>}
                </div>
                <p className="mt-3 text-sm leading-6 text-white/55">{advice?.text ?? 'Demandez une recommandation fondée sur la séance, l’alimentation, l’eau et votre progression du jour.'}</p>
                <button disabled={adviceLoading} onClick={() => void generateAdvice()} className="mt-4 w-full rounded-xl border border-white/12 py-2.5 text-xs font-mono hover:bg-white/5 disabled:opacity-40">
                  {adviceLoading ? <Loader2 size={14} className="animate-spin mx-auto" /> : 'Analyser ma journée'}
                </button>
              </div>

              <div className="glass-panel rounded-2xl p-5">
                <div className="flex items-center justify-between"><div className="flex items-center gap-2"><Apple size={17} /><h3>Alimentation</h3></div><button onClick={() => setMealOpen(value => !value)} className="w-8 h-8 rounded-lg border border-white/10 grid place-items-center"><Plus size={15} /></button></div>
                {mealOpen && (
                  <div className="mt-4 space-y-2">
                    <select value={mealType} onChange={event => setMealType(event.target.value)} className="w-full bg-black/30 border border-white/10 rounded-lg px-3 py-2 text-sm">
                      {Object.entries(MEAL_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                    </select>
                    <textarea value={mealDescription} onChange={event => setMealDescription(event.target.value)} placeholder="Ce que vous avez mangé" className="w-full min-h-20 bg-black/30 border border-white/10 rounded-lg px-3 py-2 text-sm resize-none" />
                    <div className="grid grid-cols-2 gap-2"><input value={mealCalories} onChange={event => setMealCalories(event.target.value)} inputMode="numeric" placeholder="kcal" className="bg-black/30 border border-white/10 rounded-lg px-3 py-2 text-sm" /><input value={mealProtein} onChange={event => setMealProtein(event.target.value)} inputMode="decimal" placeholder="protéines g" className="bg-black/30 border border-white/10 rounded-lg px-3 py-2 text-sm" /></div>
                    <button disabled={saving || !mealDescription.trim()} onClick={() => void addMeal()} className="w-full rounded-lg bg-white text-black py-2 text-sm disabled:opacity-40">Enregistrer</button>
                  </div>
                )}
                <div className="mt-4 space-y-2">
                  {dashboard.meals.length ? dashboard.meals.map(meal => <div key={meal.id} className="rounded-lg bg-black/20 px-3 py-2"><div className="flex justify-between gap-3 text-xs"><span className="text-white/55">{MEAL_LABELS[meal.meal_type ?? ''] ?? 'Repas'}</span><span className="font-mono text-white/30">{meal.calories_estimate ?? '?'} kcal · {meal.protein_g ?? '?'} g</span></div><p className="mt-1 text-sm text-white/40 line-clamp-2">{meal.description}</p></div>) : <p className="text-sm text-white/30">Aucun repas noté aujourd'hui.</p>}
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-1 gap-4">
                <div className="glass-panel rounded-2xl p-4"><div className="flex items-center gap-2 text-sm"><Droplets size={16} /> Eau</div><div className="mt-3 flex gap-2">{[250, 500, 750].map(amount => <button key={amount} disabled={saving} onClick={() => void addWater(amount)} className="flex-1 rounded-lg border border-white/10 py-2 text-xs font-mono hover:bg-white/5">+{amount} ml</button>)}</div></div>
                <div className="glass-panel rounded-2xl p-4"><div className="flex items-center gap-2 text-sm"><Scale size={16} /> Pesée hebdomadaire</div><div className="mt-3 flex gap-2"><input value={weight} onChange={event => setWeight(event.target.value)} inputMode="decimal" placeholder={dashboard.latest_weight ? `${dashboard.latest_weight.weight_kg} kg` : 'Poids kg'} className="min-w-0 flex-1 bg-black/30 border border-white/10 rounded-lg px-3 py-2 text-sm" /><button disabled={saving || !weight} onClick={() => void addWeight()} className="rounded-lg border border-white/10 px-3"><Save size={14} /></button></div></div>
              </div>
            </aside>
          </section>
        </>
      ) : (
        <section className="space-y-5">
          <div className="glass-panel rounded-2xl p-5 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
            <div><h2 className="text-xl">{program.name}</h2><p className="mt-1 text-sm text-white/40">4 séances prévues · minimum {program.weekly_min_sessions} · natation occasionnelle compatible</p></div>
            <button onClick={openSettings} className="inline-flex items-center gap-2 rounded-xl border border-white/12 px-4 py-2.5 text-sm hover:bg-white/5"><BellRing size={15} /> Objectifs et rappels</button>
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {program.sessions.map(item => (
              <article key={item.id} className="glass-panel rounded-2xl p-5">
                <div className="flex items-start justify-between gap-3"><div><p className="text-xs font-mono uppercase tracking-wider text-white/35">{DAYS[item.day_of_week]} · Séance {item.position}</p><h3 className="mt-1 text-xl">{item.title}</h3><p className="mt-1 text-sm text-white/40">{item.description}</p></div><button onClick={() => openSessionEditor(item)} className="w-9 h-9 rounded-lg border border-white/10 grid place-items-center text-white/45 hover:text-white"><Pencil size={15} /></button></div>
                <div className="mt-4 space-y-2">{item.exercises.map(exercise => <div key={exercise.name} className="flex justify-between gap-4 border-t border-white/6 pt-2 text-sm"><span className="text-white/65">{exercise.name}</span><span className="font-mono text-xs text-white/30 text-right">{prescription(exercise)}</span></div>)}</div>
                <details className="mt-4 group"><summary className="cursor-pointer list-none text-xs text-white/35 flex items-center gap-1">Échauffement et étirements <ChevronDown size={13} className="group-open:rotate-180" /></summary><div className="mt-2 text-xs text-white/35 space-y-1"><p>Échauffement : {item.warmup.map(value => value.name).join(', ')}</p><p>Étirements : {item.stretches.map(value => value.name).join(', ')}</p></div></details>
              </article>
            ))}
          </div>
          <div className="rounded-2xl border border-sky-400/15 bg-sky-400/[0.04] p-5 text-sm text-white/50"><strong className="text-white/75">Natation.</strong> Évitez-la juste avant la séance jambes et compensez toujours la dépense cardio par un repas complet après la nage.</div>
        </section>
      )}

      {settingsOpen && (
        <div className="fixed inset-0 z-50 bg-black/75 backdrop-blur-sm grid place-items-center p-4" onMouseDown={event => event.currentTarget === event.target && setSettingsOpen(false)}>
          <div className="w-full max-w-xl rounded-2xl border border-white/12 bg-[#111116] p-5 max-h-[90vh] overflow-auto">
            <div className="flex items-center justify-between"><h2 className="text-xl">Objectifs et rappels</h2><button onClick={() => setSettingsOpen(false)}><X size={18} /></button></div>
            <div className="mt-5 grid grid-cols-2 gap-3">
              {([['calories_min', 'Calories min'], ['calories_max', 'Calories max'], ['protein_min_g', 'Protéines min'], ['protein_max_g', 'Protéines max'], ['weekly_min_sessions', 'Séances minimum'], ['reminder_interval_min', 'Rappel toutes les min']] as const).map(([key, label]) => <label key={key} className="text-xs text-white/45">{label}<input type="number" value={String(settings[key] ?? '')} onChange={event => setSettings(value => ({ ...value, [key]: Number(event.target.value) }))} className="mt-1 w-full bg-black/30 border border-white/10 rounded-lg px-3 py-2 text-sm text-white" /></label>)}
              <label className="text-xs text-white/45">Premier rappel<input type="time" value={String(settings.reminder_time ?? '')} onChange={event => setSettings(value => ({ ...value, reminder_time: event.target.value }))} className="mt-1 w-full bg-black/30 border border-white/10 rounded-lg px-3 py-2 text-sm text-white" /></label>
            </div>
            <div className="mt-4 space-y-2">{([['reminders_enabled', 'Relances séance à voix haute'], ['meal_tracking_enabled', 'Questions sur les repas']] as const).map(([key, label]) => <label key={key} className="flex items-center justify-between rounded-lg border border-white/8 px-3 py-3 text-sm"><span>{label}</span><input type="checkbox" checked={Boolean(settings[key])} onChange={event => setSettings(value => ({ ...value, [key]: event.target.checked }))} /></label>)}</div>
            <button disabled={saving} onClick={() => void saveSettings()} className="mt-5 w-full rounded-xl bg-white text-black py-2.5 text-sm font-medium disabled:opacity-40">Enregistrer les réglages</button>
          </div>
        </div>
      )}

      {editSession && (
        <div className="fixed inset-0 z-50 bg-black/75 backdrop-blur-sm grid place-items-center p-4" onMouseDown={event => event.currentTarget === event.target && setEditSession(null)}>
          <div className="w-full max-w-2xl rounded-2xl border border-white/12 bg-[#111116] p-5 max-h-[90vh] overflow-auto">
            <div className="flex items-center justify-between"><h2 className="text-xl">Modifier la séance</h2><button onClick={() => setEditSession(null)}><X size={18} /></button></div>
            <div className="mt-5 grid sm:grid-cols-[1fr_180px] gap-3"><label className="text-xs text-white/45">Titre<input value={editTitle} onChange={event => setEditTitle(event.target.value)} className="mt-1 w-full bg-black/30 border border-white/10 rounded-lg px-3 py-2 text-sm text-white" /></label><label className="text-xs text-white/45">Jour<select value={editDay} onChange={event => setEditDay(Number(event.target.value))} className="mt-1 w-full bg-black/30 border border-white/10 rounded-lg px-3 py-2 text-sm text-white">{DAYS.map((day, index) => <option key={day} value={index}>{day}</option>)}</select></label></div>
            <label className="block mt-3 text-xs text-white/45">Description<input value={editDescription} onChange={event => setEditDescription(event.target.value)} className="mt-1 w-full bg-black/30 border border-white/10 rounded-lg px-3 py-2 text-sm text-white" /></label>
            <p className="mt-4 text-[11px] text-white/30">Format par ligne : nom | séries | répétitions | secondes | côtés. Laissez une colonne vide si elle ne s’applique pas.</p>
            <label className="block mt-3 text-xs text-white/45">Échauffement<textarea value={editWarmup} onChange={event => setEditWarmup(event.target.value)} className="mt-1 w-full min-h-28 bg-black/30 border border-white/10 rounded-lg px-3 py-2 text-sm font-mono text-white resize-y" /></label>
            <label className="block mt-3 text-xs text-white/45">Exercices<textarea value={editExercises} onChange={event => setEditExercises(event.target.value)} className="mt-1 w-full min-h-48 bg-black/30 border border-white/10 rounded-lg px-3 py-2 text-sm font-mono text-white resize-y" /></label>
            <label className="block mt-3 text-xs text-white/45">Étirements<textarea value={editStretches} onChange={event => setEditStretches(event.target.value)} className="mt-1 w-full min-h-28 bg-black/30 border border-white/10 rounded-lg px-3 py-2 text-sm font-mono text-white resize-y" /></label>
            <label className="block mt-3 text-xs text-white/45">Notes<textarea value={editNotes} onChange={event => setEditNotes(event.target.value)} className="mt-1 w-full min-h-20 bg-black/30 border border-white/10 rounded-lg px-3 py-2 text-sm text-white resize-y" /></label>
            <button disabled={saving || !editTitle.trim()} onClick={() => void saveSessionEditor()} className="mt-5 w-full rounded-xl bg-white text-black py-2.5 text-sm font-medium disabled:opacity-40">Enregistrer la séance</button>
          </div>
        </div>
      )}
    </div>
  );
}
