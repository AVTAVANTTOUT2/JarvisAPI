"""Machines connectées — lecture SQLite de la table devices.

Retourne la liste des devices avec leur statut online/heartbeat.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import config as cfg

logger = logging.getLogger(__name__)


def get_devices_status() -> dict[str, Any]:
    """Retourne la liste des devices et le coût API du jour."""
    db_path = _resolve_db_path()
    if not db_path:
        return {"devices": [], "total": 0}

    devices: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row

        cur = conn.execute(
            """SELECT id, device_id, device_name, device_type, is_active,
                      is_online, last_heartbeat, last_screen_at, ip_tailscale, created_at
               FROM devices
               ORDER BY is_active DESC, last_heartbeat DESC"""
        )
        rows = cur.fetchall()
        conn.close()
    except sqlite3.OperationalError as exc:
        logger.warning("Table devices indisponible: %s", exc)
        return {"devices": [], "total": 0}

    for row in rows:
        heartbeat = row["last_heartbeat"]
        status = "offline"
        idle_text = "offline"
        if heartbeat:
            try:
                hb_str = str(heartbeat).replace("Z", "+00:00")
                hb_dt = datetime.fromisoformat(hb_str)
                if hb_dt.tzinfo is None:
                    hb_dt = hb_dt.replace(tzinfo=timezone.utc)
                delta_min = (now - hb_dt).total_seconds() / 60
                if delta_min < 2:
                    status = "online"
                    idle_text = "active"
                elif delta_min < 10:
                    status = "idle"
                    idle_text = f"idle {int(delta_min)}min"
                else:
                    idle_h = delta_min / 60
                    if idle_h < 24:
                        idle_text = f"idle {idle_h:.0f}h"
                    else:
                        idle_text = f"idle {idle_h / 24:.0f}j"
            except ValueError:
                status = "unknown"
                idle_text = "unknown"

        devices.append({
            "id": row["id"],
            "device_id": row["device_id"],
            "device_name": row["device_name"],
            "device_type": row["device_type"] or "desktop",
            "is_active": bool(row["is_active"]),
            "status": status,
            "idle_text": idle_text,
            "ip_tailscale": row["ip_tailscale"] or "",
        })

    # Récupérer le coût API du jour (somme des messages des 24 dernières heures)
    api_cost_today = _get_daily_cost(db_path, now) if db_path else 0.0

    return {
        "devices": devices,
        "total": len(devices),
        "api_cost_today": api_cost_today,
    }


def _get_daily_cost(db_path: str, now: datetime) -> float:
    """Calcule le coût API cumulé sur les dernières 24h."""
    cutoff = (now - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cur = conn.execute(
            "SELECT COALESCE(SUM(cost), 0) FROM messages WHERE created_at >= ?",
            (cutoff,),
        )
        row = cur.fetchone()
        conn.close()
        return round(row[0], 4) if row else 0.0
    except Exception:
        return 0.0


def _resolve_db_path() -> str | None:
    root = Path(__file__).resolve().parent.parent.parent
    db_full = root / "data" / "jarvis.db"
    if db_full.exists():
        return str(db_full)
    logger.warning("jarvis.db introuvable")
    return None
