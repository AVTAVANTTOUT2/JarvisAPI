'use client';

import { useMemo, useState } from 'react';
import { ChevronDown } from 'lucide-react';

import { TaskItem } from './TaskItem';
import type { TaskItemRaw } from './types';

interface Props {
  tasks: TaskItemRaw[];
  onUpdate: () => void;
  isLoading?: boolean;
}

const PRIORITY_ORDER: Record<TaskItemRaw['priority'], number> = {
  high: 0,
  medium: 1,
  low: 2,
};

function sortByPriorityThenDate(a: TaskItemRaw, b: TaskItemRaw): number {
  const pa = PRIORITY_ORDER[a.priority] ?? 99;
  const pb = PRIORITY_ORDER[b.priority] ?? 99;
  if (pa !== pb) return pa - pb;
  return (a.due_date || '\uffff').localeCompare(b.due_date || '\uffff');
}

export function TaskList({ tasks, onUpdate, isLoading }: Props) {
  const [doneOpen, setDoneOpen] = useState(false);

  const grouped = useMemo(() => {
    const todo = tasks.filter((t) => t.status === 'todo').sort(sortByPriorityThenDate);
    const doing = tasks.filter((t) => t.status === 'doing').sort(sortByPriorityThenDate);
    const done = tasks.filter((t) => t.status === 'done').sort((a, b) =>
      (b.completed_at || '').localeCompare(a.completed_at || '')
    );
    return { todo, doing, done };
  }, [tasks]);

  if (isLoading) {
    return (
      <div className="space-y-3">
        <SkeletonGroup count={3} />
        <SkeletonGroup count={2} />
      </div>
    );
  }

  if (tasks.length === 0) {
    return (
      <div className="rounded-[20px] bg-[rgba(255,255,255,0.035)] border border-[rgba(255,255,255,0.07)] py-12 px-4 text-center">
        <p className="text-[13px] text-[#666]">Aucune tache pour le moment.</p>
        <p className="text-[12px] text-[#444] mt-1">Cree une tache ci-dessus.</p>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {grouped.doing.length > 0 && (
        <Group title="En cours" count={grouped.doing.length}>
          {grouped.doing.map((task) => (
            <TaskItem key={task.id} task={task} onUpdate={onUpdate} />
          ))}
        </Group>
      )}

      {grouped.todo.length > 0 && (
        <Group title="A faire" count={grouped.todo.length}>
          {grouped.todo.map((task) => (
            <TaskItem key={task.id} task={task} onUpdate={onUpdate} />
          ))}
        </Group>
      )}

      {grouped.done.length > 0 && (
        <div>
          <button
            type="button"
            onClick={() => setDoneOpen((o) => !o)}
            className="w-full flex items-center justify-between mb-2 px-1 active:opacity-60"
          >
            <h3 className="text-[11px] font-bold tracking-[0.15em] uppercase text-[#555]">
              Terminees ({grouped.done.length})
            </h3>
            <ChevronDown
              size={14}
              className={`text-[#555] transition-transform ${doneOpen ? 'rotate-180' : ''}`}
            />
          </button>
          {doneOpen && (
            <div className="rounded-[20px] bg-[rgba(255,255,255,0.02)] border border-[rgba(255,255,255,0.05)] px-4">
              {grouped.done.map((task) => (
                <TaskItem key={task.id} task={task} onUpdate={onUpdate} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Group({
  title,
  count,
  children,
}: {
  title: string;
  count: number;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="flex items-center justify-between mb-2 px-1">
        <h3 className="text-[11px] font-bold tracking-[0.15em] uppercase text-[#555]">{title}</h3>
        <span className="text-[11px] text-[#444] tabular-nums">{count}</span>
      </div>
      <div className="rounded-[20px] bg-[rgba(255,255,255,0.035)] border border-[rgba(255,255,255,0.07)] px-4">
        {children}
      </div>
    </div>
  );
}

function SkeletonGroup({ count }: { count: number }) {
  return (
    <div>
      <div className="h-3 w-20 rounded bg-[rgba(255,255,255,0.04)] animate-pulse mb-2 mx-1" />
      <div className="rounded-[20px] bg-[rgba(255,255,255,0.035)] border border-[rgba(255,255,255,0.07)] px-4">
        {Array.from({ length: count }).map((_, i) => (
          <div
            key={i}
            className="flex items-center gap-3 py-3.5 border-b border-[rgba(255,255,255,0.04)] last:border-b-0"
          >
            <div className="w-5 h-5 rounded-full bg-[rgba(255,255,255,0.05)] animate-pulse flex-shrink-0" />
            <div className="h-3 w-3/4 rounded bg-[rgba(255,255,255,0.05)] animate-pulse" />
          </div>
        ))}
      </div>
    </div>
  );
}
