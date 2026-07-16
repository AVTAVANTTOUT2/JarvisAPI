/**
 * CognitiveView — routage, délégations Cursor, autonomie.
 */
import { useCallback, useEffect, useState } from 'react';
import { Bot, GitBranch, RefreshCw, Shield } from 'lucide-react';

type Job = {
  job_id: string;
  title: string;
  status: string;
  branch_name?: string;
  pr_url?: string;
  template_version?: string;
  error_message?: string;
  created_at?: string;
};

export default function CognitiveView() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [policy, setPolicy] = useState<Record<string, unknown> | null>(null);
  const [caps, setCaps] = useState<unknown[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [j, p, c] = await Promise.all([
        fetch('/api/cursor/jobs?limit=30', { credentials: 'include' }).then((r) => r.json()),
        fetch('/api/cognitive/llm-policy', { credentials: 'include' }).then((r) => r.json()),
        fetch('/api/cognitive/capabilities', { credentials: 'include' }).then((r) => r.json()),
      ]);
      setJobs(j.jobs || []);
      setPolicy(p.policy || null);
      setCaps(c.capabilities || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

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

      <section className="grid md:grid-cols-3 gap-4">
        <div className="p-4 border border-white/10 rounded space-y-2">
          <div className="flex items-center gap-2 text-sm opacity-80"><Bot size={16} /> Politique LLM</div>
          <pre className="text-xs whitespace-pre-wrap opacity-90">{JSON.stringify(policy, null, 2)}</pre>
        </div>
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
      </section>

      <section className="space-y-3">
        <div className="flex items-center gap-2 text-sm opacity-80">
          <GitBranch size={16} /> Jobs Cursor
        </div>
        <div className="space-y-2">
          {jobs.length === 0 && <p className="text-sm opacity-60">Aucun job pour l'instant.</p>}
          {jobs.map((job) => (
            <div key={job.job_id} className="border border-white/10 rounded p-3 text-sm space-y-1">
              <div className="flex justify-between gap-3">
                <strong>{job.title}</strong>
                <span className="opacity-70">{job.status}</span>
              </div>
              <div className="opacity-60 text-xs">
                {job.job_id}
                {job.branch_name ? ` · ${job.branch_name}` : ''}
                {job.template_version ? ` · tpl ${job.template_version}` : ''}
              </div>
              {job.pr_url && (
                <a className="text-xs underline" href={job.pr_url} target="_blank" rel="noreferrer">
                  Ouvrir la PR
                </a>
              )}
              {job.error_message && <div className="text-xs text-red-300">{job.error_message}</div>}
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
