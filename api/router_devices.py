"""Routes des appareils distants et de l'activité écran."""

from __future__ import annotations

import asyncio
import hmac
import logging

from fastapi import APIRouter, HTTPException, Request

import pipeline
from database import (
    create_conversation,
    get_active_device,
    get_all_devices,
    get_app_usage,
    get_app_usage_range,
    get_current_screen_context,
    get_device_by_id,
    get_screen_activity,
    register_device,
    save_screen_activity,
    set_active_device,
    update_device_heartbeat,
)

router = APIRouter()
logger = logging.getLogger("jarvis")


# ── Daemon JARVIS — devices, écran, app usage ──────────────

# File d'attente TTS par device (clé = device_id). Le serveur dépose les MP3
# encodés base64 que l'agent distant viendra chercher via /api/devices/{id}/tts.
_device_tts_queues: dict[str, asyncio.Queue] = {}


def _get_device_tts_queue(device_id: str) -> asyncio.Queue:
    if device_id not in _device_tts_queues:
        _device_tts_queues[device_id] = asyncio.Queue(maxsize=10)
    return _device_tts_queues[device_id]


def _require_device_token(device_id: str, request: Request) -> None:
    """Vérifie `X-Device-Token` contre le token émis à l'enregistrement du device.

    Sans ce contrôle, n'importe qui sur le réseau pouvait usurper un
    device_id enregistré (heartbeat, upload de screenshot, activation).
    """
    device = get_device_by_id(device_id)
    if not device or not device.get("auth_token"):
        raise HTTPException(404, "Device inconnu — enregistrez-le d'abord via /api/devices/register")
    provided = request.headers.get("x-device-token")
    if not provided or not hmac.compare_digest(provided, device["auth_token"]):
        raise HTTPException(401, "Jeton device invalide ou manquant")


@router.post("/api/devices/register")
async def api_register_device(body: dict):
    """Enregistre une machine (ou met à jour les infos). Retourne un token unique."""
    device_id = (body.get("device_id") or "").strip()
    device_name = (body.get("device_name") or "").strip() or device_id
    if not device_id:
        raise HTTPException(400, "`device_id` requis")
    token = register_device(
        device_id=device_id,
        device_name=device_name,
        device_type=body.get("device_type", "desktop"),
        ip_tailscale=body.get("ip_tailscale"),
    )
    return {"ok": True, "token": token, "device_id": device_id}


@router.post("/api/devices/{device_id}/heartbeat")
async def api_device_heartbeat(device_id: str, request: Request):
    _require_device_token(device_id, request)
    update_device_heartbeat(device_id)
    return {"ok": True}


@router.post("/api/devices/{device_id}/screen")
async def api_device_screen(device_id: str, body: dict, request: Request):
    """Reçoit un screenshot d'un agent distant et l'analyse localement (Ollama).

    Si l'analyse retourne un `notable`, on demande à Claude une notification
    courte qui est ensuite renvoyée au device via la file TTS dédiée.
    """
    _require_device_token(device_id, request)
    image_b64 = body.get("image_b64")
    declared_app = body.get("app", "unknown")
    change_pct = float(body.get("change_pct") or 0.0)

    if not image_b64:
        return {"ok": False, "message": "Pas d'image"}

    try:
        import base64 as _b64
        from io import BytesIO

        from PIL import Image as _Image

        img_bytes = _b64.b64decode(image_b64)
        img = _Image.open(BytesIO(img_bytes))
        img.load()
    except Exception as e:
        logger.warning("[device_screen] décodage image : %s", e)
        return {"ok": False, "message": "Image invalide"}

    # Analyse Ollama vision locale (sur le Mac Mini)
    analysis: dict | None = None
    try:
        from scripts.screen_watcher import screen_watcher as _sw

        analysis = await _sw._analyze_with_ollama(img)
    except Exception as e:
        logger.warning("[device_screen] analyse Ollama : %s", e)

    if analysis:
        try:
            save_screen_activity(
                device=device_id,
                app=analysis.get("app") or declared_app,
                activity=analysis.get("activity", ""),
                mood=analysis.get("mood"),
                notable=analysis.get("notable"),
                change_pct=change_pct,
            )
        except Exception as e:
            logger.warning("[device_screen] save_screen_activity : %s", e)

        notable = analysis.get("notable")
        if notable:
            try:
                temp_conv = create_conversation(agent="daemon_screen_remote")
                prompt = (
                    f"[NOTIFICATION ÉCRAN DISTANT] L'utilisateur est sur "
                    f"{analysis.get('app', '?')} ({analysis.get('activity', '?')}) "
                    f"sur {device_id}. Observation : {notable}. "
                    "Propose une aide courte (1 phrase). Si pas pertinent, réponds NULL."
                )
                result = await pipeline.process_message_internal(prompt, temp_conv, voice_mode=True)
                text = (result or {}).get("text") or ""
                if text and "NULL" not in text.upper():
                    try:
                        from audio.tts import tts as _tts

                        if _tts:
                            audio = await _tts.synthesize(text, emotion="neutral")
                            if audio:
                                import base64 as _b64x

                                queue = _get_device_tts_queue(device_id)
                                try:
                                    queue.put_nowait(_b64x.b64encode(audio).decode())
                                except asyncio.QueueFull:
                                    logger.debug("[device_screen] TTS queue pleine pour %s", device_id)
                    except Exception as e:
                        logger.warning("[device_screen] TTS synth : %s", e)
            except Exception as e:
                logger.warning("[device_screen] formulation Claude : %s", e)
    else:
        # Stocke quand même l'activité brute si l'analyse a échoué
        try:
            save_screen_activity(
                device=device_id,
                app=declared_app,
                activity="remote_no_analysis",
                change_pct=change_pct,
            )
        except Exception:
            pass

    return {"ok": True, "analysis": analysis}


@router.get("/api/devices/{device_id}/tts")
async def api_device_tts(device_id: str):
    """Endpoint polling — l'agent distant récupère un MP3 base64 à jouer."""
    queue = _get_device_tts_queue(device_id)
    try:
        audio_b64 = queue.get_nowait()
        return {"audio_b64": audio_b64}
    except asyncio.QueueEmpty:
        return {"audio_b64": None}


@router.post("/api/devices/{device_id}/activate")
async def api_activate_device(device_id: str):
    """Action utilisateur (dashboard navigateur) — protégée par la session, pas par le jeton device."""
    set_active_device(device_id)
    return {"ok": True, "active": device_id}


@router.get("/api/devices")
async def api_list_devices():
    return {"devices": get_all_devices(), "active": get_active_device()}


@router.get("/api/screen-activity")
async def api_screen_activity(hours: int = 24, device: str | None = None):
    """Liste les analyses d'écran sur N heures."""
    return {"activity": get_screen_activity(hours=hours, device=device)}


@router.get("/api/screen-activity/current")
async def api_screen_activity_current(device: str | None = None):
    """Dernier contexte écran connu (≤ 5 minutes)."""
    return {"context": get_current_screen_context(device=device)}


@router.get("/api/app-usage")
async def api_app_usage(days: int = 7, device: str | None = None):
    """Temps cumulé par application sur N jours (style Screen Time)."""
    if days <= 1:
        return {"usage": get_app_usage(device=device), "days": 1}
    return {"usage": get_app_usage_range(days=days, device=device), "days": int(days)}



