"""Actions IA récentes — lecture de la table llm_action_logs.

Retourne les N dernières actions exécutées par les agents JARVIS
(tous types confondus) dans les dernières 24h glissantes.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import config as cfg

logger = logging.getLogger(__name__)

# Icônes par type d'action
ACTION_ICONS: dict[str, str] = {
    "mail": "\u2709",        # ✉
    "mail_read": "\u2709",   # ✉
    "weather": "\u2601",     # ☁
    "calendar": "\uD83D\uDCC5",  # 📅
    "calendar_create": "\uD83D\uDCC5",
    "task": "\u2713",        # ✓
    "reminder": "\u23F0",    # ⏰
    "note": "\uD83D\uDCDD",  # 📝
    "terminal": "\u2328",    # ⌨
    "open_app": "\u25B6",    # ▶
    "find_file": "\uD83D\uDD0D",  # 🔍
    "clipboard": "\uD83D\uDCCB",  # 📋
    "system_info": "\u2139", # ℹ
    "mood": "\uD83D\uDE0A",  # 😊
    "name_place": "\uD83D\uDCCD",  # 📍
    "where_am_i": "\uD83D\uDDFA",  # 🗺
    "day_route": "\uD83D\uDE97",   # 🚗
    "search_conversations": "\uD83D\uDD0D",
}

# Couleurs par agent
AGENT_COLORS: dict[str, str] = {
    "productivity": "#00d4ff",   # cyan
    "productivity_triage": "#00d4ff",
    "productivity_draft": "#00d4ff",
    "school": "#ffb000",         # ambre
    "coach": "#b000ff",          # violet
    "coach_deep": "#b000ff",
    "info": "#00ff41",           # vert
    "journal": "#ffffff",        # blanc
    "orchestrator": "#888888",   # gris
    "memory": "#888888",
    "action_executor": "#00d4ff",
}


def get_recent_actions() -> list[dict[str, Any]]:
    """Retourne les actions LLM des dernières 24h depuis SQLite."""
    db_path = _resolve_db_path()
    if not db_path:
        return _error_result("Base de données jarvis.db introuvable.")

    cutoff = datetime.now(timezone.utc) - timedelta(hours=cfg.AUTOMATIONS_HOURS)
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """SELECT id, created_at, agent, action_type, payload, status, execution_time_ms
               FROM llm_action_logs
               WHERE created_at >= ?
               ORDER BY created_at DESC
               LIMIT ?""",
            (cutoff_str, cfg.MAX_AUTOMATIONS),
        )
        rows = cur.fetchall()
        conn.close()
    except sqlite3.OperationalError as exc:
        logger.warning("llm_action_logs indisponible: %s", exc)
        return []

    results: list[dict[str, Any]] = []
    for row in rows:
        agent = row["agent"] or "unknown"
        action_type = row["action_type"] or "unknown"
        icon = ACTION_ICONS.get(action_type, "\u25CF")  # ● default
        color = AGENT_COLORS.get(agent, "#888888")
        status = row["status"] or "unknown"
        created = row["created_at"] or ""

        # Extraire le timestamp HH:MM
        time_str = ""
        try:
            dt = datetime.fromisoformat(created) if created else None
            if dt:
                time_str = dt.strftime("%H:%M")
        except ValueError:
            time_str = str(created)[:5] if created else ""

        # Extraire un aperçu du payload (input_preview)
        preview = ""
        try:
            import json
            payload = row["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            if isinstance(payload, dict):
                action_data = payload.get("action", {})
                if isinstance(action_data, dict):
                    title = action_data.get("title", "")
                    command = action_data.get("command", "")
                    content = action_data.get("content", "")
                    query = action_data.get("query", "")
                    preview = title or command or content or query or ""
        except Exception:
            pass

        preview = str(preview).strip()[:60]

        results.append({
            "time": time_str,
            "agent": agent,
            "action_type": action_type,
            "icon": icon,
            "color": color,
            "status": status,
            "preview": preview,
            "execution_time_ms": row["execution_time_ms"],
        })

    return results


def _resolve_db_path() -> str | None:
    """Résout le chemin absolu vers jarvis.db."""
    root = Path(__file__).resolve().parent.parent.parent
    db_full = root / "data" / "jarvis.db"
    if db_full.exists():
        return str(db_full)
    return None


def _error_result(message: str) -> list[dict[str, Any]]:
    return [{"error": message, "time": "--:--", "agent": "system", "action_type": "error", "icon": "\u26A0", "color": "#ff0040", "status": "error", "preview": message}]
