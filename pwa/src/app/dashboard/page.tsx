'use client';

import { useQuery } from '@tanstack/react-query';
import {
  AlertTriangle,
  Calendar,
  CheckSquare,
  Mail,
  Mic,
  RefreshCw,
  Sparkles,
  Video,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import Link from 'next/link';

import { BriefingCard } from '@/components/dashboard/BriefingCard';
import { LocationWidget } from '@/components/dashboard/LocationWidget';
import { BottomNav } from '@/components/layout/BottomNav';
import { jarvisFetch } from '@/lib/api';
import { parseBriefing } from '@/lib/briefing-parser';

// ─────────────────────────────────────────────────────────────
// Types des reponses backend
// ─────────────────────────────────────────────────────────────

interface BriefingResponse {
  kind: string;
  content: string;
}

interface NotificationItem {
  id: number;
  source: string;
  title: string;
  content: string;
  priority: 'urgent' | 'high' | 'medium' | 'low';
  read: 0 | 1;
  email_id: string | null;
  created_at: string;
}

interface NotificationsResponse {
  notifications: NotificationItem[];
}

interface TaskItemRaw {
  id: number;
  title: string;
  priority: 'high' | 'medium' | 'low';
  status: 'todo' | 'doing' | 'done';
  due_date: string | null;
  category: string | null;
  created_at: string;
  completed_at: string | null;
}

interface TasksResponse {
  tasks: TaskItemRaw[];
}

interface CalendarEventRaw {
  id?: string;
  summary?: string;
  title?: string;
  start: string;
  end?: string;
  location?: string;
  notes?: string;
  calendar?: string;
  time?: string;
}

interface CalendarResponse {
  events?: CalendarEventRaw[];
  count?: number;
}

// ─────────────────────────────────────────────────────────────
// Page
// ─────────────────────────────────────────────────────────────

export default function DashboardPage() {
  const now = new Date();
  const isEvening = now.getHours() >= 17;
  const dateStr = now.toLocaleDateString('fr-FR', {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
  });

  // Briefing — staletime 5 min, le backend cache la generation
  const briefing = useQuery<BriefingResponse>({
    queryKey: ['briefing', isEvening ? 'evening' : 'morning'],
    queryFn: () => jarvisFetch<BriefingResponse>(`/api/briefing?kind=${isEvening ? 'evening' : 'morning'}`),
    staleTime: 5 * 60_000,
    retry: 1,
  });

  // Notifications — pour stats + filtrage mails / urgences
  const notifications = useQuery<NotificationsResponse>({
    queryKey: ['notifications'],
    queryFn: () => jarvisFetch<NotificationsResponse>('/api/notifications'),
    retry: 0,
  });

  // Tasks
  const tasks = useQuery<TasksResponse>({
    queryKey: ['tasks'],
    queryFn: () => jarvisFetch<TasksResponse>('/api/tasks'),
    retry: 0,
  });

  // Calendar du jour
  const todayStart = new Date();
  todayStart.setHours(0, 0, 0, 0);
  const todayEnd = new Date();
  todayEnd.setHours(23, 59, 59, 999);

  const calendar = useQuery<CalendarResponse | CalendarEventRaw[]>({
    queryKey: ['calendar-today'],
    queryFn: () =>
      jarvisFetch<CalendarResponse | CalendarEventRaw[]>(
        `/api/calendar?start=${encodeURIComponent(todayStart.toISOString())}&end=${encodeURIComponent(todayEnd.toISOString())}`
      ),
    retry: 1,
    staleTime: 2 * 60_000,
  });

  // ── Parsing du briefing ──
  const sections = briefing.data?.content ? parseBriefing(briefing.data.content) : [];

  // ── Stats ──
  const notifs = notifications.data?.notifications ?? [];
  const unreadMails = notifs.filter((n) => n.source === 'email' || n.source === 'email_watcher').length;
  const urgentNotifs = notifs.filter((n) => n.priority === 'urgent' || n.priority === 'high').length;
  const tasksDue = (tasks.data?.tasks ?? []).filter((t) => t.status !== 'done').length;

  const calendarEvents: CalendarEventRaw[] = Array.isArray(calendar.data)
    ? calendar.data
    : (calendar.data?.events ?? []);
  const eventsToday = calendarEvents.length;

  const events = [...calendarEvents].sort((a, b) => (a.start || '').localeCompare(b.start || ''));

  return (
    <main className="min-h-screen pb-28 px-5">
      {/* ── Header ── */}
      <div className="pt-[max(env(safe-area-inset-top),3.5rem)] pb-6">
        <h1 className="text-[30px] font-bold tracking-tight text-white leading-tight">
          {isEvening ? 'Bonsoir' : 'Bonjour'}, Elias.
        </h1>
        <p className="text-[13px] text-[#666] mt-1 capitalize">{dateStr}</p>
      </div>

      {/* ── Briefing ── */}
      <section className="space-y-3 mb-6">
        <div className="flex items-center gap-2 mb-1">
          <Sparkles size={13} className="text-[#9C59FF]" />
          <span className="text-[10px] font-bold tracking-[0.15em] uppercase text-[#9C59FF]">
            {isEvening ? 'Bilan du soir' : 'Briefing du matin'}
          </span>
        </div>

        {briefing.isLoading && (
          <>
            <SkeletonCard lines={3} />
            <SkeletonCard lines={2} />
            <SkeletonCard lines={4} />
          </>
        )}

        {briefing.isError && (
          <div className="rounded-[20px] bg-[rgba(255,69,58,0.06)] border border-[rgba(255,69,58,0.18)] p-4">
            <p className="text-[13px] text-[#FF453A] font-medium">Briefing indisponible</p>
            <button
              type="button"
              onClick={() => briefing.refetch()}
              className="text-[12px] text-[#4A9EFF] mt-2 inline-flex items-center gap-1.5 active:opacity-60"
            >
              <RefreshCw size={12} /> Reessayer
            </button>
          </div>
        )}

        {!briefing.isLoading && !briefing.isError && sections.length === 0 && (
          <div className="rounded-[20px] bg-[rgba(255,255,255,0.035)] border border-[rgba(255,255,255,0.07)] p-4">
            <p className="text-[13px] text-[#888]">Aucun briefing disponible pour le moment.</p>
          </div>
        )}

        {!briefing.isLoading &&
          sections.map((section, i) => <BriefingCard key={i} section={section} />)}
      </section>

      {/* ── Stats 2x2 ── */}
      <section className="grid grid-cols-2 gap-2.5 mb-6">
        <StatCard
          value={unreadMails}
          label="Notifications mail"
          icon={Mail}
          color="#4A9EFF"
          loading={notifications.isLoading}
        />
        <StatCard
          value={urgentNotifs}
          label="Urgentes"
          icon={AlertTriangle}
          color="#FF453A"
          loading={notifications.isLoading}
        />
        <StatCard
          value={eventsToday}
          label="Events aujourd'hui"
          icon={Calendar}
          color="#30D158"
          loading={calendar.isLoading}
        />
        <StatCard
          value={tasksDue}
          label="Taches en cours"
          icon={CheckSquare}
          color="#FFD60A"
          loading={tasks.isLoading}
        />
      </section>

      {/* ── Localisation ── */}
      <section className="mb-6">
        <h2 className="text-[11px] font-bold tracking-[0.15em] uppercase text-[#555] mb-3 px-1">
          Position
        </h2>
        <LocationWidget />
      </section>

      {/* ── Agenda du jour ── */}
      {events.length > 0 && (
        <section className="mb-6">
          <h2 className="text-[11px] font-bold tracking-[0.15em] uppercase text-[#555] mb-3 px-1">
            Agenda
          </h2>
          <div className="rounded-[20px] bg-[rgba(255,255,255,0.035)] border border-[rgba(255,255,255,0.07)] p-4">
            {events.map((ev, i) => {
              const time = ev.start
                ? new Date(ev.start).toLocaleTimeString('fr-FR', {
                    hour: '2-digit',
                    minute: '2-digit',
                  })
                : ev.time || '';
              const isLast = i === events.length - 1;
              const isVirtual = !!ev.location?.toLowerCase().match(/meet|zoom|teams|visio/);
              return (
                <div
                  key={ev.id ?? i}
                  className={`flex gap-3.5 py-2.5 ${
                    !isLast ? 'border-b border-[rgba(255,255,255,0.04)]' : ''
                  }`}
                >
                  <div className="flex flex-col items-center pt-1">
                    <div className="w-2 h-2 rounded-full bg-[#4A9EFF]" />
                    {!isLast && (
                      <div className="w-px flex-1 mt-1 min-h-[24px] bg-[rgba(74,158,255,0.15)]" />
                    )}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-[11px] text-[#555] font-medium tabular-nums">{time}</div>
                    <div className="text-[13px] text-white mt-0.5 truncate">
                      {ev.summary || ev.title || 'Sans titre'}
                    </div>
                    {ev.location && (
                      <div className="text-[11px] text-[#666] mt-0.5 flex items-center gap-1 truncate">
                        {isVirtual && <Video size={10} />}
                        {ev.location}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      )}

      {/* ── Actions rapides ── */}
      <section className="mb-4">
        <h2 className="text-[11px] font-bold tracking-[0.15em] uppercase text-[#555] mb-3 px-1">
          Actions
        </h2>
        <div className="grid grid-cols-3 gap-2.5">
          <QuickAction icon={Mail} label="Mails" href="/mails" />
          <QuickAction icon={CheckSquare} label="Taches" href="/tasks" />
          <QuickAction icon={Mic} label="Dicter" href="/voice" />
        </div>
      </section>

      <BottomNav />
    </main>
  );
}

// ─────────────────────────────────────────────────────────────
// Sub-components
// ─────────────────────────────────────────────────────────────

function SkeletonCard({ lines = 2 }: { lines?: number }) {
  return (
    <div className="rounded-[20px] bg-[rgba(255,255,255,0.02)] border border-[rgba(255,255,255,0.05)] p-4 space-y-2">
      <div className="flex items-center gap-2.5">
        <div className="w-8 h-8 rounded-lg bg-[rgba(255,255,255,0.04)] animate-pulse" />
        <div className="h-3 w-24 rounded bg-[rgba(255,255,255,0.04)] animate-pulse" />
      </div>
      <div className="pl-[42px] space-y-1.5">
        {Array.from({ length: lines }).map((_, i) => (
          <div
            key={i}
            className="h-2.5 rounded bg-[rgba(255,255,255,0.04)] animate-pulse"
            style={{ width: `${100 - i * 12}%` }}
          />
        ))}
      </div>
    </div>
  );
}

function StatCard({
  value,
  label,
  icon: Icon,
  color,
  loading,
}: {
  value: number;
  label: string;
  icon: LucideIcon;
  color: string;
  loading?: boolean;
}) {
  return (
    <div className="rounded-[18px] bg-[rgba(255,255,255,0.035)] border border-[rgba(255,255,255,0.07)] p-4">
      <div className="flex items-start justify-between mb-1">
        {loading ? (
          <div className="h-7 w-10 rounded bg-[rgba(255,255,255,0.04)] animate-pulse" />
        ) : (
          <span
            className="text-[28px] font-bold tracking-tight leading-none tabular-nums"
            style={{ color }}
          >
            {value}
          </span>
        )}
        <Icon size={15} className="text-[#444] mt-1" />
      </div>
      <span className="text-[12px] text-[#666] font-medium leading-tight">{label}</span>
    </div>
  );
}

function QuickAction({
  icon: Icon,
  label,
  href,
}: {
  icon: LucideIcon;
  label: string;
  href: string;
}) {
  return (
    <Link
      href={href}
      className="rounded-[18px] bg-[rgba(255,255,255,0.035)] border border-[rgba(255,255,255,0.07)] p-4 flex flex-col items-center gap-2 active:scale-95 transition-transform duration-100"
    >
      <Icon size={22} className="text-[#888]" strokeWidth={1.8} />
      <span className="text-[11px] text-[#888] font-medium">{label}</span>
    </Link>
  );
}
