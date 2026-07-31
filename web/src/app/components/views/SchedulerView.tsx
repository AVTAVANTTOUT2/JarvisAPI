/**
 * SchedulerView — suivi des jobs cron annoncés dans le README.
 *
 * Groupes : quotidien / fréquent / hebdomadaire.
 * Les ticks fréquents montrent un agrégat ; le détail s'ouvre au clic.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Clock3, Play, RefreshCw, Timer } from 'lucide-react';
import { jarvisFetch } from '@unified/lib/api';

type JobLastRun = {
  started_at?: string | null;
  status?: string | null;
  output?: string | null;
  error?: string | null;
  duration_ms?: number | null;
  trigger?: string | null;
};

type JobStats = {
  days: number;
  total: number;
  ok: number;
  error: number;
  skipped: number;
  silent: number;
  today: number;
  today_ok: number;
  today_error: number;
};

type SchedulerJob = {
  job_id: string;
  title: string;
  description: string;
  cadence: string;
  group: string;
  schedule: string;
  enabled: boolean;
  manual_run: boolean;
  today_status: string;
  next_run_at?: string | null;
  last_run?: JobLastRun | null;
  stats: JobStats;
};

type SchedulerStatus = {
  generated_at: string;
  days: number;
  scheduler_running: boolean;
  jobs: SchedulerJob[];
};

type JobRun = {
  id: number;
  job_id: string;
  trigger: string;
  status: string;
  started_at: string;
  finished_at?: string | null;
  duration_ms?: number | null;
  output?: string | null;
  error?: string | null;
};

const GROUP_LABELS: Record<string, string> = {
  daily: 'Quotidien',
  frequent: 'Fréquent',
  weekly: 'Hebdomadaire',
};

function statusClass(status: string): string {
  switch (status) {
    case 'ok':
      return 'text-emerald-300 border-emerald-500/40';
    case 'error':
    case 'missed':
      return 'text-red-300 border-red-500/40';
    case 'pending':
      return 'text-sky-300 border-sky-500/40';
    case 'skipped':
    case 'disabled':
      return 'text-zinc-400 border-white/10';
    case 'silent':
      return 'text-amber-200 border-amber-500/30';
    default:
      return 'text-zinc-300 border-white/10';
  }
}

function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`text-[11px] px-2 py-0.5 border rounded font-mono ${statusClass(status)}`}>
      {status}
    </span>
  );
}

function formatWhen(value?: string | null): string {
  if (!value) return '—';
  try {
    return new Date(value).toLocaleString('fr-FR', {
      day: '2-digit',
      month: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return value;
  }
}

export default function SchedulerView() {
  const [data, setData] = useState<SchedulerStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [runs, setRuns] = useState<JobRun[]>([]);
  const [runsLoading, setRunsLoading] = useState(false);
  const [runningJob, setRunningJob] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const payload = await jarvisFetch<SchedulerStatus>('/api/scheduler/jobs?days=7');
      setData(payload);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 30000);
    return () => clearInterval(timer);
  }, [load]);

  const groups = useMemo(() => {
    const jobs = data?.jobs ?? [];
    return (['daily', 'frequent', 'weekly'] as const).map((group) => ({
      group,
      label: GROUP_LABELS[group],
      jobs: jobs.filter((j) => j.group === group),
    }));
  }, [data]);

  const openRuns = useCallback(async (jobId: string) => {
    if (expanded === jobId) {
      setExpanded(null);
      setRuns([]);
      return;
    }
    setExpanded(jobId);
    setRunsLoading(true);
    try {
      const payload = await jarvisFetch<{ runs: JobRun[] }>(
        `/api/scheduler/jobs/${encodeURIComponent(jobId)}/runs?days=7&limit=100`,
      );
      setRuns(payload.runs || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setRuns([]);
    } finally {
      setRunsLoading(false);
    }
  }, [expanded]);

  const runNow = useCallback(async (jobId: string) => {
    setRunningJob(jobId);
    setError(null);
    try {
      await jarvisFetch(`/api/scheduler/jobs/${encodeURIComponent(jobId)}/run`, {
        method: 'POST',
      });
      await load();
      if (expanded === jobId) {
        setRunsLoading(true);
        try {
          const payload = await jarvisFetch<{ runs: JobRun[] }>(
            `/api/scheduler/jobs/${encodeURIComponent(jobId)}/runs?days=7&limit=100`,
          );
          setRuns(payload.runs || []);
        } finally {
          setRunsLoading(false);
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunningJob(null);
    }
  }, [expanded, load]);

  return (
    <div className="h-full overflow-y-auto p-4 md:p-6 space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Automatisations</p>
          <h1 className="text-xl font-semibold tracking-tight">Scheduler</h1>
          <p className="text-sm text-muted-foreground mt-1 max-w-2xl">
            Suivi des jobs cron du README : fait / manqué / échec, avec sortie et historique 7 jours.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className={`text-xs font-mono px-2 py-1 border rounded ${
            data?.scheduler_running
              ? 'text-emerald-300 border-emerald-500/40'
              : 'text-zinc-400 border-white/10'
          }`}>
            {data?.scheduler_running ? 'APScheduler actif' : 'APScheduler arrêté'}
          </span>
          <button
            type="button"
            onClick={() => void load()}
            className="inline-flex items-center gap-1.5 text-xs font-mono px-3 py-1.5 border border-white/15 rounded hover:border-white/30"
          >
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
            Actualiser
          </button>
        </div>
      </header>

      {error && (
        <div className="text-sm text-red-300 border border-red-500/30 rounded-xl px-3 py-2 font-mono">
          {error}
        </div>
      )}

      {groups.map(({ group, label, jobs }) => (
        <section key={group} className="space-y-3">
          <h2 className="text-xs font-mono uppercase tracking-wider text-muted-foreground flex items-center gap-2">
            {group === 'frequent' ? <Timer size={12} /> : <Clock3 size={12} />}
            {label}
            <span className="text-foreground/40">({jobs.length})</span>
          </h2>
          <div className="space-y-2">
            {jobs.map((job) => {
              const isOpen = expanded === job.job_id;
              return (
                <article
                  key={job.job_id}
                  className="border border-white/10 rounded-xl bg-black/20 overflow-hidden"
                >
                  <button
                    type="button"
                    onClick={() => void openRuns(job.job_id)}
                    className="w-full text-left p-3 md:p-4 hover:bg-white/[0.03] transition-colors"
                  >
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <h3 className="text-sm font-medium">{job.title}</h3>
                          <StatusBadge status={job.today_status} />
                          <span className="text-[11px] font-mono text-muted-foreground">{job.schedule}</span>
                        </div>
                        <p className="text-xs text-muted-foreground mt-1">{job.description}</p>
                      </div>
                      <div className="text-right text-[11px] font-mono text-muted-foreground space-y-1">
                        <div>prochain : {formatWhen(job.next_run_at)}</div>
                        <div>dernier : {formatWhen(job.last_run?.started_at)}</div>
                      </div>
                    </div>
                    {job.cadence === 'frequent' ? (
                      <p className="mt-2 text-[11px] font-mono text-muted-foreground">
                        Aujourd&apos;hui {job.stats.today} · ok {job.stats.today_ok} · err {job.stats.today_error}
                        {' · '}7j {job.stats.total} (ok {job.stats.ok} / silent {job.stats.silent} / err {job.stats.error})
                      </p>
                    ) : (
                      <p className="mt-2 text-[11px] font-mono text-muted-foreground line-clamp-2">
                        {job.last_run?.error
                          || job.last_run?.output
                          || (job.today_status === 'pending' ? 'Pas encore exécuté aujourd\'hui.' : 'Pas de sortie.')}
                      </p>
                    )}
                  </button>
                  <div className="px-3 md:px-4 pb-3 flex flex-wrap gap-2">
                    {job.manual_run && (
                      <button
                        type="button"
                        disabled={runningJob === job.job_id}
                        onClick={() => void runNow(job.job_id)}
                        className="inline-flex items-center gap-1.5 text-[11px] font-mono px-2.5 py-1 border border-white/15 rounded hover:border-white/30 disabled:opacity-50"
                      >
                        <Play size={11} />
                        {runningJob === job.job_id ? 'Exécution…' : 'Exécuter maintenant'}
                      </button>
                    )}
                    <span className="text-[11px] font-mono text-muted-foreground self-center">
                      {isOpen ? 'détail ouvert' : 'cliquer pour le détail'}
                    </span>
                  </div>
                  {isOpen && (
                    <div className="border-t border-white/10 bg-black/30 p-3 md:p-4 space-y-2">
                      {runsLoading && (
                        <p className="text-xs font-mono text-muted-foreground">Chargement des exécutions…</p>
                      )}
                      {!runsLoading && runs.length === 0 && (
                        <p className="text-xs font-mono text-muted-foreground">Aucune exécution sur 7 jours.</p>
                      )}
                      {runs.map((run) => (
                        <div key={run.id} className="border border-white/10 rounded-lg p-2.5 space-y-1">
                          <div className="flex flex-wrap items-center gap-2 text-[11px] font-mono">
                            <StatusBadge status={run.status} />
                            <span className="text-muted-foreground">{formatWhen(run.started_at)}</span>
                            <span className="text-muted-foreground">{run.trigger}</span>
                            {typeof run.duration_ms === 'number' && (
                              <span className="text-muted-foreground">{run.duration_ms} ms</span>
                            )}
                          </div>
                          {(run.output || run.error) && (
                            <pre className="text-[11px] font-mono whitespace-pre-wrap text-foreground/80 max-h-40 overflow-y-auto">
                              {run.error || run.output}
                            </pre>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        </section>
      ))}
    </div>
  );
}
