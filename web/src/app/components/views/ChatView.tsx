import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ConversationDocument, ConversationSearchResult, ConversationSummary } from '@unified/lib/api'
import { ws } from '@desktop/services/websocket'
import { Menu, Paperclip, Plus, Search, Send, X } from 'lucide-react'

// ── Types locaux ────────────────────────────────────────────

interface UIMessage {
  role: 'user' | 'assistant' | 'system'
  content: string
  agent?: string
  meta?: string
  actionType?: string
  actionResult?: Record<string, unknown>
  pendingAction?: Record<string, unknown>
  savedFile?: string
  isError?: boolean
}

interface AttachedDoc {
  name: string
  doc_id?: number
  summary?: string | null
  content_length?: number
  file_type?: string
}

// ── Helpers date ────────────────────────────────────────────

function relativeDate(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  const now = new Date()
  const diff = now.getTime() - d.getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return "à l'instant"
  if (mins < 60) return `${mins}m`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h`
  const days = Math.floor(hrs / 24)
  if (days === 1) return 'hier'
  if (days < 7) return `${days}j`
  return d.toLocaleDateString('fr-FR', { day: 'numeric', month: 'short' })
}

function groupConversations(convs: ConversationSummary[]) {
  const now = new Date()
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const startOfYesterday = new Date(startOfToday.getTime() - 86400000)
  const startOf7Days = new Date(startOfToday.getTime() - 6 * 86400000)

  const pinned: ConversationSummary[] = []
  const today: ConversationSummary[] = []
  const yesterday: ConversationSummary[] = []
  const week: ConversationSummary[] = []
  const older: ConversationSummary[] = []

  for (const c of convs) {
    if (c.pinned) { pinned.push(c); continue }
    const d = new Date(c.last_message_at || c.started_at)
    if (d >= startOfToday) today.push(c)
    else if (d >= startOfYesterday) yesterday.push(c)
    else if (d >= startOf7Days) week.push(c)
    else older.push(c)
  }
  return { pinned, today, yesterday, week, older }
}

// ── Suggestions ─────────────────────────────────────────────

const SUGGESTIONS = [
  'Resume mes mails non lus',
  'Planifie ma semaine',
  'Aide-moi a reviser',
  'Analyse mon humeur ce mois',
]

// ═══════════════════════════════════════════════════════════════
// COMPOSANT PRINCIPAL
// ═══════════════════════════════════════════════════════════════

export function ChatView() {
  const [conversations, setConversations] = useState<ConversationSummary[]>([])
  const [activeConvId, setActiveConvId] = useState<number | null>(null)
  const [messages, setMessages] = useState<UIMessage[]>([])
  const [streamingContent, setStreamingContent] = useState('')
  const [isStreaming, setIsStreaming] = useState(false)
  const [input, setInput] = useState('')
  const [attachedDocs, setAttachedDocs] = useState<AttachedDoc[]>([])
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<ConversationSearchResult[]>([])
  const [isSearching, setIsSearching] = useState(false)
  const [isDragging, setIsDragging] = useState(false)
  const [uploadingDoc, setUploadingDoc] = useState(false)
  const [convDocs, setConvDocs] = useState<ConversationDocument[]>([])
  const [contextMenuId, setContextMenuId] = useState<number | null>(null)
  const [renamingId, setRenamingId] = useState<number | null>(null)
  const [renameDraft, setRenameDraft] = useState('')
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)

  const messagesEndRef = useRef<HTMLDivElement>(null)
  const userScrolledUp = useRef(false)
  const messagesContainerRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // ── Scroll ──────────────────────────────────────────────

  const scrollToBottom = useCallback(() => {
    if (!userScrolledUp.current) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [])

  useEffect(() => {
    scrollToBottom()
  }, [messages, streamingContent, scrollToBottom])

  // ── Chargement conversations ────────────────────────────

  const loadConversations = useCallback(async () => {
    try {
      const data = await api.getConversations()
      setConversations(data.conversations || [])
    } catch (e) {
      console.error('[ChatView] loadConversations', e)
    }
  }, [])

  // ── WebSocket events ────────────────────────────────────

  useEffect(() => {
    const unsubs: Array<() => void> = []

    unsubs.push(ws.on('connected', (d) => {
      const cid = d.conversation_id as number | undefined
      if (cid) setActiveConvId(cid)
      loadConversations()
    }))

    unsubs.push(ws.on('conversation_switched', (d) => {
      const cid = d.conversation_id as number | undefined
      if (cid) setActiveConvId(cid)
    }))

    unsubs.push(ws.on('conversation_updated', (d) => {
      const cid = d.conversation_id as number
      const title = d.title as string | null
      setConversations(prev =>
        prev.map(c => c.id === cid
          ? { ...c, title, message_count: (d.message_count as number) || c.message_count, last_message_at: new Date().toISOString() }
          : c)
      )
    }))

    unsubs.push(ws.on('chunk', (d) => {
      setStreamingContent(prev => prev + (d.content as string || ''))
    }))

    unsubs.push(ws.on('response_clean', (d) => {
      const clean = d.content as string
      setStreamingContent('')
      setMessages(prev => {
        const updated = [...prev]
        const idx = updated.map((m, i) => ({ m, i })).reverse().find(x => x.m.role === 'assistant')?.i
        if (idx != null) updated[idx] = { ...updated[idx], content: clean }
        return updated
      })
    }))

    unsubs.push(ws.on('done', () => {
      setIsStreaming(false)
      setStreamingContent('')
      loadConversations()
    }))

    unsubs.push(ws.on('response', (d) => {
      setMessages(prev => {
        const updated = [...prev]
        const emptyIdx = updated.map((m, i) => ({ m, i })).reverse()
          .find(x => x.m.role === 'assistant' && !x.m.content)?.i
        if (emptyIdx != null) {
          updated[emptyIdx] = {
            ...updated[emptyIdx],
            content: d.content as string,
            agent: d.agent as string | undefined,
          }
        } else {
          updated.push({
            role: 'assistant',
            content: d.content as string,
            agent: d.agent as string | undefined,
          })
        }
        return updated
      })
      setIsStreaming(false)
    }))

    unsubs.push(ws.on('loop_started', (d) => {
      setMessages(prev => [...prev, {
        role: 'system',
        content: `Mode autonome — ${(d.task as string) || 'tâche'}`,
        meta: 'loop',
      }])
    }))

    unsubs.push(ws.on('loop_step', (d) => {
      const step = d.step as number
      const actionType = d.action_type as string
      const status = d.status as string
      const ok = d.ok as boolean | undefined
      let label = `Étape ${step} · ${actionType}`
      if (status === 'running') label += ' · en cours…'
      else if (ok === true) label += ' · OK'
      else if (ok === false) label += ' · échec'
      setMessages(prev => [...prev, { role: 'system', content: label, meta: 'loop-step' }])
    }))

    unsubs.push(ws.on('loop_progress', (d) => {
      const msg = d.message as string
      if (msg) setMessages(prev => [...prev, { role: 'system', content: msg, meta: 'loop' }])
    }))

    unsubs.push(ws.on('loop_done', (d) => {
      const steps = d.steps as number
      const status = d.status as string
      setMessages(prev => [...prev, {
        role: 'system',
        content: `Boucle terminée — ${steps} étape(s), statut : ${status}`,
        meta: 'loop-done',
      }])
      setIsStreaming(false)
      loadConversations()
    }))

    unsubs.push(ws.on('response_followup', (d) => {
      const txt = d.content as string
      setMessages(prev => {
        const updated = [...prev]
        const idx = updated.map((m, i) => ({ m, i })).reverse().find(x => x.m.role === 'assistant')?.i
        if (idx != null) updated[idx] = { ...updated[idx], content: txt }
        return updated
      })
    }))

    unsubs.push(ws.on('transcript', (d) => {
      setMessages(prev => [...prev, { role: 'user', content: d.content as string, meta: 'transcrit' }])
    }))

    unsubs.push(ws.on('action_result', (d) => {
      const result = d.result as Record<string, unknown> | undefined
      const payload = (d.action_payload ?? d.action) as Record<string, unknown> | undefined
      const needsConfirm = result?.needs_confirmation === true
      setMessages(prev => {
        const updated = [...prev]
        const idx = updated.map((m, i) => ({ m, i })).reverse().find(x => x.m.role === 'assistant')?.i
        if (idx != null) {
          updated[idx] = {
            ...updated[idx],
            actionType: d.action as string,
            actionResult: result,
            pendingAction: needsConfirm && payload ? payload : undefined,
          }
        }
        return updated
      })
    }))

    unsubs.push(ws.on('action_pending', (d) => {
      const action = d.action as Record<string, unknown> | undefined
      setMessages(prev => {
        const updated = [...prev]
        const idx = updated.map((m, i) => ({ m, i })).reverse().find(x => x.m.role === 'assistant')?.i
        if (idx != null && action) {
          updated[idx] = {
            ...updated[idx],
            actionType: (d.action_type as string) || (action.type as string),
            pendingAction: action,
            actionResult: { ok: true, deferred: true },
          }
        }
        return updated
      })
    }))

    unsubs.push(ws.on('saved_file', (d) => {
      setMessages(prev => {
        const updated = [...prev]
        const idx = updated.map((m, i) => ({ m, i })).reverse().find(x => x.m.role === 'assistant')?.i
        if (idx != null) updated[idx] = { ...updated[idx], savedFile: d.path as string }
        return updated
      })
    }))

    unsubs.push(ws.on('error', (d) => {
      const msg = (d.message ?? d.content) as string | undefined
      setMessages(prev => [...prev, { role: 'system', content: msg || 'Erreur', isError: true }])
      setIsStreaming(false)
    }))

    unsubs.push(ws.on('status', (d) => {
      setMessages(prev => [...prev, { role: 'system', content: d.content as string }])
    }))

    unsubs.push(ws.on('welcome', (d) => {
      setMessages(prev => [...prev, { role: 'assistant', content: d.content as string }])
    }))

    const unsubBinary = ws.onBinary((blob) => {
      const url = URL.createObjectURL(blob)
      const audio = new Audio(url)
      audio.onended = () => { URL.revokeObjectURL(url); ws.send({ type: 'done_playing' }) }
      audio.play().catch(() => {})
    })

    return () => { unsubs.forEach(u => u()); unsubBinary() }
  }, [loadConversations])

  // ── Switch conversation ─────────────────────────────────

  const switchConversation = useCallback(async (convId: number) => {
    try {
      const conv = await api.getConversation(convId)
      setActiveConvId(convId)
      setMessages((conv.messages || []).map(m => ({ role: m.role, content: m.content, agent: m.agent })))
      setConvDocs(conv.documents || [])
      setAttachedDocs([])
      userScrolledUp.current = false
      ws.send({ type: 'switch_conversation', conversation_id: convId })
      setMobileSidebarOpen(false)
    } catch (e) {
      console.error('[ChatView] switchConversation', e)
    }
  }, [])

  const newConversation = useCallback(() => {
    ws.send({ type: 'new_conversation' })
    setMessages([])
    setAttachedDocs([])
    setConvDocs([])
    userScrolledUp.current = false
    setMobileSidebarOpen(false)
  }, [])

  // ── Send message ────────────────────────────────────────

  const sendMessage = useCallback(async () => {
    if (!input.trim() && attachedDocs.length === 0) return
    const text = input.trim()
    setInput('')

    if (text.startsWith('/nouveau') || text.startsWith('/new')) { newConversation(); return }
    if (text.startsWith('/cherche ') || text.startsWith('/search ')) {
      const query = text.slice(text.indexOf(' ') + 1)
      setSearchQuery(query); setIsSearching(true)
      try { const data = await api.searchConversations(query); setSearchResults(data.results || []) } catch {}
      setIsSearching(false); return
    }
    if (text.startsWith('/briefing')) {
      try {
        const r = await api.getBriefing('morning') as { content?: string }
        setMessages(prev => [...prev, { role: 'assistant', content: r.content || 'Briefing indisponible' }])
      } catch { setMessages(prev => [...prev, { role: 'system', content: 'Erreur briefing', isError: true }]) }
      return
    }
    if (text.startsWith('/tache ') || text.startsWith('/task ')) {
      const title = text.slice(text.indexOf(' ') + 1)
      try { await api.createTask({ title, priority: 'medium' }); setMessages(prev => [...prev, { role: 'system', content: `Tache creee : ${title}` }]) } catch {}
      return
    }

    setMessages(prev => [...prev, { role: 'user', content: text || '(document)' }])
    setMessages(prev => [...prev, { role: 'assistant', content: '' }])
    setIsStreaming(true)
    setStreamingContent('')
    ws.sendText(text || 'Analyse les documents attaches.', true)
  }, [input, attachedDocs, newConversation])

  // ── Textarea ────────────────────────────────────────────

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage() }
  }

  const autoResize = () => {
    const ta = textareaRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = Math.min(ta.scrollHeight, 200) + 'px'
  }

  // ── Files ───────────────────────────────────────────────

  const handleDragOver = (e: React.DragEvent) => { e.preventDefault(); setIsDragging(true) }
  const handleDragLeave = () => setIsDragging(false)

  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault(); setIsDragging(false)
    if (!activeConvId) { newConversation(); return }
    await uploadFiles(e.dataTransfer.files)
  }

  const uploadFiles = async (files: FileList) => {
    if (!activeConvId) return
    setUploadingDoc(true)
    for (const file of Array.from(files)) {
      try {
        const result = await api.uploadToConversation(activeConvId, file) as { doc_id?: number; summary?: string; content_length?: number; file_type?: string }
        setAttachedDocs(prev => [...prev, { name: file.name, doc_id: result.doc_id, summary: result.summary, content_length: result.content_length, file_type: result.file_type }])
        setMessages(prev => [...prev, { role: 'system', content: `Document analyse : ${file.name}${result.summary ? ' — ' + result.summary : ''}` }])
      } catch { setMessages(prev => [...prev, { role: 'system', content: `Erreur upload : ${file.name}`, isError: true }]) }
    }
    setUploadingDoc(false)
  }

  // ── Rename ──────────────────────────────────────────────

  const startRename = (conv: ConversationSummary) => { setRenamingId(conv.id); setRenameDraft(conv.title || ''); setContextMenuId(null) }
  const commitRename = async (convId: number) => {
    if (renameDraft.trim()) { await api.updateConversation(convId, { title: renameDraft.trim() }).catch(() => {}); setConversations(prev => prev.map(c => c.id === convId ? { ...c, title: renameDraft.trim() } : c)) }
    setRenamingId(null)
  }

  const doSearch = async (q: string) => {
    if (!q.trim()) { setSearchResults([]); return }
    setIsSearching(true)
    try { const data = await api.searchConversations(q); setSearchResults(data.results || []) } catch {}
    setIsSearching(false)
  }

  const handleScrollMessages = () => {
    const el = messagesContainerRef.current
    if (!el) return
    userScrolledUp.current = (el.scrollHeight - el.scrollTop - el.clientHeight) >= 60
  }

  // ── Confirmation d'action (commandes sensibles) ─────────

  const confirmPendingAction = useCallback((action: Record<string, unknown>) => {
    ws.send({ type: 'action_confirm', action })
    setMessages(prev => {
      const updated = [...prev]
      const idx = updated.map((m, i) => ({ m, i })).reverse().find(x => x.m.role === 'assistant')?.i
      if (idx != null) updated[idx] = { ...updated[idx], pendingAction: undefined }
      return updated
    })
  }, [])

  const cancelPendingAction = useCallback(() => {
    setMessages(prev => {
      const updated = [...prev]
      const idx = updated.map((m, i) => ({ m, i })).reverse().find(x => x.m.role === 'assistant')?.i
      if (idx != null) {
        updated[idx] = {
          ...updated[idx],
          pendingAction: undefined,
          actionResult: { ok: false, cancelled: true },
        }
      }
      return updated
    })
  }, [])

  // ── Computed state ──────────────────────────────────────

  const groups = groupConversations(conversations)
  const activeConv = conversations.find(c => c.id === activeConvId)
  const isNewConv = messages.length === 0

  // ── Sidebar content ─────────────────────────────────────

  const sidebarJsx = (
    <aside className="w-72 max-w-[88vw] shrink-0 border-r border-white/10 hidden md:flex flex-col bg-black/95 md:bg-black/20 backdrop-blur-sm h-full overflow-hidden">
      {/* New conv button */}
      <div className="p-3 border-b border-white/10">
        <button
          onClick={newConversation}
          className="w-full py-2.5 px-4 rounded-xl text-sm font-medium border border-white/20 bg-white/5 hover:bg-white/10 text-white transition-all text-left flex items-center gap-2"
        >
          <Plus size={16} />
          Nouvelle conversation
        </button>
      </div>

      {/* Search */}
      <div className="px-3 py-2 border-b border-white/10">
        <div className="relative">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-white/25" />
          <input
            type="text"
            value={searchQuery}
            onChange={e => { setSearchQuery(e.target.value); doSearch(e.target.value) }}
            placeholder="Rechercher..."
            className="w-full bg-white/5 border border-white/10 rounded-lg pl-8 pr-3 py-1.5 text-sm text-white placeholder-white/25 focus:outline-none focus:border-white/30"
          />
        </div>
      </div>

      {/* Search results */}
      {searchQuery && searchResults.length > 0 && (
        <div className="flex-1 overflow-y-auto p-2 space-y-1">
          <p className="text-xs text-white/30 px-2 mb-1">Resultats ({searchResults.length})</p>
          {searchResults.map(r => (
            <button key={`${r.id}-${r.match_date}`} onClick={() => { switchConversation(r.id); setSearchQuery(''); setSearchResults([]) }}
              className="w-full text-left px-3 py-2 rounded-lg hover:bg-white/5 transition-colors">
              <div className="text-sm text-white truncate">{r.title || 'Sans titre'}</div>
              <div className="text-xs text-white/30 truncate mt-0.5">{r.matching_message?.slice(0, 60)}</div>
            </button>
          ))}
        </div>
      )}

      {/* Conversation list */}
      {!searchQuery && (
        <div className="flex-1 overflow-y-auto p-2 space-y-0.5">
          {isSearching && <p className="text-xs text-white/30 text-center py-4">Recherche...</p>}
          {[['Epinglees', groups.pinned], ["Aujourd'hui", groups.today], ['Hier', groups.yesterday], ['7 derniers jours', groups.week], ['Plus anciennes', groups.older]]
            .filter(([, convs]) => (convs as ConversationSummary[]).length > 0)
            .map(([label, convs]) => (
              <ConvGroup key={String(label)} label={label as string} convs={convs as ConversationSummary[]}
                activeId={activeConvId} onSelect={switchConversation} onContextMenu={setContextMenuId}
                contextMenuId={contextMenuId} renamingId={renamingId} renameDraft={renameDraft}
                setRenameDraft={setRenameDraft} onStartRename={startRename} onCommitRename={commitRename}
                onPin={async (id) => { await api.pinConversation(id); loadConversations() }}
                onArchive={async (id) => { await api.archiveConversation(id); loadConversations() }}
                onDelete={async (id) => { await api.deleteConversation(id); loadConversations(); if (activeConvId === id) newConversation() }}
              />
            ))}
          {conversations.length === 0 && <p className="text-xs text-white/30 text-center py-8">Aucune conversation</p>}
        </div>
      )}
    </aside>
  )

  // ═══════════════════════════════════════════════════════════
  // RENDER
  // ═══════════════════════════════════════════════════════════

  return (
    <div className="flex h-full min-h-0 relative" onDragOver={handleDragOver} onDragLeave={handleDragLeave} onDrop={handleDrop}>
      {sidebarJsx}

      {mobileSidebarOpen && (
        <div className="fixed inset-0 z-50 flex md:hidden" role="dialog" aria-label="Historique des conversations">
          <button type="button" aria-label="Fermer l'historique" className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={() => setMobileSidebarOpen(false)} />
          <div className="relative h-full [&>aside]:!flex">{sidebarJsx}</div>
        </div>
      )}

      {/* Chat area */}
      <div className="flex-1 flex flex-col min-h-0 min-w-0">
        {/* Header */}
        <div className="flex items-center gap-2 px-3 py-2.5 border-b border-white/10 bg-black/10 shrink-0">
          <button type="button" aria-label="Ouvrir l'historique" onClick={() => setMobileSidebarOpen(true)} className="md:hidden shrink-0 w-9 h-9 rounded-xl border border-white/15 bg-white/5 flex items-center justify-center text-white/70 active:bg-white/15">
            <Menu size={17} />
          </button>
          <div className="flex-1 min-w-0 flex items-center gap-2">
            {activeConv ? (
              <ConvTitleEditor conv={activeConv} onSave={async (title) => {
                await api.updateConversation(activeConv.id, { title })
                setConversations(prev => prev.map(c => c.id === activeConv.id ? { ...c, title } : c))
              }} />
            ) : (
              <span className="text-sm text-white/40 font-mono truncate">JARVIS — Chat</span>
            )}
          </div>
          {activeConv && (
            <div className="flex items-center gap-2 text-xs text-white/30 shrink-0">
              <span className="font-mono hidden sm:inline">{activeConv.message_count || 0} msg</span>
              <button title={activeConv.pinned ? 'Desepingler' : 'Epingler'}
                onClick={async () => { await api.pinConversation(activeConv.id); loadConversations() }}
                className={`px-2 py-1 rounded hover:bg-white/10 transition-colors ${activeConv.pinned ? 'text-white' : 'text-white/30'}`}>
                ★
              </button>
            </div>
          )}
          <button type="button" aria-label="Nouvelle conversation" onClick={newConversation} className="md:hidden shrink-0 w-9 h-9 rounded-xl border border-white/15 bg-white/5 flex items-center justify-center text-white/70 active:bg-white/15">
            <Plus size={17} />
          </button>
        </div>

        {/* Messages */}
        <div ref={messagesContainerRef} onScroll={handleScrollMessages}
          className="flex-1 overflow-y-auto min-h-0 px-3 sm:px-4 py-4 space-y-3 sm:space-y-4">
          {isNewConv ? (
            <WelcomeScreen suggestions={SUGGESTIONS} onSelect={(s) => { setInput(s); textareaRef.current?.focus() }} />
          ) : (
            <>
              {messages.map((m, i) => (
                <MessageBubble key={i} message={m} onConfirmAction={confirmPendingAction} onCancelAction={cancelPendingAction} />
              ))}
              {isStreaming && streamingContent && <MessageBubble message={{ role: 'assistant', content: streamingContent }} streaming />}
              {isStreaming && !streamingContent && (
                <div className="flex gap-3">
                  <div className="glass-panel rounded-2xl rounded-tl-sm px-4 py-3">
                    <div className="flex gap-1 items-center h-5">
                      <span className="w-1.5 h-1.5 bg-white/40 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                      <span className="w-1.5 h-1.5 bg-white/40 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                      <span className="w-1.5 h-1.5 bg-white/40 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
                    </div>
                  </div>
                </div>
              )}
              <div ref={messagesEndRef} />
            </>
          )}
        </div>

        {/* Composer */}
        <div className="shrink-0 border-t border-white/10 bg-black/10 px-3 pt-2.5 pb-[max(0.625rem,env(safe-area-inset-bottom))]">
          {isDragging && (
            <div className="mb-2 border-2 border-dashed border-white/30 rounded-xl py-3 text-center text-sm text-white/50">
              Deposer un document ici
            </div>
          )}

          {attachedDocs.length > 0 && (
            <div className="mb-2 flex flex-wrap gap-1.5">
              {attachedDocs.map((doc, i) => (
                <span key={i} className="flex items-center gap-1 bg-white/10 rounded-lg px-2 py-1 text-xs text-white/70">
                  <span className="font-mono text-white/40">{doc.file_type?.toUpperCase() || 'DOC'}</span>
                  <span className="max-w-[100px] truncate">{doc.name}</span>
                  <button onClick={() => setAttachedDocs(prev => prev.filter((_, j) => j !== i))}
                    className="text-white/40 hover:text-white transition-colors ml-0.5">
                    <X size={12} />
                  </button>
                </span>
              ))}
            </div>
          )}

          {convDocs.length > 0 && attachedDocs.length === 0 && (
            <div className="mb-2 flex flex-wrap gap-1">
              {convDocs.map(doc => (
                <span key={doc.id} className="bg-white/5 rounded px-2 py-0.5 text-xs text-white/30 font-mono">{doc.original_name}</span>
              ))}
            </div>
          )}

          <div className="flex items-end gap-2">
            <button onClick={() => fileInputRef.current?.click()} disabled={uploadingDoc}
              title="Joindre un document"
              className="shrink-0 w-9 h-9 rounded-xl border border-white/15 bg-white/5 hover:bg-white/10 flex items-center justify-center text-white/40 hover:text-white/70 transition-all disabled:opacity-40">
              {uploadingDoc ? <span className="text-xs animate-spin">↻</span> : <Paperclip size={16} />}
            </button>
            <input ref={fileInputRef} type="file" className="hidden"
              accept=".pdf,.txt,.md,.csv,.json,.py,.js,.ts,.html,.css"
              onChange={e => e.target.files && uploadFiles(e.target.files)} />

            <div className="flex-1 relative">
              <textarea ref={textareaRef} value={input}
                onChange={e => { setInput(e.target.value); autoResize() }}
                onKeyDown={handleKeyDown}
                placeholder={attachedDocs.length > 0 ? 'Analyse ce document...' : 'Message a JARVIS...'}
                rows={1}
                className="w-full bg-white/5 border border-white/15 rounded-xl px-3.5 py-2.5 pr-10 text-sm text-white placeholder-white/25 focus:outline-none focus:border-white/30 resize-none"
                style={{ minHeight: '42px', maxHeight: '200px' }} />
            </div>

            <button onClick={sendMessage}
              disabled={isStreaming || (!input.trim() && attachedDocs.length === 0)}
              className="shrink-0 w-9 h-9 rounded-xl bg-white text-black flex items-center justify-center font-bold text-sm hover:bg-white/90 transition-all disabled:opacity-30 disabled:cursor-not-allowed">
              <Send size={16} />
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Sous-composants ─────────────────────────────────────────

interface ConvGroupProps {
  label: string
  convs: ConversationSummary[]
  activeId: number | null
  onSelect: (id: number) => void
  onContextMenu: (id: number | null) => void
  contextMenuId: number | null
  renamingId: number | null
  renameDraft: string
  setRenameDraft: (v: string) => void
  onStartRename: (c: ConversationSummary) => void
  onCommitRename: (id: number) => void
  onPin: (id: number) => void
  onArchive: (id: number) => void
  onDelete: (id: number) => void
}

function ConvGroup({ label, convs, activeId, onSelect, onContextMenu, contextMenuId,
  renamingId, renameDraft, setRenameDraft, onStartRename, onCommitRename,
  onPin, onArchive, onDelete }: ConvGroupProps) {
  return (
    <div className="mb-2">
      <p className="text-xs text-white/20 px-2 py-1 font-mono uppercase tracking-wider">{label}</p>
      {convs.map(c => (
        <div key={c.id} className="relative group">
          <button onClick={() => onSelect(c.id)}
            className={`w-full text-left px-3 py-2 rounded-lg transition-all ${activeId === c.id ? 'bg-white/15 border border-white/20 text-white' : 'hover:bg-white/5 text-white/70 border border-transparent'}`}>
            {renamingId === c.id ? (
              <input autoFocus value={renameDraft} onChange={e => setRenameDraft(e.target.value)}
                onBlur={() => onCommitRename(c.id)} onKeyDown={e => e.key === 'Enter' && onCommitRename(c.id)}
                onClick={e => e.stopPropagation()}
                className="w-full bg-transparent border-b border-white/30 text-sm text-white focus:outline-none" />
            ) : (
              <>
                <div className="text-sm truncate">{c.title || 'Nouvelle conversation'}</div>
                <div className="flex items-center gap-2 mt-0.5">
                  <span className="text-xs text-white/30 truncate flex-1">{c.last_message?.slice(0, 40) || ''}</span>
                  <span className="text-xs text-white/15 shrink-0">{relativeDate(c.last_message_at || c.started_at)}</span>
                </div>
              </>
            )}
          </button>
          <button onClick={e => { e.stopPropagation(); onContextMenu(contextMenuId === c.id ? null : c.id) }}
            className="absolute right-1 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 w-6 h-6 flex items-center justify-center rounded text-white/40 hover:text-white hover:bg-white/10 transition-all text-xs">
            ···
          </button>
          {contextMenuId === c.id && (
            <div className="absolute right-2 top-8 glass-panel rounded-xl shadow-2xl z-50 py-1 w-40 text-sm" onClick={e => e.stopPropagation()}>
              {[{ label: 'Renommer', action: () => { onStartRename(c); onContextMenu(null) } },
                { label: c.pinned ? 'Desepingler' : 'Epingler', action: () => { onPin(c.id); onContextMenu(null) } },
                { label: 'Archiver', action: () => { onArchive(c.id); onContextMenu(null) } },
                { label: 'Supprimer', action: () => { onDelete(c.id); onContextMenu(null) }, danger: true },
              ].map(item => (
                <button key={item.label} onClick={item.action}
                  className={`w-full text-left px-3 py-1.5 hover:bg-white/5 transition-colors ${(item as { danger?: boolean }).danger ? 'text-red-400' : 'text-white/70'}`}>
                  {item.label}
                </button>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

function ConvTitleEditor({ conv, onSave }: { conv: ConversationSummary; onSave: (t: string) => void }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(conv.title || '')
  useEffect(() => { setDraft(conv.title || '') }, [conv.title])

  if (editing) {
    return <input autoFocus value={draft} onChange={e => setDraft(e.target.value)}
      onBlur={() => { onSave(draft || conv.title || ''); setEditing(false) }}
      onKeyDown={e => { if (e.key === 'Enter') { onSave(draft); setEditing(false) } }}
      className="bg-transparent border-b border-white/30 text-sm text-white focus:outline-none w-full max-w-[180px]" />
  }
  return <button onClick={() => setEditing(true)} className="text-sm text-white/80 hover:text-white truncate font-medium text-left">{conv.title || 'Nouvelle conversation'}</button>
}

function MessageBubble({ message, streaming, onConfirmAction, onCancelAction }: {
  message: UIMessage
  streaming?: boolean
  onConfirmAction?: (action: Record<string, unknown>) => void
  onCancelAction?: () => void
}) {
  const isUser = message.role === 'user'
  const isSystem = message.role === 'system'

  if (isSystem) {
    return <div className={`flex justify-center ${message.isError ? 'text-red-400/70' : 'text-white/30'} text-xs font-mono py-0.5`}>{message.content}</div>
  }

  return (
    <div className={`flex gap-2 sm:gap-3 ${isUser ? 'flex-row-reverse' : 'flex-row'}`}>
      <div className={`max-w-[85%] sm:max-w-[75%] ${isUser ? 'items-end' : 'items-start'} flex flex-col gap-1.5`}>
        <div className={`rounded-2xl px-3 py-2 sm:px-4 sm:py-2.5 text-sm leading-relaxed ${
          isUser ? 'bg-white/15 border border-white/20 text-white rounded-tr-sm' : 'glass-panel border border-white/10 text-white/90 rounded-tl-sm'
        } ${streaming ? 'animate-pulse' : ''}`}>
          <MessageContent content={message.content} />
          {streaming && <span className="inline-block w-0.5 h-4 bg-white/60 ml-0.5 animate-blink" />}
        </div>
        <div className={`flex items-center gap-2 text-xs text-white/20 ${isUser ? 'flex-row-reverse' : ''}`}>
          {message.agent && <span className="font-mono">{message.agent}</span>}
          {message.meta && <span>{message.meta}</span>}
        </div>
        {message.actionType && (
          <ActionBadge
            type={message.actionType}
            result={message.actionResult}
            pendingAction={message.pendingAction}
            onConfirm={(action) => onConfirmAction?.(action)}
            onCancel={() => onCancelAction?.()}
          />
        )}
        {message.savedFile && (
          <div className="bg-white/5 border border-white/10 rounded-lg px-2.5 py-1 text-xs text-white/60">
            Fichier : <span className="font-mono text-white/80">{message.savedFile.split('/').pop()}</span>
          </div>
        )}
      </div>
    </div>
  )
}

function MessageContent({ content }: { content: string }) {
  if (!content) return null
  const lines = content.split('\n')
  return <>{lines.map((line, i) => {
    if (line.startsWith('# ')) return <h3 key={i} className="font-semibold text-white mt-2 mb-0.5">{line.slice(2)}</h3>
    if (line.startsWith('## ')) return <h4 key={i} className="font-medium text-white/90 mt-1.5 mb-0.5">{line.slice(3)}</h4>
    if (line.startsWith('- ') || line.startsWith('• ')) return <div key={i} className="pl-3">· {line.slice(2)}</div>
    if (line === '') return <br key={i} />
    return <span key={i}>{line}<br /></span>
  })}</>
}

function ActionBadge({
  type,
  result,
  pendingAction,
  onConfirm,
  onCancel,
}: {
  type: string
  result?: Record<string, unknown>
  pendingAction?: Record<string, unknown>
  onConfirm?: (action: Record<string, unknown>) => void
  onCancel?: () => void
}) {
  const ok = result?.ok !== false && !result?.cancelled
  const needsConfirm = Boolean(pendingAction) || result?.needs_confirmation === true
  const labels: Record<string, string> = {
    task: `Tache${result?.task_id ? ` #${result.task_id}` : ' creee'}`,
    reminder: 'Rappel cree',
    mail: 'Brouillon pret',
    weather: 'Meteo consultee',
    calendar: 'Agenda consulte',
    calendar_create: 'Evenement cree',
    mood: 'Humeur enregistree',
    note: 'Note enregistree',
    terminal: needsConfirm ? 'Commande en attente' : ok ? 'Commande executee' : 'Erreur commande',
    find_file: `${Array.isArray(result?.files) ? (result.files as unknown[]).length : 0} fichier(s) trouve(s)`,
    search_conversations: `${result?.count || 0} resultat(s) trouve(s)`,
  }
  const cmd = pendingAction?.command as string | undefined
  return (
    <div className="bg-white/5 border border-white/10 rounded-lg px-2.5 py-1.5 text-xs text-white/50 font-mono space-y-1.5">
      <div>{labels[type] || type}</div>
      {needsConfirm && cmd && (
        <div className="text-white/40 break-all">{cmd}</div>
      )}
      {needsConfirm && pendingAction && onConfirm && onCancel && (
        <div className="flex gap-2 pt-0.5">
          <button
            type="button"
            onClick={() => onConfirm(pendingAction)}
            className="px-2 py-0.5 rounded bg-white/15 border border-white/20 text-white/80 hover:bg-white/25 transition-colors"
          >
            Confirmer
          </button>
          <button
            type="button"
            onClick={onCancel}
            className="px-2 py-0.5 rounded border border-white/10 text-white/40 hover:text-white/60 transition-colors"
          >
            Annuler
          </button>
        </div>
      )}
    </div>
  )
}

function WelcomeScreen({ suggestions, onSelect }: { suggestions: string[]; onSelect: (s: string) => void }) {
  return (
    <div className="flex flex-col items-center justify-center h-full py-10 sm:py-16 text-center select-none px-4">
      <div className="w-14 h-14 sm:w-16 sm:h-16 rounded-2xl border border-white/20 bg-white/5 flex items-center justify-center mb-4 sm:mb-6">
        <span className="text-xl sm:text-2xl font-mono font-bold text-white/80">J</span>
      </div>
      <h2 className="text-lg sm:text-xl font-medium text-white/80 mb-1">Comment puis-je vous aider ?</h2>
      <p className="text-sm text-white/30 mb-6 sm:mb-8">JARVIS — Assistant personnel</p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 max-w-sm w-full">
        {suggestions.map(s => (
          <button key={s} onClick={() => onSelect(s)}
            className="glass-panel border border-white/10 rounded-xl px-3 py-2.5 text-sm text-white/60 hover:text-white hover:border-white/25 hover:bg-white/5 transition-all text-left">
            {s}
          </button>
        ))}
      </div>
    </div>
  )
}
