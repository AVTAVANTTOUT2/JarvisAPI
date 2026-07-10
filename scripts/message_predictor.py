"""Prédiction de qui va écrire prochainement — heuristique, pas de ML.

Analyse les horodatages des messages REÇUS d'un contact sur une fenêtre
récente : intervalle moyen entre deux messages, heures et jours de la
semaine les plus fréquents, temps écoulé depuis le dernier message. La
probabilité et le délai estimé sont dérivés de la position de « maintenant »
dans ces patterns — jamais un modèle entraîné, une estimation transparente
et explicable (le champ ``explanation`` dit toujours pourquoi).

``predict_from_messages()`` est une fonction pure (liste de timestamps en
entrée) — testable sans accès à ``chat.db``. ``predict_for_contact()`` est le
point d'entrée réel qui va chercher les messages via ``imessage_reader``.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta

import config


def _parse_ts(raw) -> datetime | None:
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(raw[:19], fmt)
            except ValueError:
                continue
    return None


def predict_from_messages(timestamps: list[datetime], now: datetime | None = None) -> dict:
    """Prédiction à partir d'une liste d'horodatages de messages REÇUS (triés ou non).

    Retourne ``{probability, confidence, estimated_next, explanation}``.
    ``probability`` ∈ [0, 1] : proximité de « maintenant » avec l'heure et le
    jour habituels de ce contact, pondérée par la fraîcheur de son dernier
    message par rapport à son intervalle moyen.
    """
    now = now or datetime.now()
    ts = sorted(t for t in timestamps if t is not None)

    if len(ts) < 3:
        return {
            "probability": 0.0, "confidence": "low", "estimated_next": None,
            "explanation": "Historique insuffisant (moins de 3 messages) pour dégager un pattern.",
        }

    intervals_h = [(ts[i] - ts[i - 1]).total_seconds() / 3600 for i in range(1, len(ts))]
    avg_interval_h = statistics.median(intervals_h)
    last_ts = ts[-1]
    hours_since_last = (now - last_ts).total_seconds() / 3600

    # Heure et jour habituels : mode sur les données disponibles.
    hours_of_day = [t.hour for t in ts]
    days_of_week = [t.weekday() for t in ts]
    common_hour = statistics.mode(hours_of_day)
    common_day = statistics.mode(days_of_week)

    hour_match = 1.0 - min(abs(now.hour - common_hour), 24 - abs(now.hour - common_hour)) / 12
    day_match = 1.0 if now.weekday() == common_day else 0.5

    # Position dans le cycle habituel : proche de 1.0 quand on approche/dépasse
    # l'intervalle moyen depuis le dernier message (« il devrait bientôt écrire »).
    if avg_interval_h <= 0:
        cycle_position = 0.5
    else:
        cycle_position = min(hours_since_last / avg_interval_h, 1.5) / 1.5

    probability = round(min(1.0, 0.5 * cycle_position + 0.3 * hour_match + 0.2 * day_match), 2)
    confidence = "high" if len(ts) >= 20 else ("medium" if len(ts) >= 8 else "low")

    estimated_next = last_ts + timedelta(hours=avg_interval_h)
    explanation = (
        f"Intervalle habituel ≈ {avg_interval_h:.1f}h ; dernier message il y a {hours_since_last:.1f}h ; "
        f"écrit typiquement vers {common_hour}h, plutôt le "
        f"{['lundi','mardi','mercredi','jeudi','vendredi','samedi','dimanche'][common_day]}."
    )
    return {
        "probability": probability, "confidence": confidence,
        "estimated_next": estimated_next.isoformat(timespec="minutes"),
        "explanation": explanation,
    }


async def predict_for_contact(handle: str, name: str = "") -> dict:
    """Prédiction réelle pour un contact — va chercher l'historique via imessage_reader."""
    try:
        from integrations.imessage_reader import imessage_reader
    except Exception as e:
        return {"probability": 0.0, "confidence": "low", "estimated_next": None,
                "explanation": f"iMessage indisponible : {e}"}

    if imessage_reader is None or not imessage_reader.is_available():
        return {"probability": 0.0, "confidence": "low", "estimated_next": None,
                "explanation": "chat.db indisponible (hors macOS ou permission manquante)."}

    messages = imessage_reader.get_conversation_for_period(
        handle, days=config.MESSAGE_PREDICTION_LOOKBACK_DAYS, limit=500,
    )
    received = [m for m in messages if not m.get("is_from_me")]
    timestamps = [t for t in (_parse_ts(m.get("date")) for m in received) if t is not None]
    result = predict_from_messages(timestamps)
    result["handle"] = handle
    result["name"] = name or handle
    return result


async def predict_for_all_contacts(limit: int = 20) -> list[dict]:
    """Prédictions pour les contacts les plus actifs, triées par probabilité décroissante."""
    from database import get_people_sorted_by_recent, get_relationship_profile

    people = get_people_sorted_by_recent()[:limit]
    results = []
    for p in people:
        profile = get_relationship_profile(p["id"]) or {}
        handle = profile.get("handle") or p.get("name")
        if not handle:
            continue
        pred = await predict_for_contact(handle, name=p.get("name", ""))
        results.append(pred)
    results.sort(key=lambda r: r["probability"], reverse=True)
    return results
