'use client'

import { useCallback, useEffect, useState } from 'react'
import { Dumbbell } from 'lucide-react'

import { jarvisFetch } from '@unified/lib/api'

import { FitnessForms } from '@desktop/app/components/fitness/FitnessForms'
import { FitnessSummary } from '@desktop/app/components/fitness/FitnessSummary'
import type { TodaySummary } from '@desktop/app/components/fitness/types'

export function FitnessView() {
  const [summary, setSummary] = useState<TodaySummary | undefined>()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError(false)
    try {
      const data = await jarvisFetch<TodaySummary>('/api/fitness/summary/today')
      setSummary(data)
    } catch {
      setError(true)
      setSummary(undefined)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  return (
    <div className="mx-auto max-w-2xl px-5 py-8">
      <header className="mb-6">
        <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">
          Journal personnel
        </p>
        <h1 className="mt-1 flex items-center gap-2 text-[28px] font-bold leading-tight tracking-tight text-white">
          <Dumbbell size={26} className="text-white/80" />
          Fitness
        </h1>
        <p className="mt-1 text-[13px] text-muted-foreground">
          Bouger, nourrir, hydrater, ressentir.
        </p>
      </header>

      <FitnessSummary
        summary={summary}
        loading={loading}
        error={error}
        onRetry={() => void load()}
      />

      <div className="mb-3 px-1">
        <span className="text-[10px] font-bold uppercase tracking-[0.16em] text-muted-foreground">
          Ajouter à aujourd&apos;hui
        </span>
      </div>
      <FitnessForms onLogged={() => void load()} />
    </div>
  )
}

export default FitnessView
