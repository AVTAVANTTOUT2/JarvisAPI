import { useCallback, useEffect, useMemo, useState } from 'react'
import { RefreshCw, ShieldCheck, TerminalSquare, Trash2 } from 'lucide-react'
import { api, type LlmActionLog } from '@unified/lib/api'

function statusCls(status: string) {
  if (status === 'success') return 'text-green-400 border-green-400/30 bg-green-400/10'
  if (status === 'error') return 'text-red-400 border-red-400/30 bg-red-400/10'
  return 'text-white/80 border-white/20 bg-white/10'
}

function toTime(v?: string | null): string {
  if (!v) return '--:--:--'
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return '--:--:--'
  return d.toLocaleTimeString('fr-FR', { hour12: false })
}

function isToday(v?: string | null): boolean {
  if (!v) return false
  const d = new Date(v)
  const now = new Date()
  return (
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate()
  )
}

export function LogsView() {
  const [logs, setLogs] = useState<LlmActionLog[]>([])
  const [loading, setLoading] = useState(true)
  const [clearing, setClearing] = useState(false)
  const [typeFilter, setTypeFilter] = useState('')
  const [limit, setLimit] = useState(200)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.getLogs({ limit, type: typeFilter || undefined })
      setLogs(res.logs || [])
    } finally {
      setLoading(false)
    }
  }, [limit, typeFilter])

  useEffect(() => {
    void load()
  }, [load])

  const clearLogs = useCallback(async () => {
    if (!window.confirm('Effacer définitivement tous les journaux d’actions ?')) return
    setClearing(true)
    try {
      await api.clearLogs()
      setLogs([])
    } finally {
      setClearing(false)
    }
  }, [])

  const actionTypes = useMemo(() => {
    const s = new Set<string>()
    for (const l of logs) if (l.action_type) s.add(l.action_type)
    return Array.from(s).sort()
  }, [logs])

  const todayCount = useMemo(() => logs.filter((l) => isToday(l.created_at)).length, [logs])
  const errorCount = useMemo(() => logs.filter((l) => l.status === 'error').length, [logs])

  return (
    <div className="p-6 space-y-4 bg-grid-pattern min-h-full">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl tracking-wide">SYSTEM LOGS</h1>
          <p className="text-sm text-muted-foreground font-mono">Métadonnées de diagnostic protégées</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="glass-panel rounded-xl px-3 py-2 border border-white/10">
            <p className="text-xs text-muted-foreground font-mono">AUJOURD'HUI</p>
            <p className="text-lg font-mono">{todayCount}</p>
          </div>
          <div className="glass-panel rounded-xl px-3 py-2 border border-white/10">
            <p className="text-xs text-muted-foreground font-mono">ERREURS</p>
            <p className="text-lg font-mono text-red-400">{errorCount}</p>
          </div>
        </div>
      </div>

      <div className="glass-panel rounded-2xl border border-emerald-400/20 bg-emerald-400/5 p-3 flex items-start gap-3">
        <ShieldCheck size={18} className="mt-0.5 shrink-0 text-emerald-400" />
        <div>
          <p className="text-sm font-mono text-emerald-300">CONFIDENTIALITÉ ACTIVE</p>
          <p className="text-xs text-muted-foreground">
            Contenus, presse-papiers, commandes, jetons, PII et chemins locaux sont masqués avant stockage.
            Conservation automatique&nbsp;: 7 jours par défaut, 30 jours maximum.
          </p>
        </div>
      </div>

      <div className="glass-panel rounded-2xl border border-white/10 p-3 flex flex-wrap items-center gap-3">
        <label className="text-xs font-mono text-muted-foreground">TYPE</label>
        <select
          className="bg-black/40 border border-white/10 rounded-lg px-2 py-1 text-sm font-mono"
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
        >
          <option value="">Tous</option>
          {actionTypes.map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
        <label className="text-xs font-mono text-muted-foreground">LIMITE</label>
        <select
          className="bg-black/40 border border-white/10 rounded-lg px-2 py-1 text-sm font-mono"
          value={limit}
          onChange={(e) => setLimit(Number(e.target.value))}
        >
          <option value={100}>100</option>
          <option value={200}>200</option>
          <option value={500}>500</option>
        </select>
        <button
          className="ml-auto inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border border-red-400/30 text-red-300 hover:bg-red-400/10 text-sm font-mono disabled:opacity-50"
          onClick={() => void clearLogs()}
          disabled={clearing}
        >
          <Trash2 size={14} />
          {clearing ? 'Effacement…' : 'Tout effacer'}
        </button>
        <button
          className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border border-white/15 hover:bg-white/5 text-sm font-mono"
          onClick={() => void load()}
          disabled={loading}
        >
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          Rafraîchir
        </button>
      </div>

      <div className="glass-panel rounded-2xl border border-white/10 overflow-hidden">
        <div className="px-4 py-3 border-b border-white/10 text-xs font-mono text-muted-foreground flex items-center gap-2">
          <TerminalSquare size={14} />
          LOG STREAM ({logs.length})
        </div>

        <div className="max-h-[70vh] overflow-auto">
          {logs.length === 0 && (
            <div className="p-6 text-sm text-muted-foreground font-mono">Aucun log trouvé.</div>
          )}
          {logs.map((log) => {
            const payload = log.payload || '{}'
            const prettyPayload = (() => {
              try {
                return JSON.stringify(JSON.parse(payload), null, 2)
              } catch {
                return payload
              }
            })()
            return (
              <details key={log.id} className="border-b border-white/5 px-4 py-3 group">
                <summary className="list-none cursor-pointer">
                  <div className="grid grid-cols-[90px_130px_120px_1fr_90px] items-center gap-2 text-sm">
                    <span className="font-mono text-white/80">{toTime(log.created_at)}</span>
                    <span className="font-mono truncate">{log.agent || 'unknown'}</span>
                    <span className={`inline-flex items-center justify-center rounded-full border px-2 py-0.5 text-xs font-mono ${statusCls(log.status)}`}>
                      {log.status}
                    </span>
                    <span className="font-mono text-white truncate">{log.action_type || 'unknown'}</span>
                    <span className="font-mono text-right text-xs text-muted-foreground">
                      {log.execution_time_ms != null ? `${log.execution_time_ms}ms` : '—'}
                    </span>
                  </div>
                </summary>
                <div className="mt-2 ml-[90px]">
                  <pre className="text-xs font-mono bg-black/40 border border-white/10 rounded-lg p-3 overflow-auto whitespace-pre-wrap break-all">
                    {prettyPayload}
                  </pre>
                </div>
              </details>
            )
          })}
        </div>
      </div>
    </div>
  )
}
