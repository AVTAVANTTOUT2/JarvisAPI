"""Routes de contrôle des daemons et services JARVIS."""

from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from api.daemon_support import _audio_daemon_status_payload
from api.service_control import (
    INTERNAL_SERVICES,
    _SERVICE_LOG_TAGS,
    _get_all_services_status,
    _start_service,
    _stop_service,
    get_service_detail,
)
from database import get_voice_debug_logs
from websocket_registry import broadcast_ws

router = APIRouter()
logger = logging.getLogger("jarvis")


@router.get("/api/audio-daemon/status")
async def audio_daemon_status():
    """État complet du daemon audio."""
    return _audio_daemon_status_payload()


@router.post("/api/audio-daemon/start")
async def audio_daemon_start():
    """Démarre le daemon audio (micro + wake word)."""
    from scripts.audio_daemon import audio_daemon as _ad
    if _ad.enabled:
        return {"ok": True, "message": "Déjà actif"}
    _ad.set_broadcast(broadcast_ws)
    asyncio.create_task(_ad.start())
    return {"ok": True, "message": "Daemon audio démarré"}


@router.post("/api/audio-daemon/stop")
async def audio_daemon_stop():
    """Arrête le daemon audio."""
    from scripts.audio_daemon import audio_daemon as _ad
    if not _ad.enabled:
        return {"ok": True, "message": "Déjà inactif"}
    await _ad.stop()
    return {"ok": True, "message": "Daemon audio arrêté"}


@router.post("/api/audio-daemon/wake-word")
async def audio_daemon_wake_word(body: dict[str, Any]):
    """Active/désactive le wake word. Body: {"enabled": true/false}"""
    from scripts.audio_daemon import audio_daemon as _ad
    await _ad.set_wake_word(body.get("enabled", True))
    return {"ok": True, "wake_word_enabled": _ad.wake_word_enabled}


@router.post("/api/audio-daemon/continuous")
async def audio_daemon_continuous(body: dict[str, Any]):
    """Active/désactive le mode écoute continue. Body: {"enabled": true/false}"""
    from scripts.audio_daemon import audio_daemon as _ad
    await _ad.set_continuous_mode(body.get("enabled", True))
    return {"ok": True, "continuous_mode": _ad.continuous_mode}


@router.get("/api/voice-debug")
async def api_voice_debug_logs(limit: int = 50):
    """Retourne les dernières traces de debug du pipeline vocal."""
    try:
        logs = get_voice_debug_logs(limit=limit)
    except Exception as e:
        logger.error(f"voice_debug_logs : {e}")
        raise HTTPException(500, str(e))
    return {"logs": logs}



@router.get("/api/control/services")
async def control_list_services():
    """Liste tous les services avec leur etat."""
    return {"services": _get_all_services_status()}


@router.get("/api/control/{service}/detail")
async def control_service_detail(service: str):
    """Detail enrichi (health Ollama, heartbeat Screen Watcher, …)."""
    return await get_service_detail(service)


@router.post("/api/control/{service}/start")
async def control_start_service(service: str):
    """Demarre un service specifique."""
    result = await _start_service(service)
    return result


@router.post("/api/control/{service}/stop")
async def control_stop_service(service: str):
    """Arrete un service specifique."""
    result = await _stop_service(service)
    return result


@router.post("/api/control/{service}/restart")
async def control_restart_service(service: str):
    """Redemarre un service (stop + start)."""
    svc = service.strip().lower().replace("-", "_")
    # Restart Ollama : SW s'arrête avec Ollama, pas de relance auto SW
    await _stop_service(service)
    await asyncio.sleep(1.0)
    result = await _start_service(service)
    if svc == "ollama":
        result = {
            **result,
            "screen_watcher_note": "Screen Watcher arrêté — démarrage manuel requis",
        }
    return result


@router.post("/api/control/restart-all")
async def control_restart_all():
    """Redemarre tous les services internes (pas le backend lui-meme)."""
    results: dict[str, object] = {}
    for svc in INTERNAL_SERVICES:
        try:
            await _stop_service(svc)
            await asyncio.sleep(0.5)
            r = await _start_service(svc)
            results[svc] = r
        except Exception as e:
            results[svc] = {"ok": False, "error": str(e)}
    return {"results": results}


@router.post("/api/control/stop-all")
async def control_stop_all():
    """Arrete tous les services internes."""
    results: dict[str, object] = {}
    for svc in INTERNAL_SERVICES:
        try:
            r = await _stop_service(svc)
            results[svc] = r
        except Exception as e:
            results[svc] = {"ok": False, "error": str(e)}
    return {"results": results}


@router.post("/api/control/start-all")
async def control_start_all():
    """Demarre tous les services internes."""
    results: dict[str, object] = {}
    for svc in INTERNAL_SERVICES:
        try:
            r = await _start_service(svc)
            results[svc] = r
        except Exception as e:
            results[svc] = {"ok": False, "error": str(e)}
    return {"results": results}


@router.get("/api/control/{service}/logs")
async def control_service_logs(service: str, lines: int = 50):
    """Retourne les dernieres lignes de log pertinentes pour un service."""
    tag = _SERVICE_LOG_TAGS.get(service, service)
    log_file = Path("data/.jarvis_restart/backend.log")

    if not log_file.exists():
        return {"logs": [], "message": "Pas de fichier de log"}

    try:
        result = subprocess.run(
            ["grep", "-i", tag, str(log_file)],
            capture_output=True, text=True, timeout=5,
        )
        all_lines = result.stdout.strip().split("\n")
        recent = all_lines[-lines:] if len(all_lines) > lines else all_lines
        return {"logs": [line for line in recent if line.strip()], "count": len(recent)}
    except Exception as e:
        return {"logs": [], "error": str(e)}
