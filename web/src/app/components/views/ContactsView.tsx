import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Search,
  MessageSquare,
  Tag,
  TrendingUp,
  Loader2,
  Sparkles,
  RefreshCw,
  Pencil,
} from 'lucide-react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import { api, ApiError } from '@unified/lib/api';
import type { ApiPerson, RelationshipProfile } from '@unified/types/jarvis';
import { formatRelativeTime, formatHoursFromMinutes } from '@desktop/app/lib/timeFormat';

// TODO: série historique depuis API | mock design conservé
const interactionHistoryMock = [
  { date: 'Jan', messages: 45, calls: 12, photos: 8 },
  { date: 'Fév', messages: 52, calls: 19, photos: 12 },
  { date: 'Mar', messages: 38, calls: 8, photos: 5 },
  { date: 'Avr', messages: 65, calls: 22, photos: 15 },
  { date: 'Mai', messages: 72, calls: 28, photos: 20 },
  { date: 'Juin', messages: 58, calls: 15, photos: 10 },
];

function initials(name: string) {
  const p = name.trim().split(/\s+/).filter(Boolean);
  if (p.length >= 2) return (p[0]![0]! + p[p.length - 1]![0]!).toUpperCase();
  return name.slice(0, 2).toUpperCase() || '?';
}

function parseTopics(rp: Record<string, unknown> | null | undefined): string[] {
  if (!rp) return [];
  const t = rp.topics;
  if (Array.isArray(t)) return t.map((x) => String(x));
  if (typeof t === 'string') {
    try {
      const j = JSON.parse(t) as unknown;
      if (Array.isArray(j)) return j.map((x) => String(x));
    } catch {
      return t ? [t] : [];
    }
  }
  return [];
}

function sentimentColor(score: number): string {
  if (score >= 0.7) return 'rgba(34, 197, 94, 0.8)';
  if (score >= 0.55) return 'rgba(34, 197, 94, 0.4)';
  if (score >= 0.45) return 'rgba(156, 163, 175, 0.45)';
  if (score >= 0.3) return 'rgba(239, 68, 68, 0.4)';
  return 'rgba(239, 68, 68, 0.8)';
}

export function ContactsView() {
  const [loading, setLoading] = useState(true);
  const [people, setPeople] = useState<ApiPerson[]>([]);
  const [selected, setSelected] = useState<ApiPerson | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [filterRel, setFilterRel] = useState<string | null>(null);
  const [detail, setDetail] = useState<{
    person?: ApiPerson
    events?: { event_type?: string; content?: string; summary?: string; created_at?: string; event_date?: string }[]
  } | null>(null);
  const [rel, setRel] = useState<RelationshipProfile | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [analyzeBusy, setAnalyzeBusy] = useState(false);
  const [aiDescription, setAiDescription] = useState('');
  const [descRefreshing, setDescRefreshing] = useState(false);
  const [contactMessages, setContactMessages] = useState<Array<{ role: string; content: string }>>([]);
  const [contactInput, setContactInput] = useState('');
  const [contactLoading, setContactLoading] = useState(false);
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState('');
  const [nameSaving, setNameSaving] = useState(false);
  const [analytics, setAnalytics] = useState<Record<string, unknown> | null>(null);
  const [analyticsLoading, setAnalyticsLoading] = useState(false);
  const [timelineEvents, setTimelineEvents] = useState<Array<Record<string, unknown>>>([]);
  const [timelineLoading, setTimelineLoading] = useState(false);
  const [timelineUpdatedAt, setTimelineUpdatedAt] = useState<string | null>(null);
  const [timelineRegenerating, setTimelineRegenerating] = useState(false);
  const [imessageDraft, setImessageDraft] = useState('');
  const [actionBusy, setActionBusy] = useState(false);

  const loadList = useCallback(async () => {
    setLoading(true);
    try {
      const r = (await api.getPeople()) as { people?: ApiPerson[] };
      const list = r.people ?? [];
      setPeople(list);
      setSelected((s) => {
        if (!s) return list[0] ?? null;
        if (s.id != null) {
          const byId = list.find((p) => p.id === s.id);
          if (byId) return byId;
        }
        if (list.some((p) => p.name === s.name)) return s;
        return list[0] ?? null;
      });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  useEffect(() => {
    if (!selected?.name) {
      setAiDescription('');
      setContactMessages([]);
      setContactInput('');
      setEditingName(false);
      setNameDraft('');
      setAnalytics(null);
      setTimelineEvents([]);
      setTimelineUpdatedAt(null);
      setImessageDraft('');
      return;
    }
    setContactMessages([]);
    setContactInput('');
    setEditingName(false);
    setNameDraft(selected.name);
    setTimelineEvents([]);
    setTimelineUpdatedAt(null);
    setImessageDraft('');
    api
      .getPersonDescription(selected.name)
      .then((res) => setAiDescription((res.description || '').trim()))
      .catch(() => setAiDescription(''));
  }, [selected?.name]);

  useEffect(() => {
    if (!selected?.name) {
      setAnalytics(null);
      return;
    }
    setAnalyticsLoading(true);
    api
      .getPersonAnalytics(selected.name)
      .then((d) => setAnalytics(d as Record<string, unknown>))
      .catch(() => setAnalytics(null))
      .finally(() => setAnalyticsLoading(false));
  }, [selected?.name]);

  const loadDetail = useCallback(async (name: string) => {
    setDetailLoading(true);
    try {
      const [p, r] = await Promise.all([api.getPerson(name), api.getRelationship(name)]);
      setDetail(p as typeof detail);
      setRel(r as RelationshipProfile | null);
    } catch {
      setDetail(null);
      setRel(null);
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    if (selected?.name) void loadDetail(selected.name);
  }, [selected?.name, loadDetail]);

  const roles = useMemo(
    () => Array.from(new Set(people.map((c) => (c.relationship || '—').trim()).filter(Boolean))),
    [people],
  );

  const sortedPeople = useMemo(() => {
    return [...people].sort((a, b) => {
      if (!a.last_mentioned && !b.last_mentioned) return 0;
      if (!a.last_mentioned) return 1;
      if (!b.last_mentioned) return -1;
      return new Date(b.last_mentioned).getTime() - new Date(a.last_mentioned).getTime();
    });
  }, [people]);

  const filtered = useMemo(() => {
    const q = searchQuery.toLowerCase();
    return sortedPeople.filter((c) => {
      if (!c.name.toLowerCase().includes(q)) return false;
      if (filterRel && (c.relationship || '').toLowerCase() !== filterRel.toLowerCase()) return false;
      return true;
    });
  }, [sortedPeople, searchQuery, filterRel]);

  const tags = useMemo(() => {
    const s = new Set<string>();
    const rp = rel?.relationship_profile as Record<string, unknown> | null | undefined;
    parseTopics(rp).forEach((t) => s.add(t));
    if (selected?.patterns) s.add(String(selected.patterns).slice(0, 40));
    return Array.from(s).filter(Boolean);
  }, [rel, selected]);

  const chartData = useMemo(() => {
    const trend = (analytics as { trend?: { months?: Array<{ month?: string; count?: number }> } } | null)?.trend;
    if (trend?.months && trend.months.length > 0) {
      return trend.months.map((m) => ({ date: m.month || '', messages: m.count || 0 }));
    }
    return interactionHistoryMock;
  }, [analytics]);

  const onRefreshDescription = async () => {
    if (!selected?.name) return;
    setDescRefreshing(true);
    try {
      const res = await api.refreshPersonDescription(selected.name);
      setAiDescription((res.description || '').trim());
    } catch {
      setAiDescription('');
    } finally {
      setDescRefreshing(false);
    }
  };

  const askAboutContact = async () => {
    if (!contactInput.trim() || !selected?.name) return;
    const question = contactInput.trim();
    setContactMessages((prev) => [...prev, { role: 'user', content: question }]);
    setContactInput('');
    setContactLoading(true);
    try {
      const res = await api.askAboutPerson(selected.name, question);
      setContactMessages((prev) => [...prev, { role: 'assistant', content: res.response || '' }]);
    } catch (e) {
      let msg = "Erreur lors de l'analyse.";
      if (e instanceof ApiError && e.body) {
        try {
          const j = JSON.parse(e.body) as { error?: string };
          if (j.error) msg = j.error;
        } catch {
          /* ignore */
        }
      }
      setContactMessages((prev) => [...prev, { role: 'assistant', content: msg }]);
    } finally {
      setContactLoading(false);
    }
  };

  const commitContactRename = async () => {
    if (!selected?.name || nameSaving) return;
    const next = nameDraft.trim();
    if (!next || next === selected.name) {
      setEditingName(false);
      setNameDraft(selected.name);
      return;
    }
    const oldName = selected.name;
    setNameSaving(true);
    try {
      const updated = (await api.updatePerson(oldName, { name: next })) as ApiPerson;
      await loadList();
      if (updated?.name) setSelected(updated);
      void loadDetail(updated.name);
    } catch {
      setNameDraft(selected.name);
    } finally {
      setNameSaving(false);
      setEditingName(false);
    }
  };

  const onAnalyze = async () => {
    if (!selected?.name) return;
    setAnalyzeBusy(true);
    try {
      await api.analyzeContact(selected.name);
      await loadList();
      await loadDetail(selected.name);
      api
        .getPersonDescription(selected.name)
        .then((res) => setAiDescription((res.description || '').trim()))
        .catch(() => {});
    } finally {
      setAnalyzeBusy(false);
    }
  };

  const loadAiTimeline = async () => {
    if (!selected?.name) return;
    setTimelineLoading(true);
    try {
      const r = await api.getPersonTimeline(selected.name);
      setTimelineEvents(r.events ?? []);
      setTimelineUpdatedAt(r.updated_at ?? null);
    } catch {
      setTimelineEvents([]);
      setTimelineUpdatedAt(null);
    } finally {
      setTimelineLoading(false);
    }
  };

  const regenerateTimeline = async () => {
    if (!selected?.name || timelineRegenerating) return;
    setTimelineRegenerating(true);
    try {
      const r = await api.regenerateTimeline(selected.name);
      setTimelineEvents(r.events ?? []);
      setTimelineUpdatedAt(r.updated_at ?? null);
    } catch {
      // garde les données actuelles en cas d'erreur
    } finally {
      setTimelineRegenerating(false);
    }
  };

  const onSendImessage = async () => {
    if (!selected?.name || !imessageDraft.trim()) return;
    setActionBusy(true);
    try {
      await api.sendImessage(selected.name, imessageDraft.trim());
      setImessageDraft('');
    } finally {
      setActionBusy(false);
    }
  };

  const onSuggestMessage = async () => {
    if (!selected?.name) return;
    setActionBusy(true);
    try {
      const r = await api.suggestMessage(selected.name);
      setImessageDraft((r.suggestion || '').trim());
    } finally {
      setActionBusy(false);
    }
  };

  const onRemindContact = async () => {
    if (!selected?.name) return;
    const when =
      typeof window !== 'undefined'
        ? window.prompt('Quand te rappeler de contacter cette personne ?', 'demain')
        : null;
    if (when === null) return;
    setActionBusy(true);
    try {
      await api.remindContact(selected.name, when || 'bientôt');
    } finally {
      setActionBusy(false);
    }
  };

  if (loading && !people.length) {
    return (
      <div className="h-full flex items-center justify-center bg-grid-pattern">
        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="h-full flex bg-grid-pattern min-h-[480px]">
      <div className="w-80 border-r border-border glass-panel overflow-y-auto shrink-0">
        <div className="p-4 space-y-4">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
            <input
              type="text"
              placeholder="Rechercher un contact…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full h-10 pl-10 pr-4 bg-secondary/30 border border-border rounded-xl
                       focus:outline-none focus:bg-secondary/50 focus:border-white/20"
            />
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => setFilterRel(null)}
              className={`px-3 py-1 rounded-lg text-xs ${filterRel === null ? 'bg-white text-black' : 'bg-white/5 border border-white/10'}`}
            >
              Tous
            </button>
            {roles.map((role) => (
              <button
                type="button"
                key={role}
                onClick={() => setFilterRel(role === filterRel ? null : role)}
                className={`px-3 py-1 rounded-lg text-xs ${
                  filterRel === role ? 'bg-white text-black' : 'bg-white/5 border border-white/10'
                }`}
              >
                {role}
              </button>
            ))}
          </div>
          <div className="space-y-2">
            {!filtered.length && (
              <p className="text-sm text-muted-foreground py-4">Aucun contact ne correspond.</p>
            )}
            {filtered.map((contact) => (
              <button
                type="button"
                key={contact.id ?? contact.name}
                onClick={() => setSelected(contact)}
                className={`w-full p-3 rounded-xl text-left transition-all ${
                  selected?.name === contact.name
                    ? 'bg-white/10 border border-white/20'
                    : 'hover:bg-white/5 border border-transparent'
                }`}
              >
                <div className="flex items-center gap-3">
                  <div className="w-12 h-12 rounded-xl bg-white/10 border border-white/20 flex items-center justify-center font-mono text-sm">
                    {initials(contact.name)}
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="truncate">{contact.name}</p>
                    <div className="flex items-center gap-2 text-xs text-muted-foreground mt-1">
                      <span className="px-2 py-0.5 rounded-full bg-white/5 font-mono">
                        {contact.relationship || '—'}
                      </span>
                      <span>·</span>
                      <span className="truncate">{formatRelativeTime(contact.last_mentioned)}</span>
                    </div>
                  </div>
                  <div className="text-right">
                    <p className="font-mono">{(contact.message_count ?? 0).toLocaleString()}</p>
                    <p className="text-xs text-muted-foreground">msgs</p>
                  </div>
                </div>
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        {!selected ? (
          <div className="p-6 text-muted-foreground">Aucun contact en base. Ajoutez des personnes côté JARVIS.</div>
        ) : (
          <div className="p-6 space-y-6">
            <div className="glass-panel rounded-xl p-6">
              <div className="flex items-start gap-6 flex-wrap">
                <div className="w-24 h-24 rounded-2xl bg-white/10 border border-white/20 flex items-center justify-center text-2xl font-mono">
                  {initials(selected.name)}
                </div>
                <div className="flex-1 min-w-[200px]">
                  <div className="flex items-start justify-between gap-4 flex-wrap">
                    <div>
                      <div className="flex items-center gap-2 mb-1 flex-wrap">
                        {editingName ? (
                          <input
                            type="text"
                            value={nameDraft}
                            onChange={(e) => setNameDraft(e.target.value)}
                            onBlur={() => void commitContactRename()}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') {
                                e.preventDefault();
                                void commitContactRename();
                              }
                              if (e.key === 'Escape') {
                                setEditingName(false);
                                setNameDraft(selected.name);
                              }
                            }}
                            autoFocus
                            disabled={nameSaving}
                            className="text-xl font-semibold bg-secondary/40 border border-border rounded-lg px-2 py-1 min-w-[12rem] max-w-full"
                            aria-label="Nom du contact"
                          />
                        ) : (
                          <>
                            <h2 className="mb-0">{selected.name}</h2>
                            <button
                              type="button"
                              onClick={() => {
                                setEditingName(true);
                                setNameDraft(selected.name);
                              }}
                              className="p-1.5 rounded-lg bg-white/5 border border-white/10 hover:bg-white/10 shrink-0"
                              aria-label="Renommer le contact"
                            >
                              <Pencil className="w-4 h-4 text-muted-foreground" />
                            </button>
                          </>
                        )}
                      </div>
                      <p className="text-muted-foreground">{selected.relationship || '—'}</p>
                      {aiDescription ? (
                        <div className="mt-3 flex items-start gap-2">
                          <p className="text-sm text-muted-foreground flex-1 whitespace-pre-wrap">{aiDescription}</p>
                          <button
                            type="button"
                            onClick={() => void onRefreshDescription()}
                            disabled={descRefreshing}
                            className="shrink-0 p-2 rounded-lg bg-white/5 border border-white/10 hover:bg-white/10 disabled:opacity-50"
                            aria-label="Régénérer la description"
                          >
                            <RefreshCw className={`w-4 h-4 ${descRefreshing ? 'animate-spin' : ''}`} />
                          </button>
                        </div>
                      ) : null}
                    </div>
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={() => void onAnalyze()}
                        disabled={analyzeBusy}
                        className="flex items-center gap-2 px-4 py-2 rounded-xl bg-white text-black text-sm font-mono disabled:opacity-50"
                      >
                        {analyzeBusy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
                        Analyser (iMessage)
                      </button>
                    </div>
                  </div>
                  {detailLoading && (
                    <p className="text-xs text-muted-foreground mt-2 font-mono">Chargement du profil…</p>
                  )}
                  <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 mt-4">
                    <div className="text-center p-3 rounded-xl bg-white/5 border border-white/10">
                      <MessageSquare className="w-5 h-5 mx-auto mb-2" />
                      <p className="text-2xl font-mono">{selected.message_count ?? '—'}</p>
                      <p className="text-xs text-muted-foreground">Messages (est.)</p>
                    </div>
                    <div className="text-center p-3 rounded-xl bg-white/5 border border-white/10">
                      <TrendingUp className="w-5 h-5 mx-auto mb-2" />
                      <p className="text-2xl font-mono">
                        {String((rel?.relationship_profile as { sentiment?: string } | null)?.sentiment || '—').slice(0, 8)}
                      </p>
                      <p className="text-xs text-muted-foreground">Sentiment</p>
                    </div>
                    <div className="text-center p-3 rounded-xl bg-white/5 border border-white/10 text-xs">
                      <p className="text-muted-foreground mb-1">Confiance</p>
                      <p className="font-mono text-lg">
                        {String((rel?.relationship_profile as { trust_level?: string | number } | null)?.trust_level ?? '—')}
                      </p>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <div className="glass-panel rounded-xl p-6">
              <h3 className="mb-1">Notes & style</h3>
              <p className="text-sm text-muted-foreground mb-3">Depuis la base JARVIS</p>
              <p className="text-sm whitespace-pre-wrap">{selected.personality_notes || '—'}</p>
              {rel?.relationship_profile && (
                <p className="text-sm text-muted-foreground mt-3 whitespace-pre-wrap">
                  {String((rel.relationship_profile as { communication_style?: string }).communication_style || '')}
                </p>
              )}
            </div>

            <div className="glass-panel rounded-xl p-6">
              <h3 className="mb-4">Événements récents</h3>
              <div className="space-y-2 max-h-48 overflow-y-auto">
                {(detail?.events ?? []).length === 0 && (
                  <p className="text-sm text-muted-foreground">Aucun événement enregistré.</p>
                )}
                {(detail?.events ?? []).slice(0, 12).map((ev, i) => (
                  <div key={i} className="p-2 rounded-lg bg-white/5 border border-white/10 text-sm">
                    <span className="font-mono text-xs text-muted-foreground">
                      {(ev.event_date || ev.created_at || '').slice(0, 16)} · {ev.event_type || 'event'}
                    </span>
                    <p className="mt-1">{((ev.summary || ev.content) || '').slice(0, 200)}</p>
                  </div>
                ))}
              </div>
            </div>

            <div className="glass-panel rounded-xl p-6">
              <div className="flex items-start justify-between mb-4">
                <div>
                  <h3 className="mb-1">Historique des Interactions</h3>
                  <p className="text-sm text-muted-foreground">Messages par mois (3 derniers mois)</p>
                </div>
              </div>
              <ResponsiveContainer width="100%" height={200}>
                <LineChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255, 255, 255, 0.05)" />
                  <XAxis dataKey="date" stroke="#6b7280" fontSize={12} />
                  <YAxis stroke="#6b7280" fontSize={12} />
                  <Tooltip
                    contentStyle={{
                      background: 'rgba(15, 15, 15, 0.95)',
                      border: '1px solid rgba(255, 255, 255, 0.1)',
                      borderRadius: '12px',
                    }}
                  />
                  <Line type="monotone" dataKey="messages" stroke="#ffffff" strokeWidth={2} dot />
                </LineChart>
              </ResponsiveContainer>
            </div>

            <div className="glass-panel rounded-xl p-6">
              <h3 className="mb-4">Tags</h3>
              <div className="flex flex-wrap gap-2">
                {tags.length === 0 && <p className="text-sm text-muted-foreground">Aucun tag.</p>}
                {tags.map((tag) => (
                  <span key={tag} className="px-3 py-1.5 rounded-lg bg-white/5 border border-white/10 text-sm inline-flex items-center gap-1">
                    <Tag className="w-3 h-3" />
                    {tag}
                  </span>
                ))}
              </div>
            </div>

            {analyticsLoading && (
              <p className="px-6 text-xs text-muted-foreground font-mono">Chargement des métriques iMessage…</p>
            )}

            {selected && analytics && (
              <>
                {(analytics as { error?: string }).error ? (
                  <div className="glass-panel rounded-xl p-6 border border-white/10">
                    <h3 className="mb-2 text-sm text-muted-foreground">Métriques iMessage</h3>
                    <p className="text-sm">{(analytics as { error: string }).error}</p>
                  </div>
                ) : null}

                {(() => {
                  const ps = (analytics as { proximity_score?: { score?: number; breakdown?: Record<string, number> } })
                    .proximity_score;
                  const score = Math.min(100, Math.max(0, ps?.score ?? 0));
                  const R = 52;
                  const C = 2 * Math.PI * R;
                  const dash = (score / 100) * C;
                  const col = score >= 70 ? '#22c55e' : score >= 40 ? '#f97316' : '#ef4444';
                  const breakdown = ps?.breakdown ?? {};
                  const blabel: Record<string, string> = {
                    frequency: 'Fréquence',
                    recency: 'Récence',
                    balance: 'Équilibre',
                    initiative: 'Initiative',
                    depth: 'Profondeur',
                    affection: 'Affect / emojis',
                  };
                  return (
                    <div className="glass-panel rounded-xl p-6">
                      <h3 className="mb-4">Score de proximité</h3>
                      <div className="flex flex-col sm:flex-row items-center gap-8">
                        <div className="relative w-32 h-32 shrink-0">
                          <svg viewBox="0 0 128 128" className="w-32 h-32 -rotate-90">
                            <circle
                              cx="64"
                              cy="64"
                              r={R}
                              fill="none"
                              stroke="rgba(255,255,255,0.12)"
                              strokeWidth="10"
                            />
                            <circle
                              cx="64"
                              cy="64"
                              r={R}
                              fill="none"
                              stroke={col}
                              strokeWidth="10"
                              strokeDasharray={`${dash} ${C}`}
                              strokeLinecap="round"
                            />
                          </svg>
                          <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                            <span className="text-3xl font-mono font-semibold">{score}</span>
                          </div>
                        </div>
                        <div className="flex-1 min-w-0 space-y-2 w-full">
                          {(Object.keys(breakdown).length ? Object.entries(breakdown) : []).map(([k, v]) => (
                            <div key={k}>
                              <div className="flex justify-between text-xs text-muted-foreground mb-0.5">
                                <span>{blabel[k] ?? k}</span>
                                <span className="font-mono">{typeof v === 'number' ? v.toFixed(1) : v}</span>
                              </div>
                              <div className="h-2 rounded-full bg-white/10 overflow-hidden">
                                <div
                                  className="h-full rounded-full bg-white/60"
                                  style={{ width: `${Math.min(100, ((v as number) / 20) * 100)}%` }}
                                />
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    </div>
                  );
                })()}

                {(() => {
                  const tr = (analytics as { trend?: Record<string, unknown> }).trend as
                    | {
                        months?: Array<{ month?: string; count?: number }>;
                        trend_pct?: number;
                        direction?: string;
                        direction_label?: string;
                        label?: string;
                      }
                    | undefined;
                  if (!tr?.months?.length) return null;
                  const maxC = Math.max(1, ...tr.months.map((m) => m.count ?? 0));
                  const up = (tr.trend_pct ?? 0) > 0;
                  const down = (tr.trend_pct ?? 0) < 0;
                  return (
                    <div className="glass-panel rounded-xl p-6">
                      <h3 className="mb-4">Tendance (messages / mois)</h3>
                      <div className="flex items-end gap-4 h-40">
                        {tr.months.map((m, i) => (
                          <div key={i} className="flex-1 h-full flex flex-col justify-end items-center gap-2">
                            <div
                              className="w-full rounded-t-lg bg-white/25"
                              style={{
                                height: `${Math.max(8, ((m.count ?? 0) / maxC) * 120)}px`,
                              }}
                            />
                            <span className="text-xs text-muted-foreground font-mono">{m.month}</span>
                            <span className="text-xs font-mono">{m.count ?? 0}</span>
                          </div>
                        ))}
                      </div>
                      <div className="mt-4 flex flex-wrap items-center gap-2">
                        <span
                          className={`text-xs font-mono px-2 py-1 rounded-lg border ${
                            up ? 'border-green-500/50 text-green-400' : down ? 'border-red-500/50 text-red-400' : 'border-white/20 text-muted-foreground'
                          }`}
                        >
                          {tr.label ?? `${tr.trend_pct ?? 0}%`}
                        </span>
                        <span className="text-sm text-muted-foreground">{tr.direction_label ?? tr.direction}</span>
                      </div>
                    </div>
                  );
                })()}

                {(() => {
                  const heat = ((analytics as { sentiment_heatmap?: Array<Record<string, unknown>> }).sentiment_heatmap ??
                    []) as Array<{ week?: string; sentiment_score?: number; total_messages?: number; positive?: number; negative?: number }>;
                  const raw = heat.slice(-12);
                  if (!raw.length) {
                    return (
                      <div className="glass-panel rounded-xl p-6">
                        <h3 className="mb-4">Sentiment (12 dernières semaines)</h3>
                        <p className="text-sm text-muted-foreground">Analyse en cours...</p>
                      </div>
                    );
                  }
                  const pad = Math.max(0, 12 - raw.length);
                  const neutral = (): {
                    week?: string;
                    sentiment_score?: number;
                    total_messages?: number;
                    positive?: number;
                    negative?: number;
                  } => ({
                    week: '',
                    sentiment_score: 0.5,
                    total_messages: 0,
                    positive: 0,
                    negative: 0,
                  });
                  const cells = [...Array.from({ length: pad }, neutral), ...raw];
                  return (
                    <div className="glass-panel rounded-xl p-6">
                      <h3 className="mb-4">Sentiment (12 dernières semaines)</h3>
                      <div className="grid grid-cols-6 gap-1">
                        {cells.map((c, i) => {
                          const s = typeof c.sentiment_score === 'number' ? c.sentiment_score : 0.5;
                          return (
                            <div
                              key={i}
                              title={`${c.week || '—'} — ${c.total_messages ?? 0} msg — +${c.positive ?? 0} / −${c.negative ?? 0}`}
                              className="aspect-square rounded-md border border-white/10"
                              style={{ backgroundColor: sentimentColor(s) }}
                            />
                          );
                        })}
                      </div>
                    </div>
                  );
                })()}

                <div className="glass-panel rounded-xl p-6">
                  <h3 className="mb-4">Sujets récurrents</h3>
                  <div className="flex flex-wrap gap-2">
                    {(!(analytics as Record<string, unknown>).topics ||
                      ((analytics as Record<string, unknown>).topics as unknown[]).length === 0) && (
                      <p className="text-sm text-muted-foreground">Aucun mot-clé dominant.</p>
                    )}
                    {(((analytics as Record<string, unknown>).topics ?? []) as unknown[]).map((raw) => {
                      const t = raw as { word?: string; count?: number };
                      return (
                      <span
                        key={`${t.word}-${t.count}`}
                        className="px-3 py-1.5 rounded-lg bg-white/5 border border-white/10 text-sm font-mono"
                      >
                        {t.word}{' '}
                        <span className="text-muted-foreground">×{t.count}</span>
                      </span>
                      );
                    })}
                  </div>
                </div>

                {(() => {
                  const u = (analytics as { unanswered?: { from_me?: Array<Record<string, unknown>>; from_them?: Array<Record<string, unknown>> } }).unanswered;
                  const mine = u?.from_me ?? [];
                  const theirs = u?.from_them ?? [];
                  if (!mine.length && !theirs.length) return null;
                  return (
                    <div className="glass-panel rounded-xl p-6 border border-amber-500/20">
                      <h3 className="mb-3">Messages en attente (&gt; 6 h)</h3>
                      {mine.length > 0 && (
                        <div className="mb-3">
                          <p className="text-xs text-muted-foreground mb-2">Tu n&apos;as pas répondu</p>
                          <ul className="space-y-2 text-sm">
                            {mine.map((x, i) => (
                              <li key={i} className="p-2 rounded-lg bg-white/5 border border-white/10">
                                {(x.text as string) || '—'}{' '}
                                <span className="text-xs text-muted-foreground">
                                  il y a {x.hours_ago as number}h
                                </span>
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                      {theirs.length > 0 && (
                        <div>
                          <p className="text-xs text-muted-foreground mb-2">Il / elle n&apos;a pas répondu</p>
                          <ul className="space-y-2 text-sm">
                            {theirs.map((x, i) => (
                              <li key={i} className="p-2 rounded-lg bg-white/5 border border-white/10">
                                {(x.text as string) || '—'}{' '}
                                <span className="text-xs text-muted-foreground">
                                  il y a {x.hours_ago as number}h
                                </span>
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </div>
                  );
                })()}

                <div className="glass-panel rounded-xl p-6">
                  <h3 className="mb-4">Derniers échanges</h3>
                  <div className="space-y-2">
                    {(
                      ((analytics as { last_exchanges?: Array<Record<string, unknown>> }).last_exchanges ??
                        []) as Array<{ sender?: string; text?: string; date?: string; is_from_me?: boolean }>
                    ).map((m, i) => (
                      <div
                        key={i}
                        className={`flex ${m.is_from_me ? 'justify-end' : 'justify-start'}`}
                      >
                        <div
                          className={`max-w-[90%] rounded-xl px-3 py-2 text-sm ${
                            m.is_from_me
                              ? 'bg-white text-black'
                              : 'bg-white/5 border border-white/10'
                          }`}
                        >
                          <p className="line-clamp-3">{m.text}</p>
                          <p className="text-[10px] text-muted-foreground mt-1 font-mono">
                            {formatRelativeTime(m.date)}
                          </p>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                {(() => {
                  const cp = (analytics as { communication_patterns?: Record<string, unknown> }).communication_patterns as
                    | {
                        total_from_me?: number;
                        total_from_them?: number;
                        avg_response_time_me_min?: number | null;
                        avg_response_time_them_min?: number | null;
                        peak_hours_me?: Array<[number, number]>;
                        peak_hours_them?: Array<[number, number]>;
                        avg_length_me?: number;
                        avg_length_them?: number;
                      }
                    | undefined;
                  if (!cp) return null;
                  const tm = cp.total_from_me ?? 0;
                  const tt = cp.total_from_them ?? 1;
                  const pm = tm + tt > 0 ? (tm / (tm + tt)) * 100 : 50;
                  return (
                    <div className="glass-panel rounded-xl p-6">
                      <h3 className="mb-4">Patterns</h3>
                      <p className="text-xs text-muted-foreground mb-2">Volume (toi vs contact)</p>
                      <div className="h-3 rounded-full bg-white/10 overflow-hidden flex mb-4">
                        <div className="h-full bg-white/70" style={{ width: `${pm}%` }} />
                        <div className="h-full bg-white/25 flex-1" />
                      </div>
                      <p className="text-sm mb-2">
                        Temps de réponse moyen — Toi :{' '}
                        <span className="font-mono">{formatHoursFromMinutes(cp.avg_response_time_me_min ?? undefined)}</span>{' '}
                        | Contact :{' '}
                        <span className="font-mono">{formatHoursFromMinutes(cp.avg_response_time_them_min ?? undefined)}</span>
                      </p>
                      <p className="text-sm mb-2 text-muted-foreground">
                        Activité — toi surtout à{' '}
                        {(cp.peak_hours_me ?? []).slice(0, 3).map((x) => `${x[0]}h`).join(', ') || '—'} ; eux à{' '}
                        {(cp.peak_hours_them ?? []).slice(0, 3).map((x) => `${x[0]}h`).join(', ') || '—'}.
                      </p>
                      <p className="text-sm">
                        Longueur moyenne — Tes messages :{' '}
                        <span className="font-mono">{cp.avg_length_me ?? 0}</span> car. | Les siens :{' '}
                        <span className="font-mono">{cp.avg_length_them ?? 0}</span> car.
                      </p>
                    </div>
                  );
                })()}

                <div className="glass-panel rounded-xl p-6">
                  <h3 className="mb-4">Dates importantes (détection texte)</h3>
                  <ul className="space-y-2 text-sm max-h-48 overflow-y-auto">
                    {(
                      ((analytics as { important_dates?: Array<Record<string, unknown>> }).important_dates ??
                        []) as Array<{ keyword?: string; context?: string; date?: string }>
                    ).length === 0 && (
                      <li className="text-muted-foreground">Aucune mention détectée.</li>
                    )}
                    {(
                      ((analytics as { important_dates?: Array<Record<string, unknown>> }).important_dates ??
                        []) as Array<{ keyword?: string; context?: string; date?: string }>
                    ).map((d, i) => (
                      <li key={i} className="p-2 rounded-lg bg-white/5 border border-white/10">
                        <span className="font-mono text-xs text-muted-foreground">{d.keyword}</span>
                        <p className="mt-1">{d.context}</p>
                        <p className="text-[10px] text-muted-foreground mt-1">{formatRelativeTime(d.date)}</p>
                      </li>
                    ))}
                  </ul>
                </div>

                <div className="glass-panel rounded-xl p-6 space-y-3">
                  <h3 className="mb-1">Actions</h3>
                  <div className="flex flex-col sm:flex-row gap-2">
                    <input
                      type="text"
                      value={imessageDraft}
                      onChange={(e) => setImessageDraft(e.target.value)}
                      placeholder="Message iMessage…"
                      disabled={actionBusy}
                      className="flex-1 h-10 px-4 bg-secondary/30 border border-border rounded-xl
                               focus:outline-none focus:bg-secondary/50 focus:border-white/20 disabled:opacity-50"
                    />
                    <button
                      type="button"
                      onClick={() => void onSendImessage()}
                      disabled={actionBusy || !imessageDraft.trim()}
                      className="px-4 py-2 rounded-xl bg-white text-black text-sm font-mono disabled:opacity-50"
                    >
                      Envoyer un iMessage
                    </button>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() => void onSuggestMessage()}
                      disabled={actionBusy}
                      className="px-4 py-2 rounded-xl bg-white/5 border border-white/10 text-sm hover:bg-white/10 disabled:opacity-50"
                    >
                      Suggérer un message
                    </button>
                    <button
                      type="button"
                      onClick={() => void onRemindContact()}
                      disabled={actionBusy}
                      className="px-4 py-2 rounded-xl bg-white/5 border border-white/10 text-sm hover:bg-white/10 disabled:opacity-50"
                    >
                      Me rappeler de contacter
                    </button>
                  </div>
                </div>

                <div className="glass-panel rounded-xl p-6">
                  {/* Header */}
                  <div className="flex items-start justify-between mb-4 flex-wrap gap-3">
                    <div>
                      <h3 className="mb-1">Timeline relationnelle</h3>
                      {timelineUpdatedAt ? (
                        <p className="text-xs text-muted-foreground font-mono">
                          Cache du {new Date(timelineUpdatedAt).toLocaleString('fr-FR', {
                            day: '2-digit', month: '2-digit', year: 'numeric',
                            hour: '2-digit', minute: '2-digit',
                          })}
                        </p>
                      ) : (
                        <p className="text-xs text-muted-foreground">Génération Haiku · tokens consommés au premier appel</p>
                      )}
                    </div>
                    <div className="flex items-center gap-2">
                      {/* Bouton principal : génère ou charge depuis cache */}
                      {!timelineEvents.length && (
                        <button
                          type="button"
                          onClick={() => void loadAiTimeline()}
                          disabled={timelineLoading || timelineRegenerating || actionBusy}
                          className="flex items-center gap-2 px-4 py-2 rounded-xl bg-white text-black text-sm font-mono disabled:opacity-50"
                        >
                          {timelineLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
                          {timelineLoading ? 'Chargement…' : 'Charger la timeline'}
                        </button>
                      )}
                      {/* Bouton Regénérer (visible si timeline déjà chargée) */}
                      {timelineEvents.length > 0 && (
                        <button
                          type="button"
                          onClick={() => void regenerateTimeline()}
                          disabled={timelineRegenerating || timelineLoading || actionBusy}
                          className="flex items-center gap-2 px-3 py-1.5 rounded-xl bg-white/5 border border-white/15 text-xs font-mono text-white/70 hover:bg-white/10 hover:text-white disabled:opacity-40 transition-colors"
                        >
                          {timelineRegenerating
                            ? <Loader2 className="w-3 h-3 animate-spin" />
                            : <span className="text-xs">↺</span>}
                          {timelineRegenerating ? 'Analyse…' : 'Regénérer'}
                        </button>
                      )}
                    </div>
                  </div>

                  {/* État de chargement */}
                  {(timelineLoading || timelineRegenerating) && (
                    <p className="text-xs text-muted-foreground font-mono mb-3 animate-pulse">
                      {timelineRegenerating
                        ? 'Analyse des nouveaux événements en cours…'
                        : 'Lecture du cache…'}
                    </p>
                  )}

                  {/* Timeline */}
                  <div className="relative border-l border-white/15 pl-6 space-y-4">
                    {timelineEvents.map((ev, i) => {
                      const t = String(ev.type ?? '');
                      const color =
                        t === 'first_contact'
                          ? 'bg-blue-500'
                          : t === 'conflict'
                            ? 'bg-red-500'
                            : t === 'reconciliation'
                              ? 'bg-green-600'
                              : t === 'milestone'
                                ? 'bg-amber-400'
                                : t === 'distance'
                                  ? 'bg-zinc-500'
                                  : t === 'support'
                                    ? 'bg-emerald-400'
                                    : 'bg-white/40';
                      return (
                        <div key={i} className="relative">
                          <span
                            className={`absolute -left-[29px] top-1.5 w-3 h-3 rounded-full ${color}`}
                          />
                          <p className="font-mono text-xs text-muted-foreground">{String(ev.date ?? '')}</p>
                          <p className="font-medium">{String(ev.title ?? '')}</p>
                          <p className="text-sm text-muted-foreground whitespace-pre-wrap">{String(ev.summary ?? '')}</p>
                        </div>
                      );
                    })}
                    {!timelineEvents.length && !timelineLoading && !timelineRegenerating && (
                      <p className="text-sm text-muted-foreground">Clique sur le bouton pour charger.</p>
                    )}
                  </div>
                </div>
              </>
            )}

            <div className="glass-panel rounded-xl p-6 flex flex-col gap-3">
              <h3 className="mb-1 text-sm font-medium">Question à JARVIS sur ce contact</h3>
              <div className="space-y-2 max-h-56 overflow-y-auto text-sm">
                {contactMessages.map((m, i) => (
                  <div
                    key={`${m.role}-${i}`}
                    className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}
                  >
                    <div
                      className={`max-w-[85%] rounded-xl px-3 py-2 ${
                        m.role === 'user'
                          ? 'bg-white text-black'
                          : 'bg-white/5 border border-white/10 text-foreground'
                      }`}
                    >
                      {m.content}
                    </div>
                  </div>
                ))}
                {contactLoading && (
                  <div className="flex justify-start">
                    <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
                  </div>
                )}
              </div>
              <div className="flex gap-2 items-center">
                <input
                  type="text"
                  value={contactInput}
                  onChange={(e) => setContactInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') void askAboutContact();
                  }}
                  placeholder="Pose une question sur cette personne…"
                  disabled={contactLoading}
                  className="flex-1 h-10 px-4 bg-secondary/30 border border-border rounded-xl
                           focus:outline-none focus:bg-secondary/50 focus:border-white/20 disabled:opacity-50"
                />
                <button
                  type="button"
                  onClick={() => void askAboutContact()}
                  disabled={contactLoading || !contactInput.trim()}
                  className="flex items-center justify-center px-4 py-2 rounded-xl bg-white text-black text-sm font-mono disabled:opacity-50"
                >
                  {contactLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <MessageSquare className="w-4 h-4" />}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
