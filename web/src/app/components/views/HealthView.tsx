/**
 * HealthView — état de santé unifié de JARVIS.
 *
 * Trois sources, aucune duplication :
 *  - `GET /api/health/detail` : agrégation des composants (backend, SQLite,
 *    bus d'événements, ressources, STT, TTS) produite par `jarvis/health.py` ;
 *  - `GET /api/voice/metrics` : latences p50/p95 du pipeline vocal, déjà
 *    exposées par le backend — la page les affiche, elle ne les recalcule pas.
 *  - `GET /api/metrics/history` : historique compacté des diagnostics santé,
 *    conservé localement selon la politique de rétention du serveur.
 *
 * Rafraîchissement : polling borné, pas de SSE. Le flux `/api/events/stream`
 * transporte des événements de domaine (tâches, notifications, mémoire) ; un
 * état de composant n'en est pas un et n'y est jamais émis. S'y abonner
 * obligerait à publier l'état de santé sur un canal que d'autres consomment,
 * pour un gain nul face à un intervalle de quinze secondes. Le relevé serveur
 * est mutualisé cinq secondes, donc plusieurs onglets ne multiplient pas le
 * coût réel.
 *
 * Le timer est suspendu quand l'onglet est masqué, et chaque requête en vol
 * est annulée au démontage : ni timer, ni requête, ni abonnement ne survit à
 * la page.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  CircleHelp,
  Database,
  Gauge,
  Mic,
  RefreshCw,
  Radio,
  Server,
  Volume2,
  XCircle,
} from 'lucide-react';
import {
  api,
  type HealthComponent,
  type HealthReport,
  type HealthState,
  type MetricHistoryResponse,
  type MetricHistorySeries,
  type VoiceLatencyMetrics,
} from '@unified/lib/api';

/** Intervalle de polling. Assez court pour être utile, assez long pour ne pas peser. */
export const HEALTH_POLL_INTERVAL_MS = 15_000;

const STATE_LABELS: Record<HealthState, string> = {
  healthy: 'Opérationnel',
  degraded: 'Dégradé',
  unavailable: 'Indisponible',
  unknown: 'Non vérifié',
};

const STATE_CLASSES: Record<HealthState, string> = {
  healthy: 'text-emerald-400 border-emerald-500/30 bg-emerald-500/10',
  degraded: 'text-amber-400 border-amber-500/30 bg-amber-500/10',
  unavailable: 'text-red-400 border-red-500/30 bg-red-500/10',
  unknown: 'text-slate-400 border-slate-500/30 bg-slate-500/10',
};

const COMPONENT_LABELS: Record<string, string> = {
  backend: 'Backend',
  database: 'Base SQLite',
  event_bus: "Bus d'événements",
  resources: 'Ressources mémoire',
  speech_to_text: 'Reconnaissance vocale',
  text_to_speech: 'Synthèse vocale',
};

const COMPONENT_ICONS: Record<string, React.ComponentType<{ size?: number; className?: string }>> = {
  backend: Server,
  database: Database,
  event_bus: Radio,
  resources: Gauge,
  speech_to_text: Mic,
  text_to_speech: Volume2,
};

/**
 * Traductions des codes serveur. Un code inconnu est affiché tel quel plutôt
 * que masqué : un frontend en retard doit rester lisible, pas muet.
 */
const REASON_LABELS: Record<string, string> = {
  internal_error: 'Erreur interne — voir les journaux serveur',
  probe_timeout: 'Sonde expirée',
  database_unreachable: 'Base inaccessible',
  database_query_failed: 'Requête de base en échec',
  event_bus_loop_unbound: "Bus non lié à la boucle applicative",
  event_bus_queue_saturated: 'File du bus saturée',
  resource_guard_disabled: 'Garde-fou ressources désactivé',
  memory_probe_unavailable: 'Mémoire non mesurable sur cette machine',
  memory_low: 'Mémoire libre sous le seuil d’alerte',
  memory_critical: 'Mémoire libre sous le seuil critique',
  stt_unavailable: 'Moteur de transcription indisponible',
  tts_provider_misconfigured: 'Fournisseur vocal mal configuré',
  tts_engine_not_probed: 'Moteur non exercé depuis le démarrage',
};

const DETAIL_LABELS: Record<string, string> = {
  uptime_s: 'Uptime',
  latency_ms: 'Latence',
  journal_mode: 'Journal',
  subscribers: 'Abonnés',
  handler_queues: 'Files',
  queued_events: 'Événements en file',
  saturated_queues: 'Files saturées',
  loop_bound: 'Boucle liée',
  free_mb: 'Mémoire libre',
  warn_free_mb: 'Seuil alerte',
  critical_free_mb: 'Seuil critique',
  engine: 'Moteur',
  provider: 'Fournisseur',
  backend: 'Backend',
  device: 'Accélérateur',
  streaming: 'Diffusion',
  offline: 'Hors ligne',
};

const VOICE_STAGE_LABELS: Record<string, string> = {
  stt: 'Transcription',
  llm_pass1: 'LLM (1re passe)',
  llm_pass2: 'LLM (2e passe)',
  tts: 'Synthèse',
  total: 'Tour complet',
};

const HISTORY_METRICS: Record<string, string> = {
  'health.score': 'Disponibilité',
  'health.duration_ms': 'Durée du diagnostic',
  'health.database.latency_ms': 'Latence SQLite',
};

function formatMetricValue(value: number, unit: string): string {
  const formatted = Number.isInteger(value) ? String(value) : value.toFixed(1);
  if (unit === 'percent') return `${formatted} %`;
  if (unit === 'ms') return `${formatted} ms`;
  return unit ? `${formatted} ${unit}` : formatted;
}

function MetricSparkline({ series }: { series: MetricHistorySeries }) {
  const width = 240;
  const height = 72;
  const padding = 6;
  const values = series.points.map((point) => point.value);
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const spread = maximum - minimum;
  const coordinates = series.points
    .map((point, index) => {
      const x =
        series.points.length === 1
          ? width / 2
          : padding + (index / (series.points.length - 1)) * (width - padding * 2);
      const y =
        spread === 0
          ? height / 2
          : padding + ((maximum - point.value) / spread) * (height - padding * 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={`Évolution de ${HISTORY_METRICS[series.metric] ?? series.metric}`}
      className="h-[72px] w-full"
    >
      <line
        x1={padding}
        y1={height / 2}
        x2={width - padding}
        y2={height / 2}
        stroke="currentColor"
        className="text-white/5"
      />
      <polyline
        points={coordinates}
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="text-cyan-400"
      />
    </svg>
  );
}

function MetricHistoryPanel({ history }: { history: MetricHistoryResponse | null }) {
  const selected = (history?.series ?? []).filter(
    (series) => HISTORY_METRICS[series.metric] && series.points.length > 0,
  );

  return (
    <section className="mt-8">
      <h2 className="mb-3 text-sm font-semibold text-slate-300">
        Tendances opérationnelles
        <span className="ml-2 text-xs font-normal text-slate-500">
          source : /api/metrics/history — {history?.hours ?? 24} dernières heures
        </span>
      </h2>
      {selected.length === 0 ? (
        <p data-testid="metric-history-empty" className="font-mono text-xs text-slate-500">
          L’historique se constituera au fil des diagnostics.
        </p>
      ) : (
        <div data-testid="metric-history" className="grid gap-3 md:grid-cols-3">
          {selected.map((series) => {
            const trend = series.summary.trend_pct;
            const trendLabel = trend === null ? 'tendance initiale' : `${trend > 0 ? '+' : ''}${trend} %`;
            return (
              <article key={series.metric} className="rounded-lg border border-white/10 bg-white/[0.02] p-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <h3 className="text-xs font-medium text-slate-300">
                      {HISTORY_METRICS[series.metric]}
                    </h3>
                    <p className="mt-1 font-mono text-lg text-slate-100">
                      {formatMetricValue(series.summary.latest, series.unit)}
                    </p>
                  </div>
                  <span className="font-mono text-[11px] text-slate-500">{trendLabel}</span>
                </div>
                <MetricSparkline series={series} />
                <p className="mt-1 text-[11px] text-slate-500">
                  Moyenne {formatMetricValue(series.summary.average, series.unit)} · {series.summary.samples}{' '}
                  échantillon(s)
                </p>
              </article>
            );
          })}
        </div>
      )}
      {history && (
        <p className="mt-2 text-[11px] text-slate-600">Rétention locale : {history.retention_days} jours.</p>
      )}
    </section>
  );
}

function StateIcon({ state, size = 16 }: { state: HealthState; size?: number }) {
  if (state === 'healthy') return <CheckCircle2 size={size} className="text-emerald-400" />;
  if (state === 'degraded') return <AlertTriangle size={size} className="text-amber-400" />;
  if (state === 'unavailable') return <XCircle size={size} className="text-red-400" />;
  return <CircleHelp size={size} className="text-slate-400" />;
}

export function formatDetailValue(key: string, value: string | number | boolean | null): string {
  if (value === null) return '—';
  if (typeof value === 'boolean') return value ? 'oui' : 'non';
  if (typeof value === 'number') {
    if (key === 'uptime_s') {
      const total = Math.max(0, Math.round(value));
      const hours = Math.floor(total / 3600);
      const minutes = Math.floor((total % 3600) / 60);
      if (hours > 0) return `${hours} h ${minutes} min`;
      if (minutes > 0) return `${minutes} min`;
      return `${total} s`;
    }
    if (key.endsWith('_ms')) return `${value} ms`;
    if (key.endsWith('_mb')) return `${Math.round(value)} Mo`;
    return String(value);
  }
  return value;
}

function ComponentCard({ component }: { component: HealthComponent }) {
  const Icon = COMPONENT_ICONS[component.name] ?? Activity;
  const label = COMPONENT_LABELS[component.name] ?? component.name;
  const entries = Object.entries(component.details);

  return (
    <article
      data-testid={`health-component-${component.name}`}
      data-state={component.state}
      className="rounded-lg border border-white/10 bg-white/[0.02] p-4"
    >
      <header className="flex items-start justify-between gap-3">
        <span className="inline-flex items-center gap-2 text-sm font-medium text-slate-200">
          <Icon size={15} className="text-slate-400" />
          {label}
          {component.critical && (
            <span className="rounded border border-white/10 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-slate-400">
              critique
            </span>
          )}
        </span>
        <span
          className={`inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-mono ${STATE_CLASSES[component.state] ?? STATE_CLASSES.unknown}`}
        >
          <StateIcon state={component.state} size={12} />
          {STATE_LABELS[component.state] ?? component.state}
        </span>
      </header>

      {component.reason && (
        <p className="mt-2 text-xs text-slate-400">
          {REASON_LABELS[component.reason] ?? component.reason}
        </p>
      )}

      {entries.length > 0 && (
        <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
          {entries.map(([key, value]) => (
            <div key={key} className="flex justify-between gap-2">
              <dt className="text-slate-500">{DETAIL_LABELS[key] ?? key}</dt>
              <dd className="font-mono text-slate-300">{formatDetailValue(key, value)}</dd>
            </div>
          ))}
        </dl>
      )}
    </article>
  );
}

export function HealthView() {
  const [report, setReport] = useState<HealthReport | null>(null);
  const [voice, setVoice] = useState<VoiceLatencyMetrics | null>(null);
  const [history, setHistory] = useState<MetricHistoryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Une seule requête en vol : le démontage — ou un rafraîchissement manuel
  // pendant un tick de polling — annule la précédente au lieu de la laisser
  // écrire dans un composant disparu.
  const abortRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);

  const load = useCallback(async (opts?: { refresh?: boolean }) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    if (opts?.refresh) setRefreshing(true);
    try {
      const [health, metrics, metricHistory] = await Promise.all([
        api.getHealthDetail({ refresh: opts?.refresh, signal: controller.signal }),
        api.getVoiceMetrics(7, controller.signal).catch(() => null),
        api.getMetricHistory(24, controller.signal).catch(() => null),
      ]);
      if (controller.signal.aborted || !mountedRef.current) return;
      setReport(health);
      setVoice(metrics);
      setHistory(metricHistory);
      setError(null);
    } catch (e) {
      if (controller.signal.aborted || !mountedRef.current) return;
      // Un 503 porte quand même le rapport : le backend répond « dégradé »,
      // pas « rien ». Seule une erreur de transport vide l'écran.
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (mountedRef.current && !controller.signal.aborted) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    void load();

    let timer: ReturnType<typeof setInterval> | null = null;
    const start = () => {
      if (timer === null) timer = setInterval(() => void load(), HEALTH_POLL_INTERVAL_MS);
    };
    const stop = () => {
      if (timer !== null) {
        clearInterval(timer);
        timer = null;
      }
    };
    const onVisibility = () => {
      if (typeof document !== 'undefined' && document.hidden) {
        stop();
      } else {
        void load();
        start();
      }
    };

    start();
    document.addEventListener('visibilitychange', onVisibility);

    return () => {
      mountedRef.current = false;
      stop();
      document.removeEventListener('visibilitychange', onVisibility);
      abortRef.current?.abort();
    };
  }, [load]);

  const status = report?.status ?? 'unknown';
  const voiceStages = Object.entries(voice?.stages ?? {}).filter(([, stage]) => stage.count > 0);

  return (
    <div className="h-full overflow-y-auto p-6" data-testid="health-view">
      <header className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2 text-lg font-semibold text-slate-100">
            <Activity size={18} className="text-slate-400" />
            Santé du système
          </h1>
          <p className="mt-1 text-xs text-slate-500">
            Composants vérifiables agrégés côté serveur. Aucun appel réseau externe.
          </p>
        </div>

        <div className="flex items-center gap-3">
          {report && (
            <span
              data-testid="health-overall"
              data-state={status}
              className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-sm font-mono ${STATE_CLASSES[status] ?? STATE_CLASSES.unknown}`}
            >
              <StateIcon state={status} />
              {STATE_LABELS[status] ?? status}
            </span>
          )}
          <button
            type="button"
            onClick={() => void load({ refresh: true })}
            disabled={refreshing}
            className="inline-flex items-center gap-2 rounded border border-white/10 px-3 py-1.5 text-xs text-slate-300 hover:bg-white/5 disabled:opacity-50"
          >
            <RefreshCw size={13} className={refreshing ? 'animate-spin' : undefined} />
            Resonder
          </button>
        </div>
      </header>

      {loading && !report && (
        <p data-testid="health-loading" className="font-mono text-sm text-slate-500">
          Relevé en cours…
        </p>
      )}

      {error && (
        <div
          data-testid="health-error"
          className="mb-6 rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-300"
        >
          <p className="font-medium">Diagnostic indisponible</p>
          <p className="mt-1 text-xs text-red-300/80">{error}</p>
        </div>
      )}

      {report && (
        <>
          <p className="mb-4 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500">
            <span data-testid="health-checked-at">
              Dernier relevé : <span className="font-mono text-slate-400">{report.checked_at}</span>
            </span>
            <span>
              Durée : <span className="font-mono text-slate-400">{report.duration_ms} ms</span>
            </span>
            <span>
              {report.summary.healthy} opérationnel(s) · {report.summary.degraded} dégradé(s) ·{' '}
              {report.summary.unavailable} indisponible(s) · {report.summary.unknown} non vérifié(s)
            </span>
          </p>

          {report.components.length === 0 ? (
            <p data-testid="health-empty" className="font-mono text-sm text-slate-500">
              Aucun composant remonté par le serveur.
            </p>
          ) : (
            <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {report.components.map((component) => (
                <ComponentCard key={component.name} component={component} />
              ))}
            </section>
          )}
        </>
      )}

      <MetricHistoryPanel history={history} />

      <section className="mt-8">
        <h2 className="mb-3 text-sm font-semibold text-slate-300">
          Latences du pipeline vocal
          <span className="ml-2 text-xs font-normal text-slate-500">
            source : /api/voice/metrics — 7 derniers jours
          </span>
        </h2>
        {voiceStages.length === 0 ? (
          <p data-testid="voice-metrics-empty" className="font-mono text-xs text-slate-500">
            Aucun tour de parole mesuré sur la période.
          </p>
        ) : (
          <div data-testid="voice-metrics" className="overflow-x-auto">
            <table className="w-full min-w-[420px] text-left text-xs">
              <thead className="text-slate-500">
                <tr>
                  <th className="py-1 pr-4 font-normal">Étape</th>
                  <th className="py-1 pr-4 font-normal">p50</th>
                  <th className="py-1 pr-4 font-normal">p95</th>
                  <th className="py-1 font-normal">Échantillons</th>
                </tr>
              </thead>
              <tbody className="font-mono text-slate-300">
                {voiceStages.map(([name, stage]) => (
                  <tr key={name} className="border-t border-white/5">
                    <td className="py-1.5 pr-4 font-sans text-slate-400">
                      {VOICE_STAGE_LABELS[name] ?? name}
                    </td>
                    <td className="py-1.5 pr-4">{stage.p50_ms} ms</td>
                    <td className="py-1.5 pr-4">{stage.p95_ms} ms</td>
                    <td className="py-1.5">{stage.count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

export default HealthView;
