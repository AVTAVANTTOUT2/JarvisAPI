'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Brain,
  Calendar,
  Cloud,
  Cpu,
  Eye,
  FileCode,
  Inbox,
  Mail,
  MapPin,
  MessageCircle,
  Mic,
  Monitor,
  RefreshCw,
  Server,
  Users,
  Volume2,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

import { LocationConfig } from '@/components/config/LocationConfig';
import { BottomNav } from '@/components/layout/BottomNav';
import { jarvisFetch } from '@/lib/api';

// ─────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────

interface StatusResponse {
  user: string;
  models: {
    haiku?: string;
    sonnet?: string;
    opus?: string;
    gemini?: string;
  };
  audio?: {
    stt_available?: boolean;
    stt_engine?: string;
    tts_available?: boolean;
    tts_backend?: string;
    tts_voice?: string;
  };
  imessage?: { available?: boolean; target?: string | null };
  email_watcher?: { running?: boolean; check_interval?: number; processed_count?: number };
  computer?: { available?: boolean; shell?: string };
  code_executor?: { available?: boolean; engine?: string };
  memory?: {
    user_facts?: number;
    relationship_profiles?: number;
    patterns_active?: number;
    episodes?: number;
    people?: number;
    cross_insights?: number;
  };
}

interface IntegrationsResponse {
  mail?: boolean;
  calendar?: { available?: boolean; error?: string | null } | boolean;
  weather?: boolean;
  imessage?: boolean;
  email_watcher?: boolean;
  computer?: { available?: boolean };
  location_tracking?: boolean;
}

// ─────────────────────────────────────────────────────────────
// Page
// ─────────────────────────────────────────────────────────────

export default function ConfigPage() {
  const qc = useQueryClient();

  const status = useQuery<StatusResponse>({
    queryKey: ['jarvis-status'],
    queryFn: () => jarvisFetch<StatusResponse>('/api/status'),
    refetchInterval: 30_000,
    retry: 0,
  });

  const integrations = useQuery<IntegrationsResponse>({
    queryKey: ['jarvis-integrations'],
    queryFn: () => jarvisFetch<IntegrationsResponse>('/api/integrations'),
    refetchInterval: 30_000,
    retry: 0,
  });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['jarvis-status'] });
    qc.invalidateQueries({ queryKey: ['jarvis-integrations'] });
  };

  const calendarOk =
    typeof integrations.data?.calendar === 'boolean'
      ? integrations.data.calendar
      : !!integrations.data?.calendar?.available;

  const integrationList = [
    { icon: Mail, label: 'Apple Mail', ok: !!integrations.data?.mail },
    { icon: Calendar, label: 'Calendar', ok: calendarOk },
    { icon: MessageCircle, label: 'iMessage', ok: !!integrations.data?.imessage },
    { icon: Cloud, label: 'Meteo', ok: !!integrations.data?.weather },
    { icon: Inbox, label: 'Email Watcher', ok: !!integrations.data?.email_watcher },
    { icon: Monitor, label: 'Computer access', ok: !!integrations.data?.computer?.available },
    { icon: MapPin, label: 'Localisation', ok: !!integrations.data?.location_tracking },
    { icon: FileCode, label: 'Code Executor', ok: !!status.data?.code_executor?.available },
  ];

  const memoryStats = [
    { label: 'Facts', value: status.data?.memory?.user_facts ?? 0 },
    { label: 'Contacts', value: status.data?.memory?.people ?? 0 },
    { label: 'Profils', value: status.data?.memory?.relationship_profiles ?? 0 },
    { label: 'Patterns', value: status.data?.memory?.patterns_active ?? 0 },
    { label: 'Episodes', value: status.data?.memory?.episodes ?? 0 },
    { label: 'Insights', value: status.data?.memory?.cross_insights ?? 0 },
  ];

  const llmModels = [
    { tier: 'Haiku', model: status.data?.models?.haiku },
    { tier: 'Sonnet', model: status.data?.models?.sonnet },
    { tier: 'Opus', model: status.data?.models?.opus },
    { tier: 'Gemini', model: status.data?.models?.gemini },
  ].filter((m) => m.model);

  return (
    <main className="min-h-screen pb-28 px-5">
      <div className="pt-[max(env(safe-area-inset-top),3.5rem)] pb-5">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-[28px] font-bold tracking-tight text-white leading-tight">
              Configuration
            </h1>
            <p className="text-[13px] text-[#666] mt-1">Etat live du backend JARVIS</p>
          </div>
          <button
            type="button"
            onClick={refresh}
            disabled={status.isFetching}
            className="w-10 h-10 rounded-full bg-[rgba(255,255,255,0.05)] border border-[rgba(255,255,255,0.07)] flex items-center justify-center active:scale-95 transition-transform"
          >
            <RefreshCw
              size={16}
              className={`text-[#888] ${status.isFetching ? 'animate-spin' : ''}`}
            />
          </button>
        </div>
      </div>

      {status.isError && (
        <div className="rounded-[20px] bg-[rgba(255,69,58,0.06)] border border-[rgba(255,69,58,0.18)] p-4 mb-4">
          <p className="text-[13px] text-[#FF453A] font-medium">Backend injoignable</p>
          <p className="text-[11px] text-[#FF453A]/70 mt-1">
            Verifier que <code>python main.py</code> tourne sur le port 8081.
          </p>
        </div>
      )}

      <div className="space-y-5">
        {/* Systeme */}
        <Section title="Systeme" icon={Server}>
          <Row label="Utilisateur" value={status.data?.user ?? '—'} />
          <Row
            label="Computer shell"
            value={status.data?.computer?.shell ?? '—'}
            mono
          />
          <Row
            label="Code engine"
            value={status.data?.code_executor?.engine ?? '—'}
          />
        </Section>

        {/* Integrations */}
        <Section title="Integrations" icon={Cpu}>
          {integrationList.map((it) => (
            <IntegrationRow
              key={it.label}
              icon={it.icon}
              label={it.label}
              active={it.ok}
              loading={integrations.isLoading}
            />
          ))}
        </Section>

        {/* Audio */}
        <Section title="Audio" icon={Volume2}>
          <Row
            label="STT"
            value={status.data?.audio?.stt_engine ?? '—'}
            badge={
              status.data?.audio?.stt_available
                ? { label: 'OK', color: '#30D158' }
                : { label: 'KO', color: '#FF453A' }
            }
          />
          <Row
            label="TTS"
            value={status.data?.audio?.tts_backend ?? '—'}
            badge={
              status.data?.audio?.tts_available
                ? { label: 'OK', color: '#30D158' }
                : { label: 'KO', color: '#FF453A' }
            }
          />
          <Row label="Voix" value={status.data?.audio?.tts_voice ?? '—'} mono />
          <Row
            label="Micro PWA"
            value={typeof window !== 'undefined' && navigator.mediaDevices ? 'Disponible' : 'Indisponible'}
            badge={
              typeof window !== 'undefined' && navigator.mediaDevices
                ? { label: 'OK', color: '#30D158' }
                : { label: 'KO', color: '#FF453A' }
            }
          />
        </Section>

        {/* Localisation GPS (PWA + backend) */}
        <LocationConfig />

        {/* Memoire SQLite */}
        <Section title="Memoire" icon={Brain}>
          <div className="grid grid-cols-3 gap-2">
            {memoryStats.map((s) => (
              <MemStat key={s.label} label={s.label} value={s.value} loading={status.isLoading} />
            ))}
          </div>
        </Section>

        {/* LLM */}
        {llmModels.length > 0 && (
          <Section title="LLM" icon={Brain}>
            {llmModels.map((m) => (
              <Row key={m.tier} label={m.tier} value={m.model ?? '—'} mono />
            ))}
          </Section>
        )}

        {/* Watchers */}
        <Section title="Watchers" icon={Eye}>
          <Row
            label="Email watcher"
            value={
              status.data?.email_watcher?.running
                ? `${status.data.email_watcher.processed_count ?? 0} mails analyses · scan ${status.data.email_watcher.check_interval ?? '?'}s`
                : 'Arrete'
            }
            badge={
              status.data?.email_watcher?.running
                ? { label: 'Actif', color: '#30D158' }
                : { label: 'Arrete', color: '#FF453A' }
            }
          />
        </Section>
      </div>

      <BottomNav />
    </main>
  );
}

// ─────────────────────────────────────────────────────────────
// Sub-components
// ─────────────────────────────────────────────────────────────

function Section({
  title,
  icon: Icon,
  children,
}: {
  title: string;
  icon: LucideIcon;
  children: React.ReactNode;
}) {
  return (
    <section>
      <div className="flex items-center gap-2 mb-2 px-1">
        <Icon size={12} className="text-[#555]" />
        <h2 className="text-[11px] font-bold tracking-[0.15em] uppercase text-[#555]">{title}</h2>
      </div>
      <div className="rounded-[20px] bg-[rgba(255,255,255,0.035)] border border-[rgba(255,255,255,0.07)] divide-y divide-[rgba(255,255,255,0.04)]">
        {children}
      </div>
    </section>
  );
}

function Row({
  label,
  value,
  mono,
  badge,
}: {
  label: string;
  value: string;
  mono?: boolean;
  badge?: { label: string; color: string };
}) {
  return (
    <div className="flex items-center justify-between gap-3 px-4 py-3 min-h-[44px]">
      <span className="text-[13px] text-[#aaa] font-medium flex-shrink-0">{label}</span>
      <div className="flex items-center gap-2 min-w-0">
        <span
          className={`text-[12px] text-[#888] truncate text-right ${mono ? 'font-mono' : ''}`}
        >
          {value}
        </span>
        {badge && (
          <span
            className="text-[10px] font-bold px-1.5 py-0.5 rounded-full flex-shrink-0"
            style={{
              backgroundColor: `${badge.color}1f`,
              color: badge.color,
              border: `1px solid ${badge.color}44`,
            }}
          >
            {badge.label}
          </span>
        )}
      </div>
    </div>
  );
}

function IntegrationRow({
  icon: Icon,
  label,
  active,
  loading,
}: {
  icon: LucideIcon;
  label: string;
  active: boolean;
  loading?: boolean;
}) {
  return (
    <div className="flex items-center gap-3 px-4 py-3 min-h-[44px]">
      <div
        className={`w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 ${
          active ? 'bg-[rgba(48,209,88,0.12)]' : 'bg-[rgba(255,255,255,0.04)]'
        }`}
      >
        <Icon size={15} className={active ? 'text-[#30D158]' : 'text-[#555]'} />
      </div>
      <span className="text-[13px] text-[#ccc] flex-1">{label}</span>
      {loading ? (
        <div className="h-4 w-12 rounded-full bg-[rgba(255,255,255,0.05)] animate-pulse" />
      ) : (
        <span
          className="text-[10px] font-bold px-2 py-0.5 rounded-full"
          style={{
            backgroundColor: active ? 'rgba(48,209,88,0.12)' : 'rgba(255,69,58,0.12)',
            color: active ? '#30D158' : '#FF453A',
            border: `1px solid ${active ? 'rgba(48,209,88,0.3)' : 'rgba(255,69,58,0.3)'}`,
          }}
        >
          {active ? 'ACTIF' : 'INACTIF'}
        </span>
      )}
    </div>
  );
}

function MemStat({
  label,
  value,
  loading,
}: {
  label: string;
  value: number;
  loading?: boolean;
}) {
  return (
    <div className="rounded-[12px] bg-[rgba(255,255,255,0.025)] p-3 text-center">
      {loading ? (
        <div className="h-5 w-10 mx-auto rounded bg-[rgba(255,255,255,0.05)] animate-pulse" />
      ) : (
        <div className="text-[18px] font-bold text-white tabular-nums leading-none">
          {value.toLocaleString('fr')}
        </div>
      )}
      <div className="text-[10px] text-[#666] mt-1 font-medium uppercase tracking-wide">
        {label}
      </div>
    </div>
  );
}
