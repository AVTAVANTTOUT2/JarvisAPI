"""Handlers de statut, maintenance et import iMessage."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import BackgroundTasks, HTTPException

import config
from api.daemon_support import _audio_daemon_status_payload
from database import count_memory_stats, get_cost_summary, get_daily_activity_stats, get_usage_stats
from integrations import imessage_bridge
from scripts.email_watcher import email_watcher

try:
    from audio import stt, tts
except ImportError:
    stt = None
    tts = None

logger = logging.getLogger("jarvis")



def _computer_status_payload() -> dict:
    try:
        from integrations.computer import computer as _c

        return {
            "available": _c.allowed,
            "shell": _c.shell,
        }
    except Exception:
        return {"available": False, "shell": config.COMPUTER_SHELL}


def _code_executor_status_payload() -> dict:
    try:
        from integrations.code_executor import code_executor
        return {
            "available": code_executor.available if code_executor else False,
            "engine": "advanced" if (code_executor and code_executor.available) else "basic",
        }
    except Exception:
        return {"available": False, "engine": "basic"}


def _safe_memory_stats() -> dict:
    try:
        return count_memory_stats()
    except Exception as e:
        logger.error("count_memory_stats : %s", e)
        return {}


async def api_status():
    """Stats d'utilisation, agents actifs, coûts."""
    try:
        stats = get_usage_stats()
    except Exception as e:
        logger.error(f"Erreur get_usage_stats : {e}")
        stats = {"msg_count": 0, "total_in": 0, "total_out": 0, "total_cost": 0.0}

    loc_payload: dict[str, Any] = {}
    try:
        from database.location_helpers import get_active_location_patterns, get_today_visits
        from integrations.location import location_manager

        st = await location_manager.get_status()
        summary = await location_manager.get_daily_summary()
        loc_payload = {
            "tracking": getattr(config, "LOCATION_TRACKING", True),
            "place_radius_m": int(getattr(config, "LOCATION_PLACE_RADIUS", 100)),
            "status": st,
            "summary_today": {
                "place_count": len(summary.get("visits") or []),
                "trip_count": summary.get("trip_count"),
                "total_distance_km": summary.get("total_distance_km"),
            },
            "today_route": [
                v.get("place_name")
                for v in (get_today_visits() or [])
                if v.get("place_name")
            ],
            "pattern_count": len(get_active_location_patterns()),
        }
    except Exception as e:
        logger.debug("api_status location : %s", e)
        loc_payload = {"error": str(e)}

    return {
        "user": config.USER_NAME,
        "models": {
            "fast": config.DEEPSEEK_FAST_MODEL,
            "main": config.DEEPSEEK_MAIN_MODEL,
        },
        "agents_registered": ["info", "school", "productivity", "coach", "journal", "memory"],
        "today": stats,
        "audio": {
            "stt_available": stt is not None and getattr(stt, "available", False),
            "stt_engine": stt.get_backend_name() if (stt and getattr(stt, "available", False)) else "none",
            "tts_available": tts is not None and getattr(tts, "available", False),
            "tts_backend": tts.get_backend_name() if tts else "none",
            "tts_voice": config.TTS_VOICE,
        },
        "voice_conversation": {
            "silence_duration_ms": getattr(config, "VOICE_SILENCE_DURATION_MS", 1200),
            "min_speech_ms": getattr(config, "VOICE_MIN_SPEECH_MS", 400),
            "max_tokens": getattr(config, "VOICE_MAX_TOKENS", 500),
        },
        "imessage": {
            "available": imessage_bridge is not None and imessage_bridge.is_available(),
            "target": config.IMESSAGE_TARGET,
            "prefix": config.IMESSAGE_PREFIX or None,
            "sourcing_enabled": config.IMESSAGE_SOURCING_ENABLED,
            "send_enabled": config.IMESSAGE_SEND_ENABLED,
            "scan_interval": config.IMESSAGE_SCAN_INTERVAL,
        },
        "email_watcher": {
            "running": email_watcher.running,
            "check_interval": email_watcher.check_interval,
            "processed_count": len(email_watcher.last_processed_ids),
        },
        "computer": _computer_status_payload(),
        "code_executor": _code_executor_status_payload(),
        "memory": _safe_memory_stats(),
        "location": loc_payload,
        "audio_daemon": _audio_daemon_status_payload(),
    }


async def api_stats_weekly(days: int = 7):
    """Série d'activité quotidienne (messages, échanges vocaux, tokens, coût).

    Retourne aussi la variation jour/jour (dernier jour vs avant-dernier) pour
    les cartes de tendance du dashboard. `days` borné à [2, 90].
    """
    days = max(2, min(days, 90))
    try:
        daily = get_daily_activity_stats(days)
    except Exception as e:
        logger.error("get_daily_activity_stats : %s", e)
        raise HTTPException(500, "Statistiques indisponibles") from e

    def _pct(cur: float, prev: float) -> float | None:
        if prev <= 0:
            return None
        return round((cur - prev) / prev * 100, 1)

    last, prev = daily[-1], daily[-2]
    change = {
        "messages_pct": _pct(last["msg_count"], prev["msg_count"]),
        "voice_pct": _pct(last["voice_count"], prev["voice_count"]),
        "interactions_pct": _pct(
            last["tokens_in"] + last["tokens_out"],
            prev["tokens_in"] + prev["tokens_out"],
        ),
        "cost_pct": _pct(last["cost"], prev["cost"]),
    }
    totals = {
        "msg_count": sum(d["msg_count"] for d in daily),
        "voice_count": sum(d["voice_count"] for d in daily),
        "tokens_in": sum(d["tokens_in"] for d in daily),
        "tokens_out": sum(d["tokens_out"] for d in daily),
        "cost": round(sum(d["cost"] for d in daily), 6),
    }
    return {"days": daily, "change": change, "totals": totals}


async def api_costs():
    """Dépenses LLM (jour / 7 jours / mois, par modèle) + budget configuré."""
    try:
        return get_cost_summary()
    except Exception as e:
        logger.error("get_cost_summary : %s", e)
        raise HTTPException(500, "Coûts indisponibles") from e


async def api_backups_list():
    """Sauvegardes SQLite présentes (plus récente en premier)."""
    from scripts.db_maintenance import list_backups

    return {
        "backups": list_backups(),
        "dir": config.BACKUP_DIR,
        "keep": config.BACKUP_KEEP,
        "enabled": config.BACKUP_ENABLED,
    }


async def api_backups_run():
    """Déclenche une sauvegarde immédiate (VACUUM INTO + rotation)."""
    from scripts.db_maintenance import run_backup

    report = await asyncio.to_thread(run_backup)
    if not report.get("ok"):
        raise HTTPException(500, report.get("error", "Sauvegarde échouée"))
    return report


async def api_backups_restore(name: str):
    """Restaure une sauvegarde (écrase la base courante — un snapshot de sécurité est pris avant)."""
    from scripts.db_maintenance import restore_backup

    report = await asyncio.to_thread(restore_backup, name)
    if not report.get("ok"):
        raise HTTPException(400, report.get("error", "Restauration échouée"))
    return report


async def api_maintenance_run():
    """Purge de rétention + optimisation FTS/WAL immédiates."""
    from scripts.db_maintenance import run_maintenance

    try:
        return await asyncio.to_thread(run_maintenance)
    except Exception as e:
        logger.exception("run_maintenance : %s", e)
        raise HTTPException(500, "Maintenance échouée") from e


# ── Import iMessage ─────────────────────────────────────────

async def api_imessage_import_run(background_tasks: BackgroundTasks):
    """Declenche un import iMessage en arriere-plan.

    Lance l'import initial si jamais fait, sinon une sync incrementale.
    Retourne immediatement un statut 'started'. L'import tourne en background.
    """
    from integrations.imessage_import import IMessageImporter

    importer = IMessageImporter()

    if not importer.is_available():
        raise HTTPException(
            503,
            "chat.db inaccessible — verifier Full Disk Access. "
            "Utilisez --doctor pour un diagnostic complet.",
        )

    cursor = importer.get_status()
    mode = "initial" if cursor.get("total_imported", 0) == 0 else "incremental"

    async def _run_import():
        try:
            if mode == "initial":
                logger.info("[api] Demarrage import iMessage initial (background)")
                result = importer.import_all()
            else:
                logger.info("[api] Demarrage sync iMessage incrementale (background)")
                result = importer.sync_incremental()
            logger.info(
                "[api] Import iMessage termine — %d messages, %d skip, %d erreurs",
                result.total_messages,
                result.total_skipped,
                result.total_failed,
            )
        except Exception as e:
            logger.exception("[api] Echec import iMessage background : %s", e)

    background_tasks.add_task(_run_import)

    return {
        "status": "started",
        "mode": mode,
        "cursor": cursor,
    }


async def api_imessage_import_status():
    """Retourne l'etat du curseur d'import iMessage."""
    from integrations.imessage_import import IMessageImporter

    importer = IMessageImporter()
    return importer.get_status()
