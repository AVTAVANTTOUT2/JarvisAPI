"""Verrou de session, contrôle CSRF et en-têtes de sécurité HTTP."""

from __future__ import annotations

import re

from fastapi import Request
from fastapi.responses import JSONResponse

import auth
import config


_DEVICE_TOKEN_POST_ROUTE_RE = re.compile(r"^/api/devices/[^/]+/(heartbeat|screen)$")
_DEVICE_TOKEN_GET_ROUTE_RE = re.compile(r"^/api/devices/[^/]+/tts$")
_CONVERSATION_DETAIL_RE = re.compile(r"^/api/conversations/\d+$")
_CONVERSATION_ACTION_RE = re.compile(r"^/api/conversations/\d+/(archive|pin)$")

# Seules les routes nécessaires pour configurer, ouvrir ou fermer une session
# navigateur sont publiques. Toute nouvelle route sous /api/auth/ reste privée
# par défaut afin d'éviter qu'une route d'administration soit exposée par
# inadvertance.
_PUBLIC_AUTH_ROUTES = frozenset(
    {
        ("GET", "/api/auth/status"),
        ("POST", "/api/auth/setup"),
        ("POST", "/api/auth/unlock"),
        ("POST", "/api/auth/verify"),
        ("POST", "/api/auth/logout"),
    }
)

# Lectures métier autorisées avec jeton mobile Bearer (Vague 1).
_MOBILE_BEARER_GET_EXACT = frozenset(
    {
        "/api/briefing",
        "/api/notifications",
        "/api/notifications/all",
        "/api/tasks",
        "/api/calendar",
        "/api/conversations",
        "/api/conversations/search",
        "/api/visits/today",
        "/api/location/status",
        "/api/mobile/location/diagnostics",
    }
)

# Mutations conversation (Vague 2 chat) — whitelist stricte, pas d'admin.
_MOBILE_BEARER_MUTATION_METHODS = frozenset({"PATCH", "DELETE", "POST"})


def _mobile_bearer_allows(method: str, path: str) -> bool:
    """True si un Bearer mobile valide peut ouvrir cette route."""
    if method == "GET":
        if path in _MOBILE_BEARER_GET_EXACT:
            return True
        if _CONVERSATION_DETAIL_RE.match(path):
            return True
        return False

    # Vague 2 : mutations conversations uniquement
    if method in _MOBILE_BEARER_MUTATION_METHODS:
        if method in ("PATCH", "DELETE") and _CONVERSATION_DETAIL_RE.match(path):
            return True
        if method == "POST" and _CONVERSATION_ACTION_RE.match(path):
            return True
    return False


def _extract_bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization") or ""
    if not header.lower().startswith("bearer "):
        return None
    token = header[7:].strip()
    return token or None


# Routes qui ne passent PAS par le verrou de session navigateur — soit parce
# qu'elles servent à s'authentifier, soit parce qu'elles sont appelées par un
# autre mécanisme (jeton device, jeton localisation) par un client qui n'est
# pas un navigateur avec cookie de session.
def _supervisor_local_control(request: Request, path: str) -> bool:
    """Appels internes supervisor → backend (localhost + header dédié)."""
    client = request.client.host if request.client else ""
    if client not in ("127.0.0.1", "::1"):
        return False
    if request.headers.get("x-jarvis-supervisor") != "1":
        return False
    return path.startswith("/api/control/")


def _bypasses_session_gate(method: str, path: str) -> bool:
    if (method, path) in _PUBLIC_AUTH_ROUTES:
        return True
    if method == "POST" and path in ("/api/location", "/api/location/batch"):
        return True
    if method == "POST" and path == "/api/devices/register":
        return True
    if method == "POST" and path in {
        "/api/mobile/pairing/complete",
        "/api/mobile/session",
        "/api/mobile/push-token",
        "/api/mobile/capabilities",
        "/api/mobile/voice/turn",
        "/api/mobile/conversations",
        "/api/mobile/chat",
        "/api/mobile/chat/confirm",
    }:
        return True
    if method == "POST" and _DEVICE_TOKEN_POST_ROUTE_RE.match(path):
        return True
    if method == "GET" and _DEVICE_TOKEN_GET_ROUTE_RE.match(path):
        return True
    return False


_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(self), microphone=(self), camera=(), payment=(), usb=()",
    "Content-Security-Policy": (
        "default-src 'self'; "
        # Next.js export statique : bootstrap RSC/hydratation via <script> inline
        # dans index.html — script-src 'self' seul bloque le montage React (page noire).
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' data: https://fonts.gstatic.com; "
        "img-src 'self' data: blob: https://*.tile.openstreetmap.org; "
        "media-src 'self' blob:; "
        "connect-src 'self' ws: wss:; "
        "worker-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    ),
}


async def security_middleware(request: Request, call_next):
    """En-têtes de sécurité sur toutes les réponses + verrou de session sur `/api/*`.

    Les routes listées par `_bypasses_session_gate` s'authentifient par un
    autre mécanisme (jeton device, jeton localisation) et ne sont pas
    concernées par le cookie de session — les autres routes `/api/*` exigent
    une session valide (fail-closed tant qu'aucun secret n'est configuré).
    """
    path = request.url.path
    method = request.method

    if (
        method != "OPTIONS"
        and path.startswith("/api/")
        and not _bypasses_session_gate(method, path)
        and not _supervisor_local_control(request, path)
    ):
        if not auth.is_configured():
            return JSONResponse({"error": "setup_required"}, status_code=428)

        token = request.cookies.get(config.SESSION_COOKIE_NAME)
        session = auth.verify_session(token)
        mobile_device = None
        if not session:
            bearer = _extract_bearer_token(request)
            if bearer and _mobile_bearer_allows(method, path):
                mobile_device = auth.verify_mobile_token(bearer)
            if not mobile_device:
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            request.state.mobile_device = mobile_device
        else:
            request.state.session = session

        if method in ("POST", "PUT", "PATCH", "DELETE") and session:
            # Défense en profondeur : le cookie de session est SameSite=Strict,
            # ce qui bloque déjà le CSRF cross-site dans tout navigateur moderne.
            # Quand un navigateur fournit Origin/Referer (quasi systématique en
            # fetch/XHR), on vérifie en plus que son HOSTNAME (parsé, pas une
            # sous-chaîne — « victim:8080.evil.com » contiendrait « victim:8080 »)
            # correspond à celui du Host demandé. Comparaison sans port : le
            # proxy Vite en dev réécrit Host vers le port backend alors que
            # l'Origin du navigateur garde le port du dev server. Les clients
            # sans Origin/Referer (scripts, curl) ne sont pas bloqués — pour
            # eux, SameSite=Strict est déjà la protection effective.
            # Bearer mobile : CSRF cookie-only — pas de check Origin.
            origin = request.headers.get("origin") or request.headers.get("referer")
            host = request.headers.get("host", "")
            if origin and host:
                from urllib.parse import urlsplit

                origin_hostname = urlsplit(origin).hostname or ""
                host_hostname = host.rsplit(":", 1)[0] if ":" in host and not host.startswith("[") else host
                if origin_hostname and origin_hostname != host_hostname:
                    return JSONResponse({"error": "csrf_check_failed"}, status_code=403)

    response = await call_next(request)
    for key, value in _SECURITY_HEADERS.items():
        response.headers[key] = value
    if config.WEB_HTTPS:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response
