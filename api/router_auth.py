"""Routes d'authentification et de gestion des sessions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import secrets
from urllib.parse import urlsplit

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


def _auth_client_key(request: Request, channel: str = "web") -> str:
    return auth.client_rate_key(_client_ip(request), channel=channel)


def _raise_if_rate_limited(
    client_key: str,
    *,
    include_global: bool = True,
) -> None:
    status = auth.rate_limit_status(client_key, include_global=include_global)
    if not status.blocked:
        return
    scope = "globalement" if status.scope == "global" else "pour ce client"
    raise HTTPException(
        429,
        f"Trop de tentatives {scope} — réessayez dans {status.retry_after}s",
        headers={"Retry-After": str(status.retry_after)},
    )


def _is_loopback(request: Request) -> bool:
    if _client_ip(request) not in {"127.0.0.1", "::1"}:
        return False
    try:
        hostname = urlsplit(f"//{request.headers.get('host', '')}").hostname
    except ValueError:
        return False
    return hostname in {"localhost", "127.0.0.1", "::1"}


def _require_browser_session(request: Request) -> dict:
    """Défense locale pour les routes d'administration d'authentification."""
    session = getattr(request.state, "session", None)
    if session:
        return session
    token = request.cookies.get(config.SESSION_COOKIE_NAME)
    session = auth.verify_session(token)
    if not session:
        raise HTTPException(401, "Session requise")
    return session


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
async def api_auth_status(request: Request, response: Response):
    """État du verrou : configuré ?, session active ?, verrouillage en cours ?"""
    response.headers["Cache-Control"] = "no-store"
    configured = auth.is_configured()
    rate_status = auth.rate_limit_status(_auth_client_key(request))
    session_token = request.cookies.get(config.SESSION_COOKIE_NAME)
    session = auth.verify_session(session_token) if configured else None
    return {
        "configured": configured,
        "authenticated": session is not None,
        "csrf_token": (
            auth.csrf_token_for_session(session_token)
            if session is not None and session_token
            else None
        ),
        "locked_out": rate_status.blocked,
        "lockout_seconds": rate_status.retry_after,
        "lockout_scope": rate_status.scope,
        "local_recovery_available": _is_loopback(request),
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
    return {"ok": True, "csrf_token": auth.csrf_token_for_session(token)}


@router.post("/api/auth/unlock")
async def api_auth_unlock(body: dict, request: Request, response: Response):
    """Déverrouille l'app et ouvre une session (cookie httpOnly)."""
    if not auth.is_configured():
        raise HTTPException(428, "Aucun secret configuré — appelez /api/auth/setup")
    client_key = _auth_client_key(request)
    _raise_if_rate_limited(client_key)

    secret = (body.get("secret") or "").strip()
    if not auth.verify_only(secret, client_key=client_key, channel="web"):
        raise HTTPException(401, "Secret incorrect")

    token, expires_at = auth.create_session(
        user_agent=request.headers.get("user-agent", ""), ip=_client_ip(request)
    )
    _set_session_cookie(response, token, expires_at)
    return {"ok": True, "csrf_token": auth.csrf_token_for_session(token)}


@router.post("/api/auth/verify")
async def api_auth_verify(body: dict, request: Request):
    """Ré-authentification de l'écran de verrouillage — ne touche pas à la session existante."""
    client_key = _auth_client_key(request)
    _raise_if_rate_limited(client_key)
    secret = (body.get("secret") or "").strip()
    return {"ok": auth.verify_only(secret, client_key=client_key, channel="web")}


@router.post("/api/auth/local-unlock")
async def api_auth_local_unlock(body: dict, request: Request, response: Response):
    """Récupère l'accès depuis la machine JARVIS, sans contourner le secret."""
    if not _is_loopback(request):
        raise HTTPException(403, "Récupération autorisée uniquement depuis la machine locale")
    if request.headers.get("x-jarvis-local-recovery") != "1":
        raise HTTPException(403, "Confirmation locale de récupération requise")
    if not auth.is_configured():
        raise HTTPException(428, "Aucun secret configuré — appelez /api/auth/setup")

    client_key = _auth_client_key(request, channel="recovery")
    # Le canal de récupération ignore le plafond global, mais conserve son
    # propre délai progressif et son verrou client.
    _raise_if_rate_limited(client_key, include_global=False)
    secret = (body.get("secret") or "").strip()
    if not auth.verify_recovery_secret(secret):
        auth.record_failed_attempt(client_key, channel="recovery")
        raise HTTPException(401, "Secret incorrect")

    auth.clear_all_rate_limits()
    token, expires_at = auth.create_session(
        user_agent=request.headers.get("user-agent", ""), ip=_client_ip(request)
    )
    _set_session_cookie(response, token, expires_at)
    return {
        "ok": True,
        "recovered": True,
        "csrf_token": auth.csrf_token_for_session(token),
    }


@router.post("/api/auth/logout")
async def api_auth_logout(request: Request, response: Response):
    token = request.cookies.get(config.SESSION_COOKIE_NAME)
    if token:
        auth.revoke_session(token)
    response.delete_cookie(config.SESSION_COOKIE_NAME, path="/")
    return {"ok": True}


@router.post("/api/auth/change-secret")
async def api_auth_change_secret(body: dict, request: Request):
    _require_browser_session(request)
    client_key = _auth_client_key(request)
    _raise_if_rate_limited(client_key)

    current = (body.get("current") or "").strip()
    new = (body.get("new") or "").strip()
    try:
        ok = auth.change_secret(current, new, client_key=client_key)
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
    _require_browser_session(request)
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
async def api_auth_revoke_session(session_id: int, request: Request):
    _require_browser_session(request)
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
    return {
        "ok": True,
        "device_id": device["device_id"],
        "csrf_token": auth.csrf_token_for_session(token),
    }


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
