"""Opérations de persistance des commandes de repas.

Chaque tentative de commande laisse une trace, y compris les paniers refusés
par un plafond : le journal sert autant d'historique que de source des
compteurs journaliers qui autorisent — ou non — la commande suivante.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import date, timedelta

from .core import get_db
from .time_buckets import local_datetime, utc_bounds_for_local_dates

logger = logging.getLogger(__name__)

ORDER_STATUSES: frozenset[str] = frozenset(
    {"planned", "simulated", "placed", "blocked", "failed"}
)

#: Seul un statut engage réellement de l'argent : lui seul alimente les
#: compteurs de plafond journalier.
BILLABLE_STATUS = "placed"

MAX_ERROR_CHARS = 1_000
MAX_RESTAURANT_CHARS = 200
MAX_TRACKING_URL_CHARS = 500
DEFAULT_HISTORY_LIMIT = 20
MAX_HISTORY_LIMIT = 200
MIN_RATING = 1
MAX_RATING = 5

#: Avancement réel de la course, distinct de ``status`` qui décrit l'issue de
#: la tentative de commande côté JARVIS.
DELIVERY_STATUSES: frozenset[str] = frozenset(
    {"placed", "preparing", "picked_up", "on_the_way", "delivered", "cancelled"}
)

#: Statuts après lesquels le suivi s'arrête.
TERMINAL_DELIVERY_STATUSES: frozenset[str] = frozenset({"delivered", "cancelled"})


class FoodOrderError(ValueError):
    """Une écriture de commande viole le contrat de la table."""


def _serialise_items(items: Sequence[Mapping[str, object]]) -> str:
    """Sérialise les articles de façon déterministe et bornée."""
    payload = [
        {
            "name": str(item.get("name", ""))[:200],
            "quantity": int(item.get("quantity", 1) or 1),
            "notes": str(item.get("notes", "") or "")[:200],
        }
        for item in items
    ]
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def record_food_order(
    *,
    restaurant: str,
    items: Sequence[Mapping[str, object]],
    status: str,
    dry_run: bool,
    total_price: float | None = None,
    currency: str = "EUR",
    plan_id: str | None = None,
    error: str | None = None,
    screenshot_path: str | None = None,
    suggestion_id: int | None = None,
    tracking_url: str | None = None,
    delivery_status: str | None = None,
) -> int:
    """Journalise une tentative de commande et retourne son identifiant.

    Retourne ``0`` lorsqu'une commande réellement passée existe déjà pour ce
    plan : l'index unique partiel rend le doublon impossible, et un rejeu de
    confirmation ne doit pas remonter une erreur au lieu d'être ignoré.
    """
    if status not in ORDER_STATUSES:
        raise FoodOrderError(
            f"Statut de commande inconnu : {status!r} "
            f"(attendu parmi {sorted(ORDER_STATUSES)})"
        )
    clean_restaurant = str(restaurant or "").strip()[:MAX_RESTAURANT_CHARS]
    if not clean_restaurant:
        raise FoodOrderError("Nom de restaurant vide : commande non journalisable")
    if total_price is not None and total_price < 0:
        raise FoodOrderError(
            f"Total négatif refusé pour {clean_restaurant} : {total_price}"
        )

    if delivery_status is not None and delivery_status not in DELIVERY_STATUSES:
        raise FoodOrderError(
            f"Statut de livraison inconnu : {delivery_status!r} "
            f"(attendu parmi {sorted(DELIVERY_STATUSES)})"
        )

    clean_error = str(error)[:MAX_ERROR_CHARS] if error else None
    try:
        with get_db() as conn:
            cursor = conn.execute(
                """INSERT INTO food_orders
                   (plan_id, restaurant, items_json, total_price, currency,
                    dry_run, status, error, screenshot_path,
                    suggestion_id, tracking_url, delivery_status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    plan_id,
                    clean_restaurant,
                    _serialise_items(items),
                    total_price,
                    str(currency or "EUR")[:8],
                    1 if dry_run else 0,
                    status,
                    clean_error,
                    screenshot_path,
                    int(suggestion_id) if suggestion_id else None,
                    str(tracking_url)[:MAX_TRACKING_URL_CHARS] if tracking_url else None,
                    delivery_status,
                ),
            )
            return int(cursor.lastrowid)
    except sqlite3.IntegrityError:
        logger.warning(
            "[food_orders] commande déjà journalisée pour le plan %s "
            "(restaurant %s) — rejeu ignoré",
            plan_id,
            clean_restaurant,
        )
        return 0


def get_food_order(order_id: int) -> dict | None:
    """Retourne une commande par identifiant."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM food_orders WHERE id = ?", (order_id,)
        ).fetchone()
    return dict(row) if row else None


def get_food_orders(limit: int = DEFAULT_HISTORY_LIMIT) -> list[dict]:
    """Retourne l'historique des commandes, de la plus récente à la plus ancienne."""
    bounded = max(1, min(int(limit), MAX_HISTORY_LIMIT))
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM food_orders ORDER BY created_at DESC, id DESC LIMIT ?",
            (bounded,),
        ).fetchall()
    return [dict(row) for row in rows]


def update_food_order_delivery(
    order_id: int,
    *,
    delivery_status: str,
    eta_minutes: int | None = None,
    tracking_url: str | None = None,
) -> dict | None:
    """Met à jour l'avancement d'une livraison et retourne la ligne à jour.

    ``delivered_at`` est posé une seule fois, à la première transition vers
    ``delivered`` : un relevé répété ne doit pas repousser l'heure d'arrivée.

    Returns:
        La commande mise à jour, ou ``None`` si l'identifiant est inconnu.

    Raises:
        FoodOrderError: statut de livraison hors du vocabulaire autorisé.
    """
    if delivery_status not in DELIVERY_STATUSES:
        raise FoodOrderError(
            f"Statut de livraison inconnu : {delivery_status!r} "
            f"(attendu parmi {sorted(DELIVERY_STATUSES)})"
        )
    bounded_eta = None if eta_minutes is None else max(0, int(eta_minutes))
    clean_url = str(tracking_url)[:MAX_TRACKING_URL_CHARS] if tracking_url else None

    with get_db() as conn:
        cursor = conn.execute(
            """UPDATE food_orders
               SET delivery_status = ?,
                   eta_minutes = COALESCE(?, eta_minutes),
                   tracking_url = COALESCE(?, tracking_url),
                   delivered_at = CASE
                       WHEN ? = 'delivered' AND delivered_at IS NULL
                           THEN CURRENT_TIMESTAMP
                       ELSE delivered_at
                   END
               WHERE id = ?""",
            (delivery_status, bounded_eta, clean_url, delivery_status, int(order_id)),
        )
        if cursor.rowcount == 0:
            return None
        row = conn.execute(
            "SELECT * FROM food_orders WHERE id = ?", (int(order_id),)
        ).fetchone()
    return dict(row) if row else None


def rate_food_order(order_id: int, rating: int) -> dict | None:
    """Enregistre la note d'un repas déjà commandé.

    Returns:
        La commande mise à jour, ou ``None`` si l'identifiant est inconnu.

    Raises:
        FoodOrderError: note hors de l'échelle autorisée.
    """
    try:
        value = int(rating)
    except (TypeError, ValueError) as exc:
        raise FoodOrderError(f"Note non entière : {rating!r}") from exc
    if not MIN_RATING <= value <= MAX_RATING:
        raise FoodOrderError(
            f"Note {value} hors bornes pour la commande {order_id} "
            f"(attendu {MIN_RATING} à {MAX_RATING})"
        )

    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE food_orders SET rating = ? WHERE id = ?", (value, int(order_id))
        )
        if cursor.rowcount == 0:
            return None
        row = conn.execute(
            "SELECT * FROM food_orders WHERE id = ?", (int(order_id),)
        ).fetchone()
    return dict(row) if row else None


def get_orders_awaiting_delivery(limit: int = 5) -> list[dict]:
    """Retourne les commandes réellement passées dont la course n'est pas finie.

    Les commandes simulées sont exclues : rien n'est en route, il n'y a rien à
    suivre.
    """
    bounded = max(1, min(int(limit), MAX_HISTORY_LIMIT))
    placeholders = ", ".join("?" * len(TERMINAL_DELIVERY_STATUSES))
    with get_db() as conn:
        rows = conn.execute(
            f"""SELECT * FROM food_orders
                WHERE status = ?
                  AND dry_run = 0
                  AND (delivery_status IS NULL OR delivery_status NOT IN ({placeholders}))
                ORDER BY created_at DESC, id DESC
                LIMIT ?""",
            (BILLABLE_STATUS, *sorted(TERMINAL_DELIVERY_STATUSES), bounded),
        ).fetchall()
    return [dict(row) for row in rows]


def get_rated_food_orders(limit: int = MAX_HISTORY_LIMIT) -> list[dict]:
    """Retourne les commandes notées, base de l'apprentissage des préférences."""
    bounded = max(1, min(int(limit), MAX_HISTORY_LIMIT))
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM food_orders
               WHERE rating IS NOT NULL
               ORDER BY created_at DESC, id DESC
               LIMIT ?""",
            (bounded,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_daily_food_order_stats(day: date | None = None) -> dict:
    """Compte les commandes réellement passées sur une journée locale.

    Les bornes sont calculées dans ``TIMEZONE`` puis converties en UTC :
    ``created_at`` est écrit par SQLite en UTC, un ``DATE()`` direct
    décalerait la journée.
    """
    local_day = day or local_datetime().date()
    start_utc, end_utc = utc_bounds_for_local_dates(
        local_day, local_day + timedelta(days=1)
    )
    with get_db() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS orders,
                      COALESCE(SUM(total_price), 0.0) AS spend
               FROM food_orders
               WHERE status = ?
                 AND dry_run = 0
                 AND created_at >= ?
                 AND created_at < ?""",
            (BILLABLE_STATUS, start_utc, end_utc),
        ).fetchone()
    return {
        "date": local_day.isoformat(),
        "orders": int(row["orders"] or 0),
        "spend": round(float(row["spend"] or 0.0), 2),
    }
