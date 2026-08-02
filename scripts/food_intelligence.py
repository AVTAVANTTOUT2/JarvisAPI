"""Apprentissage des habitudes alimentaires et génération des suggestions.

Deux étages nettement séparés, pour la même raison qui a présidé au routage
cognitif : le choix se fait par des règles vérifiables, le langage seulement
par le modèle.

1. **Ce qu'on propose** est décidé par un score déterministe calculé sur
   l'historique déjà en base — fréquence du restaurant, correspondance avec le
   jour de la semaine, notes obtenues, écart au budget habituel. Aucune
   inférence de modèle, donc rien à halluciner : chaque score expose ses
   facteurs et se recalcule à l'identique.
2. **Comment on le dit** est confié au modèle, sur une seule phrase, et
   uniquement si un modèle est disponible. Une phrase de repli déterministe
   existe toujours : l'absence de réseau ne doit pas priver l'utilisateur de
   ses suggestions.

Aucune fonction de ce module ne commande quoi que ce soit.
"""

from __future__ import annotations

import json
import logging
import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

import config

logger = logging.getLogger(__name__)

#: Nombre d'articles retenus par suggestion. Au-delà, l'estimation devient trop
#: incertaine pour un clic sans relecture.
ITEMS_PER_SUGGESTION = 2
#: Poids du score, documentés pour rester lisibles depuis l'interface.
WEIGHT_FREQUENCY = 40.0
WEIGHT_WEEKDAY = 25.0
WEIGHT_RATING = 20.0
WEIGHT_RECENCY_PENALTY = 15.0
WEIGHT_BUDGET_PENALTY = 20.0
NEUTRAL_RATING = 3.0
MIN_CONFIDENCE_SAMPLE = 3
MAX_CONFIDENCE_SAMPLE = 20
#: Un restaurant commandé il y a moins de ce délai est déprécié : proposer
#: trois fois le même plat dans la semaine n'est pas une recommandation.
RECENCY_SATURATION_DAYS = 7.0

PREFERENCE_KEYS = (
    "favorite_restaurant",
    "avg_spend",
    "typical_order_hour",
    "weekday_patterns",
    "top_rated_restaurants",
)


def _parse_timestamp(raw: object) -> datetime | None:
    """Lit un horodatage SQLite, sans lever sur une valeur inattendue."""
    text = str(raw or "").strip()
    if not text:
        return None
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[: len(pattern) + 2].strip(), pattern)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _confidence(sample_size: int) -> float:
    """Confiance croissante avec l'échantillon, plafonnée à 0.95.

    Trois commandes ne valent pas vingt : la valeur exposée doit dire à
    l'interface s'il s'agit d'une habitude établie ou d'une intuition.
    """
    if sample_size <= 0:
        return 0.0
    ratio = min(1.0, sample_size / MAX_CONFIDENCE_SAMPLE)
    return round(min(0.95, 0.25 + 0.7 * ratio), 3)


def summarise_orders(orders: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Résume l'historique en indicateurs bruts (fonction pure).

    Seules les commandes réellement passées comptent : une tentative bloquée
    par un plafond ne dit rien des goûts de l'utilisateur.
    """
    restaurants: Counter[str] = Counter()
    weekday_pairs: Counter[tuple[int, str]] = Counter()
    hours: Counter[int] = Counter()
    ratings: defaultdict[str, list[int]] = defaultdict(list)
    last_seen: dict[str, datetime] = {}
    spends: list[float] = []

    for order in orders:
        restaurant = str(order.get("restaurant") or "").strip()
        if not restaurant:
            continue
        restaurants[restaurant] += 1

        price = order.get("total_price")
        if isinstance(price, (int, float)) and price > 0:
            spends.append(float(price))

        rating = order.get("rating")
        if isinstance(rating, int) and 1 <= rating <= 5:
            ratings[restaurant].append(rating)

        moment = _parse_timestamp(order.get("created_at"))
        if moment is None:
            continue
        weekday_pairs[(moment.weekday(), restaurant)] += 1
        hours[moment.hour] += 1
        previous = last_seen.get(restaurant)
        if previous is None or moment > previous:
            last_seen[restaurant] = moment

    return {
        "order_count": sum(restaurants.values()),
        "restaurants": dict(restaurants),
        "weekday_pairs": {f"{day}|{name}": count for (day, name), count in weekday_pairs.items()},
        "hours": dict(hours),
        "avg_rating": {
            name: round(sum(values) / len(values), 2) for name, values in ratings.items()
        },
        "last_seen": {name: moment.isoformat(timespec="seconds") for name, moment in last_seen.items()},
        "avg_spend": round(sum(spends) / len(spends), 2) if spends else None,
    }


def derive_preferences(summary: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Transforme le résumé en préférences persistables (fonction pure)."""
    restaurants: Mapping[str, int] = summary.get("restaurants") or {}
    total = int(summary.get("order_count") or 0)
    preferences: dict[str, dict[str, Any]] = {}

    if restaurants:
        favourite, count = max(restaurants.items(), key=lambda item: (item[1], item[0]))
        preferences["favorite_restaurant"] = {
            "value": favourite,
            "confidence": _confidence(count),
            "sample_size": count,
        }

    avg_spend = summary.get("avg_spend")
    if avg_spend is not None:
        preferences["avg_spend"] = {
            "value": f"{float(avg_spend):.2f}",
            "confidence": _confidence(total),
            "sample_size": total,
        }

    hours: Mapping[int, int] = summary.get("hours") or {}
    if hours:
        typical_hour, hour_count = max(hours.items(), key=lambda item: (item[1], -item[0]))
        preferences["typical_order_hour"] = {
            "value": str(typical_hour),
            "confidence": _confidence(hour_count),
            "sample_size": hour_count,
        }

    weekday_pairs: Mapping[str, int] = summary.get("weekday_pairs") or {}
    if weekday_pairs:
        top = sorted(weekday_pairs.items(), key=lambda item: (-item[1], item[0]))[:8]
        preferences["weekday_patterns"] = {
            "value": dict(top),
            "confidence": _confidence(total),
            "sample_size": total,
        }

    ratings: Mapping[str, float] = summary.get("avg_rating") or {}
    if ratings:
        best = sorted(ratings.items(), key=lambda item: (-item[1], item[0]))[:5]
        preferences["top_rated_restaurants"] = {
            "value": dict(best),
            "confidence": _confidence(len(ratings)),
            "sample_size": len(ratings),
        }
    return preferences


def learn_preferences() -> dict[str, Any]:
    """Recalcule les préférences depuis l'historique et les persiste.

    Returns:
        Le nombre de commandes analysées et les clés écrites.
    """
    from database import get_food_orders, set_food_preference

    orders = [
        order
        for order in get_food_orders(limit=200)
        if order.get("status") in ("placed", "simulated")
    ]
    summary = summarise_orders(orders)
    preferences = derive_preferences(summary)

    for key, payload in preferences.items():
        set_food_preference(
            key,
            payload["value"],
            confidence=payload["confidence"],
            sample_size=payload["sample_size"],
        )

    logger.info(
        "[food] préférences recalculées sur %d commande(s) : %s",
        summary["order_count"],
        ", ".join(sorted(preferences)) or "aucune",
    )
    return {
        "orders_analysed": summary["order_count"],
        "preferences": sorted(preferences),
        "summary": summary,
    }


# ── Scoring des candidats ───────────────────────────────────────────────────


def score_restaurant(
    restaurant: str,
    *,
    summary: Mapping[str, Any],
    weekday: int,
    now: datetime,
    budget: float | None,
    estimated_price: float | None,
) -> tuple[float, dict[str, float]]:
    """Note un restaurant candidat et expose le détail du calcul.

    Fonction pure : mêmes entrées, mêmes sorties, aucun accès à la base. Le
    dictionnaire retourné est affiché tel quel dans l'interface pour que la
    recommandation reste explicable.
    """
    restaurants: Mapping[str, int] = summary.get("restaurants") or {}
    total_orders = max(1, int(summary.get("order_count") or 0))
    factors: dict[str, float] = {}

    own_orders = int(restaurants.get(restaurant, 0))
    factors["frequency"] = round(WEIGHT_FREQUENCY * own_orders / total_orders, 3)

    weekday_pairs: Mapping[str, int] = summary.get("weekday_pairs") or {}
    same_weekday = int(weekday_pairs.get(f"{weekday}|{restaurant}", 0))
    factors["weekday_match"] = round(
        WEIGHT_WEEKDAY * min(1.0, same_weekday / 3.0), 3
    )

    ratings: Mapping[str, float] = summary.get("avg_rating") or {}
    rating = float(ratings.get(restaurant, NEUTRAL_RATING))
    factors["rating"] = round(WEIGHT_RATING * (rating - NEUTRAL_RATING) / 2.0, 3)

    last_seen_raw = (summary.get("last_seen") or {}).get(restaurant)
    last_seen = _parse_timestamp(last_seen_raw)
    if last_seen is None:
        factors["recency_penalty"] = 0.0
    else:
        days = max(0.0, (now - last_seen).total_seconds() / 86_400.0)
        freshness = math.exp(-days / RECENCY_SATURATION_DAYS)
        factors["recency_penalty"] = round(-WEIGHT_RECENCY_PENALTY * freshness, 3)

    if budget and estimated_price:
        overshoot = max(0.0, (estimated_price - budget) / budget)
        factors["budget_penalty"] = round(-WEIGHT_BUDGET_PENALTY * min(1.0, overshoot), 3)
    else:
        factors["budget_penalty"] = 0.0

    return round(sum(factors.values()), 3), factors


def _pick_items(menu: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Choisit les articles d'une suggestion : les moins chers du menu connu.

    Un choix simple et prévisible vaut mieux qu'un panier surprise : c'est
    l'utilisateur qui décide de cliquer, il doit pouvoir anticiper le contenu.
    """
    priced = [item for item in menu if isinstance(item.get("price"), (int, float))]
    ordered = sorted(priced, key=lambda item: (float(item["price"]), str(item["item_name"])))
    return [
        {"name": str(item["item_name"]), "quantity": 1, "price": float(item["price"])}
        for item in ordered[:ITEMS_PER_SUGGESTION]
    ]


def build_suggestions(
    *,
    summary: Mapping[str, Any],
    menus: Mapping[str, Sequence[Mapping[str, Any]]],
    now: datetime,
    budget: float | None,
    slots: int,
) -> list[dict[str, Any]]:
    """Classe les restaurants disponibles et retourne les meilleures propositions.

    Fonction pure. Un restaurant sans menu chiffré est écarté : sans prix, le
    montant maximum autorisé au clic ne pourrait pas être calculé, et le clic
    deviendrait un chèque en blanc.
    """
    from integrations.uber_eats_settings import get_settings

    tolerance = max(0.0, float(getattr(config, "FOOD_QUICK_ORDER_PRICE_TOLERANCE", 0.15)))
    max_order_price = get_settings().max_order_price

    candidates: list[dict[str, Any]] = []
    for restaurant, menu in menus.items():
        items = _pick_items(menu)
        if not items:
            continue
        estimated = round(sum(item["price"] * item["quantity"] for item in items), 2)
        if estimated <= 0:
            continue
        score, factors = score_restaurant(
            restaurant,
            summary=summary,
            weekday=now.weekday(),
            now=now,
            budget=budget,
            estimated_price=estimated,
        )
        max_price = round(min(estimated * (1.0 + tolerance), max_order_price), 2)
        if max_price < estimated:
            # Le plafond par commande interdirait déjà ce panier : le proposer
            # ne ferait que produire un refus au moment du clic.
            continue
        currency = str(next((item.get("currency") for item in menu if item.get("currency")), "EUR"))
        candidates.append(
            {
                "restaurant": restaurant,
                "items": [
                    {"name": item["name"], "quantity": item["quantity"]} for item in items
                ],
                "estimated_price": estimated,
                "max_price": max_price,
                "currency": currency,
                "score": score,
                "factors": factors,
            }
        )

    candidates.sort(key=lambda entry: (-entry["score"], entry["restaurant"]))
    return candidates[: max(1, int(slots))]


def _fallback_reasoning(candidate: Mapping[str, Any], summary: Mapping[str, Any]) -> str:
    """Phrase de repli, déterministe, dans la voix de JARVIS."""
    restaurant = candidate["restaurant"]
    count = int((summary.get("restaurants") or {}).get(restaurant, 0))
    factors: Mapping[str, float] = candidate.get("factors") or {}
    if factors.get("weekday_match", 0) > 0:
        return f"Votre habitude du jour chez {restaurant}."
    if count >= 3:
        return f"{count} commandes chez {restaurant} : valeur sûre."
    if factors.get("rating", 0) > 0:
        return f"{restaurant}, bien noté la dernière fois."
    return f"{restaurant}, dans votre budget habituel."


async def _write_reasonings(
    candidates: Sequence[dict[str, Any]], summary: Mapping[str, Any]
) -> None:
    """Complète chaque candidat d'une phrase de conseil, en place.

    Un seul appel au modèle pour l'ensemble des suggestions : trois appels
    séparés coûteraient trois fois plus cher pour trois phrases de quinze mots.
    En cas d'échec, chaque candidat garde sa phrase déterministe.
    """
    for candidate in candidates:
        candidate["reasoning"] = _fallback_reasoning(candidate, summary)

    try:
        from llm import chat
    except ImportError as exc:  # pragma: no cover - dépend de l'installation
        logger.warning("[food] client LLM indisponible : %s", exc)
        return

    listing = "\n".join(
        f"{index}. {candidate['restaurant']} — "
        f"{', '.join(item['name'] for item in candidate['items'])} — "
        f"{candidate['estimated_price']:.2f} {candidate['currency']}"
        for index, candidate in enumerate(candidates, start=1)
    )
    prompt = (
        "Voici les suggestions de repas retenues pour l'utilisateur :\n"
        f"{listing}\n\n"
        "Pour chacune, écris UNE phrase de moins de quinze mots qui justifie la "
        "proposition. Ton de majordome britannique, sobre, sans emoji, sans "
        "point d'exclamation. Réponds uniquement par un tableau JSON de chaînes, "
        "dans le même ordre."
    )
    try:
        response = await chat(
            messages=[{"role": "user", "content": prompt}],
            model=getattr(config, "DEEPSEEK_FAST_MODEL", None),
            max_tokens=200,
            temperature=0.4,
        )
        phrases = _parse_reasoning_payload(response.get("content", ""))
    except Exception as exc:  # noqa: BLE001 - le repli couvre toute défaillance
        logger.warning("[food] phrases de conseil non générées : %s", exc)
        return

    for candidate, phrase in zip(candidates, phrases):
        cleaned = " ".join(str(phrase).split())[:200]
        if cleaned:
            candidate["reasoning"] = cleaned


def _parse_reasoning_payload(raw: str) -> list[str]:
    """Extrait la liste de phrases d'une réponse de modèle, tolérante au bruit."""
    text = str(raw or "").strip()
    if "```" in text:
        blocks = [part for part in text.split("```") if "[" in part]
        text = blocks[0] if blocks else text
        text = text.removeprefix("json").strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    return [str(item) for item in payload] if isinstance(payload, list) else []


async def generate_suggestions() -> dict[str, Any]:
    """Produit le lot de suggestions du jour et le persiste.

    Returns:
        Le nombre de suggestions écrites et, en cas d'abandon, la raison.
    """
    from database import (
        get_food_orders,
        get_food_preferences,
        get_menu_items,
        get_menu_restaurants,
        replace_suggestions,
    )

    from integrations.uber_eats_settings import get_settings

    if not get_settings().suggestions_enabled:
        return {"ok": False, "reason": "disabled", "created": 0}

    known = get_menu_restaurants()
    if not known:
        return {"ok": False, "reason": "no_menu_cached", "created": 0}

    orders = [
        order
        for order in get_food_orders(limit=200)
        if order.get("status") in ("placed", "simulated")
    ]
    minimum = max(0, int(getattr(config, "FOOD_SUGGESTION_MIN_ORDERS", 3)))
    if len(orders) < minimum:
        return {
            "ok": False,
            "reason": "not_enough_history",
            "created": 0,
            "orders": len(orders),
            "required": minimum,
        }

    summary = summarise_orders(orders)
    preferences = get_food_preferences()
    budget = _preference_float(preferences, "avg_spend")

    menus = {
        entry["restaurant"]: get_menu_items(entry["restaurant"])
        for entry in known
    }
    candidates = build_suggestions(
        summary=summary,
        menus=menus,
        now=datetime.now(),
        budget=budget,
        slots=int(getattr(config, "FOOD_SUGGESTION_SLOTS", 3)),
    )
    if not candidates:
        return {"ok": False, "reason": "no_priced_menu_item", "created": 0}

    await _write_reasonings(candidates, summary)
    created = replace_suggestions(
        candidates, ttl_hours=int(getattr(config, "FOOD_SUGGESTION_TTL_HOURS", 12))
    )
    logger.info("[food] %d suggestion(s) générée(s)", len(created))
    return {"ok": True, "created": len(created), "suggestion_ids": created}


def _preference_float(preferences: Mapping[str, Mapping[str, Any]], key: str) -> float | None:
    """Lit une préférence numérique sans lever si elle est absente ou corrompue."""
    entry = preferences.get(key)
    if not entry:
        return None
    try:
        return float(entry.get("value"))
    except (TypeError, ValueError):
        return None
