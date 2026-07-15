/**
 * DataView — monitoring des bases de données et export.
 * Design system BIG BROTHER.
 */
import { useCallback, useEffect, useState } from 'react';
import {
  RefreshCw, Database, Users, Brain, MapPin, FileText, MessageSquare,
  Activity, CheckCircle, XCircle, Download, Shield, Layers, Zap,
  Mail, Calendar, Cloud, Smartphone, Bell, Mic, Volume2, Monitor, SlidersHorizontal,
} from 'lucide-react';
import { api } from '@unified/lib/api';
import { formatRelativeTime } from '@desktop/app/lib/timeFormat';

// ── Types ─────────────────────────────────────────────────────

interface StatusData {
  user?: string;
  models?: {
    fast?: string;
    main?: string;
  };
  agents_registered?: string[];
  today?: {
    msg_count?: number;
    total_in?: number;
    total_out?: number;
    total_cost?: number;
  };
  audio?: {
    stt_available?: boolean;
    stt_engine?: string;
    tts_available?: boolean;
    tts_backend?: string;
    tts_voice?: string;
  };
  imessage?: {
    available?: boolean;
    target?: string;
  };
  email_watcher?: {
    running?: boolean;
    check_interval?: number;
    processed_count?: number;
  };
  computer?: {
    available?: boolean;
    shell?: string;
  };
  memory?: {
    user_facts?: number;
    relationship_profiles?: number;
    patterns_active?: number;
    episodes?: number;
    people?: number;
    cross_insights?: number;
  };
  location?: {
    tracking?: boolean;
  };
}

interface IntegrationsData {
  mail?: boolean;
  calendar?: boolean | { available?: boolean };
  weather?: boolean;
  imessage?: boolean;
  email_watcher?: boolean;
  computer?: { available?: boolean };
  location_tracking?: boolean;
}

interface NotifItem {
  id: number;
  title?: string;
  content?: string;
  source?: string;
  priority?: string;
  created_at?: string;
  read?: boolean;
}

// ── Constantes ────────────────────────────────────────────────

const DB_GROUPS = [
  {
    id: 'messages',
    label: 'Messages & Conversations',
    icon: MessageSquare,
    description: 'Historique des échanges, conversations, épisodes mémorisés',
    statsKeys: ['episodes'],
  },
  {
    id: 'contacts',
    label: 'Contacts & Relations',
    icon: Users,
    description: 'Fiches personnes, profils relationnels, événements',
    statsKeys: ['people', 'relationship_profiles'],
  },
  {
    id: 'facts',
    label: 'Faits & Connaissances',
    icon: Brain,
    description: 'Faits sur l\'utilisateur, journal, profil de vie',
    statsKeys: ['user_facts'],
  },
  {
    id: 'patterns',
    label: 'Patterns & Insights',
    icon: Activity,
    description: 'Patterns comportementaux, insights cross-relations, life context',
    statsKeys: ['patterns_active', 'cross_insights'],
  },
  {
    id: 'location',
    label: 'Localisation',
    icon: MapPin,
    description: 'Historique GPS, visites, trajets, lieux nommés',
    statsKeys: [],
  },
  {
    id: 'documents',
    label: 'Documents & Médias',
    icon: FileText,
    description: 'Documents scolaires, devoirs générés, enregistrements',
    statsKeys: [],
  },
];

const AGENT_DESCRIPTIONS: Record<string, string> = {
  info: 'Questions rapides',
  school: 'Cours, exercices',
  productivity: 'Emails, calendar',
  coach: 'Relations, émotions',
  journal: 'Journal, mood',
  memory: 'Mémoire, patterns',
};

const MODELS_INFO = [
  { key: 'fast', label: 'DeepSeek Fast', role: 'Routing, classification, triage, extraction' },
  { key: 'main', label: 'DeepSeek Main', role: 'Agents spécialisés, coaching, rédaction, tâches lourdes' },
];

const PRIORITY_COLOR: Record<string, string> = {
  urgent: 'text-red-400',
  high: 'text-orange-400',
  medium: 'text-yellow-400',
  low: 'text-muted-foreground',
};

const SOURCE_ICON: Record<string, React.ComponentType<{ size?: number; className?: string }>> = {
  email: Mail,
  pattern: Activity,
  calendar: Calendar,
  system: Zap,
  relationship: Users,
};

// ── Composants ────────────────────────────────────────────────

function StatusBadge({ ok, labelOn = 'ACTIF', labelOff = 'INACTIF' }: { ok: boolean; labelOn?: string; labelOff?: string }) {
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${
      ok ? 'bg-green-500/10 text-green-400 border border-green-500/20' : 'bg-red-500/10 text-red-400 border border-red-500/20'
    }`}>
      {ok
        ? <CheckCircle size={10} />
        : <XCircle size={10} />}
      {ok ? labelOn : labelOff}
    </span>
  );
}

function OverviewCard({
  icon: Icon, label, value, sub,
}: { icon: React.ComponentType<{ size?: number; className?: string }>; label: string; value: string | number; sub?: string }) {
  return (
    <div className="bg-white/3 border border-white/8 rounded-2xl p-5 flex flex-col gap-3">
      <div className="w-9 h-9 rounded-xl bg-white/8 flex items-center justify-center">
        <Icon size={18} className="text-white/70" />
      </div>
      <div>
        <p className="text-2xl font-semibold tabular-nums">{value}</p>
        <p className="text-sm text-muted-foreground mt-0.5">{label}</p>
        {sub && <p className="text-xs text-white/30 mt-1">{sub}</p>}
      </div>
    </div>
  );
}

// ── Page principale ───────────────────────────────────────────

const TTS_OPTIONS: { id: string; label: string; sub: string }[] = [
  { id: 'kokoro',     label: 'Kokoro',     sub: 'Local · ONNX · Zéro réseau' },
  { id: 'ttskit',     label: 'TTSKit',     sub: 'Local · Natif · Streaming' },
  { id: 'macos',      label: 'Apple M4',   sub: 'Local · Zéro latence réseau' },
  { id: 'edge',       label: 'Edge',       sub: 'Cloud · Gratuit' },
];

export function DataView() {
  const [status, setStatus] = useState<StatusData>({});
  const [integrations, setIntegrations] = useState<IntegrationsData>({});
  const [notifications, setNotifications] = useState<NotifItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [spinning, setSpinning] = useState(false);
  const [exporting, setExporting] = useState(false);

  // ── TTS engine selector state ──────────────────────────────
  const [ttsEngine, setTtsEngine] = useState<string>('edge');
  const [ttsSaving, setTtsSaving] = useState(false);
  const [ttsToast, setTtsToast] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    const [s, i, n, tts] = await Promise.allSettled([
      api.getStatus(),
      api.getIntegrations(),
      api.getNotifications(),
      api.getTTSSetting(),
    ]);
    if (s.status === 'fulfilled') setStatus(s.value as StatusData);
    if (i.status === 'fulfilled') setIntegrations(i.value as IntegrationsData);
    if (n.status === 'fulfilled') {
      const d = n.value as { notifications?: NotifItem[] };
      setNotifications((d.notifications || []).slice(0, 10));
    }
    if (tts.status === 'fulfilled') setTtsEngine(tts.value.engine || 'edge');
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  async function handleTTSChange(engine: string) {
    if (engine === ttsEngine || ttsSaving) return;
    setTtsSaving(true);
    try {
      await api.setTTSSetting(engine);
      setTtsEngine(engine);
      const label = TTS_OPTIONS.find(o => o.id === engine)?.label || engine;
      setTtsToast(`Moteur vocal changé : ${label}`);
      setTimeout(() => setTtsToast(null), 3000);
    } catch (e) {
      console.error('[TTS] setTTSSetting', e);
      setTtsToast('Erreur : moteur non disponible');
      setTimeout(() => setTtsToast(null), 4000);
    } finally {
      setTtsSaving(false);
    }
  }

  async function handleRefresh() {
    setSpinning(true);
    await load();
    setTimeout(() => setSpinning(false), 600);
  }

  async function handleExport() {
    setExporting(true);
    try {
      const [statusR, peopleR, journalR, tasksR, patternsR, placesR, convsR] = await Promise.allSettled([
        api.getStatus(),
        api.getPeople(),
        api.getJournal(),
        api.getTasks(),
        api.getPatterns(),
        api.getPlaces(),
        api.getConversations(false, 100),
      ]);
      const exportData: Record<string, unknown> = {
        exported_at: new Date().toISOString(),
        version: '1.0',
      };
      const resolveResult = (r: PromiseSettledResult<unknown>, key: string) => {
        if (r.status === 'fulfilled') exportData[key] = r.value;
      };
      resolveResult(statusR, 'status');
      resolveResult(peopleR, 'people');
      resolveResult(journalR, 'journal');
      resolveResult(tasksR, 'tasks');
      resolveResult(patternsR, 'patterns');
      resolveResult(placesR, 'places');
      resolveResult(convsR, 'conversations');

      const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      const date = new Date().toISOString().split('T')[0];
      a.href = url;
      a.download = `jarvis-export-${date}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      console.error('[DataView] export', e);
    } finally {
      setExporting(false);
    }
  }

  const mem = status.memory || {};
  const totalRecords = Object.values(mem).reduce<number>((acc, v) => acc + (Number(v) || 0), 0);

  // Calcul répartition stockage
  const storageSegments = [
    { label: 'Episodes', value: mem.episodes || 0, color: 'bg-white' },
    { label: 'Contacts', value: (mem.people || 0) + (mem.relationship_profiles || 0), color: 'bg-white/60' },
    { label: 'Faits', value: mem.user_facts || 0, color: 'bg-white/40' },
    { label: 'Patterns', value: (mem.patterns_active || 0) + (mem.cross_insights || 0), color: 'bg-white/20' },
  ];
  const storageTotal = storageSegments.reduce((acc, s) => acc + s.value, 0) || 1;

  // Intégrations list
  const integrationsList = [
    { id: 'mail', label: 'Apple Mail', icon: Mail, active: integrations.mail ?? false },
    {
      id: 'calendar',
      label: 'Calendar.app',
      icon: Calendar,
      active:
        typeof integrations.calendar === 'boolean'
          ? integrations.calendar
          : integrations.calendar?.available ?? false,
    },
    { id: 'weather', label: 'Météo (OpenWeatherMap)', icon: Cloud, active: integrations.weather ?? false },
    { id: 'imessage', label: 'iMessage Bridge', icon: Smartphone, active: integrations.imessage ?? false },
    { id: 'email_watcher', label: 'Email Watcher', icon: Bell, active: integrations.email_watcher ?? false },
    { id: 'stt', label: 'STT local', icon: Mic, active: status.audio?.stt_available ?? false },
    { id: 'tts', label: 'TTS (' + (status.audio?.tts_backend || 'Edge') + ')', icon: Volume2, active: status.audio?.tts_available ?? false },
    { id: 'computer', label: 'Contrôle Mac', icon: Monitor, active: (integrations.computer as { available?: boolean })?.available ?? false },
  ];

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full text-muted-foreground">
        <RefreshCw size={20} className="animate-spin mr-2" />
        Chargement…
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full overflow-y-auto">
      <div className="p-6 space-y-8 max-w-6xl w-full mx-auto">

        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-semibold">Données</h1>
            <p className="text-sm text-muted-foreground mt-0.5">Monitoring des bases de données et export</p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={handleExport}
              disabled={exporting}
              className="flex items-center gap-2 px-4 py-2 bg-white/5 border border-white/10 rounded-xl text-sm hover:bg-white/10 hover:border-white/20 transition-colors disabled:opacity-50"
            >
              <Download size={14} />
              {exporting ? 'Export…' : 'Exporter'}
            </button>
            <button
              onClick={handleRefresh}
              className="group flex items-center gap-2 px-4 py-2 bg-white text-black rounded-xl text-sm font-medium hover:bg-white/90 transition-colors"
            >
              <RefreshCw size={14} className={spinning ? 'animate-spin' : 'group-hover:rotate-180 transition-transform duration-300'} />
              Synchroniser
            </button>
          </div>
        </div>

        {/* ── 1. Overview ───────────────────────────────────── */}
        <section>
          <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-widest mb-3">Vue d'ensemble</h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <OverviewCard icon={Database} label="Enregistrements total" value={totalRecords.toLocaleString('fr')} sub="toutes tables confondues" />
            <OverviewCard icon={Layers} label="Bases de données" value={6} sub="groupes de tables SQLite" />
            <OverviewCard icon={Shield} label="Chiffrement" value="AES-256" sub="données locales macOS" />
            <OverviewCard
              icon={CheckCircle}
              label="Intégrations actives"
              value={integrationsList.filter(i => i.active).length}
              sub={`sur ${integrationsList.length} configurées`}
            />
          </div>
        </section>

        {/* ── 2. Bases de données ───────────────────────────── */}
        <section>
          <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-widest mb-3">Bases de données</h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {DB_GROUPS.map(g => {
              const Icon = g.icon;
              const count = g.statsKeys.reduce((acc, k) => acc + (mem[k as keyof typeof mem] || 0), 0);
              return (
                <div key={g.id} className="bg-white/3 border border-white/8 rounded-2xl p-4 flex items-start gap-4">
                  <div className="w-10 h-10 rounded-xl bg-white/8 flex items-center justify-center shrink-0">
                    <Icon size={18} className="text-white/70" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between gap-2 flex-wrap">
                      <p className="text-sm font-medium">{g.label}</p>
                      <StatusBadge ok labelOn="Sain" />
                    </div>
                    <p className="text-xs text-muted-foreground mt-1 leading-relaxed">{g.description}</p>
                    {g.statsKeys.length > 0 && (
                      <p className="text-xs text-white/50 mt-2 font-mono">{count.toLocaleString('fr')} entrées</p>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        {/* ── 3. Répartition stockage ───────────────────────── */}
        <section>
          <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-widest mb-3">Répartition du stockage</h2>
          <div className="bg-white/3 border border-white/8 rounded-2xl p-5 space-y-4">
            {/* Barre segmentée */}
            <div className="flex h-3 rounded-full overflow-hidden gap-px">
              {storageSegments.map(s => {
                const pct = (s.value / storageTotal) * 100;
                if (pct < 0.5) return null;
                return (
                  <div
                    key={s.label}
                    className={`${s.color} transition-all`}
                    style={{ width: `${pct}%` }}
                    title={`${s.label} : ${s.value}`}
                  />
                );
              })}
            </div>
            {/* Légende */}
            <div className="flex flex-wrap gap-4">
              {storageSegments.map(s => {
                const pct = storageTotal > 0 ? ((s.value / storageTotal) * 100).toFixed(1) : '0.0';
                return (
                  <div key={s.label} className="flex items-center gap-2 text-xs">
                    <div className={`w-2.5 h-2.5 rounded-sm ${s.color} shrink-0`} />
                    <span className="text-muted-foreground">{s.label}</span>
                    <span className="font-mono text-white/60">{s.value.toLocaleString('fr')}</span>
                    <span className="text-white/30">({pct}%)</span>
                  </div>
                );
              })}
            </div>
          </div>
        </section>

        {/* ── 4. Intégrations ──────────────────────────────── */}
        <section>
          <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-widest mb-3">Intégrations</h2>
          <div className="bg-white/3 border border-white/8 rounded-2xl divide-y divide-white/5">
            {integrationsList.map(item => {
              const Icon = item.icon;
              return (
                <div key={item.id} className="flex items-center gap-4 px-5 py-3">
                  <div className="w-8 h-8 rounded-lg bg-white/5 flex items-center justify-center shrink-0">
                    <Icon size={15} className="text-white/60" />
                  </div>
                  <span className="text-sm flex-1">{item.label}</span>
                  <StatusBadge ok={item.active} />
                </div>
              );
            })}
          </div>
        </section>

        {/* ── 5. Agents ────────────────────────────────────── */}
        <section>
          <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-widest mb-3">Agents</h2>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
            {(status.agents_registered || []).map(a => (
              <div key={a} className="bg-white/3 border border-white/8 rounded-2xl p-4 flex flex-col gap-2">
                <div className="flex items-center gap-2">
                  <div className="w-2 h-2 rounded-full bg-green-400 shrink-0" />
                  <span className="text-xs font-mono font-semibold uppercase tracking-wide">{a}</span>
                </div>
                <p className="text-xs text-muted-foreground">{AGENT_DESCRIPTIONS[a] || '—'}</p>
              </div>
            ))}
          </div>
        </section>

        {/* ── 6. Modèles LLM ───────────────────────────────── */}
        <section>
          <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-widest mb-3">Modèles LLM</h2>
          <div className="bg-white/3 border border-white/8 rounded-2xl overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-white/8">
                    <th className="text-left px-5 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-widest w-24">Alias</th>
                    <th className="text-left px-5 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-widest">Model ID</th>
                    <th className="text-left px-5 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-widest">Rôle</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/5">
                  {MODELS_INFO.map(m => (
                    <tr key={m.key} className="hover:bg-white/2 transition-colors">
                      <td className="px-5 py-3 font-medium text-white/80">{m.label}</td>
                      <td className="px-5 py-3">
                        <code className="text-xs font-mono text-white/50 bg-white/5 px-2 py-0.5 rounded">
                          {status.models?.[m.key as keyof typeof status.models] || '—'}
                        </code>
                      </td>
                      <td className="px-5 py-3 text-muted-foreground text-xs">{m.role}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </section>

        {/* ── 7. Activité récente ───────────────────────────── */}
        <section>
          <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-widest mb-3">Activité récente</h2>
          <div className="bg-white/3 border border-white/8 rounded-2xl divide-y divide-white/5">
            {notifications.length === 0 ? (
              <div className="px-5 py-8 text-center text-muted-foreground text-sm">
                Aucune notification récente
              </div>
            ) : (
              notifications.map(n => {
                const Src = SOURCE_ICON[n.source || 'system'] || Zap;
                return (
                  <div key={n.id} className="flex items-start gap-4 px-5 py-3">
                    <div className={`w-7 h-7 rounded-lg flex items-center justify-center shrink-0 mt-0.5 ${
                      n.priority === 'urgent' ? 'bg-red-500/15' :
                      n.priority === 'high' ? 'bg-orange-500/15' :
                      'bg-white/5'
                    }`}>
                      <Src size={13} className={PRIORITY_COLOR[n.priority || 'low']} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm leading-snug">{n.title || n.content || '—'}</p>
                      {n.content && n.title && (
                        <p className="text-xs text-muted-foreground mt-0.5 line-clamp-1">{n.content}</p>
                      )}
                    </div>
                    <span className="text-xs font-mono text-white/30 shrink-0 mt-0.5">
                      {n.created_at ? formatRelativeTime(n.created_at) : '—'}
                    </span>
                  </div>
                );
              })
            )}
          </div>
        </section>

        {/* ── 8. Moteur Vocal TTS ──────────────────────────── */}
        <section>
          <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-widest mb-3">Moteur Vocal</h2>
          <div className="bg-white/3 border border-white/8 rounded-2xl p-5 space-y-4">
            <div className="flex items-start gap-4">
              <div className="w-10 h-10 rounded-xl bg-white/8 flex items-center justify-center shrink-0">
                <SlidersHorizontal size={18} className="text-white/70" />
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium">Synthèse vocale (TTS)</p>
                <p className="text-xs text-muted-foreground mt-1 leading-relaxed">
                  Changement instantané, sans redémarrage. Le moteur actif sera utilisé pour toutes les réponses vocales.
                </p>
              </div>
            </div>

            {/* Segmented Control */}
            <div className="flex gap-1.5 p-1 bg-black/30 border border-white/8 rounded-xl">
              {TTS_OPTIONS.map(opt => {
                const active = ttsEngine === opt.id;
                return (
                  <button
                    key={opt.id}
                    onClick={() => handleTTSChange(opt.id)}
                    disabled={ttsSaving}
                    className={`
                      flex-1 flex flex-col items-center gap-0.5 px-3 py-2.5 rounded-lg text-center
                      transition-all duration-200 disabled:opacity-60
                      ${active
                        ? 'bg-white text-black shadow-sm'
                        : 'hover:bg-white/6 text-white/70 hover:text-white'}
                    `}
                  >
                    <span className={`text-xs font-semibold font-mono tracking-wide ${active ? 'text-black' : ''}`}>
                      {opt.label}
                    </span>
                    <span className={`text-[10px] leading-tight ${active ? 'text-black/60' : 'text-white/35'}`}>
                      {opt.sub}
                    </span>
                  </button>
                );
              })}
            </div>

            {/* Toast inline */}
            {ttsToast && (
              <div className={`flex items-center gap-2 px-3 py-2 rounded-lg text-xs border ${
                ttsToast.startsWith('Erreur')
                  ? 'bg-red-500/10 border-red-500/20 text-red-400'
                  : 'bg-green-500/10 border-green-500/20 text-green-400'
              }`}>
                {ttsToast.startsWith('Erreur') ? <XCircle size={13} /> : <CheckCircle size={13} />}
                {ttsToast}
              </div>
            )}
          </div>
        </section>

        {/* ── 9. Export ─────────────────────────────────────── */}
        <section>
          <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-widest mb-3">Export</h2>
          <div className="bg-white/3 border border-white/8 rounded-2xl p-5">
            <div className="flex items-start gap-4">
              <div className="w-10 h-10 rounded-xl bg-white/8 flex items-center justify-center shrink-0">
                <Download size={18} className="text-white/70" />
              </div>
              <div className="flex-1">
                <p className="text-sm font-medium">Export complet des données</p>
                <p className="text-xs text-muted-foreground mt-1 leading-relaxed">
                  Génère un fichier JSON contenant status, contacts, journal, tâches, patterns, lieux et conversations.
                  Fichier nommé <code className="font-mono text-white/50">jarvis-export-YYYY-MM-DD.json</code>.
                </p>
              </div>
              <button
                onClick={handleExport}
                disabled={exporting}
                className="flex items-center gap-2 px-4 py-2 bg-white text-black rounded-xl text-sm font-medium hover:bg-white/90 transition-colors disabled:opacity-50 shrink-0"
              >
                <Download size={14} />
                {exporting ? 'Export en cours…' : 'Exporter'}
              </button>
            </div>
          </div>
        </section>

        {/* Padding bas */}
        <div className="h-4" />
      </div>
    </div>
  );
}
