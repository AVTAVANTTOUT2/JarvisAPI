"""Routes d'authentification et de gestion des sessions."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Response

import auth
import config

router = APIRouter()


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



