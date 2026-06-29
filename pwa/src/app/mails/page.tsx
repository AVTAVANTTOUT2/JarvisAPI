'use client';

import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, Inbox, Mail as MailIcon, RefreshCw } from 'lucide-react';

import { BottomNav } from '@/components/layout/BottomNav';
import { MailFilterPills } from '@/components/mails/MailFilterPills';
import { MailList } from '@/components/mails/MailList';
import { MailSummaryBanner } from '@/components/mails/MailSummaryBanner';
import type { MailFilter, NotificationItem, NotificationsResponse } from '@/components/mails/types';
import { jarvisFetch } from '@/lib/api';

interface BriefingResponse {
  kind: string;
  content: string;
}

export default function MailsPage() {
  const [filter, setFilter] = useState<MailFilter>('all');

  // Toutes les notifs (lues + non lues) pour l'historique
  const notifs = useQuery<NotificationsResponse>({
    queryKey: ['notifications-all'],
    queryFn: () => jarvisFetch<NotificationsResponse>('/api/notifications/all?limit=50'),
    refetchInterval: 60_000,
    retry: 0,
  });

  // Resume IA du briefing — on extrait juste la section "emails"
  const briefing = useQuery<BriefingResponse>({
    queryKey: ['briefing-morning'],
    queryFn: () => jarvisFetch<BriefingResponse>('/api/briefing?kind=morning'),
    staleTime: 10 * 60_000,
    retry: 0,
  });

  const allNotifs = notifs.data?.notifications ?? [];

  // Filtres
  const counts = useMemo(() => {
    return {
      all: allNotifs.length,
      urgent: allNotifs.filter((n) => n.priority === 'urgent' || n.priority === 'high').length,
      todo: allNotifs.filter((n) => n.read === 0 || n.read === false).length,
      fyi: allNotifs.filter(
        (n) => (n.priority === 'low' || n.priority === 'medium') && (n.read === 1 || n.read === true)
      ).length,
    };
  }, [allNotifs]);

  const filtered = useMemo<NotificationItem[]>(() => {
    const sortByDate = (a: NotificationItem, b: NotificationItem) =>
      (b.created_at || '').localeCompare(a.created_at || '');

    switch (filter) {
      case 'urgent':
        return allNotifs
          .filter((n) => n.priority === 'urgent' || n.priority === 'high')
          .sort(sortByDate);
      case 'todo':
        return allNotifs.filter((n) => n.read === 0 || n.read === false).sort(sortByDate);
      case 'fyi':
        return allNotifs
          .filter(
            (n) =>
              (n.priority === 'low' || n.priority === 'medium') &&
              (n.read === 1 || n.read === true)
          )
          .sort(sortByDate);
      default:
        return [...allNotifs].sort(sortByDate);
    }
  }, [allNotifs, filter]);

  // Extraction du resume "emails" depuis le briefing
  const emailSummary = useMemo(() => {
    if (!briefing.data?.content) return null;
    const match = briefing.data.content.match(
      /[—\-]+\s*EMAILS?\s*\([^)]*\)\s*\n([\s\S]*?)(?=\n\s*---|\n\s*[—\-]+\s*[A-Z])/i
    );
    if (!match) return null;
    return match[1].trim().replace(/\*\*/g, '');
  }, [briefing.data]);

  return (
    <main className="min-h-screen pb-28 px-5">
      {/* Header */}
      <div className="pt-[max(env(safe-area-inset-top),3.5rem)] pb-5">
        <h1 className="text-[28px] font-bold tracking-tight text-white leading-tight">Mails</h1>
        <p className="text-[13px] text-[#666] mt-1">
          Notifications & messages analyses par JARVIS
        </p>
      </div>

      {/* Badges count */}
      <div className="flex gap-2 mb-4 flex-wrap">
        <Badge icon={Inbox} label={`${counts.all} total`} color="#888" />
        {counts.todo > 0 && (
          <Badge icon={MailIcon} label={`${counts.todo} non lues`} color="#4A9EFF" />
        )}
        {counts.urgent > 0 && (
          <Badge icon={AlertTriangle} label={`${counts.urgent} urgentes`} color="#FF453A" />
        )}
      </div>

      {/* Resume IA */}
      {(emailSummary || briefing.isLoading) && (
        <div className="mb-4">
          <MailSummaryBanner summary={emailSummary} isLoading={briefing.isLoading} />
        </div>
      )}

      {/* Pills filtres */}
      <div className="mb-4">
        <MailFilterPills current={filter} onChange={setFilter} counts={counts} />
      </div>

      {/* Liste */}
      {notifs.isError ? (
        <div className="rounded-[20px] bg-[rgba(255,69,58,0.06)] border border-[rgba(255,69,58,0.18)] p-4">
          <p className="text-[13px] text-[#FF453A] font-medium">Notifications indisponibles</p>
          <button
            type="button"
            onClick={() => notifs.refetch()}
            className="text-[12px] text-[#4A9EFF] mt-2 inline-flex items-center gap-1.5 active:opacity-60"
          >
            <RefreshCw size={12} /> Reessayer
          </button>
        </div>
      ) : (
        <MailList notifications={filtered} isLoading={notifs.isLoading} />
      )}

      <BottomNav />
    </main>
  );
}

function Badge({
  icon: Icon,
  label,
  color,
}: {
  icon: typeof MailIcon;
  label: string;
  color: string;
}) {
  return (
    <span
      className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-medium"
      style={{
        backgroundColor: `${color}1a`,
        color,
        border: `1px solid ${color}33`,
      }}
    >
      <Icon size={11} />
      {label}
    </span>
  );
}
