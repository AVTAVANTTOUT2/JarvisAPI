"""Serveur FastAPI TV JARVIS — War Room Dashboard.

Point d'entrée dédié à l'écran de monitoring TV.
Serveur HTTP simple, IP whitelist, Jinja2 templates, endpoints JSON.

Démarrage:
    cd /Users/zeldris/JarvisAPI/tv
    python3 server.py
"""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import logging
import time
import sys
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
import uvicorn
import websockets

try:
    from . import config as cfg
except ImportError:  # lancement historique: cd tv && python3 server.py
    import config as cfg

# ── Logging ───────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [TV] %(levelname)s %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("tv-server")
# Supprimer les logs verbeux des librairies
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

# ── Templates ─────────────────────────────────────────────────
TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# ── Cache simple pour les données ─────────────────────────────
_data_cache: dict[str, tuple[float, Any]] = {}


def cached(key: str, factory, ttl: int = cfg.DATA_CACHE_TTL_SECONDS):
    """Retourne la valeur du cache ou appelle factory() si expiré."""
    now = time.monotonic()
    if key in _data_cache:
        inserted_at, val = _data_cache[key]
        if now - inserted_at < ttl:
            return val
    val = factory()
    _data_cache[key] = (now, val)
    return val


# ── File d'attente SSE pour les événements daemon audio ──────
# Capacité 50 — au-dessus, les événements sont silencieusement
# jetés (le navigateur reconnectera le SSE automatiquement).
tv_event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=50)

# ── Tâche d'écoute WebSocket vers le backend principal ───────
_ws_listener_task: asyncio.Task[Any] | None = None

_WS_RECONNECT_DELAY: float = 5.0
_WS_AUTH_RETRY_DELAY: float = 30.0
_SSE_HEARTBEAT_SECONDS: float = 25.0
_UNAUTHORIZED_CLOSE_CODES: frozenset[int] = frozenset({4401, 4403})
_TV_VOICE_STATE_TYPE: str = "tv.voice_state"
_TV_HEARTBEAT_TYPE: str = "tv.heartbeat"
# Champs de l'état vocal remontés au navigateur, dans le format historique
# attendu par l'overlay. Le backend décide déjà si les transcriptions sortent.
_VOICE_STATE_FIELDS: tuple[str, ...] = (
    "enabled",
    "wake_word_enabled",
    "continuous_mode",
    "last_interaction",
    "user_text",
    "jarvis_text",
)


def _load_control_token() -> str | None:
    """Lit le jeton privé du canal supervisor.

    Le jeton n'est jamais journalisé : en cas de problème, seuls le chemin et
    le type d'erreur sont tracés.
    """
    path = Path(cfg.SUPERVISOR_CONTROL_TOKEN_FILE)
    try:
        token = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        logger.error(
            "[tv] Jeton de contrôle absent (%s) — il est créé au démarrage du backend",
            path,
        )
        return None
    except OSError as exc:
        logger.error(
            "[tv] Jeton de contrôle illisible (%s) : %s", path, type(exc).__name__
        )
        return None
    if len(token) < cfg.MIN_CONTROL_TOKEN_LENGTH:
        logger.error(
            "[tv] Jeton de contrôle trop court (%s) — canal TV non connecté", path
        )
        return None
    return token


def _browser_payload(event: dict[str, Any]) -> dict[str, Any] | None:
    """Traduit un événement du canal TV vers le format lu par le navigateur.

    L'overlay historique attend ``audio_daemon_state`` ; le reste des types TV
    est transmis tel quel, un client qui ne les connaît pas les ignore.

    Returns:
        Le message à pousser en SSE, ou None si l'événement n'a rien à afficher.
    """
    event_type = str(event.get("type") or "")
    if not event_type or event_type == _TV_HEARTBEAT_TYPE:
        return None
    if event_type != _TV_VOICE_STATE_TYPE:
        return event

    payload = event.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    message: dict[str, Any] = {
        "type": "audio_daemon_state",
        "state": event.get("state") or "idle",
    }
    for key in _VOICE_STATE_FIELDS:
        if key in payload:
            message[key] = payload[key]
    return message


def _enqueue_browser_event(event: dict[str, Any]) -> None:
    """Dépose un message pour le flux SSE, en perdant le plus ancien si besoin."""
    try:
        tv_event_queue.put_nowait(event)
        return
    except asyncio.QueueFull:
        pass
    try:
        tv_event_queue.get_nowait()
        tv_event_queue.put_nowait(event)
    except (asyncio.QueueEmpty, asyncio.QueueFull):
        pass


async def _consume_tv_events(token: str) -> None:
    """Ouvre le canal TV du backend et relaye jusqu'à déconnexion."""
    async with websockets.connect(
        cfg.BACKEND_TV_EVENTS_URL,
        additional_headers={cfg.JARVIS_CONTROL_HEADER: token},
        ping_interval=20,
        ping_timeout=10,
        close_timeout=5,
        max_size=cfg.TV_WS_MAX_FRAME_BYTES,
    ) as ws:
        logger.info("[tv] Canal d'événements TV connecté")
        async for raw in ws:
            try:
                event: dict[str, Any] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(event, dict):
                continue
            message = _browser_payload(event)
            if message is not None:
                _enqueue_browser_event(message)


async def _ws_listener() -> None:
    """Maintient la connexion au canal TV du backend, avec reconnexion.

    Ce canal est descendant : le serveur TV n'envoie jamais rien au backend.
    Un refus d'authentification n'est pas une panne réseau — il est réessayé
    beaucoup plus lentement, le temps que l'opérateur corrige le jeton.
    """
    logger.info("[tv] Canal d'événements backend : %s", cfg.BACKEND_TV_EVENTS_URL)

    while True:
        delay = _WS_RECONNECT_DELAY
        token = _load_control_token()
        if token is None:
            await asyncio.sleep(_WS_AUTH_RETRY_DELAY)
            continue
        try:
            await _consume_tv_events(token)
        except websockets.InvalidStatus as exc:
            delay = _WS_AUTH_RETRY_DELAY
            logger.error(
                "[tv] Canal TV refusé au handshake (HTTP %s) — nouvel essai dans %.0fs",
                getattr(getattr(exc, "response", None), "status_code", "?"),
                delay,
            )
        except websockets.ConnectionClosed as exc:
            code = getattr(getattr(exc, "rcvd", None), "code", None)
            if code in _UNAUTHORIZED_CLOSE_CODES:
                delay = _WS_AUTH_RETRY_DELAY
                logger.error(
                    "[tv] Canal TV refusé (code %s) — vérifier le jeton de contrôle "
                    "et l'origine ; nouvel essai dans %.0fs",
                    code,
                    delay,
                )
            else:
                logger.warning(
                    "[tv] Canal TV fermé (code %s) — reconnexion dans %.0fs",
                    code,
                    delay,
                )
        except (
            websockets.InvalidURI,
            websockets.InvalidHandshake,
            ConnectionRefusedError,
            ConnectionResetError,
            OSError,
            asyncio.TimeoutError,
        ) as exc:
            logger.warning(
                "[tv] Canal TV indisponible (%s) — reconnexion dans %.0fs",
                type(exc).__name__,
                delay,
            )
        except Exception as exc:
            logger.warning(
                "[tv] Canal TV — erreur inattendue (%s) ; reconnexion dans %.0fs",
                type(exc).__name__,
                delay,
            )

        await asyncio.sleep(delay)


# ── Frontière de sécurité HTTP ────────────────────────────────
def _parse_network(raw: str) -> ipaddress.IPv4Network | ipaddress.IPv6Network | ipaddress.IPv4Address:
    try:
        return ipaddress.ip_network(raw, strict=False)
    except ValueError:
        return ipaddress.ip_address(raw)


WHITELIST = [_parse_network(n) for n in cfg.WHITELIST_NETWORKS]
TRUSTED_PROXIES = [_parse_network(n) for n in cfg.TRUSTED_PROXY_NETWORKS]


def _address_in_networks(ip_str: str, networks: list[Any]) -> bool:
    """Vérifie une adresse contre une liste d'adresses ou de réseaux parsés."""
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    for net in networks:
        if isinstance(net, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
            if addr.version == net.version and addr in net:
                return True
        elif addr == net:
            return True
    return False


def _token_from_request(request: Request) -> str:
    authorization = request.headers.get("Authorization", "")
    scheme, separator, value = authorization.partition(" ")
    if separator and scheme.lower() == "bearer":
        return value.strip()
    header_token = request.headers.get("X-TV-Token")
    if header_token:
        return header_token.strip()
    return request.cookies.get(cfg.TV_AUTH_COOKIE_NAME, "")


def _valid_token(candidate: str) -> bool:
    expected = cfg.TV_AUTH_TOKEN
    return bool(expected and candidate and hmac.compare_digest(candidate, expected))


def _unauthorized_response() -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"error": "Unauthorized", "message": "Jeton TV requis."},
        headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
    )


class TVSecurityMiddleware(BaseHTTPMiddleware):
    """Applique la même ACL et la même authentification à toute la surface TV."""

    async def dispatch(self, request: Request, call_next):
        client_ip = _get_client_ip(request)
        if not _is_whitelisted(client_ip):
            logger.warning("IP bloquée: %s → %s %s", client_ip, request.method, request.url.path)
            return JSONResponse(
                status_code=403,
                content={"error": "Forbidden", "message": f"IP {client_ip} non autorisée."},
            )

        if not cfg.TV_AUTH_TOKEN:
            logger.error("TV_AUTH_TOKEN absent: requête refusée en mode fail-closed")
            return JSONResponse(
                status_code=503,
                content={"error": "Service unavailable", "message": "Authentification TV non configurée."},
            )

        # Bootstrap du navigateur kiosk: le jeton n'est accepté en query que
        # sur la racine, puis retiré immédiatement de l'URL et placé en cookie.
        query_token = request.query_params.get("token") if request.url.path == "/" else None
        if query_token is not None:
            if not _valid_token(query_token):
                return _unauthorized_response()
            response = RedirectResponse(url="/", status_code=303)
            response.set_cookie(
                cfg.TV_AUTH_COOKIE_NAME,
                query_token,
                httponly=True,
                secure=cfg.TV_COOKIE_SECURE,
                samesite="strict",
                path="/",
            )
            response.headers["Cache-Control"] = "no-store"
            return response

        if not _valid_token(_token_from_request(request)):
            return _unauthorized_response()

        return await call_next(request)


def _get_client_ip(request: Request) -> str:
    """Retourne l'IP cliente sans faire confiance aux headers non déclarés."""
    direct_ip = request.client.host if request.client else "unknown"
    if not _address_in_networks(direct_ip, TRUSTED_PROXIES):
        return direct_ip

    forwarded = request.headers.get("X-Forwarded-For", "")
    hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
    if not hops:
        return direct_ip
    if any(not _is_valid_ip(hop) for hop in hops):
        return "unknown"

    # Parcours de droite à gauche: ignorer uniquement les proxies déclarés et
    # retenir le premier hop non fiable, c'est-à-dire le client observable.
    for hop in reversed([*hops, direct_ip]):
        if not _address_in_networks(hop, TRUSTED_PROXIES):
            return hop
    return hops[0]


def _is_valid_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _is_whitelisted(ip_str: str) -> bool:
    """Vérifie si l'IP est dans la whitelist."""
    return _address_in_networks(ip_str, WHITELIST)


# ── Health check backend ──────────────────────────────────────
async def _check_backend_health() -> dict:
    """Vérifie la santé du backend principal JARVIS (port 8081)."""
    import httpx
    try:
        async with httpx.AsyncClient(verify=False, timeout=3.0) as client:
            resp = await client.get(f"{cfg.BACKEND_BASE_URL}/api/status")
            if resp.status_code == 200:
                data = resp.json()
                return {"alive": True, "data": data}
    except Exception:
        pass
    return {"alive": False, "data": None}


# ── Lifespan ──────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _ws_listener_task
    cfg.validate_security_config()
    logger.info(
        "JARVIS TV War Room — Démarrage sur %s:%s",
        cfg.TV_HOST,
        cfg.TV_PORT,
    )
    logger.info("Whitelist IP: %s", cfg.WHITELIST_NETWORKS)
    logger.info("Backend principal: %s", cfg.BACKEND_BASE_URL)

    # Démarrer l'écoute WebSocket vers le backend principal
    _ws_listener_task = asyncio.create_task(_ws_listener(), name="tv_ws_listener")

    yield

    # Arrêt propre
    if _ws_listener_task:
        _ws_listener_task.cancel()
        try:
            await asyncio.wait_for(_ws_listener_task, timeout=3.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        _ws_listener_task = None

    logger.info("JARVIS TV War Room — Arrêt.")
    _data_cache.clear()


# ── Application FastAPI ───────────────────────────────────────
app = FastAPI(
    title="JARVIS TV War Room",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.add_middleware(TVSecurityMiddleware)

# Security headers middleware (Starlette BaseHTTPMiddleware)
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response


app.add_middleware(SecurityHeadersMiddleware)

# ── Static files ──────────────────────────────────────────────
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ═══════════════════════════════════════════════════════════════
# ROUTES — PAGE PRINCIPALE
# ═══════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Dashboard TV v2 — War Room militaire."""
    return templates.TemplateResponse(request, "tv-v2.html")


# ═══════════════════════════════════════════════════════════════
# ROUTES — SERVER-SENT EVENTS (overlay vocal temps reel)
# ═══════════════════════════════════════════════════════════════

@app.get("/api/events")
async def tv_events():
    """SSE stream pour la TV — événements du daemon audio en temps réel.

    Le navigateur TV s'abonne à ce endpoint et reçoit les événements
    ``audio_daemon_state`` relayés depuis le backend principal.
    Reconnexion automatique native du navigateur sur perte de connexion.
    """
    async def event_generator():
        while True:
            try:
                event = await asyncio.wait_for(
                    tv_event_queue.get(),
                    timeout=_SSE_HEARTBEAT_SECONDS,
                )
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except asyncio.TimeoutError:
                # Heartbeat pour maintenir la connexion ouverte
                yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ═══════════════════════════════════════════════════════════════
# ROUTES — API ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@app.get("/api/health")
async def health():
    """Healthcheck serveur TV."""
    backend = await _check_backend_health()
    return {
        "tv": "ok",
        "timestamp": time.time(),
        "backend": backend["alive"],
        "backend_data": backend.get("data"),
    }


@app.get("/api/weather")
async def api_weather():
    """Données météo pour Lille (Open-Meteo — gratuit, sans clé)."""
    def _fetch():
        from data_sources.weather import fetch_weather
        return fetch_weather()
    return cached("weather", _fetch, cfg.WEATHER_CACHE_SECONDS)


@app.get("/api/stats")
async def api_stats():
    """Stats serveur (CPU, RAM, disque, services, backend status)."""
    from data_sources.server_stats import get_server_stats
    backend = await _check_backend_health()
    return {**await get_server_stats(), "backend": backend["alive"], "backend_data": backend.get("data")}


@app.get("/api/rituals")
async def api_rituals():
    """Citation ironique du jour + score productivité (table daily_rituals)."""
    def _fetch():
        from data_sources.rituals import get_rituals
        return get_rituals()
    return cached("rituals", _fetch, 300)


@app.get("/api/automations")
async def api_automations():
    """Actions IA récentes (dernières 24h)."""
    from data_sources.automations import get_recent_actions
    return get_recent_actions()


@app.get("/api/calendar")
async def api_calendar():
    """Événements du jour (proxy vers backend principal)."""
    from data_sources.calendar import get_today_events
    return await get_today_events()


@app.get("/api/tasks")
async def api_tasks():
    """Tâches en cours (SQLite direct)."""
    from data_sources.tasks import get_active_tasks
    return get_active_tasks()


@app.get("/api/messages")
async def api_messages():
    """Derniers messages iMessage + chat JARVIS."""
    from data_sources.messages import get_recent_messages
    return get_recent_messages()


@app.get("/api/emails")
async def api_emails():
    """Résumés emails récents (SQLite email_summaries)."""
    from data_sources.emails import get_email_summaries
    return get_email_summaries()


@app.get("/api/notifications")
async def api_notifications():
    """Notifications non lues (SQLite)."""
    from data_sources.notifications import get_unread_notifications
    return get_unread_notifications()


@app.get("/api/devices")
async def api_devices():
    """Machines connectées + last heartbeat (SQLite)."""
    from data_sources.devices import get_devices_status
    return get_devices_status()


@app.get("/api/mood")
async def api_mood():
    """Dernier mood enregistré (SQLite mood_log)."""
    from data_sources.mood import get_last_mood
    return get_last_mood()


@app.get("/api/status")
async def api_status():
    """Proxy vers /api/status du backend principal (coûts API inclus)."""
    return await _check_backend_health()


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    cfg.validate_security_config()
    uvicorn.run(
        "server:app",
        host=cfg.TV_HOST,
        port=cfg.TV_PORT,
        reload=False,
        log_level="info",
        access_log=False,
        proxy_headers=False,
    )


if __name__ == "__main__":
    main()
