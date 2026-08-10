"""Verrou de session, contrôle CSRF et en-têtes de sécurité HTTP."""

from __future__ import annotations

import re
import sqlite3
from urllib.parse import urlsplit

from fastapi import Request, Response, WebSocket
from fastapi.responses import JSONResponse

import auth
import config
from core.supervisor_auth import (
    SUPERVISOR_CONTROL_HEADER,
    verify_supervisor_control_token,
)
from security_headers import SECURITY_HEADERS

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
        ("POST", "/api/auth/local-unlock"),
        ("POST", "/api/auth/verify"),
    }
)

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Routes applicatives servies hors du préfixe `/api/`. Le verrou de session ne
# regardait que `/api/`, si bien que `POST /upload` — déclaré parmi ses voisins
# `/api/*` sans leur préfixe — acceptait un fichier de n'importe qui atteignant
# le port, application verrouillée ou non. Toute route applicative hors `/api/`
# doit être listée ici ; `tests/test_security_middleware.py` refuse qu'une
# nouvelle échappe au verrou.
_PROTECTED_NON_API_PATHS = frozenset({"/upload"})

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


def _request_size_limit(method: str, path: str) -> int | None:
    """Plafond du corps pour les routes qui transportent de gros blobs."""
    if method != "POST":
        return None
    if path == "/api/mobile/voice/turn":
        return max(1, int(config.MOBILE_VOICE_MAX_REQUEST_BYTES))
    if re.fullmatch(r"/api/devices/[^/]+/screen", path):
        return max(1, int(config.REMOTE_SCREEN_MAX_REQUEST_BYTES))
    return None


def _content_length_error(request: Request) -> JSONResponse | None:
    """Refuse un Content-Length excessif avant parsing multipart/JSON."""
    limit = _request_size_limit(request.method, request.url.path)
    if limit is None:
        return None
    raw_length = request.headers.get("content-length")
    if raw_length is None:
        # Sans en-tête, le plafond n'est qu'un vœu : un corps en
        # `Transfer-Encoding: chunked` traverse ce garde-fou et se retrouve
        # entièrement bufferisé avant la moindre validation. Les clients
        # légitimes — l'agent distant en `requests`, le Companion, le
        # navigateur — annoncent tous leur longueur.
        return JSONResponse(
            {
                "detail": {
                    "code": "length_required",
                    "message": "Content-Length obligatoire sur cette route",
                }
            },
            status_code=411,
        )
    try:
        declared = int(raw_length)
    except ValueError:
        return JSONResponse(
            {"detail": {"code": "invalid_content_length", "message": "Content-Length invalide"}},
            status_code=400,
        )
    if declared < 0:
        return JSONResponse(
            {"detail": {"code": "invalid_content_length", "message": "Content-Length invalide"}},
            status_code=400,
        )
    if declared > limit:
        return JSONResponse(
            {
                "detail": {
                    "code": "payload_too_large",
                    "message": f"Corps de requête trop volumineux (maximum {limit} octets)",
                }
            },
            status_code=413,
        )
    return None


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


def _canonical_origin(value: str) -> tuple[str, str, int] | None:
    """Normalise une origine en schéma, hostname et port effectif."""
    try:
        parsed = urlsplit(value.strip())
        scheme = parsed.scheme.lower()
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if (
            scheme not in {"http", "https"}
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            return None
        port = parsed.port
    except ValueError:
        return None
    if port is None:
        port = 443 if scheme == "https" else 80
    return scheme, hostname, port


def configured_cors_origins(value: str | None = None) -> list[str]:
    """Retourne uniquement les origines navigateur exactes explicitement déclarées."""
    configured = config.CSRF_ALLOWED_ORIGINS if value is None else value
    origins: list[str] = []
    for raw in configured.split(","):
        candidate = _canonical_origin(raw)
        if candidate is None:
            continue
        scheme, hostname, port = candidate
        host = f"[{hostname}]" if ":" in hostname else hostname
        default_port = 443 if scheme == "https" else 80
        normalized = f"{scheme}://{host}"
        if port != default_port:
            normalized += f":{port}"
        if normalized not in origins:
            origins.append(normalized)
    return origins


def _csrf_origin_allowed(request: Request) -> bool:
    """Même origine exacte, ou exception de proxy explicitement configurée."""
    source = request.headers.get("origin")
    # Une mutation portée par un cookie est un flux navigateur : son Origin
    # doit être présente et exacte. Les clients natifs Bearer ne passent pas
    # par cette vérification.
    if not source:
        return False
    candidate = _canonical_origin(source)
    if candidate is None:
        return False

    host = request.headers.get("host", "")
    public_scheme = "https" if config.WEB_HTTPS_BEHIND_PROXY else request.url.scheme
    effective = _canonical_origin(f"{public_scheme}://{host}") if host else None
    if effective is not None and candidate == effective:
        return True

    configured = {
        origin
        for raw in config.CSRF_ALLOWED_ORIGINS.split(",")
        if (origin := _canonical_origin(raw))
    }
    return candidate in configured


# En-têtes par lesquels le superviseur déclare l'origine réelle du navigateur.
#
# Le proxy WebSocket ne peut pas se contenter de relayer `Host` : la
# bibliothèque cliente le réécrit systématiquement depuis l'URI de connexion
# (`websockets/client.py` : `headers["Host"] = build_host(...)`). Le backend
# recevait donc l'Origin du navigateur (port du superviseur) avec le Host du
# backend, et refusait en 403 — indéfiniment, puisque le navigateur reconnecte.
#
# Le superviseur déclare donc explicitement la paire vue côté navigateur, et
# prouve son identité par le même jeton privé que `/api/control/*`.
WS_FORWARDED_ORIGIN_HEADER = "X-Forwarded-Origin"
WS_FORWARDED_HOST_HEADER = "X-Forwarded-Host"


def _proxied_websocket_origin_allowed(ws: WebSocket) -> bool:
    """Origine déclarée par le superviseur, sur la boucle locale, avec jeton.

    La propriété vérifiée reste la même qu'en direct : l'origine annoncée par
    le navigateur doit correspondre à l'hôte qu'il a réellement visé. Seule la
    **source** de ces deux valeurs change — le proxy les rapporte au lieu que
    le backend les lise. Une page étrangère verrait son Origin rapportée telle
    quelle, et la comparaison échouerait comme avant.
    """
    client = ws.client.host if ws.client else ""
    if client not in ("127.0.0.1", "::1"):
        return False
    if not verify_supervisor_control_token(ws.headers.get(SUPERVISOR_CONTROL_HEADER)):
        return False

    declared_origin = ws.headers.get(WS_FORWARDED_ORIGIN_HEADER)
    declared_host = ws.headers.get(WS_FORWARDED_HOST_HEADER)
    if not declared_origin or not declared_host:
        return False

    candidate = _canonical_origin(declared_origin)
    if candidate is None:
        return False

    # Le schéma est pris dans l'origine déclarée, pas dans la configuration du
    # backend. Le navigateur a visé le superviseur, dont le schéma peut différer
    # de celui du backend ; comparer avec une valeur locale ferait échouer un
    # couple pourtant cohérent. Ce qui doit correspondre, c'est l'hôte et le
    # port — c'est-à-dire ce que le navigateur a réellement contacté.
    scheme, _hostname, _port = candidate
    expected = _canonical_origin(f"{scheme}://{declared_host}")
    return expected is not None and candidate == expected


def browser_websocket_origin_allowed(ws: WebSocket) -> bool:
    """Vérifie l'Origin d'un WebSocket navigateur authentifié par cookie."""
    if _proxied_websocket_origin_allowed(ws):
        return True

    source = ws.headers.get("origin")
    if not source:
        return False
    candidate = _canonical_origin(source)
    if candidate is None:
        return False

    host = ws.headers.get("host", "")
    if config.WEB_HTTPS_BEHIND_PROXY:
        public_scheme = "https"
    else:
        public_scheme = "https" if ws.url.scheme == "wss" else "http"
    effective = _canonical_origin(f"{public_scheme}://{host}") if host else None
    if effective is not None and candidate == effective:
        return True
    return candidate in {
        origin
        for raw in config.CSRF_ALLOWED_ORIGINS.split(",")
        if (origin := _canonical_origin(raw))
    }


# Routes qui ne passent PAS par le verrou de session navigateur — soit parce
# qu'elles servent à s'authentifier, soit parce qu'elles sont appelées par un
# autre mécanisme (jeton device, jeton localisation) par un client qui n'est
# pas un navigateur avec cookie de session.
def _supervisor_control_authenticated(request: Request, path: str) -> bool:
    """Canal supervisor → backend : loopback et jeton aléatoire privé requis."""
    if not path.startswith("/api/control/"):
        return False
    client = request.client.host if request.client else ""
    if client not in ("127.0.0.1", "::1"):
        return False
    return verify_supervisor_control_token(
        request.headers.get(SUPERVISOR_CONTROL_HEADER)
    )


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


_SECURITY_HEADERS = SECURITY_HEADERS


async def _dispatch_with_session_gate(request: Request, call_next) -> Response:
    """Applique le verrou de session puis délègue à la route demandée."""
    path = request.url.path
    method = request.method

    if (
        method != "OPTIONS"
        and (path.startswith("/api/") or path in _PROTECTED_NON_API_PATHS)
        and not _bypasses_session_gate(method, path)
        and not _supervisor_control_authenticated(request, path)
    ):
        try:
            configured = auth.is_configured()
        except (OSError, sqlite3.Error):
            configured = False
        if not configured:
            return JSONResponse({"error": "setup_required"}, status_code=428)

        bearer = _extract_bearer_token(request)
        mobile_device = (
            auth.verify_mobile_token(bearer)
            if bearer and _mobile_bearer_allows(method, path)
            else None
        )
        token = request.cookies.get(config.SESSION_COOKIE_NAME)
        session = None if mobile_device else auth.verify_session(token)
        if mobile_device:
            request.state.mobile_device = mobile_device
        elif session:
            request.state.session = session
        else:
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        if method in _UNSAFE_METHODS and session:
            # SameSite protège le cross-site ; le jeton synchronisé protège en
            # plus contre une application malveillante sur le même hostname.
            # L'origine est obligatoire et doit correspondre exactement
            # (schéma + hôte + port) ou figurer dans la liste dev explicite.
            csrf_token = request.headers.get("x-csrf-token")
            if not auth.verify_csrf_token(token, csrf_token) or not _csrf_origin_allowed(request):
                return JSONResponse({"error": "csrf_check_failed"}, status_code=403)

    return await call_next(request)


# Seul en-tête qu'une réponse a le droit de porter elle-même : une page HTML
# statique fournit une CSP liée par hashes à son contenu exact, donc plus
# stricte que la politique globale. Tout le reste est imposé ici.
#
# La frontière est nommée plutôt qu'implicite : accepter n'importe quel en-tête
# déjà présent laisserait une route affaiblir silencieusement `X-Frame-Options`
# ou `Referrer-Policy` en les posant avant le middleware. Un en-tête de sécurité
# global qu'un handler peut désactiver n'en est plus un.
_ROUTE_OVERRIDABLE_SECURITY_HEADERS = frozenset({"Content-Security-Policy"})


def _apply_security_headers(response: Response) -> Response:
    """Ajoute la politique HTTP commune, y compris aux erreurs anticipées."""
    for key, value in _SECURITY_HEADERS.items():
        if (
            key in _ROUTE_OVERRIDABLE_SECURITY_HEADERS
            and key in response.headers
        ):
            continue
        response.headers[key] = value
    if config.WEB_HTTPS or config.WEB_HTTPS_BEHIND_PROXY:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


async def security_middleware(request: Request, call_next):
    """En-têtes de sécurité sur toutes les réponses + verrou de session sur `/api/*`.

    Les routes listées par `_bypasses_session_gate` s'authentifient par un
    autre mécanisme (jeton device, jeton localisation) et ne sont pas
    concernées par le cookie de session — les autres routes `/api/*` exigent
    une session valide (fail-closed tant qu'aucun secret n'est configuré).

    Le bloc d'en-têtes reste volontairement extérieur au verrou : ses réponses
    anticipées 401, 403 et 428 reçoivent ainsi exactement la même politique que
    les réponses produites par les routeurs.
    """
    response = _content_length_error(request)
    if response is None:
        response = await _dispatch_with_session_gate(request, call_next)
    return _apply_security_headers(response)
