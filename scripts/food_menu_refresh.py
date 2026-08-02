"""Relevé planifié des menus des restaurants les plus commandés.

Le relevé coûte un lancement de navigateur par restaurant : il ne peut pas
tourner sur tout le fil d'accueil. La liste suivie est donc dérivée de
l'historique — ce que l'utilisateur commande vraiment — complétée par le fil
d'accueil uniquement s'il n'a encore rien commandé.

À planifier deux fois par jour, avant les pics de commande. Le script est
exécutable directement pour un relevé manuel :

    python scripts/food_menu_refresh.py --restaurant "Chez Pierre"
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402  (dépend de sys.path)
from integrations.uber_eats import UberEatsError  # noqa: E402

logger = logging.getLogger(__name__)

#: Un menu plus vieux que ce seuil est considéré comme périmé.
DEFAULT_MAX_AGE_HOURS = 48
HISTORY_DEPTH = 100


def tracked_restaurants(limit: int) -> list[str]:
    """Retourne les restaurants à relever, les plus commandés en tête.

    Les commandes bloquées ou en échec comptent aussi : elles témoignent d'une
    intention, même si la commande n'est jamais partie.
    """
    from database import get_food_orders

    counter: Counter[str] = Counter()
    for order in get_food_orders(limit=HISTORY_DEPTH):
        name = str(order.get("restaurant") or "").strip()
        if name:
            counter[name] += 1
    return [name for name, _ in counter.most_common(max(1, limit))]


async def refresh_tracked_menus(restaurants: list[str] | None = None) -> dict[str, Any]:
    """Relève et persiste les menus demandés, ou ceux déduits de l'historique.

    Returns:
        Le détail des relevés réussis et des échecs, restaurant par restaurant.
    """
    from integrations.uber_eats_discovery import uber_eats_discovery

    limit = max(1, int(getattr(config, "FOOD_MENU_SCRAPE_MAX_RESTAURANTS", 8)))
    targets = [name.strip() for name in (restaurants or []) if str(name).strip()]
    source = "requested"

    if not targets:
        targets = tracked_restaurants(limit)
        source = "history"
    if not targets:
        try:
            targets = await uber_eats_discovery.list_feed_restaurants(limit)
            source = "feed"
        except UberEatsError as exc:
            logger.warning("[food] fil d'accueil illisible : %s", exc)
            return {"ok": False, "reason": str(exc), "source": "feed", "refreshed": {}}

    if not targets:
        return {"ok": False, "reason": "aucun restaurant à relever", "source": source, "refreshed": {}}

    result = await uber_eats_discovery.refresh_menus(targets[:limit])
    result["ok"] = bool(result.get("refreshed"))
    result["source"] = source
    return result


async def _main() -> int:
    """Point d'entrée en ligne de commande."""
    parser = argparse.ArgumentParser(description="Relève les menus Uber Eats suivis.")
    parser.add_argument(
        "--restaurant",
        action="append",
        default=[],
        help="Restaurant à relever (répétable). Sans option, déduit de l'historique.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        report = await refresh_tracked_menus(args.restaurant or None)
    except UberEatsError as exc:
        print(f"Relevé impossible : {exc}")
        return 2

    for restaurant, count in (report.get("refreshed") or {}).items():
        print(f"OK   {restaurant} — {count} article(s)")
    for failure in report.get("failures") or []:
        print(f"ÉCHEC {failure['restaurant']} — {failure['error']}")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
