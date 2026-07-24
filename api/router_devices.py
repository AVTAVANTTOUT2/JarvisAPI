"""Routes des appareils distants et de l'activité écran."""

from __future__ import annotations

import asyncio
import hmac
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request

import auth
import config
import pipeline
from database import (
    consume_device_pairing_code,
    create_device_pairing_code,
    create_conversation,
    get_active_device,
    get_all_devices,
    get_app_usage,
    get_app_usage_range,
    get_current_screen_context,
    get_device_by_id,
    get_screen_activity,
    register_remote_device,
    revoke_device,
    rotate_device_token,
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
_DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


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
    if not device:
        raise HTTPException(404, "Device inconnu — enregistrez-le d'abord via /api/devices/register")
    if device.get("revoked") or not device.get("token_hash"):
        raise HTTPException(401, "Jeton device révoqué ou non configuré")
    provided = request.headers.get("x-device-token")
    provided_hash = auth.hash_token(provided) if provided else ""
    if not provided_hash or not hmac.compare_digest(provided_hash, str(device["token_hash"])):
        raise HTTPException(401, "Jeton device invalide ou manquant")


@router.post("/api/devices/pairing/start")
async def api_start_device_pairing():
    """Crée un code à usage unique depuis une session navigateur privée."""
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
        minutes=config.DEVICE_PAIRING_TTL_MINUTES
    )
    create_device_pairing_code(
        auth.hash_token(f"device-pair:{code}"),
        expires_at.isoformat(timespec="seconds"),
    )
    return {
        "code": code,
        "expires_at": expires_at.isoformat(timespec="seconds") + "Z",
    }


@router.post("/api/devices/register")
async def api_register_device(body: dict, request: Request):
    """Échange un code de pairage à usage unique contre un jeton retourné une fois."""
    device_id = (body.get("device_id") or "").strip()
    device_name = (body.get("device_name") or "").strip() or device_id
    if not _DEVICE_ID_RE.fullmatch(device_id):
        raise HTTPException(
            400,
            "`device_id` requis (1-128 caractères : lettres, chiffres, point, tiret ou underscore)",
        )

    pairing_code = str(body.get("pairing_code") or "").strip()
    code_hash = auth.hash_token(f"device-pair:{pairing_code}")
    client_key = request.client.host if request.client else "unknown"
    status, retry_after = consume_device_pairing_code(
        code_hash,
        client_key,
        max_attempts=config.DEVICE_PAIRING_MAX_ATTEMPTS,
        window_minutes=config.DEVICE_PAIRING_ATTEMPT_WINDOW_MINUTES,
        lockout_minutes=config.DEVICE_PAIRING_LOCKOUT_MINUTES,
    )
    if status == "blocked":
        raise HTTPException(
            429,
            "Trop de tentatives de pairage",
            headers={"Retry-After": str(retry_after)},
        )
    if status != "ok":
        raise HTTPException(401, "Code de pairage invalide, expiré ou déjà utilisé")

    token = secrets.token_urlsafe(48)
    created = register_remote_device(
        device_id=device_id,
        device_name=device_name[:120],
        device_type=str(body.get("device_type") or "desktop")[:40],
        ip_tailscale=str(body.get("ip_tailscale") or "")[:64] or None,
        token_hash=auth.hash_token(token),
    )
    if not created:
        raise HTTPException(
            409,
            "Device déjà enregistré — utilisez la rotation de jeton depuis une session admin",
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
async def api_device_tts(device_id: str, request: Request):
    """Endpoint polling — l'agent distant récupère un MP3 base64 à jouer."""
    _require_device_token(device_id, request)
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


@router.post("/api/devices/{device_id}/token/rotate")
async def api_rotate_device_token(device_id: str):
    """Réémet un jeton depuis une session admin et invalide immédiatement l'ancien."""
    token = secrets.token_urlsafe(48)
    if not rotate_device_token(device_id, auth.hash_token(token)):
        raise HTTPException(404, "Device introuvable")
    return {"ok": True, "token": token, "device_id": device_id}


@router.post("/api/devices/{device_id}/revoke")
async def api_revoke_device(device_id: str):
    if not revoke_device(device_id):
        raise HTTPException(404, "Device introuvable ou déjà révoqué")
    _device_tts_queues.pop(device_id, None)
    return {"ok": True, "device_id": device_id}


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
