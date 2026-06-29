'use client';

import { Check, Circle, Clock } from 'lucide-react';

import { jarvisFetch } from '@/lib/api';
import type { TaskItemRaw } from './types';

const PRIORITY_COLOR: Record<TaskItemRaw['priority'], string> = {
  high: 'text-[#FF453A]',
  medium: 'text-[#FFD60A]',
  low: 'text-[#555]',
};

function formatDue(due: string | null): string | null {
  if (!due) return null;
  try {
    const date = new Date(due);
    if (Number.isNaN(date.getTime())) return null;
    const now = new Date();
    const isToday = date.toDateString() === now.toDateString();
    if (isToday) {
      return date.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
    }
    const tomorrow = new Date(now);
    tomorrow.setDate(now.getDate() + 1);
    if (date.toDateString() === tomorrow.toDateString()) return 'Demain';
    return date.toLocaleDateString('fr-FR', { day: 'numeric', month: 'short' });
  } catch {
    return null;
  }
}

interface Props {
  task: TaskItemRaw;
  onUpdate: () => void;
}

export function TaskItem({ task, onUpdate }: Props) {
  const isDone = task.status === 'done';
  const isDoing = task.status === 'doing';

  async function toggleStatus() {
    const next = isDone ? 'todo' : 'done';
    try {
      await jarvisFetch(`/api/tasks/${task.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ status: next }),
      });
      onUpdate();
    } catch (err) {
      console.warn('[TaskItem] toggle failed:', err);
    }
  }

  const due = formatDue(task.due_date);

  return (
    <button
      type="button"
      onClick={toggleStatus}
      className="w-full flex items-center gap-3 py-3.5 border-b border-[rgba(255,255,255,0.04)] last:border-b-0 active:bg-[rgba(255,255,255,0.02)] transition-colors text-left"
    >
      {isDone ? (
        <div className="w-5 h-5 rounded-full bg-[rgba(48,209,88,0.18)] flex items-center justify-center flex-shrink-0">
          <Check size={12} className="text-[#30D158]" strokeWidth={2.5} />
        </div>
      ) : (
        <Circle
          size={20}
          className={`flex-shrink-0 ${PRIORITY_COLOR[task.priority] ?? PRIORITY_COLOR.low}`}
          strokeWidth={isDoing ? 2.5 : 1.8}
        />
      )}
      <div className="flex-1 min-w-0">
        <div
          className={`text-[13px] truncate ${
            isDone ? 'line-through text-[#555]' : 'text-white'
          }`}
        >
          {task.title}
        </div>
        {(due || task.category) && (
          <div className="flex items-center gap-2 mt-0.5">
            {due && (
              <span className="inline-flex items-center gap-1 text-[11px] text-[#555]">
                <Clock size={10} />
                {due}
              </span>
            )}
            {task.category && (
              <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-[rgba(255,255,255,0.05)] text-[#666] border border-[rgba(255,255,255,0.05)]">
                {task.category}
              </span>
            )}
          </div>
        )}
      </div>
    </button>
  );
}
