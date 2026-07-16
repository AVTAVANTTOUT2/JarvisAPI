"""JARVIS — Entry point FastAPI + WebSocket.

Lance le serveur web local, sert l'interface SPA, et expose les routes API
+ le WebSocket de chat temps réel.

Usage :
    python main.py
    → http://localhost:8080
"""

import logging
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import config
import pipeline
from api.chat_actions import (
    _extract_action_from_text as _extract_action_from_text,
    _is_agentic_action as _is_agentic_action,
    _should_defer_action as _should_defer_action,
)
from api.chat_context import _build_enriched_context
from api.chat_processing import _process_message_internal
from api.frontend import (
    _is_mobile_device as _is_mobile_device,
    _setup_frontend,
    _setup_pwa_frontend as _setup_pwa_frontend,
)
from api.lifespan import lifespan
from api.middleware import security_middleware
from api.router_auth import router as auth_router
from api.router_conversations import router as conversations_router
from api.router_daemon import router as daemon_router
from api.router_devagent import router as devagent_router
from api.router_devices import router as devices_router
from api.router_location import router as location_router
from api.router_mobile_voice import router as mobile_voice_router
from api.router_mobile_chat import router as mobile_chat_router
from api.router_misc import router as misc_router
from api.router_people import router as people_router
from api.router_quality import router as quality_router
from api.router_recordings import router as recordings_router
from api.router_rituals import router as rituals_router
from api.router_tasks import router as tasks_router
from api.voice_processing import _process_voice_fast
from api.ws_handler import websocket_endpoint
from api.ws_session import (
    _resume_or_create_conversation as _resume_or_create_conversation,
    _ws_last_session as _ws_last_session,
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("jarvis")

BASE_DIR = Path(__file__).resolve().parent

# File handlers pour les daemons critiques (diagnostic crash)
_logs_dir = BASE_DIR / "data" / "logs"
_logs_dir.mkdir(parents=True, exist_ok=True)
_daemon_formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
for _daemon_logger_name in ("audio_daemon", "scripts.jarvis_daemon"):
    _fh = logging.FileHandler(_logs_dir / f"{_daemon_logger_name}.log")
    _fh.setLevel(logging.DEBUG)
    _fh.setFormatter(_daemon_formatter)
    logging.getLogger(_daemon_logger_name).addHandler(_fh)
    logging.getLogger(_daemon_logger_name).setLevel(logging.DEBUG)
app = FastAPI(
    title="JARVIS",
    description="Assistant personnel multi-agents",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://0.0.0.0:3000",
        "http://localhost:9000",
        "http://127.0.0.1:9000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://localhost:8081",
        "http://127.0.0.1:8081",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.middleware("http")(security_middleware)

app.include_router(auth_router)
app.include_router(conversations_router)
app.include_router(daemon_router)
app.include_router(devagent_router)
app.include_router(devices_router)
app.include_router(location_router)
app.include_router(mobile_voice_router)
app.include_router(mobile_chat_router)
app.include_router(misc_router)
app.include_router(people_router)
app.include_router(quality_router)
app.include_router(recordings_router)
app.include_router(rituals_router)
app.include_router(tasks_router)
app.websocket("/ws")(websocket_endpoint)

# ── Routes HTTP ─────────────────────────────────────────────


pipeline.configure_pipeline(
    process_message=_process_message_internal,
    process_voice=_process_voice_fast,
    build_context=_build_enriched_context,
)

_setup_frontend(app)


# ── Entry point ─────────────────────────────────────────────


def main():
    """Lance Uvicorn.

    HTTPS activé uniquement si :
      - `WEB_HTTPS=true` dans .env
      - ET les fichiers `certs/cert.pem` + `certs/key.pem` existent.

    Si WEB_HTTPS=true sans certificats valides, le processus s'arrête avec
    une erreur explicite (aucun repli HTTP silencieux).
    """
    import sys
    from pathlib import Path as _Path

    _base = _Path(__file__).resolve().parent
    _cert = config.SSL_CERT_PATH
    _key = config.SSL_KEY_PATH
    _ssl = config.WEB_USE_HTTPS

    if config.WEB_HTTPS and not config.WEB_SSL_AVAILABLE:
        logger.error(
            "[uvicorn] WEB_HTTPS=true mais certificats introuvables — "
            "attendu : %s et %s (lancez bash scripts/generate_ssl.sh)",
            _cert,
            _key,
        )
        sys.exit(1)

    _proto = "https" if _ssl else "http"
    logger.info(
        "[uvicorn] protocole=%s bind=%s:%d cert=%s key=%s",
        _proto,
        config.WEB_HOST,
        config.WEB_PORT,
        _cert if _ssl else "(n/a)",
        _key if _ssl else "(n/a)",
    )
    if not config.WEB_HTTPS:
        logger.info(
            "[uvicorn] WEB_HTTPS=false — mode HTTP (proxy PWA / dev local)",
        )

    _kwargs: dict = dict(
        host=config.WEB_HOST,
        port=config.WEB_PORT,
        reload=False,
        log_level="info",
    )
    if _ssl:
        _kwargs["ssl_certfile"] = str(_cert)
        _kwargs["ssl_keyfile"] = str(_key)

    uvicorn.run("main:app", **_kwargs)


if __name__ == "__main__":
    main()
