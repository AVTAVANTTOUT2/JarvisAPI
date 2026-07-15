"""Routes d'authentification et de gestion des sessions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import secrets

from fastapi import APIRouter, HTTPException, Request, Response

import auth
import config

router = APIRouter()


def _mobile_bearer(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def _require_mobile_device(request: Request) -> dict:
    device = auth.verify_mobile_token(_mobile_bearer(request))
    if not device:
        raise HTTPException(401, "Jeton mobile invalide ou révoqué")
    return device


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _set_session_cookie(response: Response, token: str, expires_at: datetime) -> None:
    max_age = max(1, int((expires_at - datetime.now()).total_seconds()))
    response.set_cookie(
        key=config.SESSION_COOKIE_NAME,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=config.WEB_HTTPS,
        samesite="strict",
        path="/",
    )


@router.get("/api/auth/status")
async def api_auth_status(request: Request):
    """État du verrou : configuré ?, session active ?, verrouillage en cours ?"""
    configured = auth.is_configured()
    locked_out, lockout_seconds = auth.is_locked_out()
    session = auth.verify_session(request.cookies.get(config.SESSION_COOKIE_NAME)) if configured else None
    return {
        "configured": configured,
        "authenticated": session is not None,
        "locked_out": locked_out,
        "lockout_seconds": lockout_seconds,
        "auto_lock_minutes": config.AUTO_LOCK_MINUTES,
    }


@router.post("/api/auth/setup")
async def api_auth_setup(body: dict, request: Request, response: Response):
    """Définit le PIN/passphrase initial (une seule fois) et ouvre une session."""
    if auth.is_configured():
        raise HTTPException(409, "Déjà configuré — utilisez /api/auth/change-secret")
    secret = (body.get("secret") or "").strip()
    try:
        auth.setup_secret(secret)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    token, expires_at = auth.create_session(
        user_agent=request.headers.get("user-agent", ""), ip=_client_ip(request)
    )
    _set_session_cookie(response, token, expires_at)
    return {"ok": True}


@router.post("/api/auth/unlock")
async def api_auth_unlock(body: dict, request: Request, response: Response):
    """Déverrouille l'app et ouvre une session (cookie httpOnly)."""
    if not auth.is_configured():
        raise HTTPException(428, "Aucun secret configuré — appelez /api/auth/setup")
    locked_out, seconds = auth.is_locked_out()
    if locked_out:
        raise HTTPException(429, f"Trop de tentatives — réessayez dans {seconds}s")

    secret = (body.get("secret") or "").strip()
    if not auth.verify_only(secret):
        raise HTTPException(401, "Secret incorrect")

    token, expires_at = auth.create_session(
        user_agent=request.headers.get("user-agent", ""), ip=_client_ip(request)
    )
    _set_session_cookie(response, token, expires_at)
    return {"ok": True}


@router.post("/api/auth/verify")
async def api_auth_verify(body: dict):
    """Ré-authentification de l'écran de verrouillage — ne touche pas à la session existante."""
    locked_out, seconds = auth.is_locked_out()
    if locked_out:
        raise HTTPException(429, f"Trop de tentatives — réessayez dans {seconds}s")
    secret = (body.get("secret") or "").strip()
    return {"ok": auth.verify_only(secret)}


@router.post("/api/auth/logout")
async def api_auth_logout(request: Request, response: Response):
    token = request.cookies.get(config.SESSION_COOKIE_NAME)
    if token:
        auth.revoke_session(token)
    response.delete_cookie(config.SESSION_COOKIE_NAME, path="/")
    return {"ok": True}


@router.post("/api/auth/change-secret")
async def api_auth_change_secret(body: dict, request: Request):
    current = (body.get("current") or "").strip()
    new = (body.get("new") or "").strip()
    try:
        ok = auth.change_secret(current, new)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if not ok:
        raise HTTPException(401, "Secret actuel incorrect")

    from database import revoke_all_sessions

    current_token = request.cookies.get(config.SESSION_COOKIE_NAME)
    token_hash = auth.hash_token(current_token) if current_token else None
    revoke_all_sessions(except_token_hash=token_hash)
    return {"ok": True}


@router.get("/api/auth/sessions")
async def api_auth_sessions(request: Request):
    from database import list_active_sessions

    current_token = request.cookies.get(config.SESSION_COOKIE_NAME)
    current_hash = auth.hash_token(current_token) if current_token else None
    sessions = list_active_sessions()
    for s in sessions:
        s["current"] = False
    if current_hash:
        current_row = auth.verify_session(current_token)
        if current_row:
            for s in sessions:
                if s["id"] == current_row["id"]:
                    s["current"] = True
    return {"sessions": sessions}


@router.post("/api/auth/sessions/{session_id}/revoke")
async def api_auth_revoke_session(session_id: int):
    from database import revoke_session_by_id

    if not revoke_session_by_id(session_id):
        raise HTTPException(404, "Session introuvable")
    return {"ok": True}


@router.post("/api/mobile/pairing/start")
async def api_mobile_pairing_start():
    """Crée un code à six chiffres, utilisable une fois pendant dix minutes."""
    from database import create_mobile_pairing_code

    code = f"{secrets.randbelow(1_000_000):06d}"
    expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=10)
    create_mobile_pairing_code(
        auth.hash_token(f"pair:{code}"), expires_at.isoformat(timespec="seconds")
    )
    return {"code": code, "expires_at": expires_at.isoformat(timespec="seconds") + "Z"}


@router.post("/api/mobile/pairing/complete")
async def api_mobile_pairing_complete(body: dict):
    """Échange un code affiché dans l'interface privée contre un jeton natif."""
    from database import consume_mobile_pairing_code, upsert_mobile_device

    code = str(body.get("code") or "").strip()
    device_id = str(body.get("device_id") or "").strip()[:128]
    if len(code) != 6 or not code.isdigit():
        raise HTTPException(400, "Code de pairage invalide")
    if not device_id:
        raise HTTPException(400, "device_id requis")
    if not consume_mobile_pairing_code(auth.hash_token(f"pair:{code}")):
        raise HTTPException(401, "Code expiré, invalide ou déjà utilisé")

    token = secrets.token_urlsafe(48)
    device = upsert_mobile_device(
        device_id=device_id,
        name=str(body.get("name") or "Samsung Galaxy")[:120],
        model=str(body.get("model") or "")[:120],
        token_hash=auth.hash_token(token),
        app_version=str(body.get("app_version") or "")[:40],
    )
    return {
        "token": token,
        "device": {
            "device_id": device["device_id"],
            "name": device["name"],
            "model": device.get("model") or "",
        },
    }


@router.post("/api/mobile/session")
async def api_mobile_session(request: Request, response: Response):
    """Transforme le jeton natif du téléphone en cookie web httpOnly."""
    device = _require_mobile_device(request)
    token, expires_at = auth.create_session(
        user_agent=request.headers.get("user-agent", ""),
        ip=_client_ip(request),
        mobile_device_id=str(device["device_id"]),
    )
    _set_session_cookie(response, token, expires_at)
    return {"ok": True, "device_id": device["device_id"]}


@router.post("/api/mobile/push-token")
async def api_mobile_push_token(body: dict, request: Request):
    from database import update_mobile_push_token

    device = _require_mobile_device(request)
    fcm_token = str(body.get("token") or "").strip()
    if not fcm_token:
        raise HTTPException(400, "Jeton FCM requis")
    update_mobile_push_token(str(device["device_id"]), fcm_token[:4096])
    return {"ok": True}


@router.post("/api/mobile/capabilities")
async def api_mobile_capabilities(body: dict, request: Request):
    from database import update_mobile_capabilities

    device = _require_mobile_device(request)
    allowed = {"push", "background_location", "wake_word"}
    capabilities = {key: bool(value) for key, value in body.items() if key in allowed}
    update_mobile_capabilities(str(device["device_id"]), capabilities)
    return {"ok": True, "capabilities": capabilities}


@router.get("/api/mobile/devices")
async def api_mobile_devices():
    from database import list_mobile_devices

    devices = list_mobile_devices()
    for device in devices:
        try:
            device["capabilities"] = json.loads(device.pop("capabilities_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            device["capabilities"] = {}
        device["push_enabled"] = bool(device["push_enabled"])
        device["revoked"] = bool(device["revoked"])
    return {"devices": devices}


@router.post("/api/mobile/devices/{device_id}/revoke")
async def api_mobile_device_revoke(device_id: str):
    from database import revoke_mobile_device

    if not revoke_mobile_device(device_id):
        raise HTTPException(404, "Téléphone introuvable ou déjà révoqué")
    return {"ok": True}
