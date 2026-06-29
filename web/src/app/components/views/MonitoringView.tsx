/**
 * MonitoringView — page de test et monitoring complète de JARVIS.
 *
 * 3 onglets :
 *  - Endpoints : grille de tous les endpoints REST avec bouton test + latence
 *  - Features  : cards par intégration (Mail, Calendar, Weather, etc.)
 *  - Live      : monitoring temps réel via WS + polling /api/status + logs
 *
 * Design system BIG BROTHER aligné sur DataView / LogsView.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity, Calendar, Cloud, Cpu, FileCode, Mail, MapPin,
  MessageSquare, Mic, Monitor, Pause, Play, Plus, RefreshCw,
  Smartphone, Square, Volume2, Wifi, X, CheckCircle, XCircle,
  AlertCircle, Database, Bot, Search,
} from 'lucide-react';
import { api } from '@/services/api';
import { ws } from '@/services/websocket';
import { formatRelativeTime } from '@/app/lib/timeFormat';

// ─────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────

type Tab = 'endpoints' | 'features' | 'live';

interface EndpointSpec {
  id: string;
  method: 'GET' | 'POST' | 'PATCH' | 'DELETE' | 'PUT';
  path: string;
  category: string;
  description: string;
  /** Exécute l'appel et retourne le payload brut (ou throw). */
  call: () => Promise<unknown>;
}

interface EndpointState {
  status: 'idle' | 'running' | 'ok' | 'error';
  httpCode?: number;
  latencyMs?: number;
  response?: unknown;
  error?: string;
  testedAt?: string;
}

interface FeatureSpec {
  id: string;
  label: string;
  icon: React.ComponentType<{ size?: number; className?: string }>;
  description: string;
  /** Test du feature : retourne {ok, summary, details}. */
  test: () => Promise<{ ok: boolean; summary: string; details?: unknown }>;
}

interface FeatureState {
  status: 'idle' | 'running' | 'ok' | 'error';
  summary?: string;
  details?: unknown;
  testedAt?: string;
}

interface StatusSnapshot {
  today?: {
    msg_count?: number;
    total_in?: number;
    total_out?: number;
    total_cost?: number;
  };
  memory?: Record<string, number>;
  email_watcher?: { processed_count?: number };
}

interface LiveSample {
  t: number; // timestamp ms
  endpoint: string;
  latencyMs: number;
  ok: boolean;
}

interface LogEntry {
  id: number;
  agent: string | null;
  action_type: string | null;
  status: 'success' | 'error' | 'pending';
  execution_time_ms: number | null;
  created_at: string;
}

// ─────────────────────────────────────────────────────────────
// Définition des endpoints (extrait de main.py)
// ─────────────────────────────────────────────────────────────

const ENDPOINTS: EndpointSpec[] = [
  // Status / system
  { id: 'status', method: 'GET', path: '/api/status', category: 'Système',
    description: 'État global du backend', call: () => api.getStatus() },
  { id: 'integrations', method: 'GET', path: '/api/integrations', category: 'Système',
    description: 'Disponibilité de chaque intégration', call: () => api.getIntegrations() },
  { id: 'tts-setting', method: 'GET', path: '/api/settings/tts', category: 'Système',
    description: 'Moteur TTS actif', call: () => api.getTTSSetting() },
  { id: 'logs', method: 'GET', path: '/api/logs', category: 'Système',
    description: 'Logs d\'actions LLM', call: () => api.getLogs({ limit: 5 }) },

  // Mémoire / profil
  { id: 'memory', method: 'GET', path: '/api/memory', category: 'Mémoire',
    description: 'Mémoire complète (life profile + people + episodes)', call: () => api.getMemory() },
  { id: 'life-profile', method: 'GET', path: '/api/life-profile', category: 'Mémoire',
    description: 'Profil de vie de l\'utilisateur', call: () => api.getLifeProfile() },
  { id: 'patterns', method: 'GET', path: '/api/patterns', category: 'Mémoire',
    description: 'Patterns comportementaux actifs', call: () => api.getPatterns() },

  // People
  { id: 'people', method: 'GET', path: '/api/people', category: 'Contacts',
    description: 'Liste des contacts triés par récence', call: () => api.getPeople() },
  { id: 'contacts', method: 'GET', path: '/api/contacts', category: 'Contacts',
    description: 'Contacts iMessage (handles)', call: () => api.getMacContacts() },

  // Conversations
  { id: 'conversations', method: 'GET', path: '/api/conversations', category: 'Conversations',
    description: 'Conversations récentes', call: () => api.getConversations(false, 20) },

  // Tasks
  { id: 'tasks', method: 'GET', path: '/api/tasks', category: 'Tâches',
    description: 'Toutes les tâches', call: () => api.getTasks() },

  // Notifications
  { id: 'notifications', method: 'GET', path: '/api/notifications', category: 'Notifications',
    description: 'Notifications non lues', call: () => api.getNotifications() },

  // Briefing / journal
  { id: 'briefing-morning', method: 'GET', path: '/api/briefing?kind=morning', category: 'Briefing',
    description: 'Briefing du matin', call: () => api.getBriefing('morning') },
  { id: 'journal', method: 'GET', path: '/api/journal', category: 'Briefing',
    description: 'Journal + moods', call: () => api.getJournal() },

  // Calendar
  { id: 'calendar', method: 'GET', path: '/api/calendar', category: 'Calendar',
    description: 'Événements du jour', call: () => {
      const now = new Date();
      const start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
      const end = new Date(start.getTime() + 24 * 3600 * 1000);
      return api.getCalendarEvents(start.toISOString(), end.toISOString());
    }
  },

  // Localisation
  { id: 'location-status', method: 'GET', path: '/api/location/status', category: 'Localisation',
    description: 'Statut GPS courant', call: () => api.getLocationStatus() },
  { id: 'places', method: 'GET', path: '/api/places', category: 'Localisation',
    description: 'Lieux nommés', call: () => api.getPlaces() },
  { id: 'visits-today', method: 'GET', path: '/api/visits/today', category: 'Localisation',
    description: 'Visites du jour', call: () => api.getTodayVisits() },
  { id: 'trips', method: 'GET', path: '/api/trips?days=7', category: 'Localisation',
    description: 'Trajets 7 derniers jours', call: () => api.getTrips(7) },

  // Daemon
  { id: 'devices', method: 'GET', path: '/api/devices', category: 'Daemon',
    description: 'Appareils connectés', call: () => api.getDevices() },
  { id: 'screen-current', method: 'GET', path: '/api/screen-activity/current', category: 'Daemon',
    description: 'Contexte écran courant', call: () => api.getCurrentScreenContext() },
  { id: 'app-usage', method: 'GET', path: '/api/app-usage?days=1', category: 'Daemon',
    description: 'Usage applications (1 jour)', call: () => api.getAppUsage(1) },

  // Outputs / recordings
  { id: 'outputs', method: 'GET', path: '/api/outputs', category: 'Fichiers',
    description: 'Fichiers générés par les agents', call: () => api.getOutputs() },
  { id: 'recordings', method: 'GET', path: '/api/recordings', category: 'Fichiers',
    description: 'Enregistrements vocaux', call: () => api.getRecordings(10) },
];

// ─────────────────────────────────────────────────────────────
// Définition des features
// ─────────────────────────────────────────────────────────────

const FEATURES: FeatureSpec[] = [
  {
    id: 'mail',
    label: 'Apple Mail',
    icon: Mail,
    description: 'Lecture des emails via Mail.app + AppleScript',
    test: async () => {
      const integ = (await api.getIntegrations()) as { mail?: boolean };
      const status = (await api.getStatus()) as { email_watcher?: { processed_count?: number } };
      const processed = status?.email_watcher?.processed_count ?? 0;
      return {
        ok: !!integ?.mail,
        summary: integ?.mail
          ? `Mail.app accessible · ${processed} mails analysés`
          : 'Mail.app indisponible (vérifier Automation)',
        details: { integ, email_watcher: status?.email_watcher },
      };
    },
  },
  {
    id: 'calendar',
    label: 'Calendar',
    icon: Calendar,
    description: 'Lecture / création d\'événements Calendar.app',
    test: async () => {
      const integ = (await api.getIntegrations()) as { calendar?: { available?: boolean; error?: string | null } };
      const cal = integ?.calendar;
      const ok = !!cal?.available;
      const now = new Date();
      const start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
      const end = new Date(start.getTime() + 24 * 3600 * 1000);
      let count = 0;
      try {
        const events = (await api.getCalendarEvents(start.toISOString(), end.toISOString())) as { count?: number };
        count = events?.count ?? 0;
      } catch {
        // ignore
      }
      return {
        ok,
        summary: ok
          ? `Calendar.app accessible · ${count} événement(s) aujourd'hui`
          : cal?.error || 'Calendar.app indisponible',
        details: { integ },
      };
    },
  },
  {
    id: 'weather',
    label: 'Météo',
    icon: Cloud,
    description: 'OpenWeatherMap pour Lille',
    test: async () => {
      const integ = (await api.getIntegrations()) as { weather?: boolean };
      return {
        ok: !!integ?.weather,
        summary: integ?.weather ? 'API météo configurée' : 'WEATHER_API_KEY manquante',
        details: integ,
      };
    },
  },
  {
    id: 'imessage',
    label: 'iMessage',
    icon: MessageSquare,
    description: 'Lecture chat.db + envoi via osascript',
    test: async () => {
      const status = (await api.getStatus()) as { imessage?: { available?: boolean; target?: string | null } };
      const ok = !!status?.imessage?.available;
      return {
        ok,
        summary: ok
          ? `Bridge actif · cible : ${status.imessage?.target || '—'}`
          : 'Pas d\'accès chat.db (Full Disk Access requis)',
        details: status?.imessage,
      };
    },
  },
  {
    id: 'screen',
    label: 'Screen Watcher',
    icon: Monitor,
    description: 'Capture + analyse Ollama de l\'écran',
    test: async () => {
      const [devices, current] = await Promise.allSettled([
        api.getDevices(),
        api.getCurrentScreenContext(),
      ]);
      const deviceCount =
        devices.status === 'fulfilled'
          ? ((devices.value as { devices?: unknown[] })?.devices?.length ?? 0)
          : 0;
      const ctx =
        current.status === 'fulfilled'
          ? (current.value as { context?: unknown })?.context
          : null;
      return {
        ok: deviceCount > 0,
        summary: ctx
          ? `${deviceCount} device(s) · contexte récent disponible`
          : `${deviceCount} device(s) enregistré(s) · pas de contexte récent (>5 min)`,
        details: { devices: devices.status === 'fulfilled' ? devices.value : devices.reason, current: ctx },
      };
    },
  },
  {
    id: 'email-watcher',
    label: 'Email Watcher',
    icon: Mail,
    description: 'Worker background qui analyse les mails entrants',
    test: async () => {
      const status = (await api.getStatus()) as { email_watcher?: { running?: boolean; check_interval?: number; processed_count?: number } };
      const ew = status?.email_watcher;
      return {
        ok: !!ew?.running,
        summary: ew?.running
          ? `Worker actif · scan toutes les ${ew.check_interval}s · ${ew.processed_count} traités`
          : 'Worker arrêté',
        details: ew,
      };
    },
  },
  {
    id: 'agents-llm',
    label: 'Agents LLM',
    icon: Bot,
    description: 'Pipeline Claude (Haiku + Sonnet + Opus)',
    test: async () => {
      const status = (await api.getStatus()) as {
        agents_registered?: string[];
        today?: { msg_count?: number; total_cost?: number };
      };
      const count = status?.agents_registered?.length ?? 0;
      const today = status?.today;
      return {
        ok: count >= 5,
        summary: `${count} agents enregistrés · ${today?.msg_count ?? 0} msgs aujourd'hui · $${(today?.total_cost ?? 0).toFixed(4)}`,
        details: { agents: status?.agents_registered, today },
      };
    },
  },
  {
    id: 'tts',
    label: 'TTS',
    icon: Volume2,
    description: 'Synthèse vocale (Edge / ElevenLabs / Kokoro)',
    test: async () => {
      const setting = (await api.getTTSSetting()) as { engine?: string };
      const status = (await api.getStatus()) as { audio?: { tts_available?: boolean; tts_backend?: string; tts_voice?: string } };
      return {
        ok: !!status?.audio?.tts_available,
        summary: status?.audio?.tts_available
          ? `Backend : ${setting?.engine || status?.audio?.tts_backend} · voix : ${status?.audio?.tts_voice}`
          : 'TTS indisponible',
        details: { setting, audio: status?.audio },
      };
    },
  },
  {
    id: 'stt',
    label: 'STT',
    icon: Mic,
    description: 'Speech-to-text (ElevenLabs Scribe)',
    test: async () => {
      const status = (await api.getStatus()) as { audio?: { stt_available?: boolean; stt_engine?: string } };
      return {
        ok: !!status?.audio?.stt_available,
        summary: status?.audio?.stt_available
          ? `Engine : ${status.audio?.stt_engine}`
          : 'STT indisponible',
        details: status?.audio,
      };
    },
  },
  {
    id: 'location',
    label: 'Localisation',
    icon: MapPin,
    description: 'Tracking GPS + lieux + visites',
    test: async () => {
      const [statusR, placesR] = await Promise.allSettled([
        api.getLocationStatus(),
        api.getPlaces(),
      ]);
      const tracking = statusR.status === 'fulfilled'
        ? (statusR.value as { tracking_enabled?: boolean })?.tracking_enabled
        : false;
      const placesCount = placesR.status === 'fulfilled'
        ? ((placesR.value as { places?: unknown[] })?.places?.length ?? 0)
        : 0;
      return {
        ok: !!tracking,
        summary: tracking
          ? `Tracking actif · ${placesCount} lieux nommés`
          : 'Tracking désactivé',
        details: {
          status: statusR.status === 'fulfilled' ? statusR.value : statusR.reason,
          places_count: placesCount,
        },
      };
    },
  },
  {
    id: 'code-executor',
    label: 'Code Executor',
    icon: FileCode,
    description: 'Exécution de code avancée (Open Interpreter)',
    test: async () => {
      const status = (await api.getStatus()) as { code_executor?: { available?: boolean; engine?: string } };
      const ce = status?.code_executor;
      return {
        ok: !!ce?.available,
        summary: ce?.available
          ? `Engine : ${ce.engine}`
          : `Mode basique (engine : ${ce?.engine || '—'})`,
        details: ce,
      };
    },
  },
  {
    id: 'memory-db',
    label: 'Mémoire SQLite',
    icon: Database,
    description: 'Stats des tables principales',
    test: async () => {
      const status = (await api.getStatus()) as { memory?: Record<string, number> };
      const mem = status?.memory || {};
      const total = Object.values(mem).reduce<number>((a, b) => a + (Number(b) || 0), 0);
      return {
        ok: total > 0,
        summary: `${(mem.people ?? 0).toLocaleString('fr')} contacts · ${(mem.user_facts ?? 0).toLocaleString('fr')} faits · ${(mem.episodes ?? 0).toLocaleString('fr')} episodes`,
        details: mem,
      };
    },
  },
  {
    id: 'search',
    label: 'Recherche',
    icon: Search,
    description: 'Recherche transversale (people + episodes + docs)',
    test: async () => {
      const res = (await api.search('test')) as { results?: unknown[]; query?: string };
      const count = res?.results?.length ?? 0;
      return {
        ok: count >= 0,
        summary: `Recherche "test" → ${count} résultat(s)`,
        details: res,
      };
    },
  },
];

// ─────────────────────────────────────────────────────────────
// Helpers UI
// ─────────────────────────────────────────────────────────────

function StatusDot({ status }: { status: EndpointState['status'] | FeatureState['status'] }) {
  const color =
    status === 'ok' ? 'bg-green-400'
    : status === 'error' ? 'bg-red-400'
    : status === 'running' ? 'bg-blue-400 animate-pulse'
    : 'bg-white/20';
  return <div className={`w-2 h-2 rounded-full shrink-0 ${color}`} />;
}

function MethodBadge({ method }: { method: string }) {
  const cls =
    method === 'GET' ? 'bg-blue-500/10 text-blue-400 border-blue-500/20'
    : method === 'POST' ? 'bg-green-500/10 text-green-400 border-green-500/20'
    : method === 'PATCH' || method === 'PUT' ? 'bg-yellow-500/10 text-yellow-400 border-yellow-500/20'
    : 'bg-red-500/10 text-red-400 border-red-500/20';
  return (
    <span className={`text-[10px] font-mono font-semibold px-1.5 py-0.5 rounded border ${cls}`}>
      {method}
    </span>
  );
}

function JsonViewer({ value }: { value: unknown }) {
  let str: string;
  try {
    str = JSON.stringify(value, null, 2);
  } catch {
    str = String(value);
  }
  if (str.length > 8000) {
    str = str.slice(0, 8000) + '\n…tronqué…';
  }
  return (
    <pre className="text-[11px] font-mono text-white/60 bg-black/40 border border-white/8 rounded-lg p-3 max-h-[400px] overflow-auto whitespace-pre-wrap break-all">
      {str}
    </pre>
  );
}

// ─────────────────────────────────────────────────────────────
// Onglet Endpoints
// ─────────────────────────────────────────────────────────────

function EndpointsTab({
  states, onTest, onTestAll, running,
}: {
  states: Record<string, EndpointState>;
  onTest: (id: string) => void;
  onTestAll: () => void;
  running: boolean;
}) {
  const [openResponse, setOpenResponse] = useState<string | null>(null);
  const [categoryFilter, setCategoryFilter] = useState<string>('all');

  const categories = useMemo(() => {
    const set = new Set(ENDPOINTS.map((e) => e.category));
    return ['all', ...Array.from(set)];
  }, []);

  const filtered = useMemo(
    () => (categoryFilter === 'all' ? ENDPOINTS : ENDPOINTS.filter((e) => e.category === categoryFilter)),
    [categoryFilter],
  );

  const counts = useMemo(() => {
    const all = Object.values(states);
    return {
      ok: all.filter((s) => s.status === 'ok').length,
      error: all.filter((s) => s.status === 'error').length,
      idle: ENDPOINTS.length - all.filter((s) => s.status === 'ok' || s.status === 'error').length,
    };
  }, [states]);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-2 text-xs">
          <span className="flex items-center gap-1.5 px-2.5 py-1 bg-green-500/10 text-green-400 rounded-md border border-green-500/20">
            <CheckCircle size={11} /> {counts.ok} OK
          </span>
          <span className="flex items-center gap-1.5 px-2.5 py-1 bg-red-500/10 text-red-400 rounded-md border border-red-500/20">
            <XCircle size={11} /> {counts.error} erreur(s)
          </span>
          <span className="flex items-center gap-1.5 px-2.5 py-1 bg-white/5 text-white/50 rounded-md border border-white/10">
            <AlertCircle size={11} /> {counts.idle} non testé(s)
          </span>
        </div>
        <button
          onClick={onTestAll}
          disabled={running}
          className="flex items-center gap-2 px-3 py-1.5 bg-white text-black rounded-lg text-xs font-medium hover:bg-white/90 transition-colors disabled:opacity-60"
        >
          <RefreshCw size={12} className={running ? 'animate-spin' : ''} />
          {running ? 'Test en cours…' : 'Tout tester'}
        </button>
      </div>

      <div className="flex gap-1.5 flex-wrap">
        {categories.map((c) => (
          <button
            key={c}
            onClick={() => setCategoryFilter(c)}
            className={`text-xs px-3 py-1 rounded-md border transition-colors ${
              categoryFilter === c
                ? 'bg-white/10 border-white/20 text-white'
                : 'bg-white/3 border-white/8 text-white/50 hover:text-white/80'
            }`}
          >
            {c === 'all' ? 'Toutes' : c}
          </button>
        ))}
      </div>

      <div className="bg-white/3 border border-white/8 rounded-2xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-white/3 border-b border-white/8">
            <tr className="text-left text-xs text-white/40 uppercase tracking-wider">
              <th className="px-4 py-2.5 w-8"></th>
              <th className="px-4 py-2.5 w-16">Méthode</th>
              <th className="px-4 py-2.5">Endpoint</th>
              <th className="px-4 py-2.5 w-32">Statut</th>
              <th className="px-4 py-2.5 w-24">Latence</th>
              <th className="px-4 py-2.5 w-24">Testé</th>
              <th className="px-4 py-2.5 w-32 text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5">
            {filtered.map((ep) => {
              const state = states[ep.id] || { status: 'idle' as const };
              return (
                <tr key={ep.id} className="hover:bg-white/2">
                  <td className="px-4 py-2.5">
                    <StatusDot status={state.status} />
                  </td>
                  <td className="px-4 py-2.5">
                    <MethodBadge method={ep.method} />
                  </td>
                  <td className="px-4 py-2.5">
                    <div className="font-mono text-xs text-white/80 break-all">{ep.path}</div>
                    <div className="text-[11px] text-white/40 mt-0.5">{ep.description}</div>
                  </td>
                  <td className="px-4 py-2.5">
                    {state.status === 'ok' && (
                      <span className="text-[11px] font-mono text-green-400">{state.httpCode || 200}</span>
                    )}
                    {state.status === 'error' && (
                      <span className="text-[11px] font-mono text-red-400 truncate inline-block max-w-[120px]">
                        {state.error || 'ERR'}
                      </span>
                    )}
                    {state.status === 'running' && (
                      <span className="text-[11px] text-blue-400">…</span>
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-[11px] font-mono text-white/60">
                    {state.latencyMs != null ? `${state.latencyMs} ms` : '—'}
                  </td>
                  <td className="px-4 py-2.5 text-[11px] text-white/40">
                    {state.testedAt ? formatRelativeTime(state.testedAt) : '—'}
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    <div className="inline-flex gap-1">
                      <button
                        onClick={() => onTest(ep.id)}
                        disabled={state.status === 'running'}
                        className="text-[11px] px-2 py-1 bg-white/5 hover:bg-white/10 rounded-md border border-white/10 transition-colors disabled:opacity-60"
                      >
                        Test
                      </button>
                      {state.response !== undefined && (
                        <button
                          onClick={() => setOpenResponse(ep.id)}
                          className="text-[11px] px-2 py-1 bg-white/5 hover:bg-white/10 rounded-md border border-white/10 transition-colors"
                        >
                          Voir
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {openResponse && (
        <ResponseModal
          endpoint={ENDPOINTS.find((e) => e.id === openResponse) || null}
          state={states[openResponse]}
          onClose={() => setOpenResponse(null)}
        />
      )}
    </div>
  );
}

function ResponseModal({
  endpoint, state, onClose,
}: {
  endpoint: EndpointSpec | null;
  state: EndpointState | undefined;
  onClose: () => void;
}) {
  if (!endpoint || !state) return null;
  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-6" onClick={onClose}>
      <div className="bg-[#0a0a0f] border border-white/10 rounded-2xl max-w-3xl w-full max-h-[80vh] overflow-hidden flex flex-col" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-3 border-b border-white/8">
          <div className="flex items-center gap-2">
            <MethodBadge method={endpoint.method} />
            <code className="font-mono text-sm">{endpoint.path}</code>
          </div>
          <button onClick={onClose} className="p-1 hover:bg-white/10 rounded">
            <X size={16} />
          </button>
        </div>
        <div className="p-5 overflow-auto">
          {state.error ? (
            <div className="text-sm text-red-400 mb-3">Erreur : {state.error}</div>
          ) : (
            <div className="text-xs text-white/50 mb-3">
              {state.httpCode || 200} · {state.latencyMs} ms
            </div>
          )}
          <JsonViewer value={state.response} />
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// Onglet Features
// ─────────────────────────────────────────────────────────────

function FeaturesTab({
  states, onTest, onTestAll, running,
}: {
  states: Record<string, FeatureState>;
  onTest: (id: string) => void;
  onTestAll: () => void;
  running: boolean;
}) {
  const [openDetails, setOpenDetails] = useState<string | null>(null);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-end">
        <button
          onClick={onTestAll}
          disabled={running}
          className="flex items-center gap-2 px-3 py-1.5 bg-white text-black rounded-lg text-xs font-medium hover:bg-white/90 transition-colors disabled:opacity-60"
        >
          <RefreshCw size={12} className={running ? 'animate-spin' : ''} />
          {running ? 'Test en cours…' : 'Tout tester'}
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
        {FEATURES.map((f) => {
          const state = states[f.id] || { status: 'idle' as const };
          const Icon = f.icon;
          return (
            <div key={f.id} className="bg-white/3 border border-white/8 rounded-2xl p-4 flex flex-col gap-3">
              <div className="flex items-start gap-3">
                <div className="w-9 h-9 rounded-xl bg-white/8 flex items-center justify-center shrink-0">
                  <Icon size={16} className="text-white/70" />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium">{f.label}</span>
                    <StatusDot status={state.status} />
                  </div>
                  <div className="text-[11px] text-white/40 mt-0.5">{f.description}</div>
                </div>
              </div>

              <div className="min-h-[40px] text-xs">
                {state.status === 'idle' && (
                  <span className="text-white/30 italic">Pas encore testé</span>
                )}
                {state.status === 'running' && (
                  <span className="text-blue-400">Test en cours…</span>
                )}
                {state.status === 'ok' && (
                  <span className="text-green-400">{state.summary}</span>
                )}
                {state.status === 'error' && (
                  <span className="text-red-400">{state.summary}</span>
                )}
              </div>

              <div className="flex items-center justify-between gap-2">
                <button
                  onClick={() => onTest(f.id)}
                  disabled={state.status === 'running'}
                  className="text-[11px] px-3 py-1.5 bg-white/5 hover:bg-white/10 rounded-md border border-white/10 transition-colors disabled:opacity-60"
                >
                  Tester
                </button>
                <div className="flex items-center gap-2">
                  {state.details !== undefined && (
                    <button
                      onClick={() => setOpenDetails(f.id)}
                      className="text-[11px] text-white/40 hover:text-white/80"
                    >
                      Détails
                    </button>
                  )}
                  {state.testedAt && (
                    <span className="text-[10px] text-white/30">{formatRelativeTime(state.testedAt)}</span>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {openDetails && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-6" onClick={() => setOpenDetails(null)}>
          <div className="bg-[#0a0a0f] border border-white/10 rounded-2xl max-w-3xl w-full max-h-[80vh] overflow-hidden flex flex-col" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between px-5 py-3 border-b border-white/8">
              <span className="text-sm font-medium">
                {FEATURES.find((f) => f.id === openDetails)?.label}
              </span>
              <button onClick={() => setOpenDetails(null)} className="p-1 hover:bg-white/10 rounded">
                <X size={16} />
              </button>
            </div>
            <div className="p-5 overflow-auto">
              <JsonViewer value={states[openDetails]?.details} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// Onglet Live
// ─────────────────────────────────────────────────────────────

const LIVE_POLL_INTERVAL_MS = 5000;
const LIVE_LOGS_POLL_INTERVAL_MS = 3000;
const LIVE_SAMPLES_MAX = 60;

function LiveTab({ wsConnected }: { wsConnected: boolean }) {
  const [paused, setPaused] = useState(false);
  const [snapshot, setSnapshot] = useState<StatusSnapshot | null>(null);
  const [samples, setSamples] = useState<LiveSample[]>([]);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [wsEvents, setWsEvents] = useState<Array<{ t: number; type: string; preview: string }>>([]);

  const pausedRef = useRef(paused);
  pausedRef.current = paused;

  const samplesRef = useRef<LiveSample[]>([]);
  samplesRef.current = samples;

  const pushSample = useCallback((endpoint: string, latencyMs: number, ok: boolean) => {
    if (pausedRef.current) return;
    const next: LiveSample = { t: Date.now(), endpoint, latencyMs, ok };
    setSamples((prev) => [...prev.slice(-(LIVE_SAMPLES_MAX - 1)), next]);
  }, []);

  // Polling /api/status toutes les 5s
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      if (cancelled || pausedRef.current) return;
      const t0 = performance.now();
      try {
        const data = (await api.getStatus()) as StatusSnapshot;
        const dt = Math.round(performance.now() - t0);
        if (cancelled) return;
        setSnapshot(data);
        pushSample('/api/status', dt, true);
      } catch (e) {
        const dt = Math.round(performance.now() - t0);
        pushSample('/api/status', dt, false);
        console.warn('[MonitoringView] status poll failed', e);
      }
    };
    void tick();
    const id = setInterval(tick, LIVE_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [pushSample]);

  // Polling /api/logs toutes les 3s
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      if (cancelled || pausedRef.current) return;
      const t0 = performance.now();
      try {
        const data = (await api.getLogs({ limit: 30 })) as { logs?: LogEntry[] };
        const dt = Math.round(performance.now() - t0);
        if (cancelled) return;
        setLogs((data?.logs || []).slice(0, 30));
        pushSample('/api/logs', dt, true);
      } catch (e) {
        const dt = Math.round(performance.now() - t0);
        pushSample('/api/logs', dt, false);
        console.warn('[MonitoringView] logs poll failed', e);
      }
    };
    void tick();
    const id = setInterval(tick, LIVE_LOGS_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [pushSample]);

  // Écoute WS : capture les événements pertinents
  useEffect(() => {
    const handler = (data: Record<string, unknown> & { _type?: string }) => {
      if (pausedRef.current) return;
      const type = (data._type || 'unknown') as string;
      // Filtrer les types bruyants (chunks de streaming, audio)
      if (type === 'chunk' || type === 'speaking' || type === 'speech_done') return;
      const preview = JSON.stringify(data).slice(0, 200);
      setWsEvents((prev) => [
        { t: Date.now(), type, preview },
        ...prev.slice(0, 49),
      ]);
    };
    const off = ws.on('*', handler);
    return () => {
      off();
    };
  }, []);

  const today = snapshot?.today || {};
  const mem = snapshot?.memory || {};
  const avgLatency = useMemo(() => {
    if (samples.length === 0) return null;
    return Math.round(samples.reduce((a, s) => a + s.latencyMs, 0) / samples.length);
  }, [samples]);

  const errorRate = useMemo(() => {
    if (samples.length === 0) return 0;
    return Math.round((samples.filter((s) => !s.ok).length / samples.length) * 100);
  }, [samples]);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-2 text-xs">
          <span className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md border ${
            wsConnected
              ? 'bg-green-500/10 text-green-400 border-green-500/20'
              : 'bg-red-500/10 text-red-400 border-red-500/20'
          }`}>
            <Wifi size={11} /> WebSocket {wsConnected ? 'connecté' : 'déconnecté'}
          </span>
          {paused && (
            <span className="flex items-center gap-1.5 px-2.5 py-1 bg-yellow-500/10 text-yellow-400 rounded-md border border-yellow-500/20">
              <Pause size={11} /> En pause
            </span>
          )}
        </div>
        <button
          onClick={() => setPaused((p) => !p)}
          className="flex items-center gap-2 px-3 py-1.5 bg-white/5 hover:bg-white/10 border border-white/10 rounded-lg text-xs transition-colors"
        >
          {paused ? <Play size={12} /> : <Pause size={12} />}
          {paused ? 'Reprendre' : 'Pause'}
        </button>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <LiveStat icon={MessageSquare} label="Messages aujourd'hui" value={today.msg_count ?? 0} />
        <LiveStat icon={Cpu} label="Coût du jour" value={`$${(today.total_cost ?? 0).toFixed(4)}`} />
        <LiveStat icon={Activity} label="Latence moyenne" value={avgLatency != null ? `${avgLatency} ms` : '—'} />
        <LiveStat
          icon={AlertCircle}
          label="Taux d'erreur"
          value={`${errorRate}%`}
          tone={errorRate > 10 ? 'danger' : errorRate > 0 ? 'warning' : 'ok'}
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
        <div className="lg:col-span-2 bg-white/3 border border-white/8 rounded-2xl p-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-medium">Latence (derniers {samples.length} appels)</h3>
            <span className="text-xs text-white/40">{LIVE_POLL_INTERVAL_MS / 1000}s / poll status</span>
          </div>
          <Sparkline samples={samples} />
        </div>
        <div className="bg-white/3 border border-white/8 rounded-2xl p-4">
          <h3 className="text-sm font-medium mb-3">Mémoire SQLite</h3>
          <div className="space-y-2 text-xs">
            {Object.entries(mem).map(([k, v]) => (
              <div key={k} className="flex justify-between">
                <span className="text-white/50 font-mono">{k}</span>
                <span className="font-mono text-white/80">{Number(v).toLocaleString('fr')}</span>
              </div>
            ))}
            {Object.keys(mem).length === 0 && (
              <span className="text-white/30 italic">En attente…</span>
            )}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <div className="bg-white/3 border border-white/8 rounded-2xl p-4">
          <h3 className="text-sm font-medium mb-3">Logs LLM récents</h3>
          <div className="space-y-1 max-h-[300px] overflow-auto font-mono text-[11px]">
            {logs.length === 0 ? (
              <span className="text-white/30 italic">Aucun log</span>
            ) : logs.map((l) => (
              <div key={l.id} className="flex items-center gap-2 py-0.5">
                <span className={`shrink-0 ${l.status === 'success' ? 'text-green-400' : l.status === 'error' ? 'text-red-400' : 'text-yellow-400'}`}>
                  {l.status === 'success' ? '✓' : l.status === 'error' ? '✗' : '…'}
                </span>
                <span className="text-white/40 shrink-0">{new Date(l.created_at).toLocaleTimeString('fr-FR')}</span>
                <span className="text-blue-400 shrink-0">{l.agent || '—'}</span>
                <span className="text-white/60 truncate flex-1">{l.action_type || '—'}</span>
                {l.execution_time_ms != null && (
                  <span className="text-white/30 shrink-0">{l.execution_time_ms}ms</span>
                )}
              </div>
            ))}
          </div>
        </div>

        <div className="bg-white/3 border border-white/8 rounded-2xl p-4">
          <h3 className="text-sm font-medium mb-3">Événements WebSocket</h3>
          <div className="space-y-1 max-h-[300px] overflow-auto font-mono text-[11px]">
            {wsEvents.length === 0 ? (
              <span className="text-white/30 italic">En attente d'événements…</span>
            ) : wsEvents.map((e, i) => (
              <div key={i} className="py-0.5">
                <div className="flex items-center gap-2">
                  <span className="text-white/40 shrink-0">{new Date(e.t).toLocaleTimeString('fr-FR')}</span>
                  <span className="text-purple-400 shrink-0">{e.type}</span>
                </div>
                <div className="text-white/40 pl-[88px] truncate">{e.preview}</div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function LiveStat({
  icon: Icon, label, value, tone,
}: {
  icon: React.ComponentType<{ size?: number; className?: string }>;
  label: string;
  value: string | number;
  tone?: 'ok' | 'warning' | 'danger';
}) {
  const valueClass =
    tone === 'danger' ? 'text-red-400'
    : tone === 'warning' ? 'text-yellow-400'
    : '';
  return (
    <div className="bg-white/3 border border-white/8 rounded-2xl p-4">
      <div className="flex items-center gap-2 text-white/40 text-xs mb-2">
        <Icon size={12} />
        {label}
      </div>
      <div className={`text-2xl font-semibold tabular-nums ${valueClass}`}>{value}</div>
    </div>
  );
}

function Sparkline({ samples }: { samples: LiveSample[] }) {
  if (samples.length === 0) {
    return <div className="h-24 flex items-center justify-center text-xs text-white/30">En attente…</div>;
  }
  const max = Math.max(...samples.map((s) => s.latencyMs), 100);
  const min = 0;
  const w = 100;
  const h = 100;
  const points = samples.map((s, i) => {
    const x = (i / Math.max(samples.length - 1, 1)) * w;
    const y = h - ((s.latencyMs - min) / (max - min)) * h;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  });
  const path = `M ${points.join(' L ')}`;

  return (
    <div className="relative h-24">
      <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className="absolute inset-0 w-full h-full">
        <path d={path} fill="none" stroke="currentColor" strokeWidth="0.6" className="text-blue-400" />
        {samples.map((s, i) => {
          const x = (i / Math.max(samples.length - 1, 1)) * w;
          const y = h - ((s.latencyMs - min) / (max - min)) * h;
          return (
            <circle
              key={i}
              cx={x}
              cy={y}
              r="0.8"
              className={s.ok ? 'fill-blue-400' : 'fill-red-400'}
            />
          );
        })}
      </svg>
      <div className="absolute top-1 right-2 text-[10px] text-white/30 font-mono">max {max}ms</div>
      <div className="absolute bottom-1 right-2 text-[10px] text-white/30 font-mono">0</div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// Page principale
// ─────────────────────────────────────────────────────────────

export function MonitoringView() {
  const [tab, setTab] = useState<Tab>('endpoints');
  const [endpointStates, setEndpointStates] = useState<Record<string, EndpointState>>({});
  const [featureStates, setFeatureStates] = useState<Record<string, FeatureState>>({});
  const [allRunning, setAllRunning] = useState(false);
  const [wsConnected, setWsConnected] = useState(ws.connected);

  // Suivi état WS
  useEffect(() => {
    const id = setInterval(() => setWsConnected(ws.connected), 1000);
    return () => clearInterval(id);
  }, []);

  // Tests
  const testEndpoint = useCallback(async (id: string) => {
    const ep = ENDPOINTS.find((e) => e.id === id);
    if (!ep) return;
    setEndpointStates((s) => ({ ...s, [id]: { ...s[id], status: 'running' } }));
    const t0 = performance.now();
    try {
      const data = await ep.call();
      const dt = Math.round(performance.now() - t0);
      setEndpointStates((s) => ({
        ...s,
        [id]: {
          status: 'ok',
          httpCode: 200,
          latencyMs: dt,
          response: data,
          testedAt: new Date().toISOString(),
        },
      }));
    } catch (e: unknown) {
      const dt = Math.round(performance.now() - t0);
      const err = e as { status?: number; message?: string; body?: string };
      setEndpointStates((s) => ({
        ...s,
        [id]: {
          status: 'error',
          httpCode: err.status,
          latencyMs: dt,
          error: err.message || String(e),
          response: err.body || err.message,
          testedAt: new Date().toISOString(),
        },
      }));
    }
  }, []);

  const testAllEndpoints = useCallback(async () => {
    setAllRunning(true);
    try {
      // Parallèle par paquets de 4 pour ne pas saturer le backend
      const batchSize = 4;
      for (let i = 0; i < ENDPOINTS.length; i += batchSize) {
        const batch = ENDPOINTS.slice(i, i + batchSize);
        await Promise.allSettled(batch.map((ep) => testEndpoint(ep.id)));
      }
    } finally {
      setAllRunning(false);
    }
  }, [testEndpoint]);

  const testFeature = useCallback(async (id: string) => {
    const feat = FEATURES.find((f) => f.id === id);
    if (!feat) return;
    setFeatureStates((s) => ({ ...s, [id]: { ...s[id], status: 'running' } }));
    try {
      const res = await feat.test();
      setFeatureStates((s) => ({
        ...s,
        [id]: {
          status: res.ok ? 'ok' : 'error',
          summary: res.summary,
          details: res.details,
          testedAt: new Date().toISOString(),
        },
      }));
    } catch (e: unknown) {
      const err = e as { message?: string };
      setFeatureStates((s) => ({
        ...s,
        [id]: {
          status: 'error',
          summary: `Erreur : ${err.message || String(e)}`,
          details: e,
          testedAt: new Date().toISOString(),
        },
      }));
    }
  }, []);

  const testAllFeatures = useCallback(async () => {
    setAllRunning(true);
    try {
      const batchSize = 3;
      for (let i = 0; i < FEATURES.length; i += batchSize) {
        const batch = FEATURES.slice(i, i + batchSize);
        await Promise.allSettled(batch.map((f) => testFeature(f.id)));
      }
    } finally {
      setAllRunning(false);
    }
  }, [testFeature]);

  return (
    <div className="flex flex-col h-full overflow-y-auto">
      <div className="p-6 space-y-6 max-w-6xl w-full mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-semibold">Monitoring</h1>
            <p className="text-sm text-muted-foreground mt-0.5">
              Test et monitoring de chaque endpoint et feature de JARVIS
            </p>
          </div>
        </div>

        {/* Onglets */}
        <div className="flex gap-1 bg-white/3 border border-white/8 rounded-xl p-1 w-fit">
          <TabButton active={tab === 'endpoints'} onClick={() => setTab('endpoints')}>
            <Cpu size={13} /> Endpoints ({ENDPOINTS.length})
          </TabButton>
          <TabButton active={tab === 'features'} onClick={() => setTab('features')}>
            <Square size={13} /> Features ({FEATURES.length})
          </TabButton>
          <TabButton active={tab === 'live'} onClick={() => setTab('live')}>
            <Activity size={13} /> Live
          </TabButton>
        </div>

        {tab === 'endpoints' && (
          <EndpointsTab
            states={endpointStates}
            onTest={testEndpoint}
            onTestAll={testAllEndpoints}
            running={allRunning}
          />
        )}
        {tab === 'features' && (
          <FeaturesTab
            states={featureStates}
            onTest={testFeature}
            onTestAll={testAllFeatures}
            running={allRunning}
          />
        )}
        {tab === 'live' && <LiveTab wsConnected={wsConnected} />}

        <div className="h-4" />
      </div>
    </div>
  );
}

function TabButton({
  active, onClick, children,
}: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
        active ? 'bg-white text-black' : 'text-white/60 hover:text-white hover:bg-white/5'
      }`}
    >
      {children}
    </button>
  );
}

// Silence unused-import warnings : icones gardées pour usage futur (boutons toolbar)
void Plus;
