"""Serveur FastAPI TV JARVIS — War Room Dashboard.

Point d'entrée dédié à l'écran de monitoring TV.
Serveur HTTP simple, IP whitelist, Jinja2 templates, endpoints JSON.

Démarrage:
    cd /Users/zeldris/JarvisAPI/tv
    python3 server.py
"""

from __future__ import annotations

import asyncio
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
_SSE_HEARTBEAT_SECONDS: float = 25.0


async def _ws_listener() -> None:
    """Écoute le WebSocket du backend principal et relaye vers tv_event_queue.

    Se reconnecte automatiquement en cas de déconnexion.
    Filtre uniquement les événements de type ``audio_daemon_state``.
    """
    ws_url = f"ws://{cfg.BACKEND_HOST}:{cfg.BACKEND_PORT}/ws"
    logger.info("[tv] Connexion WebSocket backend : %s", ws_url)

    while True:
        try:
            async with websockets.connect(
                ws_url,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5,
            ) as ws:
                logger.info("[tv] WebSocket backend connecté")
                async for msg in ws:
                    try:
                        data: dict[str, Any] = json.loads(msg)
                    except json.JSONDecodeError:
                        continue

                    if data.get("type", "").startswith("audio_daemon_"):
                        try:
                            tv_event_queue.put_nowait(data)
                        except asyncio.QueueFull:
                            # Queue saturée → jeter silencieusement
                            # l'événement le plus ancien pour faire place
                            try:
                                tv_event_queue.get_nowait()
                                tv_event_queue.put_nowait(data)
                            except (asyncio.QueueEmpty, asyncio.QueueFull):
                                pass

        except (
            websockets.ConnectionClosed,
            websockets.InvalidURI,
            websockets.InvalidHandshake,
            ConnectionRefusedError,
            ConnectionResetError,
            OSError,
            asyncio.TimeoutError,
        ) as e:
            logger.warning("[tv] WebSocket déconnecté (%s) — reconnexion dans %.0fs", e, _WS_RECONNECT_DELAY)
        except Exception as e:
            logger.warning("[tv] WebSocket erreur inattendue (%s) — reconnexion dans %.0fs", e, _WS_RECONNECT_DELAY)

        await asyncio.sleep(_WS_RECONNECT_DELAY)


# ── Middleware IP Whitelist ────────────────────────────────────
def _parse_network(raw: str) -> ipaddress.IPv4Network | ipaddress.IPv6Network | ipaddress.IPv4Address:
    try:
        return ipaddress.ip_network(raw, strict=False)
    except ValueError:
        return ipaddress.ip_address(raw)


WHITELIST = [_parse_network(n) for n in cfg.WHITELIST_NETWORKS]


class IPWhitelistMiddleware(BaseHTTPMiddleware):
    """Middleware qui bloque toute requête hors IP whitelist."""

    async def dispatch(self, request: Request, call_next):
        client_ip = _get_client_ip(request)
        if not _is_whitelisted(client_ip):
            logger.warning("IP bloquée: %s → %s %s", client_ip, request.method, request.url.path)
            return JSONResponse(
                status_code=403,
                content={"error": "Forbidden", "message": f"IP {client_ip} non autorisée."},
            )
        return await call_next(request)


def _get_client_ip(request: Request) -> str:
    """Extrait l'IP réelle du client, en tenant compte des proxies (X-Forwarded-For)."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def _is_whitelisted(ip_str: str) -> bool:
    """Vérifie si l'IP est dans la whitelist."""
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    for net in WHITELIST:
        if isinstance(net, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
            if addr in net:
                return True
        elif addr == net:
            return True
    return False


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
app.add_middleware(IPWhitelistMiddleware)

# Security headers middleware (Starlette BaseHTTPMiddleware)
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
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
    """Page unique du dashboard TV."""
    intervals = {
        "clock": cfg.REFRESH_CLOCK,
        "weather": cfg.REFRESH_WEATHER,
        "mood": cfg.REFRESH_MOOD,
        "stats": cfg.REFRESH_STATS,
        "automations": cfg.REFRESH_AUTOMATIONS,
        "calendar": cfg.REFRESH_CALENDAR,
        "tasks": cfg.REFRESH_TASKS,
        "messages": cfg.REFRESH_MESSAGES,
        "emails": cfg.REFRESH_EMAILS,
        "notifications": cfg.REFRESH_NOTIFICATIONS,
        "devices": cfg.REFRESH_DEVICES,
    }
    return templates.TemplateResponse("tv.html", {
        "request": request,
        "intervals": intervals,
        "timezone": cfg.TIMEZONE,
    })


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
    uvicorn.run(
        "server:app",
        host=cfg.TV_HOST,
        port=cfg.TV_PORT,
        reload=False,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
