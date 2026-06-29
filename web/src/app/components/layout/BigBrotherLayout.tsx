import { useEffect } from 'react';
import { NavLink, Outlet } from 'react-router-dom';
import { ws } from '@/services/websocket';
import { Activity, CalendarDays, MessageSquare, Mic, Settings2, TerminalSquare } from 'lucide-react';

function navCls({ isActive }: { isActive: boolean }) {
  return `block w-full text-left px-3 py-2.5 rounded-xl text-sm transition-colors border border-transparent ${
    isActive ? 'bg-white text-black' : 'hover:bg-white/5 text-muted-foreground'
  }`;
}

function topNavCls({ isActive }: { isActive: boolean }) {
  return `inline-flex items-center gap-1.5 shrink-0 rounded-lg px-3 py-1.5 text-xs font-mono border transition-colors ${
    isActive
      ? 'bg-white text-black border-white'
      : 'border-white/15 text-muted-foreground hover:border-white/30 hover:text-foreground'
  }`;
}

export function BigBrotherLayout() {
  useEffect(() => {
    ws.connect();
    return () => ws.disconnect();
  }, []);

  return (
    <div className="flex h-[100dvh] min-h-0 bg-grid-pattern">
      <aside className="w-72 shrink-0 border-r border-border glass-panel flex flex-col overflow-hidden">
        <div className="p-4 border-b border-white/10">
          <p className="text-xs font-mono text-muted-foreground">JARVIS</p>
        </div>
        <nav className="p-3 space-y-1 flex-1 overflow-y-auto">
          <p className="px-3 pb-2 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
            Conversation
          </p>
          <NavLink to="/chat" className={navCls}>
            <span className="inline-flex items-center gap-2">
              <MessageSquare size={14} />
              Chat
            </span>
          </NavLink>
          <NavLink to="/voice" className={navCls}>
            <span className="inline-flex items-center gap-2">
              <Mic size={14} />
              Voix
            </span>
          </NavLink>
          <NavLink to="/dashboard" className={navCls}>
            Dashboard
          </NavLink>
          <NavLink to="/contacts" className={navCls}>
            Contacts
          </NavLink>
          <NavLink to="/calendar" className={navCls}>
            <span className="inline-flex items-center gap-2">
              <CalendarDays size={14} />
              Agenda
            </span>
          </NavLink>
          <NavLink to="/map" className={navCls}>
            Cartographie
          </NavLink>
          <NavLink to="/documents" className={navCls}>
            Documents
          </NavLink>
          <NavLink to="/analytics" className={navCls}>
            Statistiques
          </NavLink>
          <NavLink to="/search" className={navCls}>
            Recherche
          </NavLink>
          <NavLink to="/data" className={navCls}>
            Données
          </NavLink>
          <NavLink to="/logs" className={navCls}>
            <span className="inline-flex items-center gap-2">
              <TerminalSquare size={14} />
              Logs Système
            </span>
          </NavLink>
          <NavLink to="/monitoring" className={navCls}>
            <span className="inline-flex items-center gap-2">
              <Activity size={14} />
              Monitoring
            </span>
          </NavLink>
          <NavLink to="/control" className={navCls}>
            <span className="inline-flex items-center gap-2">
              <Settings2 size={14} />
              Control
            </span>
          </NavLink>
        </nav>
      </aside>
      <main className="flex-1 min-h-0 min-w-0 flex flex-col">
        <header
          className="shrink-0 z-10 border-b border-white/10 bg-black/40 backdrop-blur-md px-3 py-2"
          aria-label="Navigation rapide"
        >
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground mr-1 hidden sm:inline">
              Accès
            </span>
            <NavLink to="/chat" className={topNavCls}>
              <MessageSquare size={14} />
              Chat
            </NavLink>
            <NavLink to="/voice" className={topNavCls}>
              <Mic size={14} />
              Voix
            </NavLink>
            <NavLink to="/calendar" className={topNavCls}>
              <CalendarDays size={14} />
              Agenda
            </NavLink>
            <NavLink to="/dashboard" className={topNavCls}>
              Dashboard
            </NavLink>
            <NavLink to="/contacts" className={topNavCls}>
              Contacts
            </NavLink>
          </div>
        </header>
        <div className="flex-1 min-h-0 overflow-y-auto">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
