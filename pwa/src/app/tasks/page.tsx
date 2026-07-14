'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { RefreshCw } from 'lucide-react';

import { BottomNav } from '@/components/layout/BottomNav';
import { ProgressBar } from '@/components/tasks/ProgressBar';
import { TaskCreator } from '@/components/tasks/TaskCreator';
import { TaskList } from '@/components/tasks/TaskList';
import type { TaskItemRaw, TasksResponse } from '@/components/tasks/types';
import { jarvisFetch } from '@/lib/api';

export default function TasksPage() {
  const qc = useQueryClient();

  const tasks = useQuery<TasksResponse>({
    queryKey: ['tasks'],
    queryFn: () => jarvisFetch<TasksResponse>('/api/tasks'),
    retry: 0,
  });

  const all: TaskItemRaw[] = tasks.data?.tasks ?? [];
  const completed = all.filter((t) => t.status === 'done').length;
  const total = all.length;

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['tasks'] });
  };

  return (
    <main className="min-h-screen pb-28 px-5">
      <div className="pt-[max(env(safe-area-inset-top),3.5rem)] pb-5">
        <h1 className="text-[28px] font-bold tracking-tight text-white leading-tight">Taches</h1>
        <p className="text-[13px] text-[#666] mt-1">Organise ta journee</p>
      </div>

      {!tasks.isLoading && total > 0 && (
        <div className="mb-4">
          <ProgressBar completed={completed} total={total} />
        </div>
      )}

      <div className="mb-5">
        <TaskCreator onCreated={invalidate} />
      </div>

      {tasks.isError ? (
        <div className="rounded-[20px] bg-[rgba(255,69,58,0.06)] border border-[rgba(255,69,58,0.18)] p-4">
          <p className="text-[13px] text-[#FF453A] font-medium">Taches indisponibles</p>
          <button
            type="button"
            onClick={() => tasks.refetch()}
            className="text-[12px] text-[#4A9EFF] mt-2 inline-flex items-center gap-1.5 active:opacity-60"
          >
            <RefreshCw size={12} /> Reessayer
          </button>
        </div>
      ) : (
        <TaskList tasks={all} onUpdate={invalidate} isLoading={tasks.isLoading} />
      )}

      <BottomNav />
    </main>
  );
}
