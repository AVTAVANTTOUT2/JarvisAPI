"""Lieux favoris et détection d'opportunités manquées — heuristique sur `places`/`visits`.

Un lieu est « favori » quand son `visit_count` dépasse
``config.FAVORITE_PLACE_MIN_VISITS``. Une « opportunité manquée » est un lieu
favori qu'on n'a pas revisité depuis ``config.OPPORTUNITY_MIN_DAYS_NAMED``
jours — un habitué qui décroche, détectable seulement à partir des données
GPS déjà stockées (aucune source externe).
"""

from __future__ import annotations

from datetime import datetime

import config
from database.location_helpers import get_all_places


def _parse_dt(raw) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def get_favorite_places(limit: int = 10) -> list[dict]:
    """Lieux les plus fréquentés, triés par nombre de visites décroissant."""
    places = get_all_places()
    favorites = [p for p in places if int(p.get("visit_count") or 0) >= config.FAVORITE_PLACE_MIN_VISITS]
    favorites.sort(key=lambda p: int(p.get("visit_count") or 0), reverse=True)
    return favorites[:limit] if limit else favorites


def detect_missed_opportunities(now: datetime | None = None) -> list[dict]:
    """Lieux favoris délaissés depuis plus de `OPPORTUNITY_MIN_DAYS_NAMED` jours.

    Exclut la catégorie ``home`` (rentrer chez soi n'est pas une « opportunité »).
    """
    now = now or datetime.now()
    results = []
    for place in get_favorite_places(limit=None):
        if place.get("category") == "home":
            continue
        last_visit = _parse_dt(place.get("last_visit"))
        if last_visit is None:
            continue
        days_since = (now - last_visit).days
        if days_since < config.OPPORTUNITY_MIN_DAYS_NAMED:
            continue
        results.append({
            "place_id": place["id"],
            "name": place["name"],
            "category": place.get("category"),
            "visit_count": int(place.get("visit_count") or 0),
            "avg_duration_min": place.get("avg_duration_min"),
            "days_since_last_visit": days_since,
            "message": (
                f"Vous alliez à {place['name']} régulièrement "
                f"({int(place.get('visit_count') or 0)} visites) — plus rien depuis {days_since} jours."
            ),
        })
    results.sort(key=lambda r: r["days_since_last_visit"], reverse=True)
    return results


def check_and_notify_weekly() -> list[dict]:
    """Notifie une fois par semaine (ISO) s'il existe des opportunités manquées."""
    from database import create_notification, get_db

    opportunities = detect_missed_opportunities()
    if not opportunities:
        return []

    week_key = datetime.now().strftime("%G-W%V")
    title = f"Lieux délaissés ({week_key})"
    with get_db() as conn:
        already = conn.execute(
            "SELECT 1 FROM notifications WHERE title = ?", (title,)
        ).fetchone()
    if already:
        return opportunities

    names = ", ".join(o["name"] for o in opportunities[:5])
    create_notification(
        source="pattern", title=title,
        content=f"{len(opportunities)} lieu(x) favoris délaissés : {names}.",
        priority="low",
    )
    return opportunities
