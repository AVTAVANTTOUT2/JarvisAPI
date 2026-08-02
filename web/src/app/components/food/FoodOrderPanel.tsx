import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, Loader2, Minus, Plus, ShoppingCart, X } from 'lucide-react';
import { api } from '@unified/lib/api';
import type { FoodCartPlan, FoodMenuItem, FoodMenuSummary } from '@unified/lib/api';

/**
 * Composition d'un panier libre, en deux passes.
 *
 * La première passe construit le panier chez Uber et lit le total réel : rien
 * n'est engagé tant que l'utilisateur n'a pas vu ce montant. La seconde
 * consomme le plan une seule fois. C'est le chemin normal de l'intégration,
 * pas un raccourci ouvert à l'interface web.
 */

interface CartLine {
  name: string;
  quantity: number;
}

interface Props {
  menus: FoodMenuSummary[];
  maxItems: number;
  maxItemQuantity: number;
  onOrdered: () => void;
}

function formatAmount(value: number | null | undefined, currency: string): string {
  if (value === null || value === undefined) return '—';
  return `${value.toFixed(2)} ${currency === 'EUR' ? '€' : currency}`;
}

export function FoodOrderPanel({ menus, maxItems, maxItemQuantity, onOrdered }: Props) {
  const [restaurant, setRestaurant] = useState('');
  const [lines, setLines] = useState<CartLine[]>([]);
  const [draft, setDraft] = useState('');
  const [menuItems, setMenuItems] = useState<FoodMenuItem[]>([]);
  const [plan, setPlan] = useState<FoodCartPlan | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadMenu = useCallback(async (name: string) => {
    setMenuItems([]);
    if (!name.trim()) return;
    try {
      const data = await api.getFoodMenuItems(name);
      setMenuItems(data.items ?? []);
    } catch {
      // Aucun menu en cache : la saisie libre reste possible.
      setMenuItems([]);
    }
  }, []);

  useEffect(() => {
    loadMenu(restaurant);
  }, [restaurant, loadMenu]);

  const addLine = (name: string) => {
    const clean = name.trim();
    if (!clean) return;
    setLines((current) => {
      const existing = current.find((line) => line.name === clean);
      if (existing) {
        return current.map((line) =>
          line.name === clean
            ? { ...line, quantity: Math.min(line.quantity + 1, maxItemQuantity) }
            : line,
        );
      }
      if (current.length >= maxItems) return current;
      return [...current, { name: clean, quantity: 1 }];
    });
    setDraft('');
  };

  const changeQuantity = (name: string, delta: number) => {
    setLines((current) =>
      current
        .map((line) =>
          line.name === name
            ? { ...line, quantity: Math.min(Math.max(line.quantity + delta, 0), maxItemQuantity) }
            : line,
        )
        .filter((line) => line.quantity > 0),
    );
  };

  const prepare = async () => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      setPlan(await api.prepareFoodCart(restaurant.trim(), lines));
    } catch (e: any) {
      setError(e?.message || 'Panier impossible à construire');
    } finally {
      setBusy(false);
    }
  };

  const confirm = async () => {
    if (!plan) return;
    setBusy(true);
    setError(null);
    try {
      const outcome = await api.confirmFoodCart(plan.plan_id);
      setPlan(null);
      setLines([]);
      setNotice(
        outcome.dry_run
          ? `Simulation : ${outcome.items_label} chez ${outcome.restaurant}, ${formatAmount(outcome.total_price, outcome.currency)}.`
          : `Commandé chez ${outcome.restaurant} — ${formatAmount(outcome.total_price, outcome.currency)}.`,
      );
      onOrdered();
    } catch (e: any) {
      setError(e?.message || 'Commande refusée');
    } finally {
      setBusy(false);
    }
  };

  const cancel = async () => {
    if (!plan) return;
    setBusy(true);
    try {
      await api.cancelFoodCart(plan.plan_id);
      setPlan(null);
      setNotice('Panier abandonné, rien n’a été commandé.');
    } catch (e: any) {
      setError(e?.message || 'Abandon impossible');
    } finally {
      setBusy(false);
    }
  };

  const canPrepare = restaurant.trim().length > 0 && lines.length > 0 && !busy && !plan;

  return (
    <div className="space-y-4">
      {error && (
        <div className="flex items-center gap-2 p-3 rounded-lg border border-red-400/20 bg-red-400/5 text-red-400 text-sm">
          <AlertTriangle size={15} />
          {error}
        </div>
      )}
      {notice && (
        <div className="p-3 rounded-lg border border-white/10 bg-white/5 text-sm text-white/70">
          {notice}
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        <div className="space-y-3">
          <label className="block text-xs font-mono uppercase tracking-wide text-white/40">
            Restaurant
            <input
              value={restaurant}
              onChange={(event) => setRestaurant(event.target.value)}
              list="food-known-restaurants"
              placeholder="Nom exact tel qu'affiché sur Uber Eats"
              disabled={plan !== null}
              className="mt-1 w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2
                         text-sm font-sans text-white/90 placeholder:text-white/25
                         focus:border-white/30 focus:outline-none disabled:opacity-40"
            />
          </label>
          <datalist id="food-known-restaurants">
            {menus.map((menu) => (
              <option key={menu.restaurant} value={menu.restaurant} />
            ))}
          </datalist>

          <label className="block text-xs font-mono uppercase tracking-wide text-white/40">
            Article
            <div className="mt-1 flex gap-2">
              <input
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault();
                    addLine(draft);
                  }
                }}
                placeholder="Nom exact de l'article"
                disabled={plan !== null || lines.length >= maxItems}
                className="flex-1 rounded-lg border border-white/10 bg-black/30 px-3 py-2
                           text-sm font-sans text-white/90 placeholder:text-white/25
                           focus:border-white/30 focus:outline-none disabled:opacity-40"
              />
              <button
                onClick={() => addLine(draft)}
                disabled={plan !== null || !draft.trim() || lines.length >= maxItems}
                className="rounded-lg border border-white/15 px-3 text-white/70
                           hover:bg-white/5 hover:text-white disabled:opacity-30"
                title="Ajouter au panier"
              >
                <Plus size={14} />
              </button>
            </div>
          </label>

          {menuItems.length > 0 && (
            <div>
              <div className="text-[10px] font-mono uppercase tracking-wide text-white/30 mb-1">
                Menu relevé — cliquer pour ajouter
              </div>
              <div className="flex flex-wrap gap-1.5 max-h-40 overflow-y-auto">
                {menuItems.slice(0, 40).map((item) => (
                  <button
                    key={item.id}
                    onClick={() => addLine(item.item_name)}
                    disabled={plan !== null}
                    className="rounded-md border border-white/10 px-2 py-1 text-xs text-white/60
                               hover:border-white/30 hover:text-white disabled:opacity-30"
                  >
                    {item.item_name}
                    {item.price !== null && (
                      <span className="ml-1.5 text-white/30">{item.price.toFixed(2)} €</span>
                    )}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="space-y-3">
          <div className="text-xs font-mono uppercase tracking-wide text-white/40">
            Panier ({lines.length}/{maxItems})
          </div>
          {lines.length === 0 ? (
            <div className="rounded-lg border border-white/5 p-3 text-sm text-white/35">
              Aucun article.
            </div>
          ) : (
            <div className="space-y-1.5">
              {lines.map((line) => (
                <div
                  key={line.name}
                  className="flex items-center gap-2 rounded-lg border border-white/5 px-3 py-2"
                >
                  <span className="flex-1 truncate text-sm">{line.name}</span>
                  <button
                    onClick={() => changeQuantity(line.name, -1)}
                    disabled={plan !== null}
                    className="text-white/40 hover:text-white disabled:opacity-30"
                    aria-label={`Retirer un ${line.name}`}
                  >
                    <Minus size={13} />
                  </button>
                  <span className="w-6 text-center font-mono text-xs">{line.quantity}</span>
                  <button
                    onClick={() => changeQuantity(line.name, 1)}
                    disabled={plan !== null || line.quantity >= maxItemQuantity}
                    className="text-white/40 hover:text-white disabled:opacity-30"
                    aria-label={`Ajouter un ${line.name}`}
                  >
                    <Plus size={13} />
                  </button>
                  <button
                    onClick={() => changeQuantity(line.name, -line.quantity)}
                    disabled={plan !== null}
                    className="text-white/25 hover:text-red-400 disabled:opacity-30"
                    aria-label={`Supprimer ${line.name}`}
                  >
                    <X size={13} />
                  </button>
                </div>
              ))}
            </div>
          )}

          {!plan && (
            <button
              onClick={prepare}
              disabled={!canPrepare}
              className="inline-flex w-full items-center justify-center gap-2 rounded-lg
                         border border-white/15 px-3 py-2 text-sm text-white/80
                         hover:bg-white/5 hover:text-white disabled:opacity-30"
            >
              {busy ? <Loader2 size={14} className="animate-spin" /> : <ShoppingCart size={14} />}
              Construire le panier et lire le total
            </button>
          )}

          {plan && (
            <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-3 space-y-2">
              <div className="text-sm">
                <span className="font-medium">{plan.restaurant}</span> — {plan.items_label}
              </div>
              <div className="font-mono text-lg">
                {formatAmount(plan.total_price, plan.currency)}
              </div>
              <div className="text-[10px] font-mono text-white/40">
                {plan.dry_run
                  ? 'Simulation active : la confirmation ne dépensera rien.'
                  : 'La confirmation déclenche un paiement réel.'}
                {' '}Panier valable {Math.max(0, Math.round(plan.expires_in_seconds / 60))} min.
              </div>
              <div className="flex gap-2">
                <button
                  onClick={confirm}
                  disabled={busy}
                  className="flex-1 rounded-lg bg-white px-3 py-2 text-sm font-medium text-black
                             hover:bg-white/90 disabled:opacity-40"
                >
                  {busy ? 'Envoi…' : 'Confirmer la commande'}
                </button>
                <button
                  onClick={cancel}
                  disabled={busy}
                  className="rounded-lg border border-white/15 px-3 py-2 text-sm text-white/60
                             hover:bg-white/5 hover:text-white disabled:opacity-40"
                >
                  Abandonner
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
