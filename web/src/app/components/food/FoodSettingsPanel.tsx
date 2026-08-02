import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, Loader2, RotateCcw } from 'lucide-react';
import { api } from '@unified/lib/api';
import type { FoodSettings, FoodSettingsCeilings } from '@unified/lib/api';

/**
 * Réglages pilotables depuis le navigateur.
 *
 * Chaque valeur est bornée par le fichier `.env` de la machine : l'interface
 * peut resserrer une limite, jamais l'élargir. Les bornes sont affichées à
 * côté de chaque champ pour que le refus soit compréhensible avant l'envoi.
 */

interface Props {
  onChanged: () => void;
}

const BOOLEAN_LABELS: Record<string, { label: string; help: string }> = {
  enabled: {
    label: 'Intégration active',
    help: 'Sans elle, aucun panier ne peut être construit.',
  },
  dry_run: {
    label: 'Mode simulation',
    help: 'Le bouton de paiement n’est jamais cliqué.',
  },
  menu_scrape_enabled: {
    label: 'Relevé des menus',
    help: 'Lecture seule des pages restaurant et du suivi de livraison.',
  },
  suggestions_enabled: {
    label: 'Suggestions du jour',
    help: 'Génère trois propositions par service.',
  },
  headless: {
    label: 'Navigateur invisible',
    help: 'Décocher pour voir la fenêtre pendant un diagnostic.',
  },
};

const NUMBER_LABELS: Record<string, { label: string; unit: string; step: number }> = {
  max_order_price: { label: 'Plafond par commande', unit: '€', step: 1 },
  max_daily_spend: { label: 'Plafond par jour', unit: '€', step: 1 },
  max_daily_orders: { label: 'Commandes par jour', unit: '', step: 1 },
  max_items: { label: 'Articles distincts', unit: '', step: 1 },
  max_item_quantity: { label: 'Quantité par article', unit: '', step: 1 },
};

export function FoodSettingsPanel({ onChanged }: Props) {
  const [settings, setSettings] = useState<FoodSettings | null>(null);
  const [ceilings, setCeilings] = useState<FoodSettingsCeilings | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const data = await api.getFoodSettings();
      setSettings(data.settings);
      setCeilings(data.ceilings);
    } catch (e: any) {
      setError(e?.message || 'Réglages illisibles');
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const apply = async (patch: Partial<FoodSettings>) => {
    setBusy(true);
    setError(null);
    try {
      const data = await api.updateFoodSettings(patch);
      setSettings(data.settings);
      setCeilings(data.ceilings);
      onChanged();
    } catch (e: any) {
      setError(e?.message || 'Réglage refusé');
      load();
    } finally {
      setBusy(false);
    }
  };

  const reset = async () => {
    setBusy(true);
    try {
      const data = await api.resetFoodSettings();
      setSettings(data.settings);
      setCeilings(data.ceilings);
      onChanged();
    } catch (e: any) {
      setError(e?.message || 'Réinitialisation impossible');
    } finally {
      setBusy(false);
    }
  };

  if (!settings || !ceilings) {
    return (
      <div className="flex items-center gap-2 text-sm text-white/40">
        <Loader2 size={14} className="animate-spin" />
        Chargement des réglages…
      </div>
    );
  }

  const dryRunLocked = ceilings.dry_run_forced;

  return (
    <div className="space-y-5">
      {error && (
        <div className="flex items-start gap-2 p-3 rounded-lg border border-red-400/20 bg-red-400/5 text-red-400 text-sm">
          <AlertTriangle size={15} className="mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      <div className="space-y-2">
        {(Object.keys(BOOLEAN_LABELS) as (keyof typeof BOOLEAN_LABELS)[]).map((key) => {
          const value = settings[key as keyof FoodSettings] as boolean;
          const envAllows =
            key === 'dry_run' ? !dryRunLocked : (ceilings[key as keyof FoodSettingsCeilings] as boolean);
          const locked = busy || !envAllows;
          return (
            <label
              key={key}
              className={`flex items-start gap-3 rounded-lg border border-white/5 p-3 ${
                locked ? 'opacity-50' : 'hover:border-white/15'
              }`}
            >
              <input
                type="checkbox"
                checked={value}
                disabled={locked}
                onChange={(event) => apply({ [key]: event.target.checked } as Partial<FoodSettings>)}
                className="mt-0.5 accent-white"
              />
              <span className="min-w-0">
                <span className="block text-sm">{BOOLEAN_LABELS[key].label}</span>
                <span className="block text-xs text-white/35">{BOOLEAN_LABELS[key].help}</span>
                {!envAllows && (
                  <span className="mt-1 block text-[10px] font-mono text-amber-400/70">
                    {key === 'dry_run'
                      ? 'Verrouillé : UBER_EATS_DRY_RUN=true dans .env'
                      : 'Verrouillé : interrupteur fermé dans .env'}
                  </span>
                )}
              </span>
            </label>
          );
        })}
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        {(Object.keys(NUMBER_LABELS) as (keyof typeof NUMBER_LABELS)[]).map((key) => {
          const value = settings[key as keyof FoodSettings] as number;
          const ceiling = ceilings[key as keyof FoodSettingsCeilings] as number;
          const meta = NUMBER_LABELS[key];
          return (
            <label key={key} className="block rounded-lg border border-white/5 p-3">
              <span className="block text-sm">{meta.label}</span>
              <span className="mt-2 flex items-center gap-2">
                <input
                  type="number"
                  min={0}
                  max={ceiling}
                  step={meta.step}
                  defaultValue={value}
                  disabled={busy}
                  onBlur={(event) => {
                    const next = Number(event.target.value);
                    if (!Number.isNaN(next) && next !== value) {
                      apply({ [key]: next } as Partial<FoodSettings>);
                    }
                  }}
                  className="w-28 rounded-lg border border-white/10 bg-black/30 px-2 py-1
                             text-sm font-mono text-white/90 focus:border-white/30 focus:outline-none"
                />
                {meta.unit && <span className="text-xs text-white/40">{meta.unit}</span>}
                <span className="text-[10px] font-mono text-white/30">
                  borne .env {ceiling}
                  {meta.unit}
                </span>
              </span>
            </label>
          );
        })}
      </div>

      <button
        onClick={reset}
        disabled={busy}
        className="inline-flex items-center gap-1.5 rounded-lg border border-white/15 px-3 py-1.5
                   text-xs font-mono text-white/60 hover:bg-white/5 hover:text-white disabled:opacity-30"
      >
        <RotateCcw size={13} />
        Revenir aux valeurs du .env
      </button>
    </div>
  );
}
