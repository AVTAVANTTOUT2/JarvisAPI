/**
 * SearchView — recherche unifiée JARVIS.
 *
 * Mode "rapide" : filtrage côté client (conversations, contacts, tâches, documents).
 * Mode "JARVIS" : la question est envoyée via WebSocket ; JARVIS cherche dans toutes
 * les données (iMessage, fichiers, mémoire, etc.) et renvoie une réponse en streaming.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Search, Zap, MessageSquare, Users, FileText, CheckSquare, Loader2 } from 'lucide-react';
import { api } from '@/services/api';
import { ws } from '@/services/websocket';

interface ConvItem {
  id: number;
  title: string | null;
  last_message?: string;
  last_message_at?: string;
  message_count?: number;
}

interface PersonItem {
  id: number;
  name: string;
  relationship?: string;
}

interface TaskItem {
  id: number;
  title: string;
  status: string;
  priority: string;
}

interface DocItem {
  id: number;
  title: string;
  doc_type?: string;
  subject_name?: string;
}

type SearchMode = 'quick' | 'jarvis';

type ResultCategory = 'conversations' | 'contacts' | 'tasks' | 'docs';

const CATEGORY_LABELS: Record<ResultCategory, string> = {
  conversations: 'Conversations',
  contacts: 'Contacts',
  tasks: 'Tâches',
  docs: 'Documents',
};

const CATEGORY_ICONS: Record<ResultCategory, React.ComponentType<{ size?: number; className?: string }>> = {
  conversations: MessageSquare,
  contacts: Users,
  tasks: CheckSquare,
  docs: FileText,
};

function highlight(text: string, query: string): React.ReactNode {
  if (!query.trim()) return text;
  const parts = text.split(new RegExp(`(${query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi'));
  return parts.map((part, i) =>
    part.toLowerCase() === query.toLowerCase()
      ? <mark key={i} className="bg-white/20 text-white rounded px-0.5">{part}</mark>
      : part
  );
}

export function SearchView() {
  const [query, setQuery] = useState('');
  const [mode, setMode] = useState<SearchMode>('quick');
  const [activeCategory, setActiveCategory] = useState<ResultCategory | 'all'>('all');

  // Data sources
  const [conversations, setConversations] = useState<ConvItem[]>([]);
  const [contacts, setContacts] = useState<PersonItem[]>([]);
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [docs, setDocs] = useState<DocItem[]>([]);
  const [loading, setLoading] = useState(true);

  // JARVIS mode state
  const [jarvisResponse, setJarvisResponse] = useState('');
  const [jarvisStreaming, setJarvisStreaming] = useState(false);
  const [jarvisEmotion, setJarvisEmotion] = useState('neutral');
  const inputRef = useRef<HTMLInputElement>(null);

  // ── Chargement des données ────────────────────────────────
  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [convData, peopleData, tasksData, memData] = await Promise.allSettled([
        api.getConversations(false, 200),
        api.getPeople(),
        api.getTasks(),
        api.getMemory(),
      ]);
      if (convData.status === 'fulfilled') {
        const d = convData.value as { conversations?: ConvItem[] };
        setConversations(d.conversations || []);
      }
      if (peopleData.status === 'fulfilled') {
        const d = peopleData.value as { people?: PersonItem[] } | PersonItem[];
        setContacts(Array.isArray(d) ? d : (d as { people?: PersonItem[] }).people || []);
      }
      if (tasksData.status === 'fulfilled') {
        const d = tasksData.value as { tasks?: TaskItem[] } | TaskItem[];
        setTasks(Array.isArray(d) ? d : (d as { tasks?: TaskItem[] }).tasks || []);
      }
      if (memData.status === 'fulfilled') {
        const d = memData.value as { school_documents?: DocItem[] };
        setDocs(d.school_documents || []);
      }
    } catch (e) {
      console.error('[SearchView] loadData', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // ── JARVIS WebSocket events ───────────────────────────────
  useEffect(() => {
    if (mode !== 'jarvis') return;

    const unsubs: Array<() => void> = [];

    unsubs.push(ws.on('chunk', (d) => {
      setJarvisResponse(prev => prev + (d.content as string || ''));
    }));

    unsubs.push(ws.on('response', (d) => {
      const text = (d.content as string) || '';
      if (text) setJarvisResponse(text);
      setJarvisEmotion((d.emotion as string) || 'neutral');
      setJarvisStreaming(false);
    }));

    unsubs.push(ws.on('response_clean', (d) => {
      const text = (d.content as string) || '';
      if (text) setJarvisResponse(text);
      setJarvisStreaming(false);
    }));

    unsubs.push(ws.on('response_followup', (d) => {
      const text = (d.content as string) || '';
      if (text) setJarvisResponse(text);
      setJarvisStreaming(false);
    }));

    unsubs.push(ws.on('done', () => {
      setJarvisStreaming(false);
    }));

    unsubs.push(ws.on('error', () => {
      setJarvisStreaming(false);
    }));

    return () => unsubs.forEach(u => u());
  }, [mode]);

  // ── Filtrage côté client ──────────────────────────────────
  const q = query.trim().toLowerCase();

  const filteredConvs = q
    ? conversations.filter(c =>
        (c.title || '').toLowerCase().includes(q) ||
        (c.last_message || '').toLowerCase().includes(q)
      )
    : conversations.slice(0, 20);

  const filteredContacts = q
    ? contacts.filter(p =>
        (p.name || '').toLowerCase().includes(q) ||
        (p.relationship || '').toLowerCase().includes(q)
      )
    : contacts.slice(0, 20);

  const filteredTasks = q
    ? tasks.filter(t =>
        (t.title || '').toLowerCase().includes(q)
      )
    : tasks.slice(0, 20);

  const filteredDocs = q
    ? docs.filter(d =>
        (d.title || '').toLowerCase().includes(q) ||
        (d.doc_type || '').toLowerCase().includes(q) ||
        (d.subject_name || '').toLowerCase().includes(q)
      )
    : docs.slice(0, 20);

  const totalResults = filteredConvs.length + filteredContacts.length + filteredTasks.length + filteredDocs.length;

  // ── Actions ───────────────────────────────────────────────
  function handleSearch(e: React.FormEvent) {
    e.preventDefault();
    if (!query.trim()) return;

    if (mode === 'jarvis') {
      setJarvisResponse('');
      setJarvisStreaming(true);
      ws.sendText(query, true, false);
    }
    // En mode quick, le filtrage est réactif — rien à faire
  }

  function switchMode(m: SearchMode) {
    setMode(m);
    setJarvisResponse('');
    setJarvisStreaming(false);
    inputRef.current?.focus();
  }

  // ── Affichage des sections ────────────────────────────────
  const showConvs = activeCategory === 'all' || activeCategory === 'conversations';
  const showContacts = activeCategory === 'all' || activeCategory === 'contacts';
  const showTasks = activeCategory === 'all' || activeCategory === 'tasks';
  const showDocs = activeCategory === 'all' || activeCategory === 'docs';

  const priorityColor = (p: string) =>
    p === 'high' ? 'text-red-400' : p === 'medium' ? 'text-yellow-400' : 'text-green-400';

  const statusColor = (s: string) =>
    s === 'done' ? 'text-green-400' : s === 'doing' ? 'text-blue-400' : 'text-muted-foreground';

  return (
    <div className="flex flex-col h-full p-6 gap-4 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between shrink-0">
        <div>
          <h1 className="text-xl font-semibold">Recherche</h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            Rechercher dans toutes vos données ou demander directement à JARVIS
          </p>
        </div>
        {/* Toggle mode */}
        <div className="flex items-center gap-1 bg-white/5 rounded-xl p-1 border border-white/10">
          <button
            onClick={() => switchMode('quick')}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm transition-all ${
              mode === 'quick'
                ? 'bg-white text-black font-medium'
                : 'text-muted-foreground hover:text-white'
            }`}
          >
            <Search size={14} />
            Recherche rapide
          </button>
          <button
            onClick={() => switchMode('jarvis')}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm transition-all ${
              mode === 'jarvis'
                ? 'bg-white text-black font-medium'
                : 'text-muted-foreground hover:text-white'
            }`}
          >
            <Zap size={14} />
            Demander à JARVIS
          </button>
        </div>
      </div>

      {/* Barre de recherche */}
      <form onSubmit={handleSearch} className="shrink-0">
        <div className="relative">
          <Search size={18} className="absolute left-4 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder={
              mode === 'jarvis'
                ? 'Posez une question à JARVIS — "Quand ai-je parlé du projet X ?" …'
                : 'Rechercher dans conversations, contacts, tâches, documents…'
            }
            className="w-full bg-white/5 border border-white/10 rounded-xl pl-11 pr-28 py-3 text-sm placeholder:text-muted-foreground focus:outline-none focus:border-white/30 transition-colors"
            autoFocus
          />
          <button
            type="submit"
            disabled={!query.trim() || (mode === 'jarvis' && jarvisStreaming)}
            className="absolute right-3 top-1/2 -translate-y-1/2 px-3 py-1.5 bg-white text-black rounded-lg text-sm font-medium disabled:opacity-40 disabled:cursor-not-allowed hover:bg-white/90 transition-opacity"
          >
            {mode === 'jarvis' && jarvisStreaming ? (
              <Loader2 size={14} className="animate-spin" />
            ) : mode === 'jarvis' ? 'Envoyer' : 'Rechercher'}
          </button>
        </div>
      </form>

      {/* Réponse JARVIS */}
      {mode === 'jarvis' && (jarvisResponse || jarvisStreaming) && (
        <div className="shrink-0 bg-white/5 border border-white/10 rounded-xl p-4 text-sm">
          <div className="flex items-center gap-2 mb-2 text-muted-foreground text-xs">
            <Zap size={12} />
            <span>JARVIS — {jarvisEmotion}</span>
            {jarvisStreaming && <Loader2 size={12} className="animate-spin ml-auto" />}
          </div>
          <p className="whitespace-pre-wrap leading-relaxed">
            {jarvisResponse || <span className="text-muted-foreground italic">Recherche en cours…</span>}
          </p>
        </div>
      )}

      {/* Filtres par catégorie */}
      {(mode === 'quick' || query) && (
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={() => setActiveCategory('all')}
            className={`px-3 py-1 rounded-lg text-xs transition-colors ${
              activeCategory === 'all'
                ? 'bg-white text-black font-medium'
                : 'bg-white/5 text-muted-foreground hover:text-white border border-white/10'
            }`}
          >
            Tout {q && `(${totalResults})`}
          </button>
          {(Object.entries(CATEGORY_LABELS) as [ResultCategory, string][]).map(([cat, label]) => {
            const count = cat === 'conversations' ? filteredConvs.length
              : cat === 'contacts' ? filteredContacts.length
              : cat === 'tasks' ? filteredTasks.length
              : filteredDocs.length;
            const Icon = CATEGORY_ICONS[cat];
            return (
              <button
                key={cat}
                onClick={() => setActiveCategory(cat)}
                className={`flex items-center gap-1.5 px-3 py-1 rounded-lg text-xs transition-colors ${
                  activeCategory === cat
                    ? 'bg-white text-black font-medium'
                    : 'bg-white/5 text-muted-foreground hover:text-white border border-white/10'
                }`}
              >
                <Icon size={12} />
                {label} {q && `(${count})`}
              </button>
            );
          })}
        </div>
      )}

      {/* Résultats */}
      <div className="flex-1 overflow-y-auto space-y-6 min-h-0">
        {loading ? (
          <div className="flex items-center justify-center h-32 text-muted-foreground">
            <Loader2 size={20} className="animate-spin mr-2" />
            Chargement…
          </div>
        ) : (
          <>
            {/* Conversations */}
            {showConvs && filteredConvs.length > 0 && (
              <section>
                <h3 className="flex items-center gap-2 text-xs font-semibold text-muted-foreground uppercase tracking-widest mb-2">
                  <MessageSquare size={12} />
                  Conversations ({filteredConvs.length})
                </h3>
                <div className="space-y-1.5">
                  {filteredConvs.map(c => (
                    <div
                      key={c.id}
                      className="flex items-start gap-3 p-3 rounded-xl bg-white/3 border border-white/5 hover:bg-white/5 hover:border-white/10 transition-colors cursor-pointer"
                      onClick={() => window.location.href = '/chat'}
                    >
                      <MessageSquare size={14} className="mt-0.5 shrink-0 text-muted-foreground" />
                      <div className="min-w-0">
                        <p className="text-sm font-medium truncate">
                          {q ? highlight(c.title || 'Sans titre', q) : (c.title || 'Sans titre')}
                        </p>
                        {c.last_message && (
                          <p className="text-xs text-muted-foreground truncate mt-0.5">
                            {q ? highlight(c.last_message, q) : c.last_message}
                          </p>
                        )}
                      </div>
                      {c.message_count != null && (
                        <span className="ml-auto shrink-0 text-xs text-muted-foreground">{c.message_count} msg</span>
                      )}
                    </div>
                  ))}
                </div>
              </section>
            )}

            {/* Contacts */}
            {showContacts && filteredContacts.length > 0 && (
              <section>
                <h3 className="flex items-center gap-2 text-xs font-semibold text-muted-foreground uppercase tracking-widest mb-2">
                  <Users size={12} />
                  Contacts ({filteredContacts.length})
                </h3>
                <div className="space-y-1.5">
                  {filteredContacts.map(p => (
                    <div
                      key={p.id}
                      className="flex items-center gap-3 p-3 rounded-xl bg-white/3 border border-white/5 hover:bg-white/5 hover:border-white/10 transition-colors cursor-pointer"
                      onClick={() => window.location.href = '/contacts'}
                    >
                      <div className="w-8 h-8 rounded-full bg-white/10 flex items-center justify-center text-sm font-semibold shrink-0">
                        {(p.name || '?')[0].toUpperCase()}
                      </div>
                      <div className="min-w-0">
                        <p className="text-sm font-medium">
                          {q ? highlight(p.name, q) : p.name}
                        </p>
                        {p.relationship && (
                          <p className="text-xs text-muted-foreground">
                            {q ? highlight(p.relationship, q) : p.relationship}
                          </p>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {/* Tâches */}
            {showTasks && filteredTasks.length > 0 && (
              <section>
                <h3 className="flex items-center gap-2 text-xs font-semibold text-muted-foreground uppercase tracking-widest mb-2">
                  <CheckSquare size={12} />
                  Tâches ({filteredTasks.length})
                </h3>
                <div className="space-y-1.5">
                  {filteredTasks.map(t => (
                    <div
                      key={t.id}
                      className="flex items-center gap-3 p-3 rounded-xl bg-white/3 border border-white/5 hover:bg-white/5 hover:border-white/10 transition-colors"
                    >
                      <CheckSquare size={14} className="shrink-0 text-muted-foreground" />
                      <p className="text-sm flex-1 truncate">
                        {q ? highlight(t.title, q) : t.title}
                      </p>
                      <span className={`text-xs shrink-0 ${priorityColor(t.priority)}`}>{t.priority}</span>
                      <span className={`text-xs shrink-0 ${statusColor(t.status)}`}>{t.status}</span>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {/* Documents */}
            {showDocs && filteredDocs.length > 0 && (
              <section>
                <h3 className="flex items-center gap-2 text-xs font-semibold text-muted-foreground uppercase tracking-widest mb-2">
                  <FileText size={12} />
                  Documents ({filteredDocs.length})
                </h3>
                <div className="space-y-1.5">
                  {filteredDocs.map(d => (
                    <div
                      key={d.id}
                      className="flex items-center gap-3 p-3 rounded-xl bg-white/3 border border-white/5 hover:bg-white/5 hover:border-white/10 transition-colors cursor-pointer"
                      onClick={() => window.location.href = '/documents'}
                    >
                      <FileText size={14} className="shrink-0 text-muted-foreground" />
                      <div className="min-w-0">
                        <p className="text-sm truncate">
                          {q ? highlight(d.title, q) : d.title}
                        </p>
                        {(d.doc_type || d.subject_name) && (
                          <p className="text-xs text-muted-foreground">
                            {[d.doc_type, d.subject_name].filter(Boolean).join(' · ')}
                          </p>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {/* Empty state */}
            {q && totalResults === 0 && !loading && (
              <div className="flex flex-col items-center justify-center h-40 text-muted-foreground gap-2">
                <Search size={32} className="opacity-30" />
                <p className="text-sm">Aucun résultat pour « {query} »</p>
                {mode === 'quick' && (
                  <button
                    onClick={() => switchMode('jarvis')}
                    className="text-xs text-white/60 hover:text-white underline transition-colors"
                  >
                    Demander à JARVIS
                  </button>
                )}
              </div>
            )}

            {!q && !loading && (
              <div className="flex flex-col items-center justify-center h-40 text-muted-foreground gap-2">
                <Search size={32} className="opacity-20" />
                <p className="text-sm">Tapez pour rechercher dans toutes vos données</p>
                <p className="text-xs opacity-60">
                  {conversations.length} conversations · {contacts.length} contacts · {tasks.length} tâches · {docs.length} documents
                </p>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
