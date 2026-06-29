'use client';

interface Props {
  completed: number;
  total: number;
}

export function ProgressBar({ completed, total }: Props) {
  const pct = total > 0 ? Math.round((completed / total) * 100) : 0;
  const remaining = Math.max(total - completed, 0);

  return (
    <div className="rounded-[18px] bg-[rgba(255,255,255,0.035)] border border-[rgba(255,255,255,0.07)] p-4 space-y-2.5">
      <div className="flex items-baseline justify-between">
        <div>
          <span className="text-[24px] font-bold text-white tabular-nums leading-none">
            {completed}
          </span>
          <span className="text-[14px] text-[#666] tabular-nums ml-1">/ {total}</span>
        </div>
        <span className="text-[12px] text-[#888] tabular-nums">
          {remaining === 0 ? 'Tout fait' : `${remaining} restant${remaining > 1 ? 's' : ''}`}
        </span>
      </div>
      <div className="h-1.5 rounded-full bg-[rgba(255,255,255,0.06)] overflow-hidden">
        <div
          className="h-full bg-gradient-to-r from-[#4A9EFF] to-[#30D158] transition-all duration-300"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}
