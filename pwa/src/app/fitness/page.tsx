'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';

import { FitnessForms } from '@mobile/components/fitness/FitnessForms';
import { FitnessSummary } from '@mobile/components/fitness/FitnessSummary';
import type { TodaySummary } from '@mobile/components/fitness/types';
import { BottomNav } from '@mobile/components/layout/BottomNav';
import { jarvisFetch } from '@unified/lib/api';

export default function FitnessPage() {
  const queryClient = useQueryClient();
  const summary = useQuery<TodaySummary>({
    queryKey: ['fitness', 'summary', 'today'],
    queryFn: () => jarvisFetch<TodaySummary>('/api/fitness/summary/today'),
    retry: 0,
  });

  function refreshSummary() {
    void queryClient.invalidateQueries({
      queryKey: ['fitness', 'summary', 'today'],
    });
  }

  return (
    <main className="min-h-screen bg-[#0a0a0f] px-5 pb-28">
      <header className="pb-5 pt-[max(env(safe-area-inset-top),3.5rem)]">
        <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-[#555]">
          Journal personnel
        </p>
        <h1 className="mt-1 text-[30px] font-bold leading-tight tracking-tight text-white">
          Fitness
        </h1>
        <p className="mt-1 text-[13px] text-[#666]">
          Bouger, nourrir, hydrater, ressentir.
        </p>
      </header>

      <FitnessSummary
        summary={summary.data}
        loading={summary.isLoading}
        error={summary.isError}
        onRetry={() => void summary.refetch()}
      />

      <div className="mb-3 px-1">
        <span className="text-[10px] font-bold uppercase tracking-[0.16em] text-[#555]">
          Ajouter à aujourd&apos;hui
        </span>
      </div>
      <FitnessForms onLogged={refreshSummary} />

      <BottomNav />
    </main>
  );
}
