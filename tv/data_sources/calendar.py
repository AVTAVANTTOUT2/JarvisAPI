"""Événements du jour — proxy vers le backend principal JARVIS.

Appelle GET /api/calendar du backend principal pour obtenir les
événements Apple Calendar du jour.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

import httpx

import config as cfg

logger = logging.getLogger(__name__)


async def get_today_events() -> list[dict[str, Any]]:
    """Récupère les événements du jour via le backend principal."""
    today = date.today().isoformat()
    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0) as client:
            resp = await client.get(
                f"{cfg.BACKEND_BASE_URL}/api/calendar",
                params={"start": today, "end": today},
            )
            if resp.status_code != 200:
                logger.warning("Calendar endpoint HTTP %s", resp.status_code)
                return _error_result("Backend inaccessible.")
            data = resp.json()
            events = data.get("events", []) if isinstance(data, dict) else []
    except httpx.ConnectError:
        logger.warning("Backend principal injoignable pour le calendrier.")
        return _error_result("SIGNAL PERDU — Backend inaccessible")
    except Exception as exc:
        logger.exception("Erreur calendar: %s", exc)
        return _error_result(f"Erreur: {str(exc)}")

    # Trier par heure de début
    formatted: list[dict[str, Any]] = []
    now = datetime.now()
    for evt in events:
        start_str = evt.get("start", "") or evt.get("date", "")
        title = evt.get("title", evt.get("summary", "Sans titre"))
        location = evt.get("location", "")

        # Détecter si l'événement est en cours
        is_live = False
        try:
            if start_str:
                start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                fmt_time = start_dt.strftime("%H:%M")
                end_str = evt.get("end", "")
                if end_str:
                    end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                    if start_dt <= now <= end_dt:
                        is_live = True
            else:
                fmt_time = "--:--"
        except ValueError:
            fmt_time = start_str[:5] if len(start_str) >= 5 else "--:--"

        formatted.append({
            "time": fmt_time,
            "title": title,
            "location": location,
            "is_live": is_live,
        })

    formatted.sort(key=lambda e: e["time"])
    return formatted


def _error_result(message: str) -> list[dict[str, Any]]:
    return [{"error": True, "message": message, "time": "--:--", "title": message}]
