"""Persistance des menus relevés, des préférences dérivées et des suggestions.

Trois jeux de données distincts, tous alimentés par des mesures et jamais par
une saisie libre :

- ``food_menu_cache`` : ce que le restaurant propose réellement, relevé en
  lecture seule. Sans lui, une suggestion inventerait des plats et le panier
  échouerait à l'ajout.
- ``food_preferences`` : ce que l'historique dit des habitudes, avec la taille
  d'échantillon qui a produit la déduction.
- ``food_suggestions`` : les propositions du jour. Chacune porte le montant
  maximum que l'utilisateur autorise en cliquant, figé au moment de la
  génération et vérifié côté serveur avant tout paiement.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import Any

from .core import get_db
from .time_buckets import sqlite_utc_timestamp, utc_datetime

logger = logging.getLogger(__name__)

MAX_RESTAURANT_CHARS = 200
MAX_ITEM_NAME_CHARS = 200
MAX_CATEGORY_CHARS = 120
MAX_REASONING_CHARS = 400
MAX_PREFERENCE_VALUE_CHARS = 2_000
MAX_MENU_ITEMS_PER_RESTAURANT = 400
DEFAULT_SUGGESTION_TTL_HOURS = 12
MAX_SUGGESTION_SLOTS = 3
MIN_RATING = 1
MAX_RATING = 5

DELIVERY_STATUSES: frozenset[str] = frozenset(
    {"placed", "preparing", "picked_up", "on_the_way", "delivered", "cancelled"}
)

#: Statuts après lesquels plus aucun suivi n'est nécessaire.
TERMINAL_DELIVERY_STATUSES: frozenset[str] = frozenset({"delivered", "cancelled"})


class FoodIntelligenceError(ValueError):
    """Une écriture viole le contrat d'une table du module."""


def _clean(text: object, limit: int) -> str:
    """Normalise une chaîne non fiable et la borne."""
    return " ".join(str(text or "").split())[:limit]


def _positive_price(value: object) -> float | None:
    """Convertit un prix en flottant positif, ou ``None`` s'il est inexploitable."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return round(number, 2)


# ── Menus relevés ───────────────────────────────────────────────────────────


def replace_menu_items(
    restaurant: str,
    items: Sequence[Mapping[str, object]],
    *,
    currency: str = "EUR",
) -> int:
    """Remplace le menu connu d'un restaurant par le relevé fourni.

    Le remplacement est transactionnel : un relevé partiel ne laisse jamais un
    menu à moitié effacé, sans quoi une suggestion pourrait proposer un plat
    supprimé pendant que la reconstruction est en cours.

    Args:
        restaurant: Nom du restaurant tel qu'affiché sur la plateforme.
        items: Articles relevés, avec au minimum une clé ``item_name``.
        currency: Devise appliquée à tous les prix du relevé.

    Returns:
        Le nombre d'articles réellement enregistrés.

    Raises:
        FoodIntelligenceError: nom de restaurant vide ou relevé hors bornes.
    """
    clean_restaurant = _clean(restaurant, MAX_RESTAURANT_CHARS)
    if not clean_restaurant:
        raise FoodIntelligenceError("Nom de restaurant vide : menu non enregistrable")
    if len(items) > MAX_MENU_ITEMS_PER_RESTAURANT:
        raise FoodIntelligenceError(
            f"Menu de {clean_restaurant} trop volumineux : {len(items)} articles, "
            f"maximum {MAX_MENU_ITEMS_PER_RESTAURANT}"
        )

    rows: list[tuple[Any, ...]] = []
    seen: set[str] = set()
    for item in items:
        name = _clean(item.get("item_name") or item.get("name"), MAX_ITEM_NAME_CHARS)
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        rows.append(
            (
                clean_restaurant,
                name,
                _clean(item.get("category"), MAX_CATEGORY_CHARS) or None,
                _positive_price(item.get("price")),
                str(currency or "EUR")[:8],
                _clean(item.get("cuisine_type"), MAX_CATEGORY_CHARS) or None,
                0 if item.get("available") is False else 1,
            )
        )

    with get_db() as conn:
        conn.execute(
            "DELETE FROM food_menu_cache WHERE restaurant = ?", (clean_restaurant,)
        )
        conn.executemany(
            """INSERT INTO food_menu_cache
               (restaurant, item_name, category, price, currency, cuisine_type, available)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
    logger.info(
        "[food] menu de %s rafraîchi : %d article(s)", clean_restaurant, len(rows)
    )
    return len(rows)


def get_menu_items(restaurant: str, *, available_only: bool = True) -> list[dict]:
    """Retourne le menu connu d'un restaurant, du moins cher au plus cher."""
    clean_restaurant = _clean(restaurant, MAX_RESTAURANT_CHARS)
    query = "SELECT * FROM food_menu_cache WHERE restaurant = ?"
    params: list[Any] = [clean_restaurant]
    if available_only:
        query += " AND available = 1"
    query += " ORDER BY (price IS NULL), price ASC, item_name ASC"
    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def get_menu_restaurants() -> list[dict]:
    """Liste les restaurants dont le menu est en cache, avec leur fraîcheur."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT restaurant,
                      COUNT(*) AS item_count,
                      MAX(scraped_at) AS scraped_at
               FROM food_menu_cache
               WHERE available = 1
               GROUP BY restaurant
               ORDER BY scraped_at DESC"""
        ).fetchall()
    return [dict(row) for row in rows]


# ── Préférences dérivées ────────────────────────────────────────────────────


def set_food_preference(
    key: str,
    value: object,
    *,
    confidence: float = 0.5,
    sample_size: int = 0,
) -> None:
    """Écrit ou met à jour une préférence dérivée de l'historique.

    Raises:
        FoodIntelligenceError: clé vide ou confiance hors de l'intervalle [0, 1].
    """
    clean_key = _clean(key, 80)
    if not clean_key:
        raise FoodIntelligenceError("Clé de préférence vide")
    if not 0.0 <= float(confidence) <= 1.0:
        raise FoodIntelligenceError(
            f"Confiance hors bornes pour {clean_key!r} : {confidence} (attendu 0.0 à 1.0)"
        )

    serialised = (
        value
        if isinstance(value, str)
        else json.dumps(value, ensure_ascii=False, sort_keys=True)
    )
    with get_db() as conn:
        conn.execute(
            """INSERT INTO food_preferences (key, value, confidence, sample_size, updated_at)
               VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(key) DO UPDATE SET
                   value = excluded.value,
                   confidence = excluded.confidence,
                   sample_size = excluded.sample_size,
                   updated_at = CURRENT_TIMESTAMP""",
            (
                clean_key,
                str(serialised)[:MAX_PREFERENCE_VALUE_CHARS],
                round(float(confidence), 3),
                max(0, int(sample_size)),
            ),
        )


def get_food_preferences() -> dict[str, dict]:
    """Retourne toutes les préférences connues, indexées par clé."""
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM food_preferences ORDER BY key").fetchall()
    return {row["key"]: dict(row) for row in rows}


# ── Suggestions ─────────────────────────────────────────────────────────────


def _serialise_items(items: Sequence[Mapping[str, object]]) -> str:
    """Sérialise les articles d'une suggestion de façon déterministe."""
    payload = [
        {
            "name": _clean(item.get("name"), MAX_ITEM_NAME_CHARS),
            "quantity": max(1, int(item.get("quantity", 1) or 1)),
        }
        for item in items
    ]
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def replace_suggestions(
    suggestions: Sequence[Mapping[str, object]],
    *,
    ttl_hours: int = DEFAULT_SUGGESTION_TTL_HOURS,
) -> list[int]:
    """Périme les suggestions actives et enregistre le nouveau lot.

    Les anciennes lignes sont périmées plutôt que supprimées : une commande
    passée conserve ainsi le lien vers la suggestion qui l'a déclenchée, ce
    qui permet de mesurer plus tard quelles recommandations ont été suivies.

    Args:
        suggestions: Propositions ordonnées, une par emplacement.
        ttl_hours: Durée de validité, au-delà de laquelle un clic est refusé.

    Returns:
        Les identifiants créés, dans l'ordre des emplacements.

    Raises:
        FoodIntelligenceError: plus d'emplacements que le maximum autorisé.
    """
    if len(suggestions) > MAX_SUGGESTION_SLOTS:
        raise FoodIntelligenceError(
            f"{len(suggestions)} suggestions proposées, maximum {MAX_SUGGESTION_SLOTS}"
        )
    expires_at = sqlite_utc_timestamp(
        utc_datetime() + timedelta(hours=max(1, int(ttl_hours)))
    )

    created: list[int] = []
    with get_db() as conn:
        conn.execute(
            """UPDATE food_suggestions
               SET expires_at = CURRENT_TIMESTAMP
               WHERE ordered = 0 AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)"""
        )
        for slot, suggestion in enumerate(suggestions, start=1):
            restaurant = _clean(suggestion.get("restaurant"), MAX_RESTAURANT_CHARS)
            if not restaurant:
                raise FoodIntelligenceError(
                    f"Suggestion {slot} sans restaurant : proposition inutilisable"
                )
            raw_items = suggestion.get("items")
            items = raw_items if isinstance(raw_items, Sequence) else []
            cursor = conn.execute(
                """INSERT INTO food_suggestions
                   (slot, restaurant, items_json, estimated_price, max_price, currency,
                    reasoning, score, factors_json, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    slot,
                    restaurant,
                    _serialise_items(items),
                    _positive_price(suggestion.get("estimated_price")),
                    _positive_price(suggestion.get("max_price")),
                    str(suggestion.get("currency") or "EUR")[:8],
                    _clean(suggestion.get("reasoning"), MAX_REASONING_CHARS) or None,
                    round(float(suggestion.get("score") or 0.0), 4),
                    json.dumps(
                        suggestion.get("factors") or {},
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    expires_at,
                ),
            )
            created.append(int(cursor.lastrowid))
    return created


def _row_to_suggestion(row: sqlite3.Row) -> dict:
    """Convertit une ligne en dictionnaire prêt pour l'interface."""
    data = dict(row)
    try:
        data["items"] = json.loads(data.get("items_json") or "[]")
    except json.JSONDecodeError:
        logger.warning(
            "[food] suggestion %s : items_json illisible, liste vidée", data.get("id")
        )
        data["items"] = []
    try:
        data["factors"] = json.loads(data.get("factors_json") or "{}")
    except json.JSONDecodeError:
        data["factors"] = {}
    return data


def get_active_suggestions() -> list[dict]:
    """Retourne les suggestions encore cliquables, par emplacement croissant."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM food_suggestions
               WHERE ordered = 0
                 AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
               ORDER BY slot ASC"""
        ).fetchall()
    return [_row_to_suggestion(row) for row in rows]


def get_active_suggestion_by_slot(slot: int) -> dict | None:
    """Retourne la suggestion active d'un emplacement, ou ``None``."""
    with get_db() as conn:
        row = conn.execute(
            """SELECT * FROM food_suggestions
               WHERE slot = ?
                 AND ordered = 0
                 AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
               ORDER BY generated_at DESC, id DESC
               LIMIT 1""",
            (int(slot),),
        ).fetchone()
    return _row_to_suggestion(row) if row else None


def claim_suggestion(suggestion_id: int) -> bool:
    """Marque une suggestion comme consommée, de façon atomique.

    Retourne ``False`` si elle avait déjà été prise ou si elle a expiré entre
    l'affichage et le clic : deux clics rapides ne peuvent pas déclencher deux
    commandes.
    """
    with get_db() as conn:
        cursor = conn.execute(
            """UPDATE food_suggestions
               SET ordered = 1
               WHERE id = ?
                 AND ordered = 0
                 AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)""",
            (int(suggestion_id),),
        )
        return cursor.rowcount > 0


def release_suggestion(suggestion_id: int) -> bool:
    """Rend une suggestion à nouveau cliquable après un échec de commande."""
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE food_suggestions SET ordered = 0 WHERE id = ?",
            (int(suggestion_id),),
        )
        return cursor.rowcount > 0
