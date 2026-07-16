/**
 * ControlView — gestion hierarchique de tous les services JARVIS.
 *
 * Niveau 1 — Processus principaux (supervisor, port 9000) :
 *   Backend JARVIS, TV Dashboard, Ollama, Vite Dev
 *   Controle via /api/supervisor/{id}/start|stop|restart
 *   Etat temps reel via WebSocket /ws/supervisor
 *
 * Niveau 2 — Sous-services (backend, port 8081) :
 *   Audio Daemon, Email Watcher, JARVIS Daemon, Screen Watcher,
 *   iMessage Bridge, Scheduler, Relationship Analyzer
 *   Visible uniquement si le backend est actif
 *   Controle via /api/supervisor/sub/{id}/{action}
 *
 * Design system BIG BROTHER.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Activity, Brain, Clock, Mic, Mail, MessageSquare,
  Monitor, RefreshCw, Search, Settings2, Terminal,
  Tv, Play, Square, RotateCw,
  TerminalSquare, Server, Cpu,
} from 'lucide-react'
import {
  api,
  type ServiceInfo,
  type SupervisorService,
  supervisorWsUrl,
} from '@unified/lib/api'

// ── Constantes ────────────────────────────────────────────────

const POLL_FALLBACK_MS = 5000 // fallback polling si pas de WebSocket
const WS_RECONNECT_MS = 3000

const TOP_CATEGORY_ORDER = ['core', 'external', 'dev'] as const
const TOP_CATEGORY_LABELS: Record<string, string> = {
  core: 'CORE',
  external: 'EXTERNE',
  dev: 'DEV',
}

const SUB_CATEGORY_ORDER = ['audio', 'core', 'integrations', 'monitoring', 'analysis'] as const
const SUB_CATEGORY_LABELS: Record<string, string> = {
  audio: 'AUDIO',
  core: 'CORE',
  integrations: 'INTEGRATIONS',
  monitoring: 'MONITORING',
  analysis: 'ANALYSE',
}

const SERVICE_ICONS: Record<string, React.ComponentType<{ size?: number; className?: string }>> = {
  backend: Server,
  tv_dashboard: Tv,
  ollama: Cpu,
  vite_dev: Terminal,
  audio_daemon: Mic,
  email_watcher: Mail,
  jarvis_daemon: Brain,
  screen_watcher: Monitor,
  imessage_bridge: MessageSquare,
  scheduler: Clock,
  relationship_analyzer: Search,
}

type GlobalAction = 'start-all' | 'stop-all' | 'restart-all' | null

// ── Composant principal ───────────────────────────────────────

export default function ControlView() {
  // Top-level services (from supervisor)
  const [topServices, setTopServices] = useState<SupervisorService[]>([])
  const [supervisorInfo, setSupervisorInfo] = useState<{ pid: number; uptime_s: number } | null>(null)

  // Sub-services (from backend, only shown when backend is running)
  const [subServices, setSubServices] = useState<ServiceInfo[]>([])

  // Loading & errors
  const [loading, setLoading] = useState(true)
  const [actionLoading, setActionLoading] = useState<Record<string, boolean>>({})
  const [globalLoading, setGlobalLoading] = useState<GlobalAction>(null)
  const [error, setError] = useState<string | null>(null)

  // Logs panel
  const [logsPanel, setLogsPanel] = useState<{
    serviceId: string
    lines: string[]
    loading: boolean
    source: 'supervisor' | 'sub'
  } | null>(null)

  const mountedRef = useRef(true)
  const wsRef = useRef<WebSocket | null>(null)
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // ── Fetch functions ──────────────────────────────────────

  const fetchTopServices = useCallback(async () => {
    try {
      const data = await api.getSupervisorStatus()
      if (!mountedRef.current) return
      setTopServices(data.services)
      setSupervisorInfo(data.supervisor)
      setError(null)
    } catch {
      // Supervisor indisponible — fallback silencieux
      if (mountedRef.current) {
        setError("Superviseur inaccessible — demarrez-le : ./scripts/launch_supervisor.sh")
      }
    }
  }, [])

  const fetchSubServices = useCallback(async () => {
    try {
      const data = await api.getSubServices()
      if (!mountedRef.current) return
      if (data.available && data.services) {
        setSubServices(data.services)
      }
    } catch {
      // ignore — l'interface refletera « backend arrete » via topServices
    }
  }, [])

  const fetchAll = useCallback(async () => {
    await Promise.all([fetchTopServices(), fetchSubServices()])
    if (mountedRef.current) setLoading(false)
  }, [fetchTopServices, fetchSubServices])

  // ── WebSocket ────────────────────────────────────────────

  const connectWs = useCallback(() => {
    if (!mountedRef.current) return

    try {
      const url = supervisorWsUrl()
      const ws = new WebSocket(url)
      wsRef.current = ws

      ws.onopen = () => {
        setError(null)
      }

      ws.onmessage = (event) => {
        if (!mountedRef.current) return
        try {
          const msg = JSON.parse(event.data) as {
            type: string
            services?: SupervisorService[]
            supervisor_pid?: number
          }
          if (msg.type === 'initial_state' || msg.type === 'status_update') {
            if (msg.services) {
              setTopServices(msg.services)
            }
            if (msg.supervisor_pid) {
              // Mise à jour fonctionnelle : ne PAS dépendre de supervisorInfo,
              // sinon connectWs change d'identité à chaque fetch et l'effet
              // de montage recrée le WebSocket en boucle (tempête de connexions).
              setSupervisorInfo((prev) =>
                prev ? { ...prev, pid: msg.supervisor_pid! } : prev,
              )
            }
            // Also refresh sub-services when backend is part of an update
            fetchSubServices()
          }
        } catch {
          // Ignore malformed messages
        }
      }

      ws.onclose = () => {
        // Ne reconnecter que si CE socket est toujours le socket courant —
        // un socket remplacé ne doit pas déclencher de reconnexion parallèle.
        if (mountedRef.current && wsRef.current === ws) {
          setTimeout(connectWs, WS_RECONNECT_MS)
        }
      }

      ws.onerror = () => {
        ws.close()
      }
    } catch {
      // WebSocket non supporte
    }
  }, [fetchSubServices])

  // ── Lifecycle ────────────────────────────────────────────

  useEffect(() => {
    mountedRef.current = true
    void fetchAll()
    connectWs()

    // Fallback polling si le WS ne donne pas de donnees
    pollTimerRef.current = setInterval(() => {
      if (mountedRef.current && topServices.length === 0) {
        void fetchAll()
      }
    }, POLL_FALLBACK_MS)

    return () => {
      mountedRef.current = false
      wsRef.current?.close()
      if (pollTimerRef.current) clearInterval(pollTimerRef.current)
    }
  }, [fetchAll, connectWs])

  // ── Action handlers ──────────────────────────────────────

  const handleTopAction = useCallback(
    async (serviceId: string, action: 'start' | 'stop' | 'restart') => {
      if (serviceId === 'ollama' && action === 'stop') {
        const ok = window.confirm(
          'Arrêter Ollama arrêtera également Screen Watcher.',
        )
        if (!ok) return
      }
      setActionLoading((prev) => ({ ...prev, [serviceId]: true }))
      try {
        if (action === 'start') await api.supervisorStart(serviceId)
        else if (action === 'stop') await api.supervisorStop(serviceId)
        else await api.supervisorRestart(serviceId)
        await fetchAll()
      } catch (e) {
        console.error(`[ControlView] Echec ${action} ${serviceId}`, e)
      } finally {
        setActionLoading((prev) => ({ ...prev, [serviceId]: false }))
      }
    },
    [fetchAll],
  )

  const handleSubAction = useCallback(
    async (serviceId: string, action: 'start' | 'stop' | 'restart') => {
      setActionLoading((prev) => ({ ...prev, [serviceId]: true }))
      try {
        const result = await api.subServiceAction(serviceId, action)
        if (
          serviceId === 'screen_watcher' &&
          action === 'start' &&
          result &&
          result.ok === false
        ) {
          const msg = result.error || result.message || 'Echec demarrage'
          if (/ollama/i.test(msg)) {
            const startOllama = window.confirm(
              `${msg}\n\nDémarrer Ollama maintenant ?`,
            )
            if (startOllama) {
              await api.supervisorStart('ollama')
              await fetchAll()
              setActionLoading((prev) => ({ ...prev, [serviceId]: false }))
              return
            }
          } else {
            window.alert(msg)
          }
        }
        await fetchSubServices()
      } catch (e) {
        console.error(`[ControlView] Echec ${action} ${serviceId}`, e)
      } finally {
        setActionLoading((prev) => ({ ...prev, [serviceId]: false }))
      }
    },
    [fetchAll, fetchSubServices],
  )

  const handleGlobal = useCallback(
    async (action: NonNullable<GlobalAction>) => {
      setGlobalLoading(action)
      try {
        if (action === 'start-all') await api.supervisorStartAll()
        else if (action === 'stop-all') await api.supervisorStopAll()
        else await api.supervisorRestartAll()
        await fetchAll()
      } catch (e) {
        console.error(`[ControlView] Echec ${action}`, e)
      } finally {
        setGlobalLoading(null)
      }
    },
    [fetchAll],
  )

  const handleToggleLogs = useCallback(
    async (serviceId: string, source: 'supervisor' | 'sub') => {
      if (logsPanel && logsPanel.serviceId === serviceId) {
        setLogsPanel(null)
        return
      }
      setLogsPanel({ serviceId, lines: [], loading: true, source })
      try {
        let data: { logs: string[]; message?: string; error?: string }
        if (source === 'supervisor') {
          data = await api.supervisorLogs(serviceId, 50)
        } else {
          data = await api.getServiceLogs(serviceId, 50)
        }
        setLogsPanel({
          serviceId,
          lines: data.logs || [],
          loading: false,
          source,
        })
      } catch {
        setLogsPanel({
          serviceId,
          lines: ['Erreur de chargement des logs'],
          loading: false,
          source,
        })
      }
    },
    [logsPanel],
  )

  // ── Computed ─────────────────────────────────────────────

  const topGrouped = useMemo(() => {
    const result: { category: string; services: SupervisorService[] }[] = []
    for (const cat of TOP_CATEGORY_ORDER) {
      const items = topServices.filter((s) => s.category === cat)
      if (items.length > 0) result.push({ category: cat, services: items })
    }
    return result
  }, [topServices])

  const subGrouped = useMemo(() => {
    // Ollama / TV sont pilotés au niveau supervisor — éviter le doublon
    const filtered = subServices.filter(
      (s) => s.id !== 'ollama' && s.id !== 'tv_dashboard' && s.id !== 'vite_dev',
    )
    const result: { category: string; services: ServiceInfo[] }[] = []
    for (const cat of SUB_CATEGORY_ORDER) {
      const items = filtered.filter((s) => s.category === cat)
      if (items.length > 0) result.push({ category: cat, services: items })
    }
    return result
  }, [subServices])

  const topRunning = topServices.filter((s) => s.running).length
  const subRunning = subServices.filter((s) => s.running).length
  const backendRunning = topServices.find((s) => s.id === 'backend')?.running ?? false

  // ── Format helpers ───────────────────────────────────────

  const formatUptime = (seconds: number): string => {
    if (seconds < 60) return `${seconds}s`
    if (seconds < 3600) return `${Math.floor(seconds / 60)}min`
    const h = Math.floor(seconds / 3600)
    const m = Math.floor((seconds % 3600) / 60)
    return `${h}h ${m}min`
  }

  // ── Render ───────────────────────────────────────────────

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <RefreshCw size={24} className="animate-spin text-white/30" />
      </div>
    )
  }

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Header */}
      <div className="shrink-0 px-6 pt-5 pb-3 border-b border-white/[0.06]">
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div>
            <h1 className="text-lg font-semibold tracking-tight">Control Center</h1>
            <div className="flex items-center gap-3 mt-0.5">
              {supervisorInfo && (
                <span className="text-xs text-white/50 font-mono">
                  Supervisor PID {supervisorInfo.pid}
                </span>
              )}
              {supervisorInfo && (
                <span className="text-[10px] text-white/30 font-mono">
                  uptime {formatUptime(supervisorInfo.uptime_s)}
                </span>
              )}
              {error && (
                <span className="text-[10px] text-amber-400/70 font-mono">{error}</span>
              )}
            </div>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-xs text-white/40 font-mono">
              {topRunning}/{topServices.length} process
              {backendRunning && subServices.length > 0 && (
                <span>, {subRunning}/{subServices.length} sous-svcs</span>
              )}
            </span>
            <button
              onClick={fetchAll}
              className="p-2 rounded-lg hover:bg-white/5 transition-colors"
              title="Rafraichir"
            >
              <RefreshCw size={14} className="text-white/40" />
            </button>
          </div>
        </div>

        {/* Actions globales */}
        <div className="flex items-center gap-2 mt-3 flex-wrap">
          <button
            onClick={() => handleGlobal('start-all')}
            disabled={globalLoading !== null}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 border border-emerald-500/20 text-sm font-medium transition-colors disabled:opacity-40 active:scale-[0.97]"
          >
            {globalLoading === 'start-all' ? (
              <RefreshCw size={14} className="animate-spin" />
            ) : (
              <Play size={14} />
            )}
            Tout demarrer
          </button>
          <button
            onClick={() => handleGlobal('stop-all')}
            disabled={globalLoading !== null}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-red-500/10 hover:bg-red-500/20 text-red-400 border border-red-500/20 text-sm font-medium transition-colors disabled:opacity-40 active:scale-[0.97]"
          >
            {globalLoading === 'stop-all' ? (
              <RefreshCw size={14} className="animate-spin" />
            ) : (
              <Square size={14} />
            )}
            Tout arreter
          </button>
          <button
            onClick={() => handleGlobal('restart-all')}
            disabled={globalLoading !== null}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-blue-500/10 hover:bg-blue-500/20 text-blue-400 border border-blue-500/20 text-sm font-medium transition-colors disabled:opacity-40 active:scale-[0.97]"
          >
            {globalLoading === 'restart-all' ? (
              <RefreshCw size={14} className="animate-spin" />
            ) : (
              <RotateCw size={14} />
            )}
            Tout relancer
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-5">
        {/* Niveau 1 — Processus principaux */}
        <section>
          <h2 className="text-[10px] font-mono uppercase tracking-[0.15em] text-white/30 mb-2.5 px-0.5 flex items-center gap-2">
            <Server size={11} />
            Processus principaux
          </h2>
          {topGrouped.map((group, gi) => (
            <div key={group.category} className="mb-3">
              <h3 className="text-[9px] font-mono uppercase tracking-[0.12em] text-white/15 mb-2 px-0.5">
                {TOP_CATEGORY_LABELS[group.category] || group.category}
              </h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
                {group.services.map((svc, si) => (
                  <TopServiceCard
                    key={svc.id}
                    service={svc}
                    actionLoading={actionLoading[svc.id] || false}
                    onStart={() => handleTopAction(svc.id, 'start')}
                    onStop={() => handleTopAction(svc.id, 'stop')}
                    onRestart={() => handleTopAction(svc.id, 'restart')}
                    onToggleLogs={() => handleToggleLogs(svc.id, 'supervisor')}
                    logsOpen={logsPanel?.serviceId === svc.id}
                    animationDelay={gi * 30 + si * 50}
                    subCount={
                      svc.id === 'backend' && svc.running
                        ? (svc.sub_services?.length ?? 0)
                        : undefined
                    }
                  />
                ))}
              </div>
            </div>
          ))}
        </section>

        {/* Niveau 2 — Sous-services (backend) */}
        {backendRunning && subServices.length > 0 && (
          <section>
            <h2 className="text-[10px] font-mono uppercase tracking-[0.15em] text-white/30 mb-2.5 px-0.5 mt-4 flex items-center gap-2">
              <Activity size={11} />
              Sous-services (Backend)
            </h2>
            {subGrouped.map((group, gi) => (
              <div key={group.category} className="mb-3">
                <h3 className="text-[9px] font-mono uppercase tracking-[0.12em] text-white/15 mb-2 px-0.5">
                  {SUB_CATEGORY_LABELS[group.category] || group.category}
                </h3>
                <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
                  {group.services.map((svc, si) => (
                    <SubServiceCard
                      key={svc.id}
                      service={svc}
                      actionLoading={actionLoading[svc.id] || false}
                      onStart={() => handleSubAction(svc.id, 'start')}
                      onStop={() => handleSubAction(svc.id, 'stop')}
                      onRestart={() => handleSubAction(svc.id, 'restart')}
                      onToggleLogs={() => handleToggleLogs(svc.id, 'sub')}
                      logsOpen={logsPanel?.serviceId === svc.id}
                      animationDelay={gi * 25 + si * 40}
                    />
                  ))}
                </div>
              </div>
            ))}
          </section>
        )}

        {/* Backend arrete — message */}
        {!backendRunning && topServices.length > 0 && (
          <section className="mt-4">
            <div className="rounded-xl border border-white/[0.06] bg-white/[0.01] p-6 text-center">
              <Server size={28} className="text-white/15 mx-auto mb-3" />
              <p className="text-sm text-white/30 mb-2">
                Le backend JARVIS est arrete
              </p>
              <p className="text-xs text-white/15 mb-4">
                Les sous-services (Audio Daemon, Email Watcher, etc.) seront visibles
                une fois le backend redemarre.
              </p>
              <button
                onClick={() => handleTopAction('backend', 'start')}
                disabled={actionLoading['backend']}
                className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 border border-emerald-500/20 text-sm font-medium transition-colors disabled:opacity-40 active:scale-[0.97]"
              >
                {actionLoading['backend'] ? (
                  <RefreshCw size={14} className="animate-spin" />
                ) : (
                  <Play size={14} />
                )}
                Demarrer le backend
              </button>
            </div>
          </section>
        )}
      </div>

      {/* Logs panel en bas */}
      {logsPanel && (
        <div className="shrink-0 border-t border-white/[0.06] bg-black/60 backdrop-blur-sm max-h-[30vh] overflow-hidden flex flex-col">
          <div className="flex items-center justify-between px-4 py-2 border-b border-white/[0.06]">
            <div className="flex items-center gap-2">
              <TerminalSquare size={13} className="text-white/40" />
              <span className="text-xs font-medium text-white/70">
                Logs — {logsPanel.serviceId}{' '}
                <span className="text-[10px] text-white/25">
                  ({logsPanel.source === 'supervisor' ? 'superviseur' : 'backend'})
                </span>
              </span>
              {logsPanel.loading && (
                <RefreshCw size={11} className="animate-spin text-white/30" />
              )}
              {!logsPanel.loading && logsPanel.lines.length > 0 && (
                <span className="text-[10px] text-white/30">
                  {logsPanel.lines.length} ligne{logsPanel.lines.length > 1 ? 's' : ''}
                </span>
              )}
            </div>
            <button
              onClick={() => setLogsPanel(null)}
              className="p-1 rounded hover:bg-white/5 transition-colors"
            >
              <Square size={12} className="text-white/30" />
            </button>
          </div>
          <div className="flex-1 overflow-y-auto p-3 font-mono text-[11px] leading-relaxed text-white/50 space-y-0.5">
            {logsPanel.loading ? (
              <span className="text-white/20 italic">Chargement...</span>
            ) : logsPanel.lines.length === 0 ? (
              <span className="text-white/20 italic">Aucune ligne de log</span>
            ) : (
              logsPanel.lines.map((line, i) => (
                <div key={i} className="break-all">
                  <span className="text-white/15 select-none">
                    {String(i + 1).padStart(3, ' ')}{' '}
                  </span>
                  {line}
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  )
}

// ══════════════════════════════════════════════════════════════
// TopServiceCard — niveau 1 (supervisor)
// ══════════════════════════════════════════════════════════════

interface TopServiceCardProps {
  service: SupervisorService
  actionLoading: boolean
  onStart: () => void
  onStop: () => void
  onRestart: () => void
  onToggleLogs: () => void
  logsOpen: boolean
  animationDelay: number
  subCount?: number
}

function TopServiceCard({
  service,
  actionLoading,
  onStart,
  onStop,
  onRestart,
  onToggleLogs,
  logsOpen,
  animationDelay,
  subCount,
}: TopServiceCardProps) {
  const Icon = SERVICE_ICONS[service.id] || Settings2
  const isRunning = service.running

  return (
    <div
      className="glass-panel border border-white/[0.07] rounded-xl p-4 hover:border-white/[0.12] transition-all animate-slide-up"
      style={{ animationDelay: `${animationDelay}ms` }}
    >
      <div className="flex items-start gap-3">
        {/* Icone + point etat */}
        <div className="relative shrink-0 mt-0.5">
          <div className="w-9 h-9 rounded-lg bg-white/[0.04] border border-white/[0.06] flex items-center justify-center">
            <Icon size={18} className="text-white/50" />
          </div>
          <span
            className={`absolute -bottom-0.5 -right-0.5 w-3 h-3 rounded-full border-2 border-[#0a0a0f] ${
              isRunning ? 'bg-emerald-500' : 'bg-white/15'
            }`}
          />
        </div>

        {/* Contenu */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-medium text-white/90 truncate">
              {service.name}
            </span>
            <span
              className={`text-[10px] font-mono px-1.5 py-0.5 rounded-md border ${
                isRunning
                  ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                  : 'bg-white/[0.03] text-white/30 border-white/[0.06]'
              }`}
            >
              {isRunning ? 'ACTIF' : 'INACTIF'}
            </span>
          </div>
          <p className="text-[11px] text-white/40 mt-0.5 leading-snug">
            {service.description}
            {isRunning && (
              <span className="text-white/20"> — port {service.port}</span>
            )}
            {subCount !== undefined && subCount > 0 && (
              <span className="text-emerald-400/60"> — {subCount} sous-services</span>
            )}
          </p>

          {service.id === 'ollama' && (
            <div className="mt-2 space-y-1 text-[10px] font-mono text-white/45">
              <div>
                API:{' '}
                <span className={service.healthy ? 'text-emerald-400' : 'text-amber-400'}>
                  {service.healthy ? 'healthy' : service.status || 'down'}
                </span>
                {service.latency_ms != null && (
                  <span className="text-white/25"> · {service.latency_ms}ms</span>
                )}
              </div>
              <div>
                Vision:{' '}
                {service.vision_model_resolved || service.vision_model || '—'}
                {service.vision_model_available === false && (
                  <span className="text-amber-400"> (absent)</span>
                )}
              </div>
              {service.models && service.models.length > 0 && (
                <div className="text-white/30 truncate">
                  Modeles: {service.models.map((m) => m.name).slice(0, 4).join(', ')}
                  {service.models.length > 4 ? '…' : ''}
                </div>
              )}
              {service.error && (
                <div className="text-amber-400/80 leading-snug">{service.error}</div>
              )}
            </div>
          )}

          {/* Boutons d'action */}
          <div className="flex items-center gap-1.5 mt-3 flex-wrap">
            {service.can_control ? (
              <>
                {!isRunning ? (
                  <ActionButton
                    color="emerald"
                    icon={Play}
                    label="Start"
                    onClick={onStart}
                    loading={actionLoading}
                  />
                ) : (
                  <>
                    <ActionButton
                      color="red"
                      icon={Square}
                      label="Stop"
                      onClick={onStop}
                      loading={actionLoading}
                    />
                    <ActionButton
                      color="blue"
                      icon={RotateCw}
                      label="Restart"
                      onClick={onRestart}
                      loading={actionLoading}
                    />
                  </>
                )}
              </>
            ) : (
              <span className="text-[10px] text-white/20 italic px-2 py-1">
                Controle manuel
              </span>
            )}
            <button
              onClick={onToggleLogs}
              className={`inline-flex items-center gap-1 px-2.5 py-1.5 rounded-md text-[11px] transition-colors active:scale-[0.97] ${
                logsOpen
                  ? 'bg-white/[0.08] text-white/80 border border-white/[0.12]'
                  : 'bg-white/[0.03] hover:bg-white/[0.06] text-white/40 border border-transparent'
              }`}
            >
              <TerminalSquare size={11} />
              Logs
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ══════════════════════════════════════════════════════════════
// SubServiceCard — niveau 2 (backend)
// ══════════════════════════════════════════════════════════════

interface SubServiceCardProps {
  service: ServiceInfo
  actionLoading: boolean
  onStart: () => void
  onStop: () => void
  onRestart: () => void
  onToggleLogs: () => void
  logsOpen: boolean
  animationDelay: number
}

function SubServiceCard({
  service,
  actionLoading,
  onStart,
  onStop,
  onRestart,
  onToggleLogs,
  logsOpen,
  animationDelay,
}: SubServiceCardProps) {
  const Icon = SERVICE_ICONS[service.id] || Settings2
  const isRunning = service.running

  const stateLabel = useMemo(() => {
    const status = service.status || service.state
    if (service.id === 'screen_watcher' && status) {
      return status.toUpperCase()
    }
    if (!isRunning) return 'INACTIF'
    if (status && status !== 'listening' && status !== 'idle') {
      return status.toUpperCase()
    }
    if (service.id === 'audio_daemon' && service.state) {
      const labels: Record<string, string> = {
        wake_listening: 'ECOUTE',
        listening: 'ECOUTE',
        processing: 'TRAITEMENT',
        speaking: 'PARLE',
      }
      return labels[service.state] || service.state.toUpperCase()
    }
    return 'ACTIF'
  }, [isRunning, service.state, service.status, service.id])

  const screenBlocked = service.id === 'screen_watcher' && service.status === 'blocked_ollama'
  const screenCanStart =
    service.id !== 'screen_watcher'
      ? !isRunning
      : !isRunning &&
        !screenBlocked &&
        service.status !== 'starting' &&
        service.status !== 'disabled' &&
        service.status !== 'stopping'

  return (
    <div
      className="glass-panel border border-white/[0.05] rounded-xl p-3.5 hover:border-white/[0.08] transition-all animate-slide-up"
      style={{ animationDelay: `${animationDelay}ms` }}
    >
      <div className="flex items-start gap-2.5">
        {/* Icone + point */}
        <div className="relative shrink-0 mt-0.5">
          <div className="w-8 h-8 rounded-lg bg-white/[0.03] border border-white/[0.05] flex items-center justify-center">
            <Icon size={16} className="text-white/40" />
          </div>
          <span
            className={`absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full border-2 border-[#0a0a0f] ${
              isRunning ? 'bg-emerald-500' : 'bg-white/12'
            }`}
          />
        </div>

        {/* Contenu */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="text-xs font-medium text-white/80 truncate">
              {service.name}
            </span>
            <span
              className={`text-[9px] font-mono px-1 py-0.5 rounded border ${
                isRunning
                  ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/15'
                  : 'bg-white/[0.02] text-white/25 border-white/[0.05]'
              }`}
            >
              {stateLabel}
            </span>
          </div>
          <p className="text-[10px] text-white/35 mt-0.5 leading-snug line-clamp-2">
            {service.description}
          </p>

          {service.id === 'screen_watcher' && (
            <div className="mt-1.5 space-y-0.5 text-[9px] font-mono text-white/40">
              <div>
                Autostart: {service.autostart ? 'oui' : 'non'}
                {service.vision_model ? ` · ${service.vision_model}` : ''}
              </div>
              {service.last_heartbeat && (
                <div className="text-white/30">Heartbeat: {service.last_heartbeat}</div>
              )}
              {service.last_capture_at && (
                <div className="text-white/30">Capture: {service.last_capture_at}</div>
              )}
              {service.last_analysis_at && (
                <div className="text-white/30">Analyse: {service.last_analysis_at}</div>
              )}
              {(service.error_count ?? 0) > 0 && (
                <div className="text-amber-400/80">Erreurs: {service.error_count}</div>
              )}
              {(screenBlocked || service.detail) && (
                <div className="text-amber-400/90">
                  {service.detail || 'Ollama indisponible'}
                </div>
              )}
              {!isRunning && service.status === 'stopped' && (
                <div className="text-white/25">
                  Arrete — demarrage manuel requis si Ollama vient d&apos;etre relance
                </div>
              )}
            </div>
          )}

          {/* Boutons */}
          <div className="flex items-center gap-1 mt-2.5 flex-wrap">
            {service.can_control ? (
              <>
                {screenCanStart && !isRunning ? (
                  <ActionButton
                    color="emerald"
                    icon={Play}
                    label="Start"
                    onClick={onStart}
                    loading={actionLoading}
                    compact
                  />
                ) : null}
                {isRunning ? (
                  <>
                    <ActionButton
                      color="red"
                      icon={Square}
                      label="Stop"
                      onClick={onStop}
                      loading={actionLoading}
                      compact
                    />
                    <ActionButton
                      color="blue"
                      icon={RotateCw}
                      label="Restart"
                      onClick={onRestart}
                      loading={actionLoading}
                      compact
                    />
                  </>
                ) : null}
                {screenBlocked && (
                  <ActionButton
                    color="emerald"
                    icon={Play}
                    label="Start Ollama"
                    onClick={() => {
                      void api.supervisorStart('ollama').then(() => {
                        /* parent refresh via WS / poll */
                      })
                    }}
                    loading={actionLoading}
                    compact
                  />
                )}
              </>
            ) : (
              <span className="text-[9px] text-white/15 italic px-1">
                Manuel
              </span>
            )}
            <button
              onClick={onToggleLogs}
              className={`inline-flex items-center gap-1 px-2 py-1 rounded text-[10px] transition-colors active:scale-[0.97] ${
                logsOpen
                  ? 'bg-white/[0.06] text-white/70 border border-white/[0.10]'
                  : 'bg-white/[0.02] hover:bg-white/[0.04] text-white/30 border border-transparent'
              }`}
            >
              <TerminalSquare size={10} />
              Logs
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ══════════════════════════════════════════════════════════════
// ActionButton
// ══════════════════════════════════════════════════════════════

type ActionColor = 'emerald' | 'red' | 'blue'

interface ActionButtonProps {
  color: ActionColor
  icon: React.ComponentType<{ size?: number; className?: string }>
  label: string
  onClick: () => void
  loading: boolean
  compact?: boolean
}

const COLOR_CLASSES: Record<ActionColor, { bg: string; hoverBg: string; text: string; border: string }> = {
  emerald: {
    bg: 'bg-emerald-500/10',
    hoverBg: 'hover:bg-emerald-500/20',
    text: 'text-emerald-400',
    border: 'border-emerald-500/20',
  },
  red: {
    bg: 'bg-red-500/10',
    hoverBg: 'hover:bg-red-500/20',
    text: 'text-red-400',
    border: 'border-red-500/20',
  },
  blue: {
    bg: 'bg-blue-500/10',
    hoverBg: 'hover:bg-blue-500/20',
    text: 'text-blue-400',
    border: 'border-blue-500/20',
  },
}

function ActionButton({ color, icon: IconEl, label, onClick, loading, compact }: ActionButtonProps) {
  const c = COLOR_CLASSES[color]

  return (
    <button
      onClick={onClick}
      disabled={loading}
      className={`inline-flex items-center gap-1 rounded-md font-medium
        ${c.bg} ${c.text} ${c.border} border
        transition-colors disabled:opacity-40 active:scale-[0.97]
        ${loading ? '' : c.hoverBg}
        ${compact ? 'px-2 py-1 text-[10px]' : 'px-2.5 py-1.5 text-[11px]'}`}
    >
      {loading ? (
        <RefreshCw size={compact ? 10 : 11} className="animate-spin" />
      ) : (
        <IconEl size={compact ? 10 : 11} />
      )}
      {label}
    </button>
  )
}
