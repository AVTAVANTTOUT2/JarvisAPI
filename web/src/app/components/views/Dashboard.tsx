import { useCallback, useEffect, useState, type ElementType } from 'react';
import { TrendingUp, TrendingDown, MessageSquare, MapPin, Users, Activity, Clock, ArrowUpRight, Zap, Eye, Monitor, Laptop, Smartphone, Headphones } from 'lucide-react';
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar } from 'recharts';
import { api } from '@/services/api';
import type { ApiPerson, NotificationItem } from '@/app/types/jarvis';
import type { DeviceInfo, AudioDaemonStatus, WeeklyStats } from '@/services/api';
import { formatRelativeTime } from '@/app/lib/timeFormat';

const DAY_LABELS = ['Dim', 'Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam'];

/** '2026-07-09' → 'Jeu' (fuseau local, pas d'UTC shift). */
function frDayLabel(isoDate: string): string {
  const d = new Date(`${isoDate}T00:00:00`);
  return Number.isNaN(d.getTime()) ? isoDate : DAY_LABELS[d.getDay()]!;
}

/** 12.5 → '+12.5%' ; null (pas d'historique) → undefined (badge masqué). */
function pctLabel(pct: number | null | undefined): string | undefined {
  if (pct == null) return undefined;
  return `${pct > 0 ? '+' : ''}${pct}%`;
}

interface StatCardProps {
  title: string;
  value: string;
  change?: string;
  changeType?: 'up' | 'down';
  icon: ElementType;
  delay?: number;
}

function StatCard({ title, value, change, changeType, icon: Icon, delay = 0 }: StatCardProps) {
  const [isHovered, setIsHovered] = useState(false);
  
  return (
    <div 
      className="glass-panel rounded-xl p-6 hover:scale-[1.02] transition-all animate-slide-up cursor-pointer group"
      style={{ animationDelay: `${delay}ms` }}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      <div className="flex items-start justify-between mb-4">
        <div className={`w-12 h-12 rounded-xl bg-white/5 border border-white/10 flex items-center justify-center transition-all group-hover:bg-white/10 group-hover:border-white/20 ${
          isHovered ? 'scale-110 rotate-6' : ''
        }`}>
          <Icon className="w-6 h-6 text-white" />
        </div>
        {change !== undefined && (
          <div className={`flex items-center gap-1 px-2 py-1 rounded-lg ${
            changeType === 'up' ? 'text-white bg-white/10' : 'text-gray-400 bg-white/5'
          }`}>
            {changeType === 'up' ? (
              <TrendingUp className="w-4 h-4" />
            ) : (
              <TrendingDown className="w-4 h-4" />
            )}
            <span className="text-sm font-mono">{change}</span>
          </div>
        )}
      </div>
      <div className="space-y-1">
        <p className="text-sm text-muted-foreground">{title}</p>
        <p className="text-3xl font-mono tracking-tight">{value}</p>
      </div>
      <div className="mt-4 pt-4 border-t border-white/5 flex items-center gap-2 text-xs text-muted-foreground">
        <Eye className="w-3 h-3" />
        <span>Dernières 24h</span>
      </div>
    </div>
  );
}

function deviceIcon(type: string | null | undefined): ElementType {
  if (type === 'laptop') return Laptop;
  if (type === 'phone') return Smartphone;
  return Monitor;
}

function CustomTooltip({ active, payload }: any) {
  if (active && payload && payload.length) {
    return (
      <div className="glass-panel rounded-lg p-3 animate-scale-in">
        <p className="text-sm mb-2 font-mono">{payload[0].payload.name}</p>
        {payload.map((entry: any, index: number) => (
          <div key={index} className="flex items-center gap-2 text-xs">
            <div className="w-2 h-2 rounded-full" style={{ backgroundColor: entry.color }}></div>
            <span className="text-muted-foreground">{entry.name}:</span>
            <span className="font-mono">{entry.value}</span>
          </div>
        ))}
      </div>
    );
  }
  return null;
}

export function Dashboard() {
  const [hoveredContact, setHoveredContact] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [msgCount, setMsgCount] = useState(0);
  const [tokensIn, setTokensIn] = useState(0);
  const [tokensOut, setTokensOut] = useState(0);
  const [peopleCount, setPeopleCount] = useState(0);
  const [placesCount, setPlacesCount] = useState(0);
  const [topPeople, setTopPeople] = useState<ApiPerson[]>([]);
  const [notifications, setNotifications] = useState<NotificationItem[]>([]);
  const [activityRadar, setActivityRadar] = useState<{ subject: string; A: number; fullMark: number }[]>([]);
  const [weekly, setWeekly] = useState<WeeklyStats | null>(null);
  const [devices, setDevices] = useState<DeviceInfo[]>([]);
  const [activeDevice, setActiveDevice] = useState<DeviceInfo | null>(null);
  const [daemon, setDaemon] = useState<AudioDaemonStatus>({
    enabled: false, state: 'idle', wake_word_enabled: false,
    continuous_mode: false, last_interaction: 0, stt_engine: 'none', tts_engine: 'macos', has_porcupine: false,
  })

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [st, peopleR, placesR, notifR, weeklyR, devR, daemonR] = await Promise.all([
        api.getStatus() as Promise<{ today?: { msg_count?: number; total_in?: number; total_out?: number } }>,
        api.getPeople(),
        api.getPlaces() as Promise<{ places?: unknown[] }>,
        api.getNotifications(),
        api.getWeeklyStats().catch(() => null),
        api.getDevices().catch(() => ({ devices: [], active: null })),
        api.getAudioDaemonStatus().catch(() => ({
          enabled: false, state: 'idle' as const, wake_word_enabled: false,
          continuous_mode: false, last_interaction: 0, stt_engine: 'none', tts_engine: 'macos', has_porcupine: false,
        })),
      ]);
      setWeekly(weeklyR);
      setDevices(devR?.devices ?? []);
      setActiveDevice(devR?.active ?? null);
      setDaemon(daemonR);
      const t = st.today;
      setMsgCount(t?.msg_count ?? 0);
      setTokensIn(t?.total_in ?? 0);
      setTokensOut(t?.total_out ?? 0);
      const plist = peopleR.people ?? [];
      setPeopleCount(plist.length);
      setPlacesCount((placesR.places ?? []).length);
      const sorted = [...plist].sort((a, b) => {
        const da = a.last_mentioned || '';
        const db = b.last_mentioned || '';
        return db.localeCompare(da);
      });
      setTopPeople(sorted.slice(0, 5));
      setNotifications(notifR.notifications ?? []);
      const ti = t?.total_in ?? 0;
      const to = t?.total_out ?? 0;
      const mc = t?.msg_count ?? 0;
      setActivityRadar([
        { subject: 'Messages', A: Math.min(150, mc * 3), fullMark: 150 },
        { subject: 'Tokens in', A: Math.min(150, Math.floor(ti / 100)), fullMark: 150 },
        { subject: 'Tokens out', A: Math.min(150, Math.floor(to / 100)), fullMark: 150 },
        { subject: 'Lieux', A: Math.min(150, (placesR.places?.length ?? 0) * 15), fullMark: 150 },
        { subject: 'Contacts', A: Math.min(150, plist.length * 10), fullMark: 150 },
      ]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const id = window.setInterval(load, 60_000);
    return () => window.clearInterval(id);
  }, [load]);

  const interactions = tokensIn + tokensOut;
  const messageData = (weekly?.days ?? []).map(d => ({
    name: frDayLabel(d.date),
    messages: d.msg_count,
    calls: d.voice_count,
  }));
  const messagesChange = pctLabel(weekly?.change.messages_pct);
  const interactionsChange = pctLabel(weekly?.change.interactions_pct);
  const radarScorePct =
    activityRadar.length > 0
      ? Math.round(
          (activityRadar.reduce((s, x) => s + x.A, 0) / (activityRadar.length * 150)) * 100,
        )
      : 0;

  return (
    <div className="p-6 space-y-6 bg-grid-pattern">
      {/* Header Section */}
      <div className="animate-fade-in">
        <h2 className="text-2xl mb-1">Tableau de Bord</h2>
        <p className="text-sm text-muted-foreground font-mono">Surveillance et analyse en temps réel</p>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          title="Messages Total"
          value={loading ? '…' : String(msgCount)}
          change={messagesChange}
          changeType={(weekly?.change.messages_pct ?? 0) >= 0 ? 'up' : 'down'}
          icon={MessageSquare}
          delay={0}
        />
        <StatCard
          title="Contacts Actifs"
          value={loading ? '…' : String(peopleCount)}
          icon={Users}
          delay={100}
        />
        <StatCard
          title="Lieux Visités"
          value={loading ? '…' : String(placesCount)}
          icon={MapPin}
          delay={200}
        />
        <StatCard
          title="Interactions"
          value={loading ? '…' : interactions.toLocaleString()}
          change={interactionsChange}
          changeType={(weekly?.change.interactions_pct ?? 0) >= 0 ? 'up' : 'down'}
          icon={Activity}
          delay={300}
        />
      </div>

      {/* Charts Section */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Activity Chart */}
        <div className="lg:col-span-2 glass-panel rounded-xl p-6 animate-slide-up group" style={{ animationDelay: '400ms' }}>
          <div className="mb-6 flex items-start justify-between">
            <div>
              <h3 className="mb-1">Activité Hebdomadaire</h3>
              <p className="text-sm text-muted-foreground">Messages et appels des 7 derniers jours</p>
            </div>
            <div className="flex gap-4 text-xs">
              <div className="flex items-center gap-2">
                <div className="w-3 h-3 rounded-full bg-white"></div>
                <span className="text-muted-foreground">Messages</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="w-3 h-3 rounded-full bg-gray-400"></div>
                <span className="text-muted-foreground">Appels</span>
              </div>
            </div>
          </div>
          {!loading && messageData.length === 0 && (
            <p className="text-sm text-muted-foreground py-10 text-center">Statistiques indisponibles.</p>
          )}
          <ResponsiveContainer width="100%" height={300}>
            <AreaChart data={messageData}>
              <defs>
                <linearGradient id="colorMessages" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#ffffff" stopOpacity={0.4}/>
                  <stop offset="95%" stopColor="#ffffff" stopOpacity={0}/>
                </linearGradient>
                <linearGradient id="colorCalls" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#a1a1a1" stopOpacity={0.3}/>
                  <stop offset="95%" stopColor="#a1a1a1" stopOpacity={0}/>
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255, 255, 255, 0.05)" />
              <XAxis dataKey="name" stroke="#6b7280" fontSize={12} />
              <YAxis stroke="#6b7280" fontSize={12} />
              <Tooltip content={<CustomTooltip />} />
              <Area 
                type="monotone" 
                dataKey="messages" 
                stroke="#ffffff" 
                strokeWidth={2}
                fillOpacity={1} 
                fill="url(#colorMessages)"
                animationDuration={1500}
              />
              <Area 
                type="monotone" 
                dataKey="calls" 
                stroke="#a1a1a1" 
                strokeWidth={2}
                fillOpacity={1} 
                fill="url(#colorCalls)"
                animationDuration={1500}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>

        {/* Radar Chart */}
        <div className="glass-panel rounded-xl p-6 animate-slide-up" style={{ animationDelay: '500ms' }}>
          <div className="mb-4">
            <h3 className="mb-1">Performance</h3>
            <p className="text-sm text-muted-foreground">Analyse multimodale</p>
          </div>
          <ResponsiveContainer width="100%" height={300}>
            <RadarChart data={activityRadar.length ? activityRadar : [{ subject: '—', A: 0, fullMark: 150 }]}>
              <PolarGrid stroke="rgba(255, 255, 255, 0.1)" />
              <PolarAngleAxis dataKey="subject" stroke="#6b7280" fontSize={11} />
              <PolarRadiusAxis stroke="#6b7280" />
              <Radar 
                name="Activity" 
                dataKey="A" 
                stroke="#ffffff" 
                fill="#ffffff" 
                fillOpacity={0.25}
                strokeWidth={2}
                animationDuration={1500}
              />
            </RadarChart>
          </ResponsiveContainer>
          <div className="mt-4 p-3 bg-white/5 rounded-lg border border-white/10">
            <div className="flex items-center justify-between text-sm">
              <span className="text-muted-foreground">Score Global</span>
              <span className="font-mono">{loading ? '…' : `${radarScorePct}%`}</span>
            </div>
            <div className="mt-2 h-1.5 bg-white/10 rounded-full overflow-hidden">
              <div className="h-full bg-white rounded-full" style={{ width: `${loading ? 0 : radarScorePct}%` }}></div>
            </div>
          </div>
        </div>
      </div>

      {/* Bottom Section */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Top Contacts */}
        <div className="glass-panel rounded-xl p-6 animate-slide-up" style={{ animationDelay: '600ms' }}>
          <div className="mb-6 flex items-start justify-between">
            <div>
              <h3 className="mb-1">Top Contacts</h3>
              <p className="text-sm text-muted-foreground">Classés par volume de messages</p>
            </div>
            <button className="px-3 py-1.5 text-xs bg-white/5 hover:bg-white/10 border border-white/10 rounded-lg transition-all">
              Voir tout
            </button>
          </div>
          <div className="space-y-2">
            {!loading && topPeople.length === 0 && (
              <p className="text-sm text-muted-foreground py-6 text-center">Aucun contact en base.</p>
            )}
            {topPeople.map((contact, index) => (
              <div 
                key={contact.name} 
                className={`flex items-center gap-4 p-3 rounded-lg transition-all cursor-pointer ${
                  hoveredContact === index ? 'bg-white/10 border border-white/20' : 'hover:bg-white/5 border border-transparent'
                }`}
                onMouseEnter={() => setHoveredContact(index)}
                onMouseLeave={() => setHoveredContact(null)}
              >
                <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-white text-black font-mono">
                  {index + 1}
                </div>
                <div className="flex-1 min-w-0">
                  <p className="truncate">{contact.name}</p>
                  <p className="text-sm text-muted-foreground font-mono">
                    {(contact.message_count ?? 0).toLocaleString()} msgs · {formatRelativeTime(contact.last_mentioned)} · {contact.relationship || '—'}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <div className="w-2 h-2 rounded-full bg-gray-400" />
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Machines connectées */}
        <div className="glass-panel rounded-xl p-6 animate-slide-up" style={{ animationDelay: '650ms' }}>
          <div className="mb-6 flex items-start justify-between">
            <div>
              <h3 className="mb-1">Machines</h3>
              <p className="text-sm text-muted-foreground">
                {devices.length === 0 ? 'Aucune machine enregistrée' : `${devices.length} machine${devices.length > 1 ? 's' : ''} · active : ${activeDevice?.device_name ?? '—'}`}
              </p>
            </div>
            <div className="flex items-center gap-1 px-2 py-1 bg-white/5 border border-white/10 rounded-lg">
              <span className="text-xs font-mono">DAEMON</span>
            </div>
          </div>
          <div className="space-y-2">
            {!loading && devices.length === 0 && (
              <p className="text-sm text-muted-foreground py-6 text-center">
                Aucune machine. Lance <code className="font-mono">jarvis_agent.py</code> sur une machine distante pour la voir apparaître.
              </p>
            )}
            {devices.map((d) => {
              const Icon = deviceIcon(d.device_type);
              const isActive = Boolean(d.is_active);
              const isOnline = Boolean(d.is_online);
              return (
                <div
                  key={d.device_id}
                  className={`flex items-center gap-3 p-3 rounded-lg transition-all border ${
                    isActive
                      ? 'bg-white/10 border-white/20'
                      : 'hover:bg-white/5 border-transparent'
                  }`}
                >
                  <div className="w-10 h-10 rounded-lg bg-white/5 border border-white/10 flex items-center justify-center">
                    <Icon className="w-5 h-5" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <p className="truncate">{d.device_name}</p>
                      <div
                        className={`w-2 h-2 rounded-full ${
                          isOnline ? 'bg-white' : 'bg-gray-500'
                        }`}
                        title={isOnline ? 'En ligne' : 'Hors ligne'}
                      />
                      {isActive && (
                        <span className="text-xs font-mono px-1.5 py-0.5 rounded bg-white/10 border border-white/15">
                          ACTIF
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-muted-foreground font-mono truncate">
                      {d.device_type} · {d.last_heartbeat ? formatRelativeTime(d.last_heartbeat) : 'jamais vu'}
                    </p>
                  </div>
                  {!isActive && isOnline && (
                    <button
                      onClick={async () => {
                        try {
                          await api.activateDevice(d.device_id);
                          await load();
                        } catch {
                          /* silencieux */
                        }
                      }}
                      className="px-2.5 py-1 text-xs bg-white/5 hover:bg-white/15 border border-white/10 rounded transition-all"
                    >
                      Activer
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        {/* Audio Daemon */}
        <div className="glass-panel rounded-xl p-5 animate-slide-up" style={{ animationDelay: '675ms' }}>
          <div className="flex items-center gap-3 mb-3">
            <Headphones className="w-5 h-5 text-muted-foreground" />
            <div>
              <h3 className="text-sm">Daemon Audio</h3>
              <p className="text-xs text-muted-foreground">
                {daemon.enabled ? `Wake: ${daemon.wake_word_enabled ? 'Jarvis' : 'Continu'} · ${daemon.state.replace('_', ' ')}` : 'Inactif'}
              </p>
            </div>
            <div className="ml-auto flex items-center gap-2">
              <div
                className={`w-2 h-2 rounded-full ${
                  daemon.state === 'error' ? 'bg-red-400' :
                  daemon.state === 'listening' || daemon.state === 'wake_listening' ? 'bg-cyan-400' :
                  daemon.state === 'processing' ? 'bg-purple-400' :
                  daemon.state === 'speaking' ? 'bg-amber-400' :
                  'bg-white/20'
                }`}
              />
              <span className={`text-xs font-mono uppercase ${
                daemon.enabled ? 'text-cyan-300' : 'text-muted-foreground'
              }`}>
                {daemon.enabled ? 'ACTIF' : 'INACTIF'}
              </span>
            </div>
          </div>
          <div className="flex items-center gap-3 text-[10px] font-mono text-muted-foreground">
            <span>Dernier: {daemon.last_interaction > 0
              ? new Date(daemon.last_interaction * 1000).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })
              : '—'}</span>
            <span>·</span>
            <span>TTS: {daemon.tts_engine}</span>
            <span>·</span>
            <span>STT: {daemon.stt_engine}</span>
          </div>
        </div>

        {/* Recent Activity */}
        <div className="glass-panel rounded-xl p-6 animate-slide-up" style={{ animationDelay: '700ms' }}>
          <div className="mb-6 flex items-start justify-between">
            <div>
              <h3 className="mb-1">Activité Récente</h3>
              <p className="text-sm text-muted-foreground">Dernières notifications</p>
            </div>
            <div className="flex items-center gap-1 px-2 py-1 bg-white/5 border border-white/10 rounded-lg">
              <Zap className="w-3 h-3" />
              <span className="text-xs font-mono">LIVE</span>
            </div>
          </div>
          <div className="space-y-2">
            {!loading && notifications.length === 0 && (
              <p className="text-sm text-muted-foreground py-6 text-center">Aucune notification récente.</p>
            )}
            {notifications.slice(0, 5).map((n, index) => (
              <div 
                key={n.id ?? index} 
                className="flex items-start gap-3 p-3 rounded-lg hover:bg-white/5 transition-all cursor-pointer group border-l-2"
                style={{
                  borderLeftColor: 
                    n.priority === 'urgent' || n.priority === 'high' ? '#ffffff' :
                    n.priority === 'medium' ? '#a1a1a1' :
                    '#6b7280'
                }}
              >
                <div className="w-10 h-10 rounded-lg bg-white/5 border border-white/10 flex items-center justify-center group-hover:bg-white/10 transition-all">
                  <Clock className="w-5 h-5" />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="truncate">{n.source || 'Système'}</p>
                  <p className="text-sm text-muted-foreground truncate">{(n.title || n.content || '').slice(0, 120)}</p>
                </div>
                <div className="flex flex-col items-end gap-1">
                  <span className="text-xs text-muted-foreground font-mono whitespace-nowrap">
                    {formatRelativeTime(n.created_at)}
                  </span>
                  <ArrowUpRight className="w-4 h-4 opacity-0 group-hover:opacity-100 transition-opacity" />
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
