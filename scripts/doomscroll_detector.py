"""Détection de doomscrolling — heuristique sur `app_usage`, aucun ML.

Une journée est marquée « doomscroll » quand le temps cumulé passé sur les
apps de `config.DOOMSCROLL_APPS` (Instagram, TikTok, etc., substring
case-insensitive sur le nom d'app remonté par le screen watcher) dépasse
`config.DOOMSCROLL_DAILY_MINUTES`. Repose entièrement sur les données déjà
collectées par le daemon (`app_usage`) — pas de nouvelle source de données.
"""

from __future__ import annotations

from collections import defaultdict

import config
from jarvis.notification_service import notification_service


def _doomscroll_app_names() -> list[str]:
    return [a.strip().lower() for a in (config.DOOMSCROLL_APPS or "").split(",") if a.strip()]


def _is_doomscroll_app(app_name: str) -> bool:
    if not app_name:
        return False
    name = app_name.lower()
    return any(target in name for target in _doomscroll_app_names())


def analyze_doomscroll(usage_rows: list[dict], daily_minutes_threshold: float | None = None) -> list[dict]:
    """Analyse une liste de lignes `app_usage` (pure, testable sans DB).

    Retourne un jour par entrée dépassant le seuil, trié du plus récent au
    plus ancien : ``{date, total_minutes, apps, message}``.
    """
    threshold = daily_minutes_threshold if daily_minutes_threshold is not None else config.DOOMSCROLL_DAILY_MINUTES

    by_date: dict[str, dict[str, float]] = defaultdict(dict)
    for row in usage_rows:
        app = row.get("app") or ""
        if not _is_doomscroll_app(app):
            continue
        date = row.get("date")
        if not date:
            continue
        seconds = float(row.get("duration_seconds") or 0)
        by_date[date][app] = by_date[date].get(app, 0.0) + seconds

    results = []
    for date, apps in by_date.items():
        total_minutes = sum(apps.values()) / 60.0
        if total_minutes < threshold:
            continue
        top_apps = sorted(apps.items(), key=lambda kv: kv[1], reverse=True)
        apps_summary = [{"app": a, "minutes": round(s / 60.0, 1)} for a, s in top_apps]
        top_name = top_apps[0][0]
        results.append({
            "date": date,
            "total_minutes": round(total_minutes, 1),
            "apps": apps_summary,
            "message": (
                f"{round(total_minutes)} minutes sur {top_name} et compagnie le {date} — "
                f"au-delà de vos {threshold:.0f} minutes habituelles."
            ),
        })

    results.sort(key=lambda r: r["date"], reverse=True)
    return results


def detect_doomscrolling(days: int = 7, device: str | None = None) -> list[dict]:
    """Point d'entrée réel — va chercher les données via `get_app_usage_range`."""
    from database import get_app_usage_range

    rows = get_app_usage_range(days=days, device=device)
    return analyze_doomscroll(rows)


def check_and_notify_today() -> dict | None:
    """Vérifie la journée en cours et notifie une seule fois par jour si dépassement."""
    from database import get_db

    today_results = detect_doomscrolling(days=1)
    if not today_results:
        return None
    today = today_results[0]
    with get_db() as conn:
        already = conn.execute(
            "SELECT 1 FROM notifications WHERE title = 'Doomscrolling' AND DATE(created_at) = ?",
            (today["date"],),
        ).fetchone()
    if already:
        return None

    notification_service.create(
        source="pattern", title="Doomscrolling", content=today["message"], priority="low",
    )
    return today
