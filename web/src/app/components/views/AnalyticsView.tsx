import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Area,
  BarChart,
  Bar,
  PieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  ComposedChart,
  Line,
} from 'recharts';
import {
  MessageSquare,
  Users,
  CheckCircle,
  DollarSign,
  Brain,
  UserPlus,
  TrendingUp,
  Database,
  Zap,
  ArrowDown,
  ArrowUp,
  Loader2,
} from 'lucide-react';
import { api } from '@/services/api';
import { timeAgo } from '@/app/lib/timeFormat';

// ── Types locaux ──────────────────────────────────────────────

interface StatusData {
  today: { msg_count: number; total_in: number; total_out: number; total_cost: number };
  memory: {
    user_facts: number; relationship_profiles: number; patterns_active: number;
    episodes: number; people: number; cross_insights: number;
  };
}

interface Person {
  id: number; name: string; relationship?: string;
  last_mentioned?: string; message_count?: number;
}

interface Mood {
  mood_score?: number; energy_level?: number; context?: string; created_at: string;
}

interface Task {
  title: string; priority?: string; status?: string;
  category?: string; created_at: string; completed_at?: string;
}

interface Pattern {
  pattern_type?: string; description?: string;
  occurrences?: number; first_seen?: string; last_seen?: string; status?: string;
  created_at?: string;
}

interface Place {
  id: number; name: string; category?: string; visit_count?: number; avg_duration_min?: number;
}

interface Conversation {
  id: number; title?: string; last_message_at?: string; started_at?: string;
  created_at?: string; message_count?: number;
}

type Period = '7' | '30' | '90' | 'all';

// ── Helpers ───────────────────────────────────────────────────

const GREY = { white: '#ffffff', light: '#a1a1a1', mid: '#6b7280', dark: '#374151' };

function fmtDate(dateStr: string): string {
  const d = new Date(dateStr);
  return d.toLocaleDateString('fr-FR', { day: 'numeric', month: 'short' });
}

function fmtCost(c: number): string {
  if (!c && c !== 0) return '$0.00';
  return `$${Math.abs(c).toFixed(4)}`;
}

function fmtNum(n: number): string {
  return n.toLocaleString('fr-FR');
}


function patternBadgeStyle(type: string | undefined): React.CSSProperties {
  const map: Record<string, React.CSSProperties> = {
    behavioral:   { background: 'rgba(255,255,255,0.1)', border: '1px solid rgba(255,255,255,0.2)', color: '#fff' },
    emotional:    { background: 'rgba(161,161,161,0.12)', border: '1px solid rgba(161,161,161,0.25)', color: '#a1a1a1' },
    relational:   { background: 'rgba(107,114,128,0.12)', border: '1px solid rgba(107,114,128,0.25)', color: '#9ca3af' },
    productivity: { background: 'rgba(55,65,81,0.2)', border: '1px solid rgba(55,65,81,0.4)', color: '#6b7280' },
    health:       { background: 'rgba(161,161,161,0.1)', border: '1px solid rgba(161,161,161,0.2)', color: '#d1d5db' },
    routine:      { background: 'rgba(255,255,255,0.08)', border: '1px solid rgba(255,255,255,0.15)', color: '#e5e7eb' },
    absence:      { background: 'rgba(55,65,81,0.15)', border: '1px solid rgba(55,65,81,0.3)', color: '#9ca3af' },
  };
  return map[type ?? ''] ?? { background: 'rgba(107,114,128,0.1)', border: '1px solid rgba(107,114,128,0.2)', color: '#9ca3af' };
}

// ── Tooltip glassmorphism ─────────────────────────────────────

function CustomTooltip({ active, payload, label }: { active?: boolean; payload?: Array<{ name: string; value: number; color?: string }>; label?: string }) {
  if (!active || !payload?.length) return null;
  return (
    <div style={{
      background: 'rgba(15,15,15,0.92)',
      border: '1px solid rgba(255,255,255,0.1)',
      borderRadius: 8,
      padding: '8px 12px',
      backdropFilter: 'blur(16px)',
      fontSize: 11,
    }}>
      {label && <p style={{ color: '#fff', fontSize: 12, fontFamily: 'JetBrains Mono', marginBottom: 4 }}>{label}</p>}
      {payload.map((p, i) => (
        <p key={i} style={{ color: p.color ?? '#fff', fontFamily: 'JetBrains Mono' }}>
          {p.name}: <strong>{p.value}</strong>
        </p>
      ))}
    </div>
  );
}

// ── Sous-composants ───────────────────────────────────────────

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="font-mono text-xs uppercase tracking-widest text-muted-foreground mb-4">{children}</h2>
  );
}

function StatCard({ icon: Icon, label, value, sub }: {
  icon: React.ElementType; label: string; value: string | number; sub?: string;
}) {
  return (
    <div className="glass-panel rounded-xl p-4 border border-white/10 flex items-center gap-3">
      <div className="w-9 h-9 rounded-lg bg-white/5 border border-white/10 flex items-center justify-center shrink-0">
        <Icon className="w-4 h-4 text-muted-foreground" />
      </div>
      <div>
        <p className="font-mono text-xs text-muted-foreground leading-tight">{label}</p>
        <p className="font-bold text-base leading-tight mt-0.5 font-mono">{value}</p>
        {sub && <p className="font-mono text-xs text-muted-foreground/60 mt-0.5">{sub}</p>}
      </div>
    </div>
  );
}

// ── Composant principal ───────────────────────────────────────

export function AnalyticsView() {
  const [period, setPeriod] = useState<Period>('30');
  const [status, setStatus] = useState<StatusData | null>(null);
  const [people, setPeople] = useState<Person[]>([]);
  const [moods, setMoods] = useState<Mood[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [tasksDone, setTasksDone] = useState<Task[]>([]);
  const [patterns, setPatterns] = useState<Pattern[]>([]);
  const [locationPatterns, setLocationPatterns] = useState<Pattern[]>([]);
  const [places, setPlaces] = useState<Place[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [loading, setLoading] = useState(true);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [st, ppl, journal, tsk, tskDone, pat, locPat, plc, convs] = await Promise.all([
        api.getStatus() as Promise<StatusData>,
        api.getPeople() as Promise<{ people: Person[] }>,
        api.getJournal() as Promise<{ moods?: Mood[] }>,
        api.getTasks() as Promise<{ tasks: Task[] }>,
        api.getTasks('done') as Promise<{ tasks: Task[] }>,
        api.getPatterns() as Promise<{ patterns: Pattern[] }>,
        (api.getLocationPatterns() as Promise<{ patterns: Pattern[] }>).catch(() => ({ patterns: [] as Pattern[] })),
        (api.getPlaces() as Promise<{ places: Place[] }>).catch(() => ({ places: [] as Place[] })),
        (api.getConversations() as Promise<{ conversations: Conversation[] }>).catch(() => ({ conversations: [] as Conversation[] })),
      ]);
      setStatus(st);
      setPeople(ppl.people ?? []);
      setMoods(journal.moods ?? []);
      setTasks(tsk.tasks ?? []);
      setTasksDone(tskDone.tasks ?? []);
      setPatterns(pat.patterns ?? []);
      setLocationPatterns(locPat.patterns ?? []);
      setPlaces(plc.places ?? []);
      setConversations(convs.conversations ?? []);
    } catch (e) {
      console.error('[AnalyticsView] loadData:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void loadData(); }, [loadData, period]);

  // ── Filtrage par période ──────────────────────────────────

  function filterByPeriod<T extends { created_at?: string }>(items: T[]): T[] {
    if (period === 'all') return items;
    const days = parseInt(period);
    const cutoff = new Date(Date.now() - days * 86400000);
    return items.filter((item) => {
      const d = item.created_at ? new Date(item.created_at) : null;
      return d ? d >= cutoff : true;
    });
  }

  const filteredMoods = useMemo(() => filterByPeriod(moods), [moods, period]);
  const filteredTasks = useMemo(() => filterByPeriod(tasks), [tasks, period]);
  const filteredTasksDone = useMemo(() => filterByPeriod(tasksDone), [tasksDone, period]);
  // Patterns : filtrage par first_seen ou created_at
  const filteredPatterns = useMemo(() => {
    if (period === 'all') return patterns;
    const days = parseInt(period);
    const cutoff = new Date(Date.now() - days * 86400000);
    return patterns.filter((p) => {
      const d = p.first_seen ?? p.created_at;
      return d ? new Date(d) >= cutoff : true;
    });
  }, [patterns, period]);

  // ── Métriques principales ─────────────────────────────────

  const activePeople = useMemo(() => {
    if (period === 'all') return people;
    const cutoff = new Date(Date.now() - parseInt(period) * 86400000);
    return people.filter((p) => p.last_mentioned && new Date(p.last_mentioned) >= cutoff);
  }, [people, period]);

  // ── Données graphiques ────────────────────────────────────

  // Mood chart — 14 derniers jours
  const moodChartData = useMemo(() => {
    const sorted = [...filteredMoods]
      .sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime())
      .slice(-14);
    return sorted.map((m) => ({
      date: fmtDate(m.created_at),
      mood: m.mood_score ?? null,
      énergie: m.energy_level ?? null,
      context: m.context ?? '',
    }));
  }, [filteredMoods]);

  // Contacts par type (donut)
  const contactsByType = useMemo(() => {
    const counts: Record<string, number> = {};
    people.forEach((p) => {
      const rel = p.relationship ?? 'autre';
      counts[rel] = (counts[rel] ?? 0) + 1;
    });
    const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
    const colors = [GREY.white, GREY.light, GREY.mid, GREY.dark, '#4b5563', '#1f2937'];
    return entries.map(([name, value], i) => ({ name, value, color: colors[i % colors.length] }));
  }, [people]);

  // Tâches par statut
  const tasksByStatus = useMemo(() => {
    const counts: Record<string, number> = { todo: 0, doing: 0, done: 0 };
    [...filteredTasks, ...filteredTasksDone].forEach((t) => {
      const s = t.status ?? 'todo';
      counts[s] = (counts[s] ?? 0) + 1;
    });
    return [
      { name: 'À faire', value: counts.todo, fill: GREY.white },
      { name: 'En cours', value: counts.doing, fill: GREY.light },
      { name: 'Terminé', value: counts.done, fill: GREY.dark },
    ];
  }, [filteredTasks, filteredTasksDone]);

  // Tâches par priorité
  const tasksByPriority = useMemo(() => {
    const counts: Record<string, number> = { high: 0, medium: 0, low: 0 };
    [...filteredTasks, ...filteredTasksDone].forEach((t) => {
      const p = t.priority ?? 'low';
      counts[p] = (counts[p] ?? 0) + 1;
    });
    return [
      { name: 'Haute', value: counts.high, fill: GREY.white },
      { name: 'Moyenne', value: counts.medium, fill: GREY.light },
      { name: 'Basse', value: counts.low, fill: GREY.dark },
    ];
  }, [filteredTasks, filteredTasksDone]);

  // Activité conversations par jour (7 derniers jours)
  const convActivityData = useMemo(() => {
    const days: { date: string; messages: number }[] = [];
    for (let i = 6; i >= 0; i--) {
      const d = new Date(Date.now() - i * 86400000);
      const label = d.toLocaleDateString('fr-FR', { day: 'numeric', month: 'short' });
      const start = new Date(d.getFullYear(), d.getMonth(), d.getDate());
      const end = new Date(start.getTime() + 86400000);
      const msgs = conversations
        .filter((c) => {
          const cd = new Date(c.last_message_at ?? c.started_at ?? c.created_at ?? '');
          return cd >= start && cd < end;
        })
        .reduce((sum, c) => sum + (c.message_count ?? 1), 0);
      days.push({ date: label, messages: msgs });
    }
    // Aujourd'hui : on override avec la valeur réelle de status
    if (status?.today.msg_count && days.length > 0) {
      const today = days[days.length - 1];
      if (today) today.messages = Math.max(today.messages, status.today.msg_count);
    }
    return days;
  }, [conversations, status]);

  // Top 10 contacts par message_count
  const topContacts = useMemo(() => {
    const sorted = [...people]
      .filter((p) => (p.message_count ?? 0) > 0)
      .sort((a, b) => (b.message_count ?? 0) - (a.message_count ?? 0))
      .slice(0, 10);
    const max = Math.max(1, ...sorted.map((p) => p.message_count ?? 0));
    return sorted.map((p) => ({ ...p, pct: ((p.message_count ?? 0) / max) * 100 }));
  }, [people]);

  // Patterns fusionnés
  const allPatterns = useMemo(() => {
    const merged = [...filteredPatterns, ...locationPatterns];
    return merged.sort((a, b) => (b.occurrences ?? 0) - (a.occurrences ?? 0));
  }, [filteredPatterns, locationPatterns]);

  // Lieux top 5
  const topPlaces = useMemo(() => {
    return [...places].sort((a, b) => (b.visit_count ?? 0) - (a.visit_count ?? 0)).slice(0, 5);
  }, [places]);

  const maxPlaceVisits = Math.max(1, ...topPlaces.map((p) => p.visit_count ?? 0));

  // ── Rendu ─────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const PERIODS: { key: Period; label: string }[] = [
    { key: '7', label: '7j' }, { key: '30', label: '30j' },
    { key: '90', label: '90j' }, { key: 'all', label: 'Tout' },
  ];

  return (
    <div className="flex-1 p-6 overflow-y-auto space-y-10">

      {/* ── Header ── */}
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-sm font-bold tracking-widest uppercase">Statistiques</h1>
          <p className="font-mono text-xs text-muted-foreground mt-0.5">Analyses et métriques de performance</p>
        </div>
        <div className="flex gap-1">
          {PERIODS.map(({ key, label }) => (
            <button
              key={key}
              onClick={() => setPeriod(key)}
              className={`px-3 py-1.5 rounded-lg text-xs border transition-colors ${
                period === key ? 'bg-white text-black border-white' : 'bg-white/5 border-white/10 text-muted-foreground hover:bg-white/10'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* ── Section 1 : Métriques principales ── */}
      <section>
        <SectionTitle>Métriques principales</SectionTitle>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <StatCard icon={MessageSquare} label="Messages du jour" value={fmtNum(status?.today.msg_count ?? 0)} />
          <StatCard icon={Users} label="Contacts actifs" value={activePeople.length} sub={`sur ${people.length} total`} />
          <StatCard icon={CheckCircle} label="Tâches terminées" value={filteredTasksDone.length} />
          <StatCard icon={DollarSign} label="Coût API" value={fmtCost(status?.today.total_cost ?? 0)} />
        </div>
      </section>

      {/* ── Section 2 : Mood & Énergie ── */}
      <section>
        <SectionTitle>Humeur & Énergie ({filteredMoods.length} entrées)</SectionTitle>
        {moodChartData.length === 0 ? (
          <p className="text-sm text-muted-foreground font-mono py-4">
            Aucune donnée d'humeur. Utilise le journal pour enregistrer ton mood.
          </p>
        ) : (
          <div className="glass-panel rounded-xl border border-white/10 p-4">
            <ResponsiveContainer width="100%" height={220}>
              <ComposedChart data={moodChartData} margin={{ top: 5, right: 10, bottom: 5, left: -20 }}>
                <defs>
                  <linearGradient id="moodGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#ffffff" stopOpacity={0.15} />
                    <stop offset="95%" stopColor="#ffffff" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                <XAxis dataKey="date" stroke="#6b7280" fontSize={10} tick={{ fontFamily: 'JetBrains Mono' }} />
                <YAxis domain={[0, 10]} stroke="#6b7280" fontSize={10} tick={{ fontFamily: 'JetBrains Mono' }} />
                <Tooltip content={<CustomTooltip />} />
                <Legend
                  wrapperStyle={{ fontSize: 11, fontFamily: 'JetBrains Mono' }}
                  formatter={(v) => <span style={{ color: '#a1a1a1' }}>{v}</span>}
                />
                <Area type="monotone" dataKey="mood" name="Humeur" stroke={GREY.white} strokeWidth={2} fill="url(#moodGrad)" dot={{ r: 4, fill: GREY.white }} activeDot={{ r: 6 }} />
                <Line type="monotone" dataKey="énergie" name="Énergie" stroke={GREY.mid} strokeWidth={1.5} dot={{ r: 3, fill: GREY.mid }} activeDot={{ r: 5 }} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        )}
      </section>

      {/* ── Section 3 + 4 : Contacts (donut) + Tâches côte à côte ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">

        {/* Donut contacts */}
        <section>
          <SectionTitle>Contacts par type</SectionTitle>
          <div className="glass-panel rounded-xl border border-white/10 p-4">
            {contactsByType.length === 0 ? (
              <p className="text-sm text-muted-foreground font-mono py-4">Aucun contact.</p>
            ) : (
              <div className="flex items-center gap-4">
                <div className="relative shrink-0" style={{ width: 190, height: 190 }}>
                  <PieChart width={190} height={190}>
                    <Pie
                      data={contactsByType}
                      cx={90} cy={90}
                      innerRadius={60} outerRadius={88}
                      dataKey="value"
                      strokeWidth={0}
                    >
                      {contactsByType.map((entry, i) => (
                        <Cell key={i} fill={entry.color} opacity={0.85} />
                      ))}
                    </Pie>
                    <Tooltip content={<CustomTooltip />} />
                  </PieChart>
                  <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
                    <span className="font-bold font-mono text-2xl">{people.length}</span>
                    <span className="font-mono text-xs text-muted-foreground">contacts</span>
                  </div>
                </div>
                <div className="flex-1 space-y-1.5 min-w-0">
                  {contactsByType.map((entry) => {
                    const pct = Math.round((entry.value / Math.max(1, people.length)) * 100);
                    return (
                      <div key={entry.name} className="flex items-center gap-2">
                        <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: entry.color }} />
                        <span className="text-xs capitalize truncate flex-1">{entry.name}</span>
                        <span className="font-mono text-xs text-muted-foreground shrink-0">{entry.value}</span>
                        <span className="font-mono text-xs text-muted-foreground/60 shrink-0 w-8 text-right">{pct}%</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        </section>

        {/* Tâches */}
        <section>
          <SectionTitle>Répartition des tâches</SectionTitle>
          <div className="glass-panel rounded-xl border border-white/10 p-4 space-y-4">
            {/* Par statut */}
            <div>
              <p className="font-mono text-xs text-muted-foreground mb-2">Par statut</p>
              <ResponsiveContainer width="100%" height={100}>
                <BarChart data={tasksByStatus} margin={{ top: 0, right: 0, bottom: 0, left: -30 }} barCategoryGap="30%">
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" vertical={false} />
                  <XAxis dataKey="name" stroke="#6b7280" fontSize={10} tick={{ fontFamily: 'JetBrains Mono' }} />
                  <YAxis stroke="#6b7280" fontSize={10} tick={{ fontFamily: 'JetBrains Mono' }} />
                  <Tooltip content={<CustomTooltip />} />
                  <Bar dataKey="value" name="Tâches" radius={[4, 4, 0, 0]}>
                    {tasksByStatus.map((entry, i) => <Cell key={i} fill={entry.fill} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
            {/* Par priorité */}
            <div>
              <p className="font-mono text-xs text-muted-foreground mb-2">Par priorité</p>
              <ResponsiveContainer width="100%" height={100}>
                <BarChart data={tasksByPriority} margin={{ top: 0, right: 0, bottom: 0, left: -30 }} barCategoryGap="30%">
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" vertical={false} />
                  <XAxis dataKey="name" stroke="#6b7280" fontSize={10} tick={{ fontFamily: 'JetBrains Mono' }} />
                  <YAxis stroke="#6b7280" fontSize={10} tick={{ fontFamily: 'JetBrains Mono' }} />
                  <Tooltip content={<CustomTooltip />} />
                  <Bar dataKey="value" name="Tâches" radius={[4, 4, 0, 0]}>
                    {tasksByPriority.map((entry, i) => <Cell key={i} fill={entry.fill} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </section>
      </div>

      {/* ── Section 5 : Activité conversations ── */}
      <section>
        <SectionTitle>Activité des conversations (7 derniers jours)</SectionTitle>
        <div className="glass-panel rounded-xl border border-white/10 p-4">
          <ResponsiveContainer width="100%" height={180}>
            <BarChart data={convActivityData} margin={{ top: 5, right: 10, bottom: 5, left: -20 }} barCategoryGap="25%">
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" vertical={false} />
              <XAxis dataKey="date" stroke="#6b7280" fontSize={10} tick={{ fontFamily: 'JetBrains Mono' }} />
              <YAxis stroke="#6b7280" fontSize={10} tick={{ fontFamily: 'JetBrains Mono' }} />
              <Tooltip content={<CustomTooltip />} />
              <Bar dataKey="messages" name="Messages" fill={GREY.white} fillOpacity={0.8} radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </section>

      {/* ── Section 6 : Top contacts ── */}
      {topContacts.length > 0 && (
        <section>
          <SectionTitle>Top contacts par volume</SectionTitle>
          <div className="glass-panel rounded-xl border border-white/10 p-4 space-y-2">
            {topContacts.map((p) => (
              <div key={p.id} className="flex items-center gap-3">
                <span className="w-32 text-xs truncate shrink-0">{p.name}</span>
                <div className="flex-1 h-1.5 bg-white/5 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-white rounded-full transition-all"
                    style={{ width: `${p.pct}%` }}
                  />
                </div>
                <span className="font-mono text-xs text-muted-foreground w-16 text-right shrink-0">
                  {fmtNum(p.message_count ?? 0)} msg
                </span>
                {p.relationship && (
                  <span className="px-2 py-0.5 rounded-full bg-white/8 border border-white/10 font-mono text-xs capitalize shrink-0 hidden sm:inline">
                    {p.relationship}
                  </span>
                )}
              </div>
            ))}
          </div>
        </section>
      )}

      {/* ── Section 7 : Patterns ── */}
      {allPatterns.length > 0 && (
        <section>
          <SectionTitle>Patterns actifs ({allPatterns.length})</SectionTitle>
          <div className="grid gap-2" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))' }}>
            {allPatterns.slice(0, 12).map((pat, i) => (
              <div
                key={i}
                className="glass-panel rounded-xl border border-white/8 p-3 flex items-start gap-3 transition-opacity"
                style={{ opacity: pat.status === 'resolved' ? 0.45 : 1 }}
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span
                      className="px-2 py-0.5 rounded-full text-xs font-mono"
                      style={patternBadgeStyle(pat.pattern_type)}
                    >
                      {pat.pattern_type ?? 'pattern'}
                    </span>
                    {pat.status === 'resolved' && (
                      <span className="font-mono text-xs text-muted-foreground">résolu</span>
                    )}
                  </div>
                  <p className="text-xs leading-snug">{pat.description}</p>
                  <div className="flex items-center gap-2 mt-1.5">
                    {pat.first_seen && (
                      <span className="font-mono text-xs text-muted-foreground/60">depuis {timeAgo(pat.first_seen)}</span>
                    )}
                    {pat.last_seen && (
                      <span className="font-mono text-xs text-muted-foreground/60">· {timeAgo(pat.last_seen)}</span>
                    )}
                  </div>
                </div>
                {pat.occurrences !== undefined && (
                  <span className="font-mono text-lg font-bold text-muted-foreground shrink-0">
                    ×{pat.occurrences}
                  </span>
                )}
              </div>
            ))}
          </div>
        </section>
      )}

      {/* ── Section 8 : Mémoire JARVIS ── */}
      {status?.memory && (
        <section>
          <SectionTitle>Mémoire JARVIS</SectionTitle>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            {[
              { icon: Brain,      label: 'Faits stockés',        value: status.memory.user_facts },
              { icon: Users,      label: 'Profils relationnels', value: status.memory.relationship_profiles },
              { icon: TrendingUp, label: 'Patterns actifs',      value: status.memory.patterns_active },
              { icon: Database,   label: 'Épisodes',             value: status.memory.episodes },
              { icon: UserPlus,   label: 'Contacts',             value: status.memory.people },
              { icon: Zap,        label: 'Insights croisés',     value: status.memory.cross_insights },
            ].map(({ icon: Icon, label, value }) => (
              <div key={label} className="glass-panel rounded-xl border border-white/10 p-3 flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg bg-white/5 border border-white/10 flex items-center justify-center shrink-0">
                  <Icon className="w-3.5 h-3.5 text-muted-foreground" />
                </div>
                <div>
                  <p className="font-mono text-xs text-muted-foreground leading-tight">{label}</p>
                  <p className="font-bold font-mono text-base leading-tight">{fmtNum(value ?? 0)}</p>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* ── Section 9 : Top lieux ── */}
      {topPlaces.length > 0 && (
        <section>
          <SectionTitle>Lieux les plus visités</SectionTitle>
          <div className="glass-panel rounded-xl border border-white/10 p-4 space-y-2">
            {topPlaces.map((place) => (
              <div key={place.id} className="flex items-center gap-3">
                <span className="w-32 text-xs truncate shrink-0">{place.name}</span>
                <div className="flex-1 h-1.5 bg-white/5 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-white rounded-full transition-all"
                    style={{ width: `${((place.visit_count ?? 0) / maxPlaceVisits) * 100}%` }}
                  />
                </div>
                <span className="font-mono text-xs text-muted-foreground w-16 text-right shrink-0">
                  {fmtNum(place.visit_count ?? 0)} visites
                </span>
                {place.category && (
                  <span className="px-2 py-0.5 rounded-full bg-white/8 border border-white/10 font-mono text-xs capitalize shrink-0 hidden sm:inline">
                    {place.category}
                  </span>
                )}
              </div>
            ))}
          </div>
        </section>
      )}

      {/* ── Section 10 : Coûts API ── */}
      {status?.today && (
        <section className="pb-4">
          <SectionTitle>Coûts API</SectionTitle>
          <div className="glass-panel rounded-xl border border-white/10 p-4 space-y-4">
            <div className="grid grid-cols-3 gap-3">
              {[
                { icon: ArrowDown, label: 'Tokens input',  value: fmtNum(status.today.total_in ?? 0) },
                { icon: ArrowUp,   label: 'Tokens output', value: fmtNum(status.today.total_out ?? 0) },
                { icon: DollarSign, label: 'Coût estimé',  value: fmtCost(status.today.total_cost) },
              ].map(({ icon: Icon, label, value }) => (
                <div key={label} className="flex items-center gap-2">
                  <div className="w-7 h-7 rounded-lg bg-white/5 border border-white/10 flex items-center justify-center shrink-0">
                    <Icon className="w-3 h-3 text-muted-foreground" />
                  </div>
                  <div>
                    <p className="font-mono text-xs text-muted-foreground leading-tight">{label}</p>
                    <p className="font-mono text-sm font-bold leading-tight">{value}</p>
                  </div>
                </div>
              ))}
            </div>
            {/* Barre bicolore input / output */}
            {(() => {
              const totalTokens = (status.today.total_in ?? 0) + (status.today.total_out ?? 0);
              if (totalTokens === 0) return null;
              const inputPct = ((status.today.total_in ?? 0) / totalTokens) * 100;
              return (
                <div>
                  <div className="flex justify-between font-mono text-xs text-muted-foreground mb-1">
                    <span>Input {Math.round(inputPct)}%</span>
                    <span>Output {Math.round(100 - inputPct)}%</span>
                  </div>
                  <div className="h-2 w-full rounded-full overflow-hidden bg-white/5 flex">
                    <div className="h-full bg-white rounded-l-full transition-all" style={{ width: `${inputPct}%` }} />
                    <div className="h-full bg-white/30 rounded-r-full flex-1" />
                  </div>
                </div>
              );
            })()}
          </div>
        </section>
      )}

    </div>
  );
}
