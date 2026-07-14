'use client';

import { useState } from 'react';
import { Plus } from 'lucide-react';

import { jarvisFetch } from '@unified/lib/api';
import type { TaskItemRaw } from './types';

type Priority = TaskItemRaw['priority'];

interface Props {
  onCreated: () => void;
}

const PRIORITY_OPTIONS: { value: Priority; label: string; color: string }[] = [
  { value: 'high', label: 'Haute', color: '#FF453A' },
  { value: 'medium', label: 'Moyenne', color: '#FFD60A' },
  { value: 'low', label: 'Basse', color: '#555' },
];

export function TaskCreator({ onCreated }: Props) {
  const [title, setTitle] = useState('');
  const [priority, setPriority] = useState<Priority>('medium');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = title.trim();
    if (!trimmed) return;
    setSubmitting(true);
    setError(null);
    try {
      await jarvisFetch('/api/tasks', {
        method: 'POST',
        body: JSON.stringify({ title: trimmed, priority }),
      });
      setTitle('');
      setPriority('medium');
      onCreated();
    } catch (err) {
      console.warn('[TaskCreator] create failed:', err);
      setError('Echec creation tache');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form
      onSubmit={submit}
      className="rounded-[18px] bg-[rgba(255,255,255,0.035)] border border-[rgba(255,255,255,0.07)] p-3 space-y-2.5"
    >
      <div className="flex items-center gap-2">
        <input
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Nouvelle tache..."
          maxLength={200}
          className="flex-1 bg-transparent border-none outline-none text-[14px] text-white placeholder:text-[#555]"
          disabled={submitting}
        />
        <button
          type="submit"
          disabled={!title.trim() || submitting}
          className="w-8 h-8 rounded-lg bg-[#4A9EFF] flex items-center justify-center flex-shrink-0 active:scale-95 transition-transform disabled:opacity-40 disabled:active:scale-100"
        >
          <Plus size={16} className="text-white" strokeWidth={2.5} />
        </button>
      </div>
      <div className="flex gap-1.5">
        {PRIORITY_OPTIONS.map((opt) => {
          const active = priority === opt.value;
          return (
            <button
              key={opt.value}
              type="button"
              onClick={() => setPriority(opt.value)}
              className="text-[11px] px-2.5 py-1 rounded-full font-medium transition-colors active:scale-95"
              style={{
                backgroundColor: active ? `${opt.color}26` : 'rgba(255,255,255,0.04)',
                color: active ? opt.color : '#666',
                border: `1px solid ${active ? `${opt.color}66` : 'rgba(255,255,255,0.06)'}`,
              }}
            >
              {opt.label}
            </button>
          );
        })}
      </div>
      {error && <p className="text-[11px] text-[#FF453A]">{error}</p>}
    </form>
  );
}
