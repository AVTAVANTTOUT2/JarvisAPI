import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle2, Loader2, PlayCircle, RefreshCw, StopCircle } from 'lucide-react';
import { api } from '@unified/lib/api';
import type { FoodSelectorsReport, FoodSessionReport } from '@unified/lib/api';

/**
 * Diagnostic et réparation de l'installation Uber Eats.
 *
 * Sans cet écran, remettre l'intégration en marche après une expiration de
 * session ou une refonte du site imposerait un accès au terminal du Mac. La
 * capture ouvre bien une fenêtre sur la machine hôte : on la déclenche à
 * distance, on se connecte devant l'écran.
 */

interface Props {
  onChanged: () => void;
}

const CAPTURE_POLL_MS = 3000;

export function FoodDiagnosticsPanel({ onChanged }: Props) {
  const [session, setSession] = useState<FoodSessionReport | null>(null);
  const [selectors, setSelectors] = useState<FoodSelectorsReport | null>(null);
  const [probe, setProbe] = useState<{ ok: boolean; message: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [sessionData, selectorData] = await Promise.all([
        api.getFoodSession(),
        api.getFoodSelectors(),
      ]);
      setSession(sessionData);
      setSelectors(selectorData);
    } catch (e: any) {
      setError(e?.message || 'Diagnostic indisponible');
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Tant qu'une capture tourne, l'état est rafraîchi : la fenêtre s'est
  // peut-être fermée sur le Mac sans que ce navigateur en soit informé.
  useEffect(() => {
    if (!session?.capture.running) return;
    const timer = setInterval(load, CAPTURE_POLL_MS);
    return () => clearInterval(timer);
  }, [session?.capture.running, load]);

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      await load();
      onChanged();
    } catch (e: any) {
      setError(e?.message || 'Opération refusée');
    } finally {
      setBusy(false);
    }
  };

  const runProbe = async () => {
    setBusy(true);
    setProbe(null);
    try {
      const result = await api.probeFoodSession();
      setProbe({ ok: result.ok, message: result.message });
    } catch (e: any) {
      setProbe({ ok: false, message: e?.message || 'Sonde impossible' });
    } finally {
      setBusy(false);
    }
  };

  const capture = session?.capture;

  return (
    <div className="space-y-5">
      {error && (
        <div className="flex items-start gap-2 p-3 rounded-lg border border-red-400/20 bg-red-400/5 text-red-400 text-sm">
          <AlertTriangle size={15} className="mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      <section className="rounded-lg border border-white/5 p-4 space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-medium">Session Uber Eats</h3>
          {session && (
            <span
              className={`text-[10px] font-mono px-2 py-0.5 rounded border ${
                session.readable
                  ? 'border-emerald-500/30 text-emerald-400/80'
                  : 'border-amber-400/30 text-amber-400/80'
              }`}
            >
              {session.readable ? 'enregistrée' : 'absente'}
            </span>
          )}
        </div>
        {session && (
          <div className="text-xs font-mono text-white/40 space-y-0.5">
            <div className="truncate">{session.path}</div>
            {session.age_hours !== null && <div>capturée il y a {session.age_hours} h</div>}
          </div>
        )}

        {probe && (
          <div
            className={`flex items-start gap-2 text-sm ${
              probe.ok ? 'text-emerald-400' : 'text-amber-400'
            }`}
          >
            {probe.ok ? (
              <CheckCircle2 size={15} className="mt-0.5 shrink-0" />
            ) : (
              <AlertTriangle size={15} className="mt-0.5 shrink-0" />
            )}
            <span>{probe.message}</span>
          </div>
        )}

        <div className="flex flex-wrap gap-2">
          <button
            onClick={runProbe}
            disabled={busy}
            className="inline-flex items-center gap-1.5 rounded-lg border border-white/15 px-3 py-1.5
                       text-xs font-mono text-white/70 hover:bg-white/5 hover:text-white disabled:opacity-30"
          >
            {busy ? <Loader2 size={13} className="animate-spin" /> : <CheckCircle2 size={13} />}
            Tester la session
          </button>
          <button
            onClick={() => run(() => api.startFoodCapture('session'))}
            disabled={busy || capture?.running}
            className="inline-flex items-center gap-1.5 rounded-lg border border-white/15 px-3 py-1.5
                       text-xs font-mono text-white/70 hover:bg-white/5 hover:text-white disabled:opacity-30"
            title="Ouvre une fenêtre de connexion sur le Mac qui exécute JARVIS"
          >
            <PlayCircle size={13} />
            Capturer une session
          </button>
          <button
            onClick={() => run(() => api.startFoodCapture('codegen'))}
            disabled={busy || capture?.running}
            className="inline-flex items-center gap-1.5 rounded-lg border border-white/15 px-3 py-1.5
                       text-xs font-mono text-white/70 hover:bg-white/5 hover:text-white disabled:opacity-30"
            title="Capture la session et enregistre les sélecteurs réels"
          >
            <PlayCircle size={13} />
            Capturer + sélecteurs
          </button>
          {capture?.running && (
            <button
              onClick={() => run(() => api.stopFoodCapture())}
              disabled={busy}
              className="inline-flex items-center gap-1.5 rounded-lg border border-red-400/20 px-3 py-1.5
                         text-xs font-mono text-red-400/70 hover:bg-red-400/10 hover:text-red-400"
            >
              <StopCircle size={13} />
              Interrompre
            </button>
          )}
        </div>

        {capture?.running && (
          <div className="flex items-center gap-2 text-xs text-amber-400/80">
            <Loader2 size={13} className="animate-spin" />
            Fenêtre ouverte sur le Mac — s'y connecter puis la fermer.
          </div>
        )}
        {capture && !capture.running && capture.output && (
          <pre className="max-h-40 overflow-y-auto rounded border border-white/5 bg-black/30 p-2
                          text-[10px] font-mono text-white/50 whitespace-pre-wrap">
            {capture.output}
          </pre>
        )}
      </section>

      <section className="rounded-lg border border-white/5 p-4 space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-medium">Sélecteurs</h3>
          {selectors && (
            <span
              className={`text-[10px] font-mono px-2 py-0.5 rounded border ${
                selectors.verified
                  ? 'border-emerald-500/30 text-emerald-400/80'
                  : 'border-amber-400/30 text-amber-400/80'
              }`}
            >
              {selectors.verified ? 'vérifiés' : 'non vérifiés'}
            </span>
          )}
        </div>

        {selectors && !selectors.ok && (
          <div className="text-sm text-red-400">{selectors.error}</div>
        )}

        {selectors?.ok && (
          <>
            <div className="text-xs font-mono text-white/40 space-y-0.5">
              <div className="truncate">{selectors.path}</div>
              <div>capturés le {selectors.captured_at ?? 'jamais'}</div>
            </div>
            {!selectors.verified && (
              <p className="text-xs text-amber-400/70">
                Tant que le fichier n’est pas marqué vérifié, JARVIS ne clique sur aucun
                bouton de paiement. Le passage à vérifié se fait dans le fichier, après
                avoir contrôlé les sélecteurs relevés — délibérément pas d’un clic ici.
              </p>
            )}
            {selectors.missing_required.length > 0 && (
              <div className="text-xs text-red-400">
                Rôles obligatoires manquants : {selectors.missing_required.join(', ')}
              </div>
            )}
            {(selectors.missing_optional?.length ?? 0) > 0 && (
              <div className="text-xs text-white/35">
                Rôles optionnels absents : {selectors.missing_optional?.join(', ')}
              </div>
            )}
            <div className="flex flex-wrap gap-1">
              {Object.entries(selectors.roles).map(([role, count]) => (
                <span
                  key={role}
                  className={`rounded border px-1.5 py-0.5 text-[10px] font-mono ${
                    count > 0
                      ? 'border-white/10 text-white/45'
                      : 'border-amber-400/20 text-amber-400/60'
                  }`}
                >
                  {role} {count}
                </span>
              ))}
            </div>
          </>
        )}

        <button
          onClick={() => run(() => api.reloadFoodSelectors())}
          disabled={busy}
          className="inline-flex items-center gap-1.5 rounded-lg border border-white/15 px-3 py-1.5
                     text-xs font-mono text-white/70 hover:bg-white/5 hover:text-white disabled:opacity-30"
        >
          <RefreshCw size={13} />
          Relire le fichier
        </button>
      </section>
    </div>
  );
}
