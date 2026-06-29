'use client';

import type { MailFilter } from './types';

interface PillSpec {
  id: MailFilter;
  label: string;
  count?: number;
}

interface Props {
  current: MailFilter;
  onChange: (filter: MailFilter) => void;
  counts: {
    all: number;
    urgent: number;
    todo: number;
    fyi: number;
  };
}

export function MailFilterPills({ current, onChange, counts }: Props) {
  const pills: PillSpec[] = [
    { id: 'all', label: 'Tout', count: counts.all },
    { id: 'urgent', label: 'Urgentes', count: counts.urgent },
    { id: 'todo', label: 'A traiter', count: counts.todo },
    { id: 'fyi', label: 'FYI', count: counts.fyi },
  ];

  return (
    <div
      className="flex gap-2 overflow-x-auto pb-1 -mx-1 px-1"
      style={{ scrollbarWidth: 'none' }}
    >
      {pills.map((pill) => {
        const active = current === pill.id;
        return (
          <button
            key={pill.id}
            type="button"
            onClick={() => onChange(pill.id)}
            className={`flex items-center gap-1.5 px-3.5 py-1.5 rounded-full text-[12px] font-medium whitespace-nowrap transition-colors active:scale-95 ${
              active
                ? 'bg-white text-black'
                : 'bg-[rgba(255,255,255,0.05)] text-[#888] border border-[rgba(255,255,255,0.07)]'
            }`}
          >
            {pill.label}
            {pill.count !== undefined && pill.count > 0 && (
              <span
                className={`text-[10px] font-bold tabular-nums ${active ? 'text-black/60' : 'text-[#666]'}`}
              >
                {pill.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
