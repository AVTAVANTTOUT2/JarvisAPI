'use client';

import { useState } from 'react';
import {
  Check,
  Droplets,
  Dumbbell,
  HeartPulse,
  Loader2,
  Plus,
  Utensils,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

import { jarvisFetch } from '@unified/lib/api';

import { localTodayIso } from './types';
import type { MealType, WorkoutType } from './types';

interface Props {
  onLogged: () => void;
}

type Feedback = 'idle' | 'loading' | 'success' | 'error';

const fieldClass =
  'w-full rounded-[14px] border border-white/[0.07] bg-black/25 px-3 py-2.5 text-[13px] text-white outline-none placeholder:text-[#4d4d4d] focus:border-white/20';

async function postFitness(path: string, payload: Record<string, unknown>) {
  return jarvisFetch(path, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

function CardTitle({
  icon: Icon,
  title,
  subtitle,
}: {
  icon: LucideIcon;
  title: string;
  subtitle: string;
}) {
  return (
    <div className="flex items-center gap-3">
      <div className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-[12px] border border-white/[0.08] bg-white/[0.05]">
        <Icon size={17} className="text-white" />
      </div>
      <div>
        <h2 className="text-[15px] font-semibold text-white">{title}</h2>
        <p className="text-[11px] text-[#666]">{subtitle}</p>
      </div>
    </div>
  );
}

function SubmitButton({
  feedback,
  label = 'Enregistrer',
}: {
  feedback: Feedback;
  label?: string;
}) {
  return (
    <button
      type="submit"
      disabled={feedback === 'loading'}
      className="flex h-10 w-full items-center justify-center gap-2 rounded-[14px] border border-white/10 bg-white text-[12px] font-semibold text-black transition-transform active:scale-[0.98] disabled:opacity-50"
    >
      {feedback === 'loading' ? (
        <Loader2 size={14} className="animate-spin" />
      ) : feedback === 'success' ? (
        <Check size={14} />
      ) : (
        <Plus size={14} />
      )}
      {feedback === 'success' ? 'Enregistré' : label}
    </button>
  );
}

function FeedbackLine({ feedback }: { feedback: Feedback }) {
  if (feedback !== 'error') return null;
  return <p className="text-[11px] text-[#FF453A]">Enregistrement impossible. Réessaie.</p>;
}

export function FitnessForms({ onLogged }: Props) {
  return (
    <div className="space-y-3">
      <WorkoutForm onLogged={onLogged} />
      <MealForm onLogged={onLogged} />
      <WaterForm onLogged={onLogged} />
      <WellbeingForm onLogged={onLogged} />
    </div>
  );
}

function WorkoutForm({ onLogged }: Props) {
  const [type, setType] = useState<WorkoutType>('poussee');
  const [duration, setDuration] = useState('');
  const [exercise, setExercise] = useState('');
  const [sets, setSets] = useState('');
  const [reps, setReps] = useState('');
  const [feedback, setFeedback] = useState<Feedback>('idle');

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setFeedback('loading');
    try {
      await postFitness('/api/fitness/workouts', {
        date: localTodayIso(),
        type,
        duration_min: duration ? Number(duration) : null,
        exercises_json: exercise.trim()
          ? [
              {
                name: exercise.trim(),
                ...(sets ? { sets: Number(sets) } : {}),
                ...(reps ? { reps: Number(reps) } : {}),
              },
            ]
          : null,
        source: 'pwa',
      });
      setExercise('');
      setSets('');
      setReps('');
      setFeedback('success');
      onLogged();
    } catch {
      setFeedback('error');
    }
  }

  return (
    <form
      onSubmit={submit}
      className="space-y-3 rounded-[22px] border border-white/[0.07] bg-white/[0.035] p-4 backdrop-blur-xl"
    >
      <CardTitle icon={Dumbbell} title="Séance" subtitle="Mouvement et durée" />
      <div className="grid grid-cols-[1fr_100px] gap-2">
        <select
          value={type}
          onChange={(event) => setType(event.target.value as WorkoutType)}
          className={fieldClass}
        >
          <option value="poussee">Poussée</option>
          <option value="tirage">Tirage / dos</option>
          <option value="jambes">Jambes</option>
          <option value="full_body">Full body</option>
          <option value="natation">Natation</option>
          <option value="autre">Autre</option>
        </select>
        <input
          type="number"
          inputMode="numeric"
          min="1"
          max="1440"
          value={duration}
          onChange={(event) => setDuration(event.target.value)}
          placeholder="Minutes"
          className={fieldClass}
        />
      </div>
      <div className="grid grid-cols-[1fr_64px_64px] gap-2">
        <input
          value={exercise}
          onChange={(event) => setExercise(event.target.value)}
          placeholder="Exercice (optionnel)"
          maxLength={160}
          className={fieldClass}
        />
        <input
          type="number"
          inputMode="numeric"
          min="1"
          value={sets}
          onChange={(event) => setSets(event.target.value)}
          placeholder="Séries"
          aria-label="Séries"
          className={fieldClass}
        />
        <input
          type="number"
          inputMode="numeric"
          min="1"
          value={reps}
          onChange={(event) => setReps(event.target.value)}
          placeholder="Reps"
          aria-label="Répétitions"
          className={fieldClass}
        />
      </div>
      <SubmitButton feedback={feedback} />
      <FeedbackLine feedback={feedback} />
    </form>
  );
}

function MealForm({ onLogged }: Props) {
  const [mealType, setMealType] = useState<MealType>('dejeuner');
  const [description, setDescription] = useState('');
  const [calories, setCalories] = useState('');
  const [feedback, setFeedback] = useState<Feedback>('idle');

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!description.trim()) return;
    setFeedback('loading');
    try {
      await postFitness('/api/fitness/meals', {
        date: localTodayIso(),
        meal_type: mealType,
        description: description.trim(),
        calories_estimate: calories ? Number(calories) : null,
        source: 'pwa',
      });
      setDescription('');
      setCalories('');
      setFeedback('success');
      onLogged();
    } catch {
      setFeedback('error');
    }
  }

  return (
    <form
      onSubmit={submit}
      className="space-y-3 rounded-[22px] border border-white/[0.07] bg-white/[0.035] p-4 backdrop-blur-xl"
    >
      <CardTitle icon={Utensils} title="Repas" subtitle="Simple, sans comptage obligatoire" />
      <div className="grid grid-cols-[120px_1fr] gap-2">
        <select
          value={mealType}
          onChange={(event) => setMealType(event.target.value as MealType)}
          className={fieldClass}
        >
          <option value="petit_dej">Petit-déj.</option>
          <option value="dejeuner">Déjeuner</option>
          <option value="diner">Dîner</option>
          <option value="collation">Collation</option>
        </select>
        <input
          type="number"
          inputMode="numeric"
          min="0"
          max="20000"
          value={calories}
          onChange={(event) => setCalories(event.target.value)}
          placeholder="Kcal (optionnel)"
          className={fieldClass}
        />
      </div>
      <textarea
        value={description}
        onChange={(event) => setDescription(event.target.value)}
        placeholder="Qu’as-tu mangé ?"
        maxLength={2000}
        rows={2}
        className={`${fieldClass} resize-none`}
      />
      <SubmitButton feedback={feedback} />
      <FeedbackLine feedback={feedback} />
    </form>
  );
}

function WaterForm({ onLogged }: Props) {
  const [feedback, setFeedback] = useState<Feedback>('idle');
  const [activeAmount, setActiveAmount] = useState<number | null>(null);

  async function add(amountMl: number) {
    setActiveAmount(amountMl);
    setFeedback('loading');
    try {
      await postFitness('/api/fitness/water', {
        date: localTodayIso(),
        amount_ml: amountMl,
        source: 'pwa',
      });
      setFeedback('success');
      onLogged();
    } catch {
      setFeedback('error');
    }
  }

  return (
    <section className="space-y-3 rounded-[22px] border border-white/[0.07] bg-white/[0.035] p-4 backdrop-blur-xl">
      <CardTitle icon={Droplets} title="Eau" subtitle="Ajout rapide" />
      <div className="grid grid-cols-3 gap-2">
        {[
          [250, '+250 ml'],
          [500, '+500 ml'],
          [1000, '+1 L'],
        ].map(([amount, label]) => (
          <button
            key={amount}
            type="button"
            disabled={feedback === 'loading'}
            onClick={() => add(amount as number)}
            className="flex h-12 items-center justify-center gap-1.5 rounded-[15px] border border-white/10 bg-white/[0.055] text-[12px] font-semibold text-white transition-transform active:scale-95 disabled:opacity-50"
          >
            {feedback === 'loading' && activeAmount === amount ? (
              <Loader2 size={13} className="animate-spin" />
            ) : feedback === 'success' && activeAmount === amount ? (
              <Check size={13} />
            ) : (
              <Droplets size={13} className="text-[#888]" />
            )}
            {label}
          </button>
        ))}
      </div>
      <FeedbackLine feedback={feedback} />
    </section>
  );
}

function WellbeingForm({ onLogged }: Props) {
  const [rating, setRating] = useState(7);
  const [journal, setJournal] = useState('');
  const [feedback, setFeedback] = useState<Feedback>('idle');

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setFeedback('loading');
    try {
      await postFitness('/api/fitness/wellbeing', {
        date: localTodayIso(),
        rating,
        journal_text: journal.trim() || null,
        source: 'pwa',
      });
      setJournal('');
      setFeedback('success');
      onLogged();
    } catch {
      setFeedback('error');
    }
  }

  return (
    <form
      onSubmit={submit}
      className="space-y-3 rounded-[22px] border border-white/[0.07] bg-white/[0.035] p-4 backdrop-blur-xl"
    >
      <CardTitle icon={HeartPulse} title="Bien-être" subtitle="Note rapide et journal libre" />
      <div className="rounded-[16px] border border-white/[0.06] bg-black/20 px-3 py-3">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-[11px] text-[#777]">Ressenti global</span>
          <span className="text-[18px] font-semibold tabular-nums text-white">{rating}/10</span>
        </div>
        <input
          type="range"
          min="1"
          max="10"
          step="1"
          value={rating}
          onChange={(event) => setRating(Number(event.target.value))}
          className="h-1.5 w-full cursor-pointer accent-white"
          aria-label="Note de bien-être"
        />
        <div className="mt-1.5 flex justify-between text-[9px] text-[#444]">
          <span>1</span>
          <span>10</span>
        </div>
      </div>
      <textarea
        value={journal}
        onChange={(event) => setJournal(event.target.value)}
        placeholder="Une pensée, une sensation… (optionnel)"
        maxLength={2000}
        rows={3}
        className={`${fieldClass} resize-none`}
      />
      <SubmitButton feedback={feedback} />
      <FeedbackLine feedback={feedback} />
    </form>
  );
}
