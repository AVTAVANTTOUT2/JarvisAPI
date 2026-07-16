/**
 * CognitiveView — routage, délégations Cursor, vocal, autonomie.
 *
 * Quatre sections : Intelligence (politique LLM + testeur de routage),
 * Délégations (jobs Cursor avec actions), Vocal (latences p50/p95),
 * Autonomie (réglages self-repair / self-improvement).
 */
import { useCallback, useEffect, useState } from 'react';
import { Activity, Bot, GitBranch, Mic, RefreshCw, Shield } from 'lucide-react';
import { jarvisFetch } from '@unified/lib/api';

type Job = {
  job_id: string;
  title: string;
  status: string;
  branch_name?: string;
  pr_url?: string;
  prompt_template?: string;
  template_version?: string;
  error_message?: string;
  created_at?: string;
  prompt_sent?: string;
  raw_output?: string;
  structured_result?: Record<string, unknown> | null;
};

type StageMetric = { p50_ms: number; p95_ms: number; count: number };
type VoiceMetrics = { samples: number; days: number; stages: Record<string, StageMetric> };
type RoutingDecision = Record<string, unknown>;

const ACTIVE_STATUSES = new Set(['queued', 'preparing', 'running', 'testing', 'reviewing']);

function StatusBadge({ status }: { status: string }) {
  const color =
    status === 'pr_opened' || status === 'completed'
      ? 'text-emerald-300 border-emerald-500/40'
      : status === 'failed' || status === 'cancelled'
        ? 'text-red-300 border-red-500/40'
        : ACTIVE_STATUSES.has(status)
          ? 'text-sky-300 border-sky-500/40'
          : 'text-zinc-300 border-white/10';
  return <span className={`text-xs px-2 py-0.5 border rounded ${color}`}>{status}</span>;
}

export default function CognitiveView() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [policy, setPolicy] = useState<Record<string, unknown> | null>(null);
  const [caps, setCaps] = useState<unknown[]>([]);
  const [voice, setVoice] = useState<VoiceMetrics | null>(null);
  const [autonomy, setAutonomy] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [routeText, setRouteText] = useState('');
  const [routeResult, setRouteResult] = useState<RoutingDecision | null>(null);
  const [expandedJob, setExpandedJob] = useState<string | null>(null);
  const [jobDetail, setJobDetail] = useState<Job | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [j, p, c, v, a] = await Promise.all([
        jarvisFetch<{ jobs: Job[] }>('/api/cursor/jobs?limit=30'),
        jarvisFetch<{ policy: Record<string, unknown> }>('/api/cognitive/llm-policy'),
        jarvisFetch<{ capabilities: unknown[] }>('/api/cognitive/capabilities'),
        jarvisFetch<VoiceMetrics>('/api/voice/metrics?days=7').catch(() => null),
        jarvisFetch<{ settings: Record<string, unknown> }>('/api/autonomy/settings').catch(() => null),
      ]);
      setJobs(j.jobs || []);
      setPolicy(p.policy || null);
      setCaps(c.capabilities || []);
      setVoice(v);
      setAutonomy(a?.settings ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 20000);
    return () => clearInterval(timer);
  }, [load]);

  const jobAction = useCallback(
    async (jobId: string, action: 'cancel' | 'retry' | 'rollback') => {
      try {
        await jarvisFetch(`/api/cursor/jobs/${jobId}/${action}`, { method: 'POST' });
        await load();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [load],
  );

  const testRoute = useCallback(async () => {
    if (!routeText.trim()) return;
    try {
      const r = await jarvisFetch<{ routing: RoutingDecision }>('/api/cognitive/route', {
        method: 'POST',
        body: JSON.stringify({ text: routeText, interaction_mode: 'chat' }),
      });
      setRouteResult(r.routing);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [routeText]);

  const openDetail = useCallback(async (jobId: string) => {
    if (expandedJob === jobId) {
      setExpandedJob(null);
      setJobDetail(null);
      return;
    }
    setExpandedJob(jobId);
    try {
      const r = await jarvisFetch<{ job: Job }>(`/api/cursor/jobs/${jobId}`);
      setJobDetail(r.job);
    } catch {
      setJobDetail(null);
    }
  }, [expandedJob]);

  return (
    <div className="h-full overflow-auto p-6 space-y-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Intelligence & Délégations</h1>
          <p className="text-sm opacity-70">Flash / Main / Cursor — observabilité du routage cognitif</p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          className="inline-flex items-center gap-2 px-3 py-2 text-sm border border-white/10 rounded"
        >
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          Actualiser
        </button>
      </header>

      {error && <p className="text-sm text-red-400">{error}</p>}

      {/* ── Intelligence : politique LLM + testeur de routage ── */}
      <section className="grid md:grid-cols-3 gap-4">
        <div className="p-4 border border-white/10 rounded space-y-2">
          <div className="flex items-center gap-2 text-sm opacity-80"><Bot size={16} /> Politique LLM</div>
          <pre className="text-xs whitespace-pre-wrap opacity-90">{JSON.stringify(policy, null, 2)}</pre>
        </div>
        <div className="p-4 border border-white/10 rounded space-y-2 md:col-span-2">
          <div className="flex items-center gap-2 text-sm opacity-80"><Activity size={16} /> Tester le routage</div>
          <div className="flex gap-2">
            <input
              value={routeText}
              onChange={(e) => setRouteText(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && void testRoute()}
              placeholder="Ex : Corrige le bug de connexion Android"
              className="flex-1 bg-transparent border border-white/10 rounded px-3 py-2 text-sm outline-none"
            />
            <button
              type="button"
              onClick={() => void testRoute()}
              className="px-3 py-2 text-sm border border-white/10 rounded"
            >
              Router
            </button>
          </div>
          {routeResult && (
            <pre className="text-xs whitespace-pre-wrap opacity-90 max-h-40 overflow-auto">
              {JSON.stringify(routeResult, null, 2)}
            </pre>
          )}
        </div>
      </section>

      {/* ── Vocal : latences p50/p95 par étape ── */}
      <section className="p-4 border border-white/10 rounded space-y-3">
        <div className="flex items-center gap-2 text-sm opacity-80">
          <Mic size={16} /> Pipeline vocal — latences 7 jours
          {voice ? <span className="opacity-60">({voice.samples} tours)</span> : null}
        </div>
        {voice && voice.samples > 0 ? (
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
            {Object.entries(voice.stages).map(([stage, m]) => (
              <div key={stage} className="border border-white/5 rounded p-2 text-xs">
                <div className="font-medium uppercase opacity-70">{stage}</div>
                <div>p50 : {m.p50_ms} ms</div>
                <div>p95 : {m.p95_ms} ms</div>
                <div className="opacity-50">{m.count} éch.</div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm opacity-60">Pas encore de mesures vocales.</p>
        )}
      </section>

      {/* ── Capacités + Autonomie ── */}
      <section className="grid md:grid-cols-3 gap-4">
        <div className="p-4 border border-white/10 rounded space-y-2 md:col-span-2">
          <div className="flex items-center gap-2 text-sm opacity-80"><Shield size={16} /> Capacités</div>
          <div className="grid sm:grid-cols-2 gap-2 max-h-48 overflow-auto">
            {(caps as Array<{ name: string; available: boolean; executor: string; description: string }>).map((cap) => (
              <div key={cap.name} className="text-xs border border-white/5 rounded p-2">
                <div className="font-medium">{cap.name} {cap.available ? '●' : '○'}</div>
                <div className="opacity-60">{cap.executor} — {cap.description}</div>
              </div>
            ))}
          </div>
        </div>
        <div className="p-4 border border-white/10 rounded space-y-2">
          <div className="flex items-center gap-2 text-sm opacity-80"><Shield size={16} /> Autonomie</div>
          {autonomy ? (
            <ul className="text-xs space-y-1 opacity-90">
              {Object.entries(autonomy).map(([k, v]) => (
                <li key={k} className="flex justify-between gap-2">
                  <span className="opacity-60">{k}</span>
                  <span>{String(v)}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-xs opacity-60">Réglages indisponibles.</p>
          )}
        </div>
      </section>

      {/* ── Délégations Cursor ── */}
      <section className="space-y-3">
        <div className="flex items-center gap-2 text-sm opacity-80">
          <GitBranch size={16} /> Jobs Cursor
        </div>
        <div className="space-y-2">
          {jobs.length === 0 && <p className="text-sm opacity-60">Aucun job pour l'instant.</p>}
          {jobs.map((job) => (
            <div key={job.job_id} className="border border-white/10 rounded p-3 text-sm space-y-1">
              <div className="flex justify-between items-center gap-3">
                <strong className="truncate">{job.title}</strong>
                <StatusBadge status={job.status} />
              </div>
              <div className="opacity-60 text-xs">
                {job.job_id}
                {job.branch_name ? ` · ${job.branch_name}` : ''}
                {job.prompt_template ? ` · ${job.prompt_template}` : ''}
                {job.template_version ? ` v${job.template_version}` : ''}
                {job.created_at ? ` · ${job.created_at}` : ''}
              </div>
              {job.error_message && <div className="text-xs text-red-300">{job.error_message}</div>}
              <div className="flex flex-wrap items-center gap-2 pt-1">
                {job.pr_url && (
                  <a
                    className="text-xs px-2 py-1 border border-emerald-500/40 text-emerald-300 rounded"
                    href={job.pr_url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Ouvrir la PR
                  </a>
                )}
                {ACTIVE_STATUSES.has(job.status) && (
                  <button
                    type="button"
                    onClick={() => void jobAction(job.job_id, 'cancel')}
                    className="text-xs px-2 py-1 border border-white/10 rounded"
                  >
                    Annuler
                  </button>
                )}
                {(job.status === 'failed' || job.status === 'cancelled') && (
                  <button
                    type="button"
                    onClick={() => void jobAction(job.job_id, 'retry')}
                    className="text-xs px-2 py-1 border border-white/10 rounded"
                  >
                    Relancer
                  </button>
                )}
                {job.status !== 'rolled_back' && job.branch_name && (
                  <button
                    type="button"
                    onClick={() => void jobAction(job.job_id, 'rollback')}
                    className="text-xs px-2 py-1 border border-amber-500/40 text-amber-300 rounded"
                  >
                    Rollback
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => void openDetail(job.job_id)}
                  className="text-xs px-2 py-1 border border-white/10 rounded"
                >
                  {expandedJob === job.job_id ? 'Fermer' : 'Détails'}
                </button>
              </div>
              {expandedJob === job.job_id && jobDetail && (
                <div className="mt-2 space-y-2 border-t border-white/5 pt-2">
                  {jobDetail.structured_result ? (
                    <div>
                      <div className="text-xs font-medium opacity-70">Résultat structuré</div>
                      <pre className="text-xs whitespace-pre-wrap opacity-80 max-h-40 overflow-auto">
                        {JSON.stringify(jobDetail.structured_result, null, 2)}
                      </pre>
                    </div>
                  ) : null}
                  {jobDetail.prompt_sent ? (
                    <div>
                      <div className="text-xs font-medium opacity-70">Prompt envoyé</div>
                      <pre className="text-xs whitespace-pre-wrap opacity-80 max-h-40 overflow-auto">
                        {jobDetail.prompt_sent}
                      </pre>
                    </div>
                  ) : null}
                  {jobDetail.raw_output ? (
                    <div>
                      <div className="text-xs font-medium opacity-70">Sortie Cursor (fin)</div>
                      <pre className="text-xs whitespace-pre-wrap opacity-80 max-h-40 overflow-auto">
                        {jobDetail.raw_output.slice(-4000)}
                      </pre>
                    </div>
                  ) : null}
                </div>
              )}
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
