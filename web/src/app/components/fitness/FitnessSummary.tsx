'use client'

import { Activity, Droplets, Flame, HeartPulse, RefreshCw } from 'lucide-react'

import type { TodaySummary } from './types'

interface Props {
  summary?: TodaySummary
  loading: boolean
  error: boolean
  onRetry: () => void
}

export function FitnessSummary({ summary, loading, error, onRetry }: Props) {
  if (loading) {
    return (
      <div className="mb-6 rounded-[24px] border border-[rgba(255,255,255,0.08)] bg-[rgba(255,255,255,0.04)] p-4">
        <div className="h-3 w-28 animate-pulse rounded bg-[rgba(255,255,255,0.08)]" />
        <div className="mt-4 grid grid-cols-4 gap-2">
          {Array.from({ length: 4 }).map((_, index) => (
            <div
              key={index}
              className="h-16 animate-pulse rounded-[16px] bg-[rgba(255,255,255,0.035)]"
            />
          ))}
        </div>
      </div>
    )
  }

  if (error || !summary) {
    return (
      <div className="mb-6 rounded-[22px] border border-[rgba(255,69,58,0.18)] bg-[rgba(255,69,58,0.06)] p-4">
        <p className="text-[13px] font-medium text-[#FF453A]">Résumé fitness indisponible</p>
        <button
          type="button"
          onClick={onRetry}
          className="mt-2 inline-flex items-center gap-1.5 text-[12px] text-white active:opacity-60"
        >
          <RefreshCw size={12} /> Réessayer
        </button>
      </div>
    )
  }

  const metrics = [
    {
      label: 'Séance',
      value: summary.workout_done ? `${summary.workout_count} faite` : 'Aucune',
      icon: Activity,
    },
    {
      label: 'Repas',
      value: String(summary.meal_count),
      icon: Flame,
    },
    {
      label: 'Eau',
      value:
        summary.water_ml >= 1000
          ? `${(summary.water_ml / 1000).toLocaleString('fr-FR')} L`
          : `${summary.water_ml} ml`,
      icon: Droplets,
    },
    {
      label: 'Forme',
      value:
        summary.wellbeing?.rating != null ? `${summary.wellbeing.rating}/10` : '—',
      icon: HeartPulse,
    },
  ]

  return (
    <section className="relative mb-6 overflow-hidden rounded-[24px] border border-[rgba(255,255,255,0.1)] bg-[linear-gradient(145deg,rgba(255,255,255,0.075),rgba(255,255,255,0.025))] p-4 shadow-[0_22px_70px_rgba(0,0,0,0.32)] backdrop-blur-[24px]">
      <div className="pointer-events-none absolute -right-10 -top-14 h-36 w-36 rounded-full bg-white/[0.045] blur-2xl" />
      <div className="relative flex items-center justify-between">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-[#777]">
            Aujourd&apos;hui
          </p>
          <p className="mt-1 text-[14px] font-semibold text-white">
            {summary.calories_estimate > 0
              ? `≈ ${summary.calories_estimate} kcal journalisées`
              : 'Ton état en un coup d’œil'}
          </p>
        </div>
        <div className="flex h-9 w-9 items-center justify-center rounded-full border border-white/10 bg-white/[0.05]">
          <Activity size={17} className="text-white" />
        </div>
      </div>

      <div className="relative mt-4 grid grid-cols-4 gap-2">
        {metrics.map(({ label, value, icon: Icon }) => (
          <div
            key={label}
            className="min-w-0 rounded-[16px] border border-white/[0.055] bg-black/20 px-2 py-3 text-center"
          >
            <Icon size={14} className="mx-auto text-[#888]" />
            <div className="mt-1.5 truncate text-[13px] font-semibold tabular-nums text-white">
              {value}
            </div>
            <div className="mt-0.5 text-[9px] uppercase tracking-[0.08em] text-[#555]">
              {label}
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}
