'use client';

import { Sparkles } from 'lucide-react';

interface Props {
  summary: string | null | undefined;
  isLoading?: boolean;
}

/** Banner violet contenant le resume IA des mails. */
export function MailSummaryBanner({ summary, isLoading }: Props) {
  if (!isLoading && !summary) return null;

  return (
    <div className="rounded-[20px] bg-gradient-to-br from-[rgba(156,89,255,0.1)] to-[rgba(156,89,255,0.02)] border border-[rgba(156,89,255,0.2)] p-4 space-y-2">
      <div className="flex items-center gap-2">
        <Sparkles size={13} className="text-[#9C59FF]" />
        <span className="text-[10px] font-bold tracking-[0.15em] uppercase text-[#9C59FF]">
          JARVIS Resume
        </span>
      </div>
      {isLoading ? (
        <div className="space-y-1.5">
          <div className="h-2.5 w-full rounded bg-[rgba(255,255,255,0.05)] animate-pulse" />
          <div className="h-2.5 w-4/5 rounded bg-[rgba(255,255,255,0.05)] animate-pulse" />
        </div>
      ) : (
        <p className="text-[13px] text-[#ccc] leading-relaxed whitespace-pre-line">
          {summary}
        </p>
      )}
    </div>
  );
}
