import { useCallback, useEffect, useRef, useState } from 'react';
import {
  AlertTriangle,
  Clock,
  Loader2,
  RefreshCw,
  Star,
  UtensilsCrossed,
} from 'lucide-react';
import { api } from '@unified/lib/api';
import type {
  FoodMenuSummary,
  FoodOrder,
  FoodStatusResponse,
  FoodSuggestion,
} from '@unified/lib/api';
import { FoodDiagnosticsPanel } from '@desktop/app/components/food/FoodDiagnosticsPanel';
import { FoodOrderPanel } from '@desktop/app/components/food/FoodOrderPanel';
import { FoodSettingsPanel } from '@desktop/app/components/food/FoodSettingsPanel';

type Tab = 'suggestions' | 'order' | 'menus' | 'settings' | 'diagnostics';

const TABS: [Tab, string][] = [
  ['suggestions', 'Suggestions'],
  ['order', 'Commander'],
  ['menus', 'Menus'],
  ['settings', 'Réglages'],
  ['diagnostics', 'Diagnostic'],
];

/**
 * Étapes affichées dans la barre d'avancement, dans l'ordre du parcours réel.
 */
const DELIVERY_STEPS = ['placed', 'preparing', 'picked_up', 'on_the_way', 'delivered'] as const;

const DELIVERY_LABELS: Record<string, string> = {
  placed: 'Commande reçue',
  preparing: 'En préparation',
  picked_up: 'Récupérée',
  on_the_way: 'En route',
  delivered: 'Livrée',
  cancelled: 'Annulée',
};

const SLOT_KEYS = ['1', '2', '3'];

function formatAmount(value: number | null | undefined, currency: string): string {
  if (value === null || value === undefined) return '—';
  return `${value.toFixed(2)} ${currency === 'EUR' ? '€' : currency}`;
}

function parseItems(raw: string): { name: string; quantity: number }[] {
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function itemsLabel(items: { name: string; quantity: number }[]): string {
  return items.map((item) => `${item.quantity}x ${item.name}`).join(', ');
}

export default function FoodView() {
  const [tab, setTab] = useState<Tab>('suggestions');
  const [status, setStatus] = useState<FoodStatusResponse | null>(null);
  const [suggestions, setSuggestions] = useState<FoodSuggestion[]>([]);
  const [orders, setOrders] = useState<FoodOrder[]>([]);
  const [delivery, setDelivery] = useState<FoodOrder[]>([]);
  const [menus, setMenus] = useState<FoodMenuSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busySlot, setBusySlot] = useState<number | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // Un emplacement « armé » attend une seconde touche : au clavier, une frappe
  // isolée ne doit jamais déclencher une dépense.
  const [armedSlot, setArmedSlot] = useState<number | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const suggestionsRef = useRef<FoodSuggestion[]>([]);

  suggestionsRef.current = suggestions;

  const loadAll = useCallback(async () => {
    setError(null);
    try {
      const [statusData, suggestionData, orderData, deliveryData, menuData] = await Promise.all([
        api.getFoodStatus(),
        api.getFoodSuggestions(),
        api.getFoodOrders(30),
        api.getFoodDelivery(),
        api.getFoodMenus(),
      ]);
      setStatus(statusData);
      setSuggestions(suggestionData.suggestions ?? []);
      setOrders(orderData.orders ?? []);
      setDelivery(deliveryData.orders ?? []);
      setMenus(menuData.restaurants ?? []);
    } catch (e: any) {
      setError(e?.message || 'Chargement impossible');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  // Le suivi de livraison arrive par le flux d'événements déjà en place ;
  // aucun second WebSocket n'est ouvert pour cette page.
  useEffect(() => {
    const source = new EventSource('/api/events/stream');
    source.onmessage = (message) => {
      try {
        const event = JSON.parse(message.data);
        if (event?.type === 'food.order_updated') loadAll();
      } catch {
        // Flux partagé : un événement illisible ne concerne pas cette page.
      }
    };
    source.addEventListener('stream.reset', () => {
      void loadAll();
    });
    source.onerror = () => {
      // EventSource se reconnecte seul.
    };
    return () => source.close();
  }, [loadAll]);

  const orderSlot = useCallback(
    async (slot: number) => {
      const suggestion = suggestionsRef.current.find((item) => item.slot === slot);
      if (!suggestion || suggestion.max_price === null) return;
      setArmedSlot(null);
      setBusySlot(slot);
      setNotice(null);
      try {
        const result = await api.quickOrderFood(slot, suggestion.max_price);
        if (result.status === 'confirmation_required') {
          setNotice(
            `Le panier chez ${result.restaurant} coûte ${formatAmount(
              result.total_price,
              result.currency,
            )}, au-dessus des ${formatAmount(
              result.authorised_price ?? null,
              result.currency,
            )} autorisés. Rien n'a été commandé.`,
          );
        } else if (result.ok) {
          setNotice(
            result.dry_run
              ? `Simulation : ${result.items_label} chez ${result.restaurant}, ${formatAmount(result.total_price, result.currency)}.`
              : `Commandé chez ${result.restaurant} — ${formatAmount(result.total_price, result.currency)}.`,
          );
        } else {
          setNotice(result.error || 'Commande refusée.');
        }
      } catch (e: any) {
        setNotice(e?.message || 'Commande impossible');
      } finally {
        setBusySlot(null);
        loadAll();
      }
    },
    [loadAll],
  );

  // Clavier : 1/2/3 arment l'emplacement, Entrée confirme, Échap annule.
  // Deux gestes distincts, parce qu'une frappe parasite ne doit pas payer.
  // Actif uniquement sur l'onglet des suggestions : ailleurs, ces touches
  // servent à saisir du texte.
  useEffect(() => {
    if (tab !== 'suggestions') return;
    const handler = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;

      if (SLOT_KEYS.includes(event.key)) {
        const slot = Number.parseInt(event.key, 10);
        if (suggestionsRef.current.some((item) => item.slot === slot)) {
          setArmedSlot((current) => (current === slot ? null : slot));
        }
        return;
      }
      if (event.key === 'Escape') setArmedSlot(null);
      if (event.key === 'Enter' && armedSlot !== null) void orderSlot(armedSlot);
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [armedSlot, orderSlot, tab]);

  const rate = async (orderId: number, rating: number) => {
    try {
      await api.rateFoodOrder(orderId, rating);
      loadAll();
    } catch (e: any) {
      setNotice(e?.message || 'Note non enregistrée');
    }
  };

  const refreshMenus = async () => {
    setRefreshing(true);
    setNotice(null);
    try {
      await api.refreshFoodMenus();
      await api.generateFoodSuggestions();
      setNotice('Menus relevés et suggestions régénérées.');
    } catch (e: any) {
      setNotice(e?.message || 'Relevé impossible');
    } finally {
      setRefreshing(false);
      loadAll();
    }
  };

  const integration = status?.integration;
  const activeDelivery = delivery[0];

  return (
    <div className="flex flex-col h-full">
      <div className="shrink-0 border-b border-white/10 px-6 py-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <UtensilsCrossed size={20} className="text-white/60" />
            <h1 className="text-lg font-semibold tracking-tight">Nourriture</h1>
          </div>
          <button
            onClick={refreshMenus}
            disabled={refreshing || !integration?.can_scrape}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-mono
                       border border-white/15 text-white/70 hover:bg-white/5 hover:text-white
                       transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
            title={
              integration?.can_scrape
                ? 'Relever les menus puis régénérer les suggestions'
                : 'Relevé de menus désactivé'
            }
          >
            {refreshing ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
            Rafraîchir les menus
          </button>
        </div>

        {status && (
          <div className="flex flex-wrap items-center gap-3 mt-3 text-xs font-mono text-white/40">
            <span>
              Aujourd'hui {status.today.orders}/{integration?.max_daily_orders} commande(s)
            </span>
            <span>
              {status.today.spend.toFixed(2)} € / {integration?.max_daily_spend.toFixed(2)} €
            </span>
            <span>Plafond par commande {integration?.max_order_price.toFixed(2)} €</span>
            {integration?.dry_run && (
              <span className="px-2 py-0.5 rounded border border-amber-400/30 text-amber-400/80">
                simulation
              </span>
            )}
            {menus.length > 0 && <span>{menus.length} menu(s) en cache</span>}
          </div>
        )}

        <div className="flex items-center gap-1.5 mt-3">
          {TABS.map(([key, label]) => (
            <button
              key={key}
              onClick={() => setTab(key)}
              className={`px-2.5 py-1 rounded-md text-xs font-mono transition-colors border ${
                tab === key
                  ? 'bg-white/10 border-white/20 text-white'
                  : 'border-transparent text-white/40 hover:text-white/70 hover:bg-white/5'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-8">
        {loading && (
          <div className="flex items-center justify-center py-20">
            <Loader2 size={24} className="animate-spin text-white/30" />
          </div>
        )}

        {!loading && error && (
          <div className="flex items-center gap-2 p-4 rounded-lg border border-red-400/20 bg-red-400/5 text-red-400 text-sm">
            <AlertTriangle size={16} />
            {error}
            <button onClick={loadAll} className="ml-auto text-xs underline hover:text-red-300">
              Réessayer
            </button>
          </div>
        )}

        {!loading && notice && (
          <div className="p-3 rounded-lg border border-white/10 bg-white/5 text-sm text-white/70">
            {notice}
          </div>
        )}

        {!loading && integration && !integration.can_browse && (
          <div className="p-4 rounded-lg border border-amber-400/20 bg-amber-400/5 text-sm text-amber-200/80">
            <div className="font-medium mb-1">Commande indisponible</div>
            <ul className="list-disc list-inside space-y-0.5 text-xs">
              {integration.reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          </div>
        )}

        {!loading && activeDelivery && (
          <section>
            <div className="flex items-center gap-2 text-xs font-mono text-white/40 mb-2">
              <Clock size={13} />
              Commande en cours — {activeDelivery.restaurant}
              {activeDelivery.eta_minutes !== null && (
                <span>· arrivée estimée dans {activeDelivery.eta_minutes} min</span>
              )}
            </div>
            <div className="flex gap-1">
              {DELIVERY_STEPS.map((step) => {
                const current = activeDelivery.delivery_status ?? 'placed';
                const reached = DELIVERY_STEPS.indexOf(step) <= DELIVERY_STEPS.indexOf(current as never);
                return (
                  <div
                    key={step}
                    title={DELIVERY_LABELS[step]}
                    className={`h-1.5 flex-1 rounded ${reached ? 'bg-emerald-500' : 'bg-white/10'}`}
                  />
                );
              })}
            </div>
            <div className="mt-1 text-xs text-white/40">
              {DELIVERY_LABELS[activeDelivery.delivery_status ?? 'placed']}
            </div>
          </section>
        )}

        {!loading && tab === 'order' && (
          <FoodOrderPanel
            menus={menus}
            maxItems={10}
            maxItemQuantity={5}
            onOrdered={loadAll}
          />
        )}

        {!loading && tab === 'settings' && <FoodSettingsPanel onChanged={loadAll} />}

        {!loading && tab === 'diagnostics' && <FoodDiagnosticsPanel onChanged={loadAll} />}

        {!loading && tab === 'menus' && (
          <section>
            <h2 className="text-sm uppercase tracking-wide text-white/40 mb-3">
              Menus en cache
            </h2>
            {menus.length === 0 ? (
              <div className="p-4 rounded-lg border border-white/5 text-sm text-white/40">
                Aucun menu relevé. Utiliser « Rafraîchir les menus » une fois le relevé
                activé dans les réglages.
              </div>
            ) : (
              <div className="space-y-2">
                {menus.map((menu) => (
                  <div
                    key={menu.restaurant}
                    className="flex items-center justify-between rounded-lg border border-white/5 p-3"
                  >
                    <div className="min-w-0">
                      <div className="font-medium truncate">{menu.restaurant}</div>
                      <div className="text-[10px] font-mono text-white/30">
                        {menu.item_count} article(s) · relevé {menu.scraped_at}
                      </div>
                    </div>
                    <button
                      onClick={() =>
                        api
                          .refreshFoodMenus([menu.restaurant])
                          .then(loadAll)
                          .catch((e: any) => setNotice(e?.message || 'Relevé impossible'))
                      }
                      className="shrink-0 rounded-lg border border-white/15 px-2.5 py-1
                                 text-[10px] font-mono text-white/60 hover:bg-white/5 hover:text-white"
                    >
                      Relever
                    </button>
                  </div>
                ))}
              </div>
            )}
          </section>
        )}

        {!loading && tab === 'suggestions' && (
          <section>
            <div className="flex items-baseline justify-between mb-3">
              <h2 className="text-sm uppercase tracking-wide text-white/40">Suggestions</h2>
              {suggestions.length > 0 && (
                <span className="text-[10px] font-mono text-white/30">
                  1 / 2 / 3 pour choisir, Entrée pour commander
                </span>
              )}
            </div>

            {suggestions.length === 0 ? (
              <div className="p-4 rounded-lg border border-white/5 text-sm text-white/40">
                Aucune suggestion active. Relevez les menus pour en générer.
              </div>
            ) : (
              <div className="grid gap-4 md:grid-cols-3">
                {suggestions.map((suggestion) => {
                  const armed = armedSlot === suggestion.slot;
                  const busy = busySlot === suggestion.slot;
                  return (
                    <button
                      key={suggestion.id}
                      onClick={() => orderSlot(suggestion.slot)}
                      disabled={busySlot !== null || suggestion.max_price === null}
                      className={`text-left rounded-lg border p-4 transition-colors disabled:opacity-40
                                  disabled:cursor-not-allowed ${
                                    armed
                                      ? 'border-emerald-500/60 bg-emerald-500/5'
                                      : 'border-white/5 hover:border-white/20 hover:bg-white/5'
                                  }`}
                    >
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-2xl font-semibold text-white/80">
                          {suggestion.slot}
                        </span>
                        <span className="text-xs font-mono text-white/40">
                          {formatAmount(suggestion.estimated_price, suggestion.currency)}
                        </span>
                      </div>
                      <div className="font-medium">{suggestion.restaurant}</div>
                      <div className="text-sm text-white/50 mt-0.5">
                        {itemsLabel(suggestion.items)}
                      </div>
                      {suggestion.reasoning && (
                        <div className="text-xs text-white/35 italic mt-2">
                          {suggestion.reasoning}
                        </div>
                      )}
                      <div className="text-[10px] font-mono text-white/30 mt-2">
                        engage au plus {formatAmount(suggestion.max_price, suggestion.currency)}
                      </div>
                      {busy && (
                        <div className="flex items-center gap-1.5 text-xs text-emerald-400 mt-2">
                          <Loader2 size={12} className="animate-spin" />
                          Commande en cours
                        </div>
                      )}
                      {armed && !busy && (
                        <div className="text-xs text-emerald-400 mt-2">
                          Entrée pour confirmer, Échap pour annuler
                        </div>
                      )}
                    </button>
                  );
                })}
              </div>
            )}
          </section>
        )}

        {!loading && (tab === 'suggestions' || tab === 'order') && (
          <section>
            <h2 className="text-sm uppercase tracking-wide text-white/40 mb-3">Historique</h2>
            {orders.length === 0 ? (
              <div className="p-4 rounded-lg border border-white/5 text-sm text-white/40">
                Aucune commande enregistrée.
              </div>
            ) : (
              <div className="space-y-2">
                {orders.map((order) => (
                  <div
                    key={order.id}
                    className="flex items-start justify-between gap-4 rounded-lg border border-white/5 p-3"
                  >
                    <div className="min-w-0">
                      <div className="font-medium truncate">{order.restaurant}</div>
                      <div className="text-sm text-white/50 truncate">
                        {itemsLabel(parseItems(order.items_json))} ·{' '}
                        {formatAmount(order.total_price, order.currency)}
                      </div>
                      <div className="text-[10px] font-mono text-white/30 mt-0.5">
                        {order.created_at} · {order.status}
                        {order.dry_run ? ' · simulation' : ''}
                        {order.delivery_status
                          ? ` · ${DELIVERY_LABELS[order.delivery_status] ?? order.delivery_status}`
                          : ''}
                      </div>
                    </div>
                    <div className="flex shrink-0 gap-0.5">
                      {[1, 2, 3, 4, 5].map((value) => (
                        <button
                          key={value}
                          onClick={() => rate(order.id, value)}
                          title={`Noter ${value} sur 5`}
                          aria-label={`Noter ${value} sur 5`}
                        >
                          <Star
                            size={15}
                            className={
                              value <= (order.rating ?? 0)
                                ? 'fill-amber-400 text-amber-400'
                                : 'text-white/15 hover:text-white/40'
                            }
                          />
                        </button>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>
        )}
      </div>
    </div>
  );
}
