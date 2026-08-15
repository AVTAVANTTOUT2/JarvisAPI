import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Check,
  Loader2,
  Play,
  RefreshCw,
  Trash2,
  Zap,
} from 'lucide-react';
import { api } from '@unified/lib/api';
import type {
  AppleShortcutInstalledRow,
  AppleShortcutPlan,
  AppleShortcutRecipe,
  AppleShortcutRegistryRow,
  AppleShortcutRunRow,
  AppleShortcutsStatus,
} from '@unified/lib/api';

type Tab = 'registry' | 'installed' | 'recipes' | 'runs';

const TABS: [Tab, string][] = [
  ['registry', 'Registre'],
  ['installed', 'Installés'],
  ['recipes', 'Recettes'],
  ['runs', 'Historique'],
];

const RISK_LABEL: Record<string, string> = {
  low: 'faible',
  medium: 'moyen',
  high: 'élevé',
};

export default function AppleShortcutsView() {
  const [tab, setTab] = useState<Tab>('registry');
  const [status, setStatus] = useState<AppleShortcutsStatus | null>(null);
  const [registry, setRegistry] = useState<AppleShortcutRegistryRow[]>([]);
  const [installed, setInstalled] = useState<AppleShortcutInstalledRow[]>([]);
  const [folders, setFolders] = useState<string[]>([]);
  const [folder, setFolder] = useState('');
  const [recipes, setRecipes] = useState<AppleShortcutRecipe[]>([]);
  const [runs, setRuns] = useState<AppleShortcutRunRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [pendingPlan, setPendingPlan] = useState<AppleShortcutPlan | null>(null);
  const [aliasDraft, setAliasDraft] = useState<Record<number, string>>({});
  const [manualName, setManualName] = useState('');
  const [manualAlias, setManualAlias] = useState('');

  const refresh = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [st, reg, rec, run] = await Promise.all([
        api.getAppleShortcutsStatus(),
        api.getAppleShortcutsRegistry(),
        api.getAppleShortcutsRecipes(),
        api.getAppleShortcutsRuns(30),
      ]);
      setStatus(st);
      setRegistry(reg.shortcuts);
      setRecipes(rec.recipes);
      setRuns(run.runs);
      if (st.available) {
        const inst = await api.getAppleShortcutsInstalled(folder || undefined);
        setInstalled(inst.shortcuts);
        setFolders(inst.folders);
      } else {
        setInstalled([]);
        setFolders([]);
      }
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : 'Chargement impossible';
      setError(message);
    } finally {
      setLoading(false);
    }
  }, [folder]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const unregistered = useMemo(
    () => installed.filter((row) => !row.registered),
    [installed],
  );

  const registerOne = async (name: string, alias = '') => {
    setBusy(name);
    setError('');
    setNotice('');
    try {
      await api.createAppleShortcutRegistry({
        name,
        alias: alias.trim(),
        risk: 'medium',
        requires_confirmation: true,
      });
      setNotice(`« ${name} » enregistré dans le registre.`);
      await refresh();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Enregistrement impossible');
    } finally {
      setBusy('');
    }
  };

  const registerAllVisible = async () => {
    if (!unregistered.length) return;
    setBusy('bulk');
    setError('');
    setNotice('');
    let ok = 0;
    try {
      for (const row of unregistered) {
        await api.createAppleShortcutRegistry({
          name: row.name,
          risk: 'medium',
          requires_confirmation: true,
        });
        ok += 1;
      }
      setNotice(`${ok} raccourci(s) ajouté(s) au registre.`);
      await refresh();
    } catch (e: unknown) {
      setError(
        e instanceof Error
          ? `${ok} ajouté(s), puis erreur : ${e.message}`
          : 'Import partiel',
      );
      await refresh();
    } finally {
      setBusy('');
    }
  };

  const toggleEnabled = async (row: AppleShortcutRegistryRow) => {
    setBusy(`en-${row.id}`);
    try {
      await api.updateAppleShortcutRegistry(row.id, { enabled: !row.enabled });
      await refresh();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Mise à jour impossible');
    } finally {
      setBusy('');
    }
  };

  const saveAlias = async (row: AppleShortcutRegistryRow) => {
    const alias = (aliasDraft[row.id] ?? row.alias).trim();
    setBusy(`alias-${row.id}`);
    try {
      await api.updateAppleShortcutRegistry(row.id, { alias });
      setNotice(`Alias mis à jour pour « ${row.name} ».`);
      await refresh();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Alias non enregistré');
    } finally {
      setBusy('');
    }
  };

  const removeRow = async (row: AppleShortcutRegistryRow) => {
    if (!window.confirm(`Retirer « ${row.name} » du registre JARVIS ?`)) return;
    setBusy(`del-${row.id}`);
    try {
      await api.deleteAppleShortcutRegistry(row.id);
      await refresh();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Suppression impossible');
    } finally {
      setBusy('');
    }
  };

  const prepareRun = async (row: AppleShortcutRegistryRow) => {
    setBusy(`run-${row.id}`);
    setError('');
    setNotice('');
    try {
      const plan = await api.prepareAppleShortcutRun({ registry_id: row.id });
      setPendingPlan(plan);
      setNotice(plan.message);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Préparation impossible');
    } finally {
      setBusy('');
    }
  };

  const confirmPending = async () => {
    if (!pendingPlan) return;
    setBusy('confirm');
    try {
      const result = await api.confirmAppleShortcutRun(pendingPlan.plan_id);
      setNotice(result.message || `Raccourci « ${result.shortcut_name} » exécuté.`);
      setPendingPlan(null);
      await refresh();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Exécution refusée');
    } finally {
      setBusy('');
    }
  };

  const cancelPending = async () => {
    if (!pendingPlan) return;
    setBusy('cancel');
    try {
      await api.cancelAppleShortcutRun(pendingPlan.plan_id);
      setPendingPlan(null);
      setNotice('Plan annulé.');
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Annulation impossible');
    } finally {
      setBusy('');
    }
  };

  return (
    <section className="mx-auto w-full max-w-5xl p-4 sm:p-8 space-y-6">
      <header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-xs font-mono uppercase tracking-[0.2em] text-muted-foreground">
            Apple · Raccourcis
          </p>
          <h1 className="mt-2 text-2xl font-semibold">Raccourcis personnalisés</h1>
          <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
            Registre allowlisté des raccourcis Shortcuts.app. Mail, Calendar et
            Messages restent en AppleScript ; ici seuls tes raccourcis natifs
            (HomeKit, Siri, automatisations) sont pilotables après confirmation.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void refresh()}
          className="inline-flex items-center gap-2 rounded-xl border border-white/15 px-3 py-2 text-sm text-muted-foreground hover:border-white/30"
        >
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          Actualiser
        </button>
      </header>

      <div className="grid gap-3 sm:grid-cols-4">
        <StatusTile
          label="Opt-in"
          value={status?.enabled ? 'activé' : 'désactivé'}
          tone={status?.enabled ? 'ok' : 'warn'}
        />
        <StatusTile
          label="CLI shortcuts"
          value={status?.available ? 'disponible' : 'indisponible'}
          tone={status?.available ? 'ok' : 'warn'}
        />
        <StatusTile
          label="Registre"
          value={`${status?.registry_enabled ?? 0}/${status?.registry_count ?? 0}`}
        />
        <StatusTile
          label="Ingest iOS"
          value={status?.ingest_configured ? 'jeton OK' : 'non configuré'}
          tone={status?.ingest_configured ? 'ok' : 'muted'}
        />
      </div>

      {!status?.enabled && (
        <p className="rounded-xl border border-amber-400/20 bg-amber-400/10 px-4 py-3 text-sm text-amber-100">
          Active <code className="font-mono">APPLE_SHORTCUTS_ENABLED=true</code> dans
          le <code className="font-mono">.env</code>, puis redémarre le backend.
        </p>
      )}

      {pendingPlan && (
        <div className="rounded-2xl border border-sky-400/30 bg-sky-400/10 p-4 space-y-3">
          <div className="flex items-start gap-3">
            <Zap size={18} className="mt-0.5 text-sky-200" />
            <div className="min-w-0 flex-1">
              <p className="font-medium text-sky-50">
                Confirmation — « {pendingPlan.shortcut_name} »
              </p>
              <p className="mt-1 text-xs text-sky-100/70">
                Risque {RISK_LABEL[pendingPlan.risk] || pendingPlan.risk}
                {pendingPlan.has_input
                  ? ` · entrée : ${pendingPlan.input_preview || '…'}`
                  : ''}
              </p>
            </div>
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => void confirmPending()}
              disabled={busy === 'confirm'}
              className="rounded-xl bg-white px-4 py-2 text-sm font-medium text-black disabled:opacity-50"
            >
              {busy === 'confirm' ? 'Exécution…' : 'Confirmer'}
            </button>
            <button
              type="button"
              onClick={() => void cancelPending()}
              className="rounded-xl border border-white/20 px-4 py-2 text-sm text-white/80"
            >
              Annuler
            </button>
          </div>
        </div>
      )}

      {error && (
        <p className="rounded-xl border border-red-400/20 bg-red-400/10 p-3 text-sm text-red-200">
          {error}
        </p>
      )}
      {notice && (
        <p className="rounded-xl border border-emerald-400/20 bg-emerald-400/10 p-3 text-sm text-emerald-100">
          {notice}
        </p>
      )}

      <div className="flex flex-wrap gap-2 border-b border-white/10 pb-3">
        {TABS.map(([id, label]) => (
          <button
            key={id}
            type="button"
            onClick={() => setTab(id)}
            className={`rounded-lg px-3 py-1.5 text-sm ${
              tab === id
                ? 'bg-white text-black'
                : 'text-muted-foreground hover:bg-white/5'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {loading && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 size={16} className="animate-spin" />
          Chargement…
        </div>
      )}

      {!loading && tab === 'registry' && (
        <div className="space-y-4">
          <form
            className="flex flex-col gap-2 rounded-2xl border border-white/10 bg-white/[0.02] p-4 sm:flex-row"
            onSubmit={(event) => {
              event.preventDefault();
              if (!manualName.trim()) return;
              void registerOne(manualName.trim(), manualAlias);
              setManualName('');
              setManualAlias('');
            }}
          >
            <input
              value={manualName}
              onChange={(e) => setManualName(e.target.value)}
              placeholder="Nom exact dans Raccourcis.app"
              className="min-w-0 flex-1 rounded-xl border border-white/15 bg-black/30 px-3 py-2 text-sm"
            />
            <input
              value={manualAlias}
              onChange={(e) => setManualAlias(e.target.value)}
              placeholder="Alias (ex. chambre)"
              className="w-full rounded-xl border border-white/15 bg-black/30 px-3 py-2 text-sm sm:w-40"
            />
            <button
              type="submit"
              className="rounded-xl bg-white px-4 py-2 text-sm font-medium text-black"
            >
              Ajouter
            </button>
          </form>

          {registry.length === 0 ? (
            <EmptyState text="Aucun raccourci enregistré. Importe-en depuis l’onglet Installés." />
          ) : (
            <ul className="space-y-3">
              {registry.map((row) => (
                <li
                  key={row.id}
                  className="rounded-2xl border border-white/10 bg-white/[0.02] p-4"
                >
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className="font-medium">{row.name}</h3>
                        <span className="rounded-md border border-white/15 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
                          {RISK_LABEL[row.risk] || row.risk}
                        </span>
                        {!row.enabled && (
                          <span className="text-[10px] uppercase text-amber-200/80">
                            désactivé
                          </span>
                        )}
                      </div>
                      <p className="mt-1 text-xs text-muted-foreground">
                        Alias : {row.alias || '—'}
                        {row.allow_input ? ' · accepte une entrée texte' : ''}
                        {' · confirmation toujours exigée'}
                      </p>
                      <div className="mt-3 flex flex-wrap items-center gap-2">
                        <input
                          value={aliasDraft[row.id] ?? row.alias}
                          onChange={(e) =>
                            setAliasDraft((prev) => ({
                              ...prev,
                              [row.id]: e.target.value,
                            }))
                          }
                          placeholder="Alias vocal"
                          className="w-40 rounded-lg border border-white/15 bg-black/30 px-2 py-1 text-xs"
                        />
                        <button
                          type="button"
                          onClick={() => void saveAlias(row)}
                          className="rounded-lg border border-white/15 px-2 py-1 text-xs"
                        >
                          Sauver alias
                        </button>
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <button
                        type="button"
                        onClick={() => void prepareRun(row)}
                        disabled={!row.enabled || !status?.available || Boolean(busy)}
                        className="inline-flex items-center gap-1.5 rounded-xl border border-white/15 px-3 py-1.5 text-xs disabled:opacity-40"
                      >
                        <Play size={12} />
                        Lancer
                      </button>
                      <button
                        type="button"
                        onClick={() => void toggleEnabled(row)}
                        className="rounded-xl border border-white/15 px-3 py-1.5 text-xs"
                      >
                        {row.enabled ? 'Désactiver' : 'Activer'}
                      </button>
                      <button
                        type="button"
                        onClick={() => void removeRow(row)}
                        className="inline-flex items-center gap-1 rounded-xl border border-red-400/30 px-3 py-1.5 text-xs text-red-200"
                      >
                        <Trash2 size={12} />
                        Retirer
                      </button>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {!loading && tab === 'installed' && (
        <div className="space-y-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex flex-wrap items-center gap-2">
              <label className="text-xs text-muted-foreground">Dossier</label>
              <select
                value={folder}
                onChange={(e) => setFolder(e.target.value)}
                className="rounded-lg border border-white/15 bg-black/30 px-2 py-1.5 text-sm"
              >
                <option value="">Tous</option>
                {folders.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </div>
            <button
              type="button"
              onClick={() => void registerAllVisible()}
              disabled={!unregistered.length || busy === 'bulk'}
              className="rounded-xl bg-white px-4 py-2 text-sm font-medium text-black disabled:opacity-40"
            >
              {busy === 'bulk'
                ? 'Import…'
                : `Enregistrer les ${unregistered.length} non listés`}
            </button>
          </div>

          {!status?.available ? (
            <EmptyState text="CLI shortcuts indisponible (macOS + APPLE_SHORTCUTS_ENABLED requis)." />
          ) : installed.length === 0 ? (
            <EmptyState text="Aucun raccourci trouvé sur cette machine." />
          ) : (
            <ul className="space-y-2">
              {installed.map((row) => (
                <li
                  key={row.name}
                  className="flex items-center justify-between gap-3 rounded-xl border border-white/10 px-3 py-2.5"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{row.name}</p>
                    <p className="text-[11px] text-muted-foreground">
                      {row.registered ? 'Déjà dans le registre' : 'Non enregistré'}
                    </p>
                  </div>
                  {row.registered ? (
                    <span className="inline-flex items-center gap-1 text-xs text-emerald-200/80">
                      <Check size={12} /> OK
                    </span>
                  ) : (
                    <button
                      type="button"
                      onClick={() => void registerOne(row.name)}
                      disabled={busy === row.name}
                      className="rounded-lg border border-white/15 px-3 py-1 text-xs disabled:opacity-40"
                    >
                      Enregistrer
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {!loading && tab === 'recipes' && (
        <ul className="space-y-3">
          {recipes.map((recipe) => (
            <li
              key={recipe.id}
              className="rounded-2xl border border-white/10 bg-white/[0.02] p-4 space-y-3"
            >
              <div>
                <h3 className="font-medium">{recipe.title}</h3>
                <p className="mt-1 text-sm text-muted-foreground">{recipe.summary}</p>
                <p className="mt-2 text-[11px] font-mono text-white/40">
                  {recipe.endpoint.method} {recipe.endpoint.path} · {recipe.auth}
                </p>
              </div>
              {recipe.triggers && recipe.triggers.length > 0 && (
                <p className="text-xs text-muted-foreground">
                  Déclencheurs : {recipe.triggers.join(' · ')}
                </p>
              )}
              <ol className="list-decimal space-y-1 pl-5 text-xs text-white/65">
                {recipe.steps.map((step) => (
                  <li key={step}>{step}</li>
                ))}
              </ol>
              {recipe.requires.length > 0 && (
                <p className="text-[11px] text-amber-100/70">
                  Requis : {recipe.requires.join(' · ')}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}

      {!loading && tab === 'runs' && (
        <div className="space-y-2">
          {runs.length === 0 ? (
            <EmptyState text="Aucune exécution enregistrée." />
          ) : (
            runs.map((run) => (
              <article
                key={run.id}
                className="rounded-xl border border-white/10 px-3 py-2.5 text-sm"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className={run.ok ? 'text-emerald-200' : 'text-red-200'}>
                    {run.ok ? 'OK' : 'Échec'}
                  </span>
                  <span className="font-medium">{run.shortcut_name}</span>
                  <span className="text-[11px] text-muted-foreground">
                    {run.created_at}
                  </span>
                </div>
                {(run.output_preview || run.error) && (
                  <p className="mt-1 text-xs text-muted-foreground break-all">
                    {run.error || run.output_preview}
                  </p>
                )}
              </article>
            ))
          )}
        </div>
      )}
    </section>
  );
}

function StatusTile({
  label,
  value,
  tone = 'muted',
}: {
  label: string
  value: string
  tone?: 'ok' | 'warn' | 'muted'
}) {
  const toneClass =
    tone === 'ok'
      ? 'border-emerald-400/20 bg-emerald-400/5'
      : tone === 'warn'
        ? 'border-amber-400/20 bg-amber-400/5'
        : 'border-white/10 bg-white/[0.02]';
  return (
    <div className={`rounded-2xl border px-4 py-3 ${toneClass}`}>
      <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
        {label}
      </p>
      <p className="mt-1 text-sm font-medium">{value}</p>
    </div>
  );
}

function EmptyState({ text }: { text: string }) {
  return (
    <div className="rounded-2xl border border-dashed border-white/15 p-8 text-center text-sm text-muted-foreground">
      {text}
    </div>
  );
}
