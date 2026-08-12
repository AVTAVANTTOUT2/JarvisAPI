/** Provider-neutral supervision for long-running JARVIS work. */
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Activity,
  AlertTriangle,
  Bot,
  CheckCircle2,
  Clock3,
  FileText,
  ListChecks,
  Pause,
  Play,
  RefreshCw,
  ShieldCheck,
  ShieldX,
  XCircle,
} from 'lucide-react'
import {
  AGENTIC_EVENT_TYPES,
  decideAgenticApproval,
  getAgenticRun,
  getAgenticRuntimeStatus,
  listAgenticRuns,
  mergeAgenticEvent,
  runAgenticAction,
} from '@unified/lib/agenticApi'
import type {
  AgenticApprovalDecision,
  AgenticRun,
  AgenticRunAction,
  AgenticRuntimeStatus,
  AgenticSanitizedArgumentValue,
} from '@unified/types/agentic'
import { ws } from '@desktop/services/websocket'

const TERMINAL_STATUSES = new Set(['cancelled', 'failed', 'completed', 'expired'])
const PAUSABLE_STATUSES = new Set(['planning', 'running', 'verifying', 'reviewing'])
const RESUMABLE_STATUSES = new Set(['paused', 'blocked'])

const STATUS_LABELS: Record<string, string> = {
  pending: 'En attente',
  approved: 'Autorisée',
  denied: 'Refusée',
  rejected: 'Refusée',
  resolved: 'Traitée',
  created: 'Créée',
  classified: 'Classée',
  queued: 'En attente',
  provisioning: 'Préparation',
  planning: 'Planification',
  awaiting_approval: 'Approbation requise',
  running: 'En cours',
  verifying: 'Vérification',
  reviewing: 'Revue',
  paused: 'En pause',
  blocked: 'Bloquée',
  cancelling: 'Annulation',
  cancelled: 'Annulée',
  failed: 'Échec',
  completed: 'Terminée',
  expired: 'Expirée',
  provider_unavailable: 'Moteur indisponible',
}

function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status.replaceAll('_', ' ')
}

function StatusBadge({ status }: { status: string }) {
  const color = status === 'completed'
    ? 'border-emerald-500/40 text-emerald-300'
    : status === 'failed' || status === 'cancelled' || status === 'provider_unavailable'
      ? 'border-red-500/40 text-red-300'
      : status === 'awaiting_approval' || status === 'blocked'
        ? 'border-amber-500/40 text-amber-300'
        : TERMINAL_STATUSES.has(status)
          ? 'border-white/10 text-zinc-300'
          : 'border-sky-500/40 text-sky-300'
  return <span className={`rounded border px-2 py-0.5 text-xs ${color}`}>{statusLabel(status)}</span>
}

function formatTimestamp(value: string | undefined): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('fr-FR', { dateStyle: 'short', timeStyle: 'short' }).format(date)
}

function resultSummary(value: unknown): string | null {
  if (typeof value === 'string' && value.trim()) return value.trim()
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const source = value as Record<string, unknown>
  for (const key of ['summary', 'message', 'text', 'content']) {
    const candidate = source[key]
    if (typeof candidate === 'string' && candidate.trim()) return candidate.trim()
  }
  return 'Résultat structuré disponible.'
}

function safeArtifactHref(value: string | undefined): string | null {
  if (!value) return null
  if (value.startsWith('/api/agentic/')) return value
  return null
}

function formatApprovalArgument(value: AgenticSanitizedArgumentValue): string {
  if (value === null) return 'null'
  if (Array.isArray(value)) return value.map(formatApprovalArgument).join(', ')
  if (typeof value === 'object') {
    return Object.entries(value)
      .map(([key, item]) => `${key}: ${formatApprovalArgument(item)}`)
      .join(', ')
  }
  return String(value)
}

function RuntimeCard({ runtime }: { runtime: AgenticRuntimeStatus | null }) {
  const available = runtime?.available === true
  return (
    <div className="rounded border border-white/10 p-4">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm text-white/80">
          <Activity size={16} /> Moteur d’exécution
        </div>
        <span className={available ? 'text-emerald-300' : 'text-amber-300'}>
          {available ? 'Disponible' : 'Indisponible'}
        </span>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-white/60">
        <span>Actives</span><strong className="text-right text-white/90">{runtime?.active_runs ?? 0}</strong>
        <span>En file</span><strong className="text-right text-white/90">{runtime?.queued_runs ?? 0}</strong>
        <span>État</span><strong className="truncate text-right text-white/90">{runtime?.status ?? 'inconnu'}</strong>
      </div>
      {runtime?.error_code && <p className="mt-2 text-xs text-amber-300">Diagnostic : {runtime.error_code}</p>}
    </div>
  )
}

export default function CognitiveView() {
  const [runs, setRuns] = useState<AgenticRun[]>([])
  const [runtime, setRuntime] = useState<AgenticRuntimeStatus | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [selectedRun, setSelectedRun] = useState<AgenticRun | null>(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    const [runsResult, runtimeResult] = await Promise.allSettled([
      listAgenticRuns(50),
      getAgenticRuntimeStatus(),
    ])
    if (runsResult.status === 'fulfilled') {
      setRuns(runsResult.value.runs)
      setSelectedId((current) => current ?? runsResult.value.runs[0]?.id ?? null)
    }
    if (runtimeResult.status === 'fulfilled') setRuntime(runtimeResult.value)
    if (runtimeResult.status === 'fulfilled' && runsResult.status === 'fulfilled') {
      const activeRuns = runsResult.value.runs.filter((run) => !TERMINAL_STATUSES.has(run.status)).length
      const queuedRuns = runsResult.value.runs.filter((run) => run.status === 'queued').length
      setRuntime({ ...runtimeResult.value, active_runs: activeRuns, queued_runs: queuedRuns })
    }
    if (runsResult.status === 'rejected' && runtimeResult.status === 'rejected') {
      const cause = runsResult.reason
      setError(cause instanceof Error ? cause.message : 'Activité agentique indisponible')
    }
    setLoading(false)
  }, [])

  const loadDetail = useCallback(async (runId: string) => {
    setError(null)
    try {
      const detail = await getAgenticRun(runId)
      setSelectedRun(detail)
      setRuns((current) => current.map((run) => run.id === detail.id ? { ...run, ...detail } : run))
    } catch (cause) {
      setSelectedRun(null)
      setError(cause instanceof Error ? cause.message : 'Détail du run indisponible')
    }
  }, [])

  useEffect(() => {
    void load()
    const timer = window.setInterval(() => void load(), 20_000)
    return () => window.clearInterval(timer)
  }, [load])

  useEffect(() => {
    if (!selectedId) {
      setSelectedRun(null)
      return
    }
    void loadDetail(selectedId)
  }, [loadDetail, selectedId])

  useEffect(() => {
    const onEvent = (event: Record<string, unknown>) => {
      setRuns((current) => mergeAgenticEvent(current, event))
      setSelectedRun((current) => current ? mergeAgenticEvent([current], event)[0] ?? current : current)
    }
    const unsubscribers = AGENTIC_EVENT_TYPES.map((type) => ws.on(type, onEvent))
    ws.connect()
    return () => unsubscribers.forEach((unsubscribe) => unsubscribe())
  }, [])

  const activeRun = useMemo(
    () => selectedRun ?? runs.find((run) => run.id === selectedId) ?? null,
    [runs, selectedId, selectedRun],
  )

  const performAction = useCallback(async (runId: string, action: AgenticRunAction) => {
    setBusy(`${runId}:${action}`)
    setError(null)
    try {
      await runAgenticAction(runId, action)
      await Promise.all([load(), loadDetail(runId)])
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : `Action ${action} impossible`)
    } finally {
      setBusy(null)
    }
  }, [load, loadDetail])

  const decideApproval = useCallback(async (
    runId: string,
    approvalId: string,
    decision: AgenticApprovalDecision,
  ) => {
    setBusy(`${approvalId}:${decision}`)
    setError(null)
    try {
      await decideAgenticApproval(runId, approvalId, decision)
      await Promise.all([load(), loadDetail(runId)])
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Décision impossible')
    } finally {
      setBusy(null)
    }
  }, [load, loadDetail])

  return (
    <div className="h-full overflow-auto p-6">
      <header className="mb-6 flex items-start justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold tracking-tight">
            <Bot size={20} /> Activité agentique
          </h1>
          <p className="mt-1 text-sm text-white/60">Planification, progression et validations des tâches JARVIS.</p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          className="inline-flex items-center gap-2 rounded border border-white/10 px-3 py-2 text-sm"
        >
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> Actualiser
        </button>
      </header>

      {error && (
        <div role="alert" className="mb-4 flex items-center gap-2 rounded border border-red-500/30 bg-red-500/5 p-3 text-sm text-red-300">
          <AlertTriangle size={16} /> {error}
        </div>
      )}

      <div className="mb-5 grid gap-4 md:grid-cols-3">
        <RuntimeCard runtime={runtime} />
        <div className="rounded border border-white/10 p-4">
          <div className="flex items-center gap-2 text-sm text-white/70"><Clock3 size={16} /> En cours</div>
          <div className="mt-3 text-3xl font-semibold">{runs.filter((run) => !TERMINAL_STATUSES.has(run.status)).length}</div>
        </div>
        <div className="rounded border border-white/10 p-4">
          <div className="flex items-center gap-2 text-sm text-white/70"><ShieldCheck size={16} /> À valider</div>
          <div className="mt-3 text-3xl font-semibold">
            {runs.filter((run) => run.requires_attention || run.status === 'awaiting_approval').length}
          </div>
        </div>
      </div>

      <div className="grid min-h-[36rem] gap-4 lg:grid-cols-[minmax(18rem,0.8fr)_minmax(0,1.7fr)]">
        <section aria-label="Runs agentiques" className="rounded border border-white/10 p-3">
          <div className="mb-3 flex items-center gap-2 px-1 text-sm text-white/70">
            <ListChecks size={16} /> Runs récents
          </div>
          <div className="max-h-[42rem] space-y-2 overflow-auto">
            {!loading && runs.length === 0 && <p className="p-3 text-sm text-white/50">Aucune tâche agentique.</p>}
            {runs.map((run) => (
              <button
                key={run.id}
                type="button"
                onClick={() => setSelectedId(run.id)}
                className={`w-full rounded border p-3 text-left transition-colors ${
                  selectedId === run.id ? 'border-sky-400/50 bg-sky-400/5' : 'border-white/5 hover:border-white/15'
                }`}
              >
                <div className="flex items-start justify-between gap-2">
                  <strong className="line-clamp-2 text-sm">{run.title}</strong>
                  <StatusBadge status={run.status} />
                </div>
                <div className="mt-2 flex items-center justify-between text-xs text-white/50">
                  <span className="truncate">{run.phase ? statusLabel(run.phase) : run.category ?? run.channel ?? 'JARVIS'}</span>
                  <span>{formatTimestamp(run.updated_at ?? run.created_at)}</span>
                </div>
                {run.progress !== undefined && (
                  <div className="mt-2 h-1.5 overflow-hidden rounded bg-white/5">
                    <div className="h-full rounded bg-sky-400" style={{ width: `${run.progress}%` }} />
                  </div>
                )}
              </button>
            ))}
          </div>
        </section>

        <section aria-label="Détail du run" className="rounded border border-white/10 p-4">
          {!activeRun ? (
            <div className="grid h-full place-items-center text-sm text-white/50">Sélectionnez un run.</div>
          ) : (
            <div className="space-y-5">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 className="text-lg font-semibold">{activeRun.title}</h2>
                    <StatusBadge status={activeRun.status} />
                  </div>
                  <p className="mt-1 text-xs text-white/45">{activeRun.id}</p>
                  {activeRun.summary && <p className="mt-2 text-sm text-white/70">{activeRun.summary}</p>}
                </div>
                <div className="flex flex-wrap gap-2">
                  {PAUSABLE_STATUSES.has(activeRun.status) && (
                    <button type="button" disabled={busy !== null} onClick={() => void performAction(activeRun.id, 'pause')} className="inline-flex items-center gap-1 rounded border border-white/10 px-2 py-1 text-xs disabled:opacity-40">
                      <Pause size={13} /> Pause
                    </button>
                  )}
                  {RESUMABLE_STATUSES.has(activeRun.status) && (
                    <button type="button" disabled={busy !== null} onClick={() => void performAction(activeRun.id, 'resume')} className="inline-flex items-center gap-1 rounded border border-emerald-500/40 px-2 py-1 text-xs text-emerald-300 disabled:opacity-40">
                      <Play size={13} /> Reprendre
                    </button>
                  )}
                  {!TERMINAL_STATUSES.has(activeRun.status) && activeRun.status !== 'cancelling' && (
                    <button type="button" disabled={busy !== null} onClick={() => void performAction(activeRun.id, 'cancel')} className="inline-flex items-center gap-1 rounded border border-red-500/40 px-2 py-1 text-xs text-red-300 disabled:opacity-40">
                      <XCircle size={13} /> Annuler
                    </button>
                  )}
                </div>
              </div>

              {activeRun.error && (
                <div className="rounded border border-red-500/30 bg-red-500/5 p-3 text-sm text-red-200">
                  <strong>{activeRun.error.category ?? activeRun.error.code ?? 'Erreur'}</strong>
                  <p className="mt-1">{activeRun.error.message}</p>
                </div>
              )}

              {activeRun.progress !== undefined && (
                <div>
                  <div className="mb-1 flex justify-between text-xs text-white/60">
                    <span>{activeRun.phase ? statusLabel(activeRun.phase) : 'Progression'}</span><span>{activeRun.progress}%</span>
                  </div>
                  <div className="h-2 overflow-hidden rounded bg-white/5"><div className="h-full rounded bg-sky-400" style={{ width: `${activeRun.progress}%` }} /></div>
                </div>
              )}

              {activeRun.plan?.length ? (
                <div>
                  <h3 className="mb-2 text-sm font-medium">Plan</h3>
                  <ol className="list-decimal space-y-1 pl-5 text-sm text-white/70">
                    {activeRun.plan.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}
                  </ol>
                </div>
              ) : null}

              <div>
                <h3 className="mb-2 text-sm font-medium">Étapes</h3>
                {activeRun.steps.length === 0 ? <p className="text-sm text-white/45">Le plan détaillé n’est pas encore disponible.</p> : (
                  <div className="space-y-2">
                    {activeRun.steps.map((step) => (
                      <div key={step.id} className="rounded border border-white/5 p-3 text-sm">
                        <div className="flex items-center justify-between gap-2">
                          <span className="flex items-center gap-2">
                            {step.status === 'completed' ? <CheckCircle2 size={14} className="text-emerald-300" /> : <Clock3 size={14} className="text-sky-300" />}
                            {step.title}
                          </span>
                          <span className="text-xs text-white/50">{statusLabel(step.status)}</span>
                        </div>
                        {step.summary && <p className="mt-1 text-xs text-white/55">{step.summary}</p>}
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {activeRun.approvals.length > 0 && (
                <div>
                  <h3 className="mb-2 text-sm font-medium">Approbations</h3>
                  <div className="space-y-2">
                    {activeRun.approvals.map((approval) => (
                      <div key={approval.id} className="rounded border border-amber-500/25 bg-amber-500/5 p-3 text-sm">
                        <div className="flex flex-wrap items-start justify-between gap-2">
                          <div>
                            <strong>{approval.title}</strong>
                            {approval.summary && <p className="mt-1 text-xs text-white/60">{approval.summary}</p>}
                            {approval.risk_level && <p className="mt-1 text-xs text-amber-200/80">Niveau de risque : {approval.risk_level}</p>}
                          </div>
                          <StatusBadge status={approval.status} />
                        </div>
                        {approval.sanitized_arguments && Object.keys(approval.sanitized_arguments).length > 0 && (
                          <div className="mt-3 rounded border border-white/10 bg-black/20 p-2">
                            <p className="mb-1 text-xs font-medium text-white/70">Paramètres sanitisés</p>
                            <dl className="space-y-1 text-xs text-white/60">
                              {Object.entries(approval.sanitized_arguments).map(([key, value]) => (
                                <div key={key} className="grid grid-cols-[minmax(7rem,auto)_1fr] gap-2">
                                  <dt className="font-mono text-white/45">{key}</dt>
                                  <dd className="break-words font-mono">{formatApprovalArgument(value)}</dd>
                                </div>
                              ))}
                            </dl>
                          </div>
                        )}
                        {approval.risks && approval.risks.length > 0 && (
                          <div className="mt-3">
                            <p className="mb-1 text-xs font-medium text-amber-200/80">Risques</p>
                            <ul className="list-disc space-y-1 pl-5 text-xs text-white/60">
                              {approval.risks.map((risk, index) => <li key={`${approval.id}-risk-${index}`}>{risk}</li>)}
                            </ul>
                          </div>
                        )}
                        {approval.status === 'pending' && (
                          <div className="mt-3 flex gap-2">
                            <button type="button" disabled={busy !== null} onClick={() => void decideApproval(activeRun.id, approval.id, 'approved')} className="inline-flex items-center gap-1 rounded border border-emerald-500/40 px-2 py-1 text-xs text-emerald-300 disabled:opacity-40"><ShieldCheck size={13} /> Autoriser</button>
                            <button type="button" disabled={busy !== null} onClick={() => void decideApproval(activeRun.id, approval.id, 'denied')} className="inline-flex items-center gap-1 rounded border border-red-500/40 px-2 py-1 text-xs text-red-300 disabled:opacity-40"><ShieldX size={13} /> Refuser</button>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {activeRun.artifacts.length > 0 && (
                <div>
                  <h3 className="mb-2 text-sm font-medium">Artefacts</h3>
                  <div className="grid gap-2 sm:grid-cols-2">
                    {activeRun.artifacts.map((artifact) => {
                      const href = safeArtifactHref(artifact.url)
                      const content = <><FileText size={15} /><span className="truncate">{artifact.name}</span>{artifact.kind && <span className="ml-auto text-xs text-white/45">{artifact.kind}</span>}</>
                      return href
                        ? <a key={artifact.id} href={href} className="flex items-center gap-2 rounded border border-white/10 p-2 text-sm hover:border-white/25">{content}</a>
                        : <div key={artifact.id} className="flex items-center gap-2 rounded border border-white/10 p-2 text-sm">{content}</div>
                    })}
                  </div>
                </div>
              )}

              {resultSummary(activeRun.result) && (
                <div>
                  <h3 className="mb-2 text-sm font-medium">Résultat</h3>
                  <div className="whitespace-pre-wrap rounded border border-emerald-500/20 bg-emerald-500/5 p-3 text-sm text-white/75">
                    {resultSummary(activeRun.result)}
                  </div>
                </div>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
