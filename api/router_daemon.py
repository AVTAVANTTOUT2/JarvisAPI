"""Routes de contrôle des daemons et services JARVIS."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

from api.daemon_support import _audio_daemon_status_payload
from api.errors import api_error, internal_error
from api.service_control import (
    _SERVICE_LOG_TAGS,
    INTERNAL_SERVICES,
    UnknownServiceError,
    _get_all_services_status,
    _start_service,
    _stop_service,
    get_service_detail,
)
from database import get_voice_debug_logs
from websocket_registry import broadcast_ws

router = APIRouter()
logger = logging.getLogger("jarvis")
BACKEND_LOG_FILE = (
    Path(__file__).resolve().parent.parent / "data" / ".jarvis_restart" / "backend.log"
)


def _unknown_service_error(service: str) -> HTTPException:
    """Traduit l'erreur métier en contrat HTTP stable."""
    return api_error(404, "service_not_found", f"Service inconnu : {service}")


def _require_service_success(
    result: dict[str, object],
    *,
    action: str,
) -> dict[str, object]:
    """Transforme un refus métier en véritable erreur HTTP publique."""
    if result.get("ok") is False:
        logger.warning("[control/%s] refus interne : %s", action, result.get("error"))
        raise api_error(
            503,
            f"service_{action}_failed",
            f"Action {action} impossible sur le service",
        )
    return result


def _raise_bulk_failure(action: str, failed_services: list[str]) -> None:
    """Signale un résultat partiel sans exposer les exceptions internes."""
    if failed_services:
        raise api_error(
            503,
            f"service_bulk_{action}_failed",
            f"Action groupée {action} partiellement impossible",
            context={"failed_services": failed_services},
        )


def _read_service_log_lines(
    log_file: Path,
    *,
    tag: str,
    lines: int,
) -> dict[str, object]:
    """Filtre un log en Python, hors de la boucle asyncio."""
    needle = tag.casefold()
    matches = [
        line
        for line in log_file.read_text(errors="replace").splitlines()
        if needle in line.casefold()
    ]
    recent = matches[-lines:]
    return {"logs": recent, "count": len(recent)}


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
        logger.exception("voice_debug_logs indisponibles")
        raise internal_error(
            "voice_debug_unavailable", "Traces vocales indisponibles"
        ) from e
    return {"logs": logs}


@router.get("/api/control/services")
async def control_list_services():
    """Liste tous les services avec leur etat."""
    return {"services": _get_all_services_status()}


@router.get("/api/control/{service}/detail")
async def control_service_detail(service: str):
    """Detail enrichi (health Ollama, heartbeat Screen Watcher, …)."""
    try:
        return await get_service_detail(service)
    except UnknownServiceError as exc:
        raise _unknown_service_error(service) from exc


@router.post("/api/control/{service}/start")
async def control_start_service(service: str):
    """Demarre un service specifique."""
    try:
        return _require_service_success(await _start_service(service), action="start")
    except UnknownServiceError as exc:
        raise _unknown_service_error(service) from exc


@router.post("/api/control/{service}/stop")
async def control_stop_service(service: str):
    """Arrete un service specifique."""
    try:
        return _require_service_success(await _stop_service(service), action="stop")
    except UnknownServiceError as exc:
        raise _unknown_service_error(service) from exc


@router.post("/api/control/{service}/restart")
async def control_restart_service(service: str):
    """Redemarre un service (stop + start)."""
    svc = service.strip().lower().replace("-", "_")
    # Restart Ollama : SW s'arrête avec Ollama, pas de relance auto SW
    try:
        _require_service_success(await _stop_service(service), action="restart")
        await asyncio.sleep(1.0)
        result = _require_service_success(
            await _start_service(service), action="restart"
        )
    except UnknownServiceError as exc:
        raise _unknown_service_error(service) from exc
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
    failed_services: list[str] = []
    for svc in INTERNAL_SERVICES:
        try:
            _require_service_success(await _stop_service(svc), action="restart")
            await asyncio.sleep(0.5)
            r = _require_service_success(await _start_service(svc), action="restart")
            results[svc] = r
        except Exception:
            logger.exception("[control/restart-all] %s", svc)
            results[svc] = {"ok": False, "error": "service_restart_failed"}
            failed_services.append(svc)
    _raise_bulk_failure("restart", failed_services)
    return {"results": results}


@router.post("/api/control/stop-all")
async def control_stop_all():
    """Arrete tous les services internes."""
    results: dict[str, object] = {}
    failed_services: list[str] = []
    for svc in INTERNAL_SERVICES:
        try:
            r = _require_service_success(await _stop_service(svc), action="stop")
            results[svc] = r
        except Exception:
            logger.exception("[control/stop-all] %s", svc)
            results[svc] = {"ok": False, "error": "service_stop_failed"}
            failed_services.append(svc)
    _raise_bulk_failure("stop", failed_services)
    return {"results": results}


@router.post("/api/control/start-all")
async def control_start_all():
    """Demarre tous les services internes."""
    results: dict[str, object] = {}
    failed_services: list[str] = []
    for svc in INTERNAL_SERVICES:
        try:
            r = _require_service_success(await _start_service(svc), action="start")
            results[svc] = r
        except Exception:
            logger.exception("[control/start-all] %s", svc)
            results[svc] = {"ok": False, "error": "service_start_failed"}
            failed_services.append(svc)
    _raise_bulk_failure("start", failed_services)
    return {"results": results}


@router.get("/api/control/{service}/logs")
async def control_service_logs(
    service: str,
    lines: Annotated[int, Query(ge=1, le=500)] = 50,
):
    """Retourne les dernieres lignes de log pertinentes pour un service."""
    try:
        tag = _SERVICE_LOG_TAGS[service]
    except KeyError as exc:
        raise _unknown_service_error(service) from exc
    log_file = BACKEND_LOG_FILE

    if not log_file.exists():
        return {"logs": [], "message": "Pas de fichier de log"}

    try:
        return await asyncio.to_thread(
            _read_service_log_lines,
            log_file,
            tag=tag,
            lines=lines,
        )
    except Exception as e:
        logger.exception("[control/logs] %s", service)
        raise internal_error(
            "service_logs_unavailable", "Logs du service indisponibles"
        ) from e
