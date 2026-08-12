#!/usr/bin/env python3
"""
JARVIS Supervisor — processus permanent qui controle tous les services.
Port 9000 — toujours actif, sert le frontend desktop, proxy vers le backend.

Frontend bureau unique : export statique frontend/out (Next.js).
Ce processus ne s'arrete JAMAIS depuis l'UI.
"""

from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import signal
import socket
from database import dbapi as sqlite3
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import Response

import auth
from api.middleware import (
    browser_websocket_origin_allowed,
    configured_cors_origins,
    security_middleware,
)
from core.frontend_resolution import (
    log_lines_for_resolution,
    resolve_desktop_frontend,
)
from core.frontend_static import register_desktop_frontend_routes
from core.network_security import validate_supervisor_network_bind
from core.supervisor_auth import (
    load_supervisor_control_token,
    supervisor_control_headers,
)

# ── Configuration ───────────────────────────────────────────────────────
PROJECT_DIR = Path(__file__).parent.resolve()
VENV_PYTHON = str(PROJECT_DIR / "venv" / "bin" / "python")

from env_loader import load_jarvis_env  # noqa: E402

load_jarvis_env()
import config  # noqa: E402

# Source unique : `config.SUPERVISOR_PORT`. Le backend a besoin de la même
# valeur pour indiquer où vit le plan de contrôle, et deux lectures
# indépendantes finiraient par diverger.
SUPERVISOR_PORT = config.SUPERVISOR_PORT
BACKEND_PORT = config.WEB_PORT
CERT_PATH = config.SSL_CERT_PATH
KEY_PATH = config.SSL_KEY_PATH


def _backend_scheme() -> str:
    return "https" if config.WEB_USE_HTTPS else "http"


def _backend_url() -> str:
    return f"{_backend_scheme()}://127.0.0.1:{BACKEND_PORT}"


def _backend_http_verify() -> str | bool:
    """Vérifie TLS du backend local — CA auto-signée JARVIS, pas de TrustAll."""
    if config.WEB_USE_HTTPS:
        return str(CERT_PATH)
    return True


BACKEND_URL = _backend_url()
# Résolution basée sur PROJECT_DIR (fichier), pas sur os.getcwd()
FRONTEND_RESOLUTION = resolve_desktop_frontend(PROJECT_DIR)
LOGS_DIR = PROJECT_DIR / "data" / "logs"
LOCK_PATH = "/tmp/jarvis_supervisor.lock"

# ── Logging ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] supervisor: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("supervisor")

# ── FastAPI App ─────────────────────────────────────────────────────────
app = FastAPI(title="JARVIS Supervisor", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=configured_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.middleware("http")(security_middleware)

# ── Etat global ─────────────────────────────────────────────────────────
_start_time = time.time()
_managed: dict[str, subprocess.Popen | None] = {
    "backend": None, "tv_dashboard": None, "ollama": None, "claw3d": None,
}
_caffeinate_proc: subprocess.Popen | None = None
_ws_clients: set[WebSocket] = set()
_health_check_task: asyncio.Task | None = None
_backend_restart_count: int = 0
_health_check_interval: int = int(os.getenv("SUPERVISOR_HEALTH_CHECK_S", "10"))
_resource_guard: Any = None  # ResourceGuard | None — init lazy au 1er tick


# ── HTTP client partage (connection pooling) ────────────────────────────
_http = httpx.AsyncClient(
    timeout=httpx.Timeout(30.0, connect=5.0),
    limits=httpx.Limits(max_keepalive_connections=5, max_connections=20),
    verify=_backend_http_verify(),
)


def _supervisor_error(
    status_code: int,
    code: str,
    message: str,
    *,
    context: dict[str, str] | None = None,
) -> HTTPException:
    """Construit une erreur publique stable sans détail d'exception interne."""
    detail: dict[str, Any] = {"code": code, "message": message}
    if context:
        detail["context"] = context
    return HTTPException(status_code=status_code, detail=detail)


def _control_result_or_error(
    result: dict[str, Any],
    *,
    service: str,
    action: str,
) -> dict[str, Any]:
    """Transforme les échecs historiques ``ok:false`` en statut HTTP réel."""
    if result.get("ok") is not False:
        return result

    declared_code = result.get("code")
    declared_message = result.get("message")
    internal_error = result.get("error")
    if (
        service == "screen_watcher"
        and action == "start"
        and isinstance(internal_error, str)
        and "ollama" in internal_error.casefold()
    ):
        declared_code = "ollama_required"
        declared_message = "Ollama doit être démarré avant Screen Watcher"
    code = (
        str(declared_code)
        if isinstance(declared_code, str) and declared_code
        else f"service_{action}_failed"
    )
    if code == "service_not_found":
        status_code = 404
    else:
        status_code = 503

    if (
        isinstance(declared_message, str)
        and declared_message.strip()
        and isinstance(declared_code, str)
    ):
        message = declared_message.strip()
    else:
        action_label = {
            "start": "démarrer",
            "stop": "arrêter",
            "restart": "redémarrer",
        }.get(action, "contrôler")
        message = f"Impossible de {action_label} le service {service}"
    raise _supervisor_error(
        status_code,
        code,
        message,
        context={"service": service, "action": action},
    )


async def _run_sync_control(
    operation: Callable[[str], dict[str, Any]],
    service: str,
    action: str,
) -> dict[str, Any]:
    try:
        result = await asyncio.to_thread(operation, service)
    except Exception:
        log.exception("Échec inattendu contrôle %s/%s", service, action)
        raise _supervisor_error(
            500,
            "supervisor_control_failed",
            "Le superviseur n'a pas pu exécuter cette action",
            context={"service": service, "action": action},
        ) from None
    return _control_result_or_error(result, service=service, action=action)


def _validate_supervisor_startup_security() -> None:
    """Refuse un bind distant sans verrou et prépare le secret inter-processus."""
    try:
        auth_configured = auth.is_configured()
    except (OSError, sqlite3.Error):
        auth_configured = False
    validate_supervisor_network_bind(
        host=config.WEB_HOST,
        allow_network_bind=config.WEB_ALLOW_NETWORK_BIND,
        https_enabled=config.WEB_HTTPS,
        https_behind_proxy=config.WEB_HTTPS_BEHIND_PROXY,
        auth_configured=auth_configured,
    )
    load_supervisor_control_token(create=True)

# ── Lock file — empeche deux supervisors de tourner en meme temps ───────
_lock_file: Any = None  # objet fichier pour fcntl.flock


def _acquire_singleton_lock() -> None:
    """Empêche deux supervisors de tourner en même temps — cause #1 de port bloqué."""
    global _lock_file
    _lock_file = open(LOCK_PATH, "w")
    try:
        fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_file.write(str(os.getpid()))
        _lock_file.flush()
        log.info("Lock singleton acquis (PID %d)", os.getpid())
    except BlockingIOError:
        log.error("Un autre supervisor tourne deja (lock %s pris) — arret.", LOCK_PATH)
        sys.exit(1)


def _release_singleton_lock() -> None:
    """Libère le lock singleton au shutdown."""
    global _lock_file
    if _lock_file is not None:
        try:
            fcntl.flock(_lock_file, fcntl.LOCK_UN)
            _lock_file.close()
        except Exception:
            pass
        _lock_file = None
    try:
        Path(LOCK_PATH).unlink(missing_ok=True)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════

def _managed_pids() -> set[int]:
    """Retourne les PIDs de tous les processus geres encore vivants."""
    pids: set[int] = {os.getpid()}  # le supervisor lui-meme
    for proc in _managed.values():
        if proc is not None and proc.poll() is None:
            pids.add(proc.pid)
    return pids


def _port_open(port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except (ConnectionRefusedError, OSError, TimeoutError):
        return False


def _managed_alive(name: str) -> bool:
    proc = _managed.get(name)
    return proc is not None and proc.poll() is None


def _pids_on_port(port: int) -> list[int]:
    try:
        r = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True, text=True, timeout=3,
        )
        return [int(p) for p in r.stdout.strip().split() if p.isdigit()]
    except Exception:
        return []


def _kill_port(port: int) -> None:
    """Tue les processus sur un port, en excluant les notres (supervisor + enfants geres)."""
    our_pids = _managed_pids()
    pids = [p for p in _pids_on_port(port) if p not in our_pids]
    if not pids:
        return
    log.warning("Port %d occupe par %d processus orphelin(s) — nettoyage", port, len(pids))
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    time.sleep(0.8)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if pids:
        time.sleep(0.5)  # laisser le port se liberer


def _force_kill_port(port: int) -> None:
    """Tue TOUT processus sur le port, sans exception. Utilise kill -9 directement."""
    our_pids = _managed_pids()
    pids = [p for p in _pids_on_port(port) if p not in our_pids]
    if not pids:
        return
    log.warning("Force kill port %d — %d processus resistant(s) : %s", port, len(pids), pids)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
            log.warning("Processus orphelin tue sur port %d : PID %d", port, pid)
        except ProcessLookupError:
            pass
    time.sleep(1)


def _child_pids(pid: int) -> list[int]:
    """Enfants directs d'un PID (macOS / Linux via ``pgrep -P``)."""
    try:
        r = subprocess.run(
            ["pgrep", "-P", str(pid)],
            capture_output=True, text=True, timeout=3,
        )
    except Exception:
        return []
    return [int(p) for p in r.stdout.strip().split() if p.isdigit()]


def _kill_process_tree(pid: int, *, sig: int = signal.SIGTERM) -> None:
    """Tue un processus et toute sa descendance (enfants avant parent)."""
    for child in _child_pids(pid):
        _kill_process_tree(child, sig=sig)
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        pass


# Sidecars de synthèse du dépôt. Le tuple existe pour que l'ajout d'un moteur
# n'oublie pas son nettoyage : un sidecar orphelin garde ses poids en mémoire
# Metal jusqu'au redémarrage de la machine.
_TTS_SIDECAR_SCRIPTS: tuple[str, ...] = ("qwen3_local.py",)


def _kill_orphan_tts_sidecars() -> int:
    """Tue les sidecars de synthèse orphelins du dépôt (hors arbre géré).

    Un restart du backend qui ne propageait pas le signal laissait des
    instances chargées en mémoire Metal — plusieurs Go chacune. On cible
    uniquement nos propres launchers, jamais un processus tiers.
    """
    pids: list[str] = []
    for script in _TTS_SIDECAR_SCRIPTS:
        marker = str(PROJECT_DIR / "native_audio" / script)
        try:
            r = subprocess.run(
                ["pgrep", "-f", marker],
                capture_output=True, text=True, timeout=3,
            )
        except Exception:
            continue
        pids.extend(r.stdout.strip().split())

    managed = _managed_pids()
    # Seuls les PID réellement jugés orphelins peuvent être escaladés. Rejouer
    # la liste brute au SIGKILL tuerait les sidecars épargnés juste au-dessus,
    # c'est-à-dire le moteur vocal encore rattaché au backend géré.
    terminated: list[int] = []
    for raw in pids:
        if not raw.isdigit():
            continue
        pid = int(raw)
        if pid in managed or pid == os.getpid():
            continue
        # Ne pas tuer un sidecar encore rattaché au backend géré.
        try:
            ppid = int(
                subprocess.check_output(
                    ["ps", "-o", "ppid=", "-p", str(pid)],
                    text=True, timeout=2,
                ).strip()
                or "0"
            )
        except Exception:
            ppid = 0
        if ppid in managed:
            continue
        log.warning("Sidecar TTS orphelin — SIGTERM PID %d", pid)
        _kill_process_tree(pid, sig=signal.SIGTERM)
        terminated.append(pid)
    if terminated:
        time.sleep(0.8)
        # Tous les launchers listés sont couverts, puisque `terminated` est
        # alimenté par la boucle qui parcourt la liste agrégée.
        for pid in terminated:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            log.warning("Sidecar TTS résistant — SIGKILL PID %d", pid)
            _kill_process_tree(pid, sig=signal.SIGKILL)
    return len(terminated)


def _tail_log(log_name: str, lines: int = 5) -> str:
    """Lit les N dernieres lignes d'un fichier de log pour forensics au crash."""
    fpath = LOGS_DIR / log_name
    if not fpath.exists():
        return "(fichier introuvable)"
    try:
        content = fpath.read_text(errors="replace")
        all_lines = content.strip().splitlines()
        return "\n".join(all_lines[-lines:])
    except Exception as e:
        return f"(erreur lecture: {e})"


async def _broadcast(event: dict[str, Any]) -> None:
    dead: set[WebSocket] = set()
    for ws in _ws_clients:
        try:
            await ws.send_json(event)
        except Exception:
            dead.add(ws)
    _ws_clients.difference_update(dead)


# ══════════════════════════════════════════════════════════════════════════
# DEFINITIONS DES SERVICES
# ══════════════════════════════════════════════════════════════════════════

SERVICES = [
    {"id": "backend", "name": "Backend JARVIS", "description": "FastAPI principal (agents, LLM, daemons)", "category": "core", "port": BACKEND_PORT, "can_control": True},
    {"id": "tv_dashboard", "name": "TV Dashboard", "description": "Dashboard War Room (port 5174)", "category": "external", "port": 5174, "can_control": True},
    {"id": "ollama", "name": "Ollama", "description": "LLM local (qwen2.5-vl, triage)", "category": "external", "port": 11434, "can_control": True},
    {
        "id": "claw3d",
        "name": "Claw3D",
        "description": f"UI visuelle optionnelle (port {getattr(config, 'CLAW3D_PORT', 3000)})",
        "category": "external",
        "port": int(getattr(config, "CLAW3D_PORT", 3000)),
        "can_control": True,
    },
]


async def _svc_status(svc: dict) -> dict:
    sid, port = svc["id"], svc["port"]
    if sid == "ollama":
        from integrations.ollama_control import check_ollama_health

        health = await asyncio.to_thread(check_ollama_health)
        return {
            **svc,
            "running": bool(health.get("healthy")),
            "status": health.get("status"),
            "healthy": bool(health.get("healthy")),
            "latency_ms": health.get("latency_ms"),
            "models": health.get("models"),
            "vision_model": health.get("vision_model"),
            "vision_model_resolved": health.get("vision_model_resolved"),
            "vision_model_available": health.get("vision_model_available"),
            "error": health.get("error"),
            "port": health.get("port") or port,
        }

    if sid == "claw3d":
        from scripts import claw3d as claw3d_manager

        installed = await asyncio.to_thread(claw3d_manager.is_installed, PROJECT_DIR)
        running = await asyncio.to_thread(claw3d_manager.is_running, PROJECT_DIR)
        port_conflict = not running and _port_open(port)
        return {
            **svc,
            "running": running,
            "installed": installed,
            "managed": bool(getattr(config, "CLAW3D_MANAGED_BY_SUPERVISOR", True)),
            "port_conflict": port_conflict,
            "office_url": f"http://{getattr(config, 'CLAW3D_HOST', '127.0.0.1')}:{port}/office",
        }

    running = _managed_alive(sid) or _port_open(port)
    result = {**svc, "running": running}
    if sid == "backend" and running:
        try:
            resp = await _http.get(
                f"{BACKEND_URL}/api/control/services",
                timeout=5,
                headers=supervisor_control_headers(),
            )
            if resp.status_code == 200:
                result["sub_services"] = resp.json().get("services", [])
        except Exception:
            pass
    return result


async def _stop_screen_watcher_via_backend() -> dict:
    """Arrête Screen Watcher via l'API backend avant un stop Ollama."""
    if not _port_open(BACKEND_PORT):
        return {"ok": True, "message": "Backend arrete — Screen Watcher hors process"}
    try:
        log.info("Screen Watcher stop requested because Ollama is stopping")
        resp = await _http.post(
            f"{BACKEND_URL}/api/control/screen_watcher/stop",
            timeout=20,
            headers=supervisor_control_headers(),
        )
        if resp.status_code >= 400:
            log.warning(
                "Backend refuse le stop Screen Watcher avant Ollama: HTTP %d",
                resp.status_code,
            )
            return {
                "ok": False,
                "code": "screen_watcher_stop_failed",
                "message": "Screen Watcher n'a pas pu être arrêté",
            }
        result = resp.json()
        if result.get("ok") is False:
            return {
                "ok": False,
                "code": "screen_watcher_stop_failed",
                "message": "Screen Watcher n'a pas pu être arrêté",
            }
        return result
    except Exception:
        log.exception("Echec stop Screen Watcher avant Ollama")
        return {
            "ok": False,
            "code": "screen_watcher_stop_failed",
            "message": "Screen Watcher n'a pas pu être arrêté",
        }


# ══════════════════════════════════════════════════════════════════════════
# CONTROLE SERVICES
# ══════════════════════════════════════════════════════════════════════════

def _log_backend_tls_plan() -> None:
    """Affiche le protocole réellement attendu pour le backend JARVIS."""
    if config.WEB_HTTPS and not config.WEB_SSL_AVAILABLE:
        log.error(
            "WEB_HTTPS=true mais certificats introuvables — cert=%s key=%s "
            "(bash scripts/generate_ssl.sh). Le backend ne sera pas démarré en HTTP.",
            CERT_PATH,
            KEY_PATH,
        )
        return
    log.info(
        "Backend TLS planifié : %s://127.0.0.1:%d | WEB_HTTPS=%s | cert=%s | key=%s",
        _backend_scheme(),
        BACKEND_PORT,
        config.WEB_HTTPS,
        CERT_PATH if config.WEB_SSL_AVAILABLE else "(absent)",
        KEY_PATH if config.WEB_SSL_AVAILABLE else "(absent)",
    )


def _backend_responds_https() -> bool:
    if not config.WEB_USE_HTTPS:
        return False
    try:
        with httpx.Client(verify=str(CERT_PATH), timeout=2.0) as client:
            resp = client.get(f"https://127.0.0.1:{BACKEND_PORT}/api/auth/status")
            return resp.status_code < 500
    except Exception:
        return False


def _backend_responds_http() -> bool:
    try:
        with httpx.Client(timeout=2.0) as client:
            resp = client.get(f"http://127.0.0.1:{BACKEND_PORT}/api/auth/status")
            return resp.status_code < 500
    except Exception:
        return False


def _backend_protocol_mismatch() -> bool:
    """True si WEB_HTTPS est demandé mais seul HTTP répond sur le port."""
    if not config.WEB_USE_HTTPS or not _port_open(BACKEND_PORT):
        return False
    if _backend_responds_https():
        return False
    return _backend_responds_http()


def _claw3d_jarvis_origin() -> str:
    """Origine loopback du backend pour le connecteur Claw3D (lecture seule)."""
    return f"{_backend_scheme()}://127.0.0.1:{BACKEND_PORT}"


def _start_claw3d_sync() -> dict:
    """Démarre Claw3D via scripts/claw3d.py — jamais bloquant pour JARVIS."""
    from scripts import claw3d as claw3d_manager
    from scripts.claw3d import Claw3DError

    if not getattr(config, "CLAW3D_MANAGED_BY_SUPERVISOR", True):
        return {
            "ok": True,
            "skipped": True,
            "message": "Claw3D non géré par le superviseur (CLAW3D_MANAGED_BY_SUPERVISOR=false)",
        }
    if not claw3d_manager.is_installed(PROJECT_DIR):
        return {
            "ok": True,
            "skipped": True,
            "code": "claw3d_not_installed",
            "message": "Claw3D non installé — python scripts/claw3d.py install",
        }

    host = str(getattr(config, "CLAW3D_HOST", "127.0.0.1"))
    port = int(getattr(config, "CLAW3D_PORT", 3000))
    mode = str(getattr(config, "CLAW3D_MODE", "jarvis-readonly"))
    if mode not in {"mock", "null", "jarvis-readonly"}:
        mode = "jarvis-readonly"

    try:
        if claw3d_manager.is_running(PROJECT_DIR):
            return {"ok": True, "message": "Claw3D déjà actif"}
        if _port_open(port):
            return {
                "ok": False,
                "code": "service_port_conflict",
                "message": f"Port Claw3D {port} occupé par un processus non géré",
            }

        origin = _claw3d_jarvis_origin() if mode == "jarvis-readonly" else ""
        claw3d_manager.sync_managed_configuration(
            PROJECT_DIR,
            mode=mode,
            jarvis_origin=origin,
            host=host,
            port=port,
        )
        (LOGS_DIR / "claw3d.log").parent.mkdir(parents=True, exist_ok=True)
        with open(str(LOGS_DIR / "claw3d.log"), "a", encoding="utf-8") as log_file:
            proc = subprocess.Popen(
                [VENV_PYTHON, "scripts/claw3d.py", "start"],
                cwd=str(PROJECT_DIR),
                stdout=log_file,
                stderr=subprocess.STDOUT,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
            try:
                proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                proc.kill()
                return {
                    "ok": False,
                    "code": "service_start_failed",
                    "message": "Démarrage Claw3D expiré (60s)",
                }
        if proc.returncode not in (0, None):
            return {
                "ok": False,
                "code": "service_start_failed",
                "message": f"scripts/claw3d.py start a échoué (code {proc.returncode})",
            }
        _managed["claw3d"] = None
        pid = claw3d_manager.running_pid(PROJECT_DIR)
        log.info("Claw3D démarré%s — http://%s:%d/office", f" (PID {pid})" if pid else "", host, port)
        return {
            "ok": True,
            "message": f"Claw3D démarré (http://{host}:{port}/office)",
            "pid": pid,
        }
    except Claw3DError as exc:
        log.warning("Claw3D start refusé : %s", exc)
        return {"ok": False, "code": "claw3d_config_error", "message": str(exc)}
    except Exception as exc:
        log.exception("Claw3D start échoué")
        return {"ok": False, "code": "service_start_failed", "message": str(exc)}


def _stop_claw3d_sync() -> dict:
    """Arrête Claw3D via scripts/claw3d.py — idempotent."""
    from scripts import claw3d as claw3d_manager
    from scripts.claw3d import Claw3DError

    port = int(getattr(config, "CLAW3D_PORT", 3000))
    try:
        if not claw3d_manager.is_installed(PROJECT_DIR):
            _managed["claw3d"] = None
            return {"ok": True, "skipped": True, "message": "Claw3D non installé"}
        if not claw3d_manager.is_running(PROJECT_DIR):
            _managed["claw3d"] = None
            return {"ok": True, "message": "Claw3D déjà arrêté"}

        with open(str(LOGS_DIR / "claw3d.log"), "a", encoding="utf-8") as log_file:
            proc = subprocess.Popen(
                [VENV_PYTHON, "scripts/claw3d.py", "stop"],
                cwd=str(PROJECT_DIR),
                stdout=log_file,
                stderr=subprocess.STDOUT,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                _managed["claw3d"] = None
                return {
                    "ok": False,
                    "code": "service_stop_failed",
                    "message": "Arrêt Claw3D expiré — aucun processus tiers n'a été touché",
                }
        _managed["claw3d"] = None
        if proc.returncode not in (0, None):
            return {
                "ok": False,
                "code": "service_stop_failed",
                "message": f"scripts/claw3d.py stop a échoué (code {proc.returncode})",
            }
        if _port_open(port):
            return {
                "ok": False,
                "code": "service_port_conflict",
                "message": f"Port Claw3D {port} encore occupé après l'arrêt sécurisé",
            }
        log.info("Claw3D arrêté")
        return {"ok": True, "message": "Claw3D arrêté"}
    except Claw3DError as exc:
        log.warning("Claw3D stop : %s", exc)
        _managed["claw3d"] = None
        return {"ok": False, "code": "claw3d_config_error", "message": str(exc)}
    except Exception as exc:
        log.exception("Claw3D stop échoué")
        _managed["claw3d"] = None
        return {"ok": False, "code": "service_stop_failed", "message": str(exc)}


def _start_sync(sid: str) -> dict:
    if sid == "backend":
        if config.WEB_HTTPS and not config.WEB_SSL_AVAILABLE:
            return {
                "ok": False,
                "code": "service_tls_unavailable",
                "message": "Les certificats HTTPS du backend sont manquants",
            }
        if _backend_protocol_mismatch():
            log.warning(
                "Backend orphelin en HTTP sur port %d alors que WEB_HTTPS=true — redémarrage",
                BACKEND_PORT,
            )
            _force_kill_port(BACKEND_PORT)
            time.sleep(1)
        elif _port_open(BACKEND_PORT):
            managed_proc = _managed.get("backend")
            if managed_proc is not None and managed_proc.poll() is None:
                return {"ok": True, "message": "Backend deja actif"}
            # Port occupe par un processus inconnu — nettoyage force
            log.warning("Port %d occupe par un processus orphelin — nettoyage force", BACKEND_PORT)
            _kill_port(BACKEND_PORT)
            time.sleep(0.5)
            if _port_open(BACKEND_PORT):
                # Premier nettoyage insuffisant → kill -9
                log.warning("Port %d toujours occupe apres SIGTERM — kill -9 force", BACKEND_PORT)
                _force_kill_port(BACKEND_PORT)
                time.sleep(1)
                if _port_open(BACKEND_PORT):
                    return {
                        "ok": False,
                        "code": "service_start_failed",
                        "message": "Le port du backend reste occupé",
                    }
        else:
            # Port libre mais on nettoie par precaution
            _kill_port(BACKEND_PORT)
        # Sidecars TTS orphelins d'un crash précédent (hors arbre main.py).
        orphan_tts = _kill_orphan_tts_sidecars()
        if orphan_tts:
            log.warning("Nettoyage pre-start : %d sidecar(s) TTS orphelin(s)", orphan_tts)
        (LOGS_DIR / "backend.log").parent.mkdir(parents=True, exist_ok=True)
        backend_env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        if config.WEB_HTTPS:
            backend_env["WEB_HTTPS"] = "true"
        proc = subprocess.Popen(
            [VENV_PYTHON, "main.py"],
            cwd=str(PROJECT_DIR),
            stdout=open(str(LOGS_DIR / "backend.log"), "a"),
            stderr=subprocess.STDOUT,
            env=backend_env,
        )
        _managed["backend"] = proc
        log.info(
            "Backend demarre (PID %d) — %s://%s:%d",
            proc.pid,
            _backend_scheme(),
            config.WEB_HOST,
            BACKEND_PORT,
        )
        return {"ok": True, "message": f"Backend demarre (PID {proc.pid})"}

    if sid == "tv_dashboard":
        if _port_open(5174):
            return {"ok": True, "message": "TV dashboard deja actif"}
        _kill_port(5174)
        proc = subprocess.Popen(
            [VENV_PYTHON, "tv/server.py"],
            cwd=str(PROJECT_DIR),
            stdout=open(str(LOGS_DIR / "tv.log"), "a"),
            stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        _managed["tv_dashboard"] = proc
        log.info("TV dashboard demarre (PID %d)", proc.pid)
        return {"ok": True, "message": f"TV dashboard demarre (PID {proc.pid})"}

    if sid == "ollama":
        from integrations.ollama_control import start_ollama

        log.info("Ollama start requested")
        result = start_ollama()
        if result.get("pid"):
            # Conservé pour _managed_alive — process peut être hors Popen si déjà up
            _managed["ollama"] = None
        if result.get("ok"):
            log.info("Ollama healthy")
        return result

    if sid == "claw3d":
        return _start_claw3d_sync()

    return {
        "ok": False,
        "code": "service_not_found",
        "message": f"Service inconnu : {sid}",
    }


def _stop_sync(sid: str) -> dict:
    if sid == "backend":
        proc = _managed.get("backend")
        if proc and proc.poll() is None:
            # Enfants d'abord (sidecar TTS) : terminate() sur main.py seul
            # laissait des processus MLX orphelins qui empilaient la RAM.
            _kill_process_tree(proc.pid, sig=signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _kill_process_tree(proc.pid, sig=signal.SIGKILL)
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
        _managed["backend"] = None
        _kill_orphan_tts_sidecars()
        _kill_port(BACKEND_PORT)
        log.info("Backend arrete")
        return {"ok": True, "message": "Backend arrete"}

    if sid == "tv_dashboard":
        proc = _managed.get("tv_dashboard")
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        _managed["tv_dashboard"] = None
        _kill_port(5174)
        log.info("TV dashboard arrete")
        return {"ok": True, "message": "TV dashboard arrete"}

    if sid == "ollama":
        from integrations.ollama_control import stop_ollama

        log.info("Ollama stop requested")
        _managed["ollama"] = None
        result = stop_ollama()
        return result

    if sid == "claw3d":
        return _stop_claw3d_sync()

    return {
        "ok": False,
        "code": "service_not_found",
        "message": f"Service inconnu : {sid}",
    }


# ══════════════════════════════════════════════════════════════════════════
# ROUTES API — /api/supervisor/*
# ══════════════════════════════════════════════════════════════════════════

@app.get("/api/supervisor/status")
async def api_status():
    svcs = []
    for s in SERVICES:
        svcs.append(await _svc_status(s))
    return {
        "supervisor": {
            "pid": os.getpid(),
            "port": SUPERVISOR_PORT,
            "uptime_s": int(time.time() - _start_time),
            "backend_restart_count": _backend_restart_count,
            "health_check_interval_s": _health_check_interval,
        },
        "frontend": FRONTEND_RESOLUTION.to_public_dict(),
        "services": svcs,
    }


@app.get("/api/supervisor/resources")
async def api_resources():
    """État du garde-fou RAM / process (politique A : JARVIS only)."""
    if not getattr(config, "RESOURCE_GUARD_ENABLED", True):
        return {
            "enabled": False,
            "level": "ok",
            "message": "RESOURCE_GUARD_ENABLED=false",
            "processes": [],
            "actions": [],
        }
    guard = _get_resource_guard()
    from jarvis.resource_guard import config_from_settings

    guard.config = config_from_settings(config, project_dir=PROJECT_DIR)
    # Relevé seul : les actions listées sont celles que le prochain tick
    # exécuterait. Une consultation ne tue aucun process et n'arrête pas Ollama.
    report = await asyncio.to_thread(guard.snapshot)
    payload = report.to_public_dict()
    payload["enabled"] = True
    payload["read_only"] = True
    payload["dry_run"] = guard.config.dry_run
    payload["interval_s"] = float(getattr(config, "RESOURCE_GUARD_INTERVAL_S", 30))
    return payload


@app.post("/api/supervisor/{sid}/start")
async def api_start(sid: str):
    result = await _run_sync_control(_start_sync, sid, "start")
    await _broadcast({"type": "service_update", "service": sid, "action": "start", **result})
    return result


@app.post("/api/supervisor/{sid}/stop")
async def api_stop(sid: str):
    sw_result = None
    if sid == "ollama":
        sw_result = await _stop_screen_watcher_via_backend()
    result = await _run_sync_control(_stop_sync, sid, "stop")
    if sw_result is not None:
        result = {**result, "screen_watcher": sw_result}
    await _broadcast({"type": "service_update", "service": sid, "action": "stop", **result})
    return result


@app.post("/api/supervisor/{sid}/restart")
async def api_restart(sid: str):
    # Restart Ollama seul : SW arrêté avec Ollama, NON relancé automatiquement
    if sid == "ollama":
        await _stop_screen_watcher_via_backend()
    await _run_sync_control(_stop_sync, sid, "stop")
    await asyncio.sleep(2)
    result = await _run_sync_control(_start_sync, sid, "restart")
    if sid == "ollama":
        result = {
            **result,
            "screen_watcher_note": "Screen Watcher arrêté — démarrage manuel requis",
        }
    await _broadcast({"type": "service_update", "service": sid, "action": "restart", **result})
    return result


@app.post("/api/supervisor/start-all")
async def api_start_all():
    results = {}
    # Ollama d'abord (health), puis backend, puis Claw3D (connecteur lecture seule)
    for sid in ["ollama", "tv_dashboard", "backend", "claw3d"]:
        results[sid] = await _run_sync_control(_start_sync, sid, "start")
        if sid == "ollama":
            await asyncio.sleep(1)
        if sid == "backend":
            await asyncio.sleep(3)
    await _broadcast({"type": "bulk_update", "action": "start-all", "results": results})
    return {"results": results}


@app.post("/api/supervisor/stop-all")
async def api_stop_all():
    results = {}
    await _stop_screen_watcher_via_backend()
    for sid in ["claw3d", "tv_dashboard", "ollama", "backend"]:
        results[sid] = await _run_sync_control(_stop_sync, sid, "stop")
    await _broadcast({"type": "bulk_update", "action": "stop-all", "results": results})
    return {"results": results}


@app.post("/api/supervisor/restart-all")
async def api_restart_all():
    await _stop_screen_watcher_via_backend()
    for sid in ["claw3d", "tv_dashboard", "ollama", "backend"]:
        await _run_sync_control(_stop_sync, sid, "stop")
    await asyncio.sleep(2)
    results = {}
    for sid in ["ollama", "tv_dashboard", "backend", "claw3d"]:
        results[sid] = await _run_sync_control(_start_sync, sid, "restart")
        if sid == "ollama":
            await asyncio.sleep(1)
        if sid == "backend":
            await asyncio.sleep(3)
    await _broadcast({"type": "bulk_update", "action": "restart-all", "results": results})
    return {"results": results}


@app.get("/api/supervisor/{sid}/logs")
async def api_logs(sid: str, lines: int = 50):
    log_map = {
        "backend": LOGS_DIR / "backend.log",
        "tv_dashboard": LOGS_DIR / "tv.log",
        "claw3d": LOGS_DIR / "claw3d.log",
    }
    f = log_map.get(sid)
    if f is None:
        raise _supervisor_error(
            404,
            "service_not_found",
            f"Service inconnu : {sid}",
            context={"service": sid},
        )
    if not f.exists():
        return {"logs": [], "message": "Pas de logs disponibles"}
    bounded_lines = max(1, min(int(lines), 500))
    try:
        content = await asyncio.to_thread(f.read_text, errors="replace")
    except OSError:
        log.exception("Lecture du journal impossible: %s", f)
        raise _supervisor_error(
            500,
            "service_logs_unavailable",
            "Les journaux du service sont indisponibles",
            context={"service": sid},
        ) from None
    all_lines = content.splitlines()
    return {"logs": all_lines[-bounded_lines:]}


# ── Sous-services ────────────────────────────────────────────────────────

@app.get("/api/supervisor/sub-services")
async def api_sub_services(request: Request):
    if not _port_open(BACKEND_PORT):
        return {"available": False, "services": [], "message": "Backend arrete"}
    try:
        resp = await _http.get(
            f"{BACKEND_URL}/api/control/services",
            headers=supervisor_control_headers(),
        )
        if resp.status_code >= 400:
            log.warning("Inventaire sous-services refusé: HTTP %d", resp.status_code)
            raise _supervisor_error(
                502,
                "backend_control_unavailable",
                "Le contrôle du backend est indisponible",
            )
        return {"available": True, **resp.json()}
    except HTTPException:
        raise
    except Exception:
        log.exception("Inventaire des sous-services indisponible")
        raise _supervisor_error(
            502,
            "backend_control_unavailable",
            "Le contrôle du backend est indisponible",
        ) from None


@app.post("/api/supervisor/sub/{sid}/{action}")
async def api_sub_action(sid: str, action: str, request: Request):
    if not _port_open(BACKEND_PORT):
        raise _supervisor_error(
            503,
            "backend_unavailable",
            "Le backend est arrêté",
        )
    if action not in ("start", "stop", "restart"):
        raise _supervisor_error(
            400,
            "invalid_service_action",
            f"Action invalide : {action}",
        )
    try:
        resp = await _http.post(
            f"{BACKEND_URL}/api/control/{sid}/{action}",
            timeout=30,
            headers=supervisor_control_headers(),
        )
        if resp.status_code >= 400:
            log.warning(
                "Action backend %s/%s refusée: HTTP %d",
                sid,
                action,
                resp.status_code,
            )
            raise _supervisor_error(
                502,
                "backend_control_failed",
                "Le backend n'a pas pu exécuter cette action",
                context={"service": sid, "action": action},
            )
        result = resp.json()
        return _control_result_or_error(result, service=sid, action=action)
    except HTTPException:
        raise
    except Exception:
        log.exception("Action backend %s/%s indisponible", sid, action)
        raise _supervisor_error(
            502,
            "backend_control_failed",
            "Le backend n'a pas pu exécuter cette action",
            context={"service": sid, "action": action},
        ) from None


@app.get("/api/supervisor/services/ollama")
async def api_ollama_detail():
    from integrations.ollama_control import check_ollama_health

    health = await asyncio.to_thread(check_ollama_health)
    return {"ok": True, **health}


@app.post("/api/supervisor/services/ollama/{action}")
async def api_ollama_action(action: str):
    if action not in ("start", "stop", "restart"):
        raise _supervisor_error(
            400,
            "invalid_service_action",
            f"Action invalide : {action}",
        )
    if action == "start":
        return await api_start("ollama")
    if action == "stop":
        return await api_stop("ollama")
    return await api_restart("ollama")


@app.get("/api/supervisor/services/screen-watcher")
async def api_screen_watcher_detail(request: Request):
    if not _port_open(BACKEND_PORT):
        raise _supervisor_error(
            503,
            "backend_unavailable",
            "Le backend est arrêté",
        )
    try:
        resp = await _http.get(
            f"{BACKEND_URL}/api/control/screen_watcher/detail",
            headers=supervisor_control_headers(),
            timeout=10,
        )
        if resp.status_code >= 400:
            log.warning("Détail Screen Watcher refusé: HTTP %d", resp.status_code)
            raise _supervisor_error(
                502,
                "screen_watcher_unavailable",
                "Screen Watcher est indisponible",
            )
        result = resp.json()
        return _control_result_or_error(
            result,
            service="screen_watcher",
            action="inspect",
        )
    except HTTPException:
        raise
    except Exception:
        log.exception("Détail Screen Watcher indisponible")
        raise _supervisor_error(
            502,
            "screen_watcher_unavailable",
            "Screen Watcher est indisponible",
        ) from None


@app.post("/api/supervisor/services/screen-watcher/{action}")
async def api_screen_watcher_action(action: str, request: Request):
    return await api_sub_action("screen_watcher", action, request)


# ══════════════════════════════════════════════════════════════════════════
# WEBSOCKET — etat temps reel
# ══════════════════════════════════════════════════════════════════════════

@app.websocket("/ws/supervisor")
async def ws_supervisor(ws: WebSocket):
    try:
        configured = auth.is_configured()
    except (OSError, sqlite3.Error):
        configured = False
    if not configured:
        await ws.close(code=4428)
        return
    if not auth.verify_session(ws.cookies.get(config.SESSION_COOKIE_NAME)):
        await ws.close(code=4401)
        return
    if not browser_websocket_origin_allowed(ws):
        await ws.close(code=4403)
        return
    await ws.accept()
    _ws_clients.add(ws)
    try:
        svcs = []
        for s in SERVICES:
            svcs.append(await _svc_status(s))
        await ws.send_json({"type": "initial_state", "services": svcs})
        while True:
            await asyncio.sleep(2)
            svcs = []
            for s in SERVICES:
                svcs.append(await _svc_status(s))
            await ws.send_json({"type": "status_update", "services": svcs})
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.discard(ws)


# ══════════════════════════════════════════════════════════════════════════
# PROXY — /api/* vers le backend (quand actif)
# ══════════════════════════════════════════════════════════════════════════

def _build_proxy_headers(incoming: dict[str, str]) -> dict[str, str]:
    """Prépare les en-têtes transmis au backend.

    Le ``Host`` original du navigateur est CONSERVÉ : le middleware backend
    compare le hostname d'``Origin`` à celui de ``Host`` (anti-CSRF). En le
    réécrivant vers 127.0.0.1 on casserait toutes les écritures via le
    proxy ; en le conservant, la vérification reste effective (une origine
    étrangère ne matchera toujours pas le Host).
    """
    headers: dict[str, str] = {}
    for k, v in incoming.items():
        if k.lower() in ("content-length", "transfer-encoding", "connection"):
            continue
        headers[k] = v
    return headers


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_to_backend(request: Request, path: str):
    if not _port_open(BACKEND_PORT):
        raise _supervisor_error(
            503,
            "backend_unavailable",
            "Le backend est arrêté",
        )

    body = None
    try:
        body = await request.body()
    except Exception:
        pass

    headers = _build_proxy_headers(dict(request.headers))

    url = f"{_backend_url()}/api/{path}"
    if request.url.query:
        url += f"?{request.url.query.decode() if isinstance(request.url.query, bytes) else request.url.query}"

    wants_sse = "text/event-stream" in request.headers.get("accept", "")

    resp: httpx.Response | None = None
    try:
        proxied = _http.build_request(
            method=request.method, url=url, headers=headers, content=body,
        )
        if wants_sse:
            # Flux longue durée : pas de read-timeout, sinon coupure toutes les 30 s.
            proxied.extensions["timeout"] = httpx.Timeout(
                None, connect=5.0
            ).as_dict()
        resp = await _http.send(proxied, stream=True, follow_redirects=False)
        resp_headers = {}
        for k, v in resp.headers.items():
            if k.lower() in ("transfer-encoding", "content-encoding", "connection", "content-length"):
                continue
            resp_headers[k] = v

        media_type = resp.headers.get("content-type", "")
        if "text/event-stream" in media_type:
            # SSE : relayer les chunks au fil de l'eau — ne jamais bufferiser.
            # Ownership de `resp` transférée au BackgroundTask (pas de double aclose).
            from starlette.background import BackgroundTask
            from starlette.responses import StreamingResponse

            stream_resp = StreamingResponse(
                resp.aiter_raw(),
                status_code=resp.status_code,
                headers=resp_headers,
                background=BackgroundTask(resp.aclose),
            )
            resp = None
            return stream_resp

        content = await resp.aread()
        status_code = resp.status_code
        await resp.aclose()
        resp = None
        return Response(content=content, status_code=status_code, headers=resp_headers)
    except Exception:
        log.exception("Proxy backend inaccessible pour /api/%s", path)
        raise _supervisor_error(
            502,
            "backend_proxy_failed",
            "Le backend est inaccessible",
        ) from None
    finally:
        if resp is not None:
            await resp.aclose()


# ── Passthrough WebSocket /ws → backend (chat, voix) ─────────────────────
# Contrat inchangé : simple relais binaire/texte, aucune inspection.

# En-têtes relayés au backend sur le canal WebSocket.
#
# Le cookie seul ne suffit pas. Le backend applique
# ``browser_websocket_origin_allowed()``, qui compare ``Origin`` à ``Host`` et
# **refuse toute connexion sans Origin**. En n'envoyant que le cookie, le proxy
# produisait donc un 403 systématique, et le navigateur — qui reconnecte sans
# se lasser — enchaînait les tentatives : 6 640 refus relevés dans un seul
# journal de backend, et une boucle serrée qui disputait le CPU au moteur vocal
# local.
#
# On relaie donc les deux, exactement comme le proxy HTTP conserve le ``Host``
# d'origine. Ce n'est pas un affaiblissement : une page étrangère enverrait son
# propre ``Origin`` avec le ``Host`` du superviseur, et la comparaison
# échouerait toujours. Le contrôle redevient effectif au lieu d'être
# inconditionnellement faux.
_WS_FORWARDED_HEADERS = ("cookie", "origin")


def _build_ws_proxy_headers(incoming: Any) -> dict[str, str]:
    """En-têtes transmis au backend pour un WebSocket proxifié.

    ``Host`` n'est **pas** relayé tel quel : la bibliothèque cliente le réécrit
    depuis l'URI de connexion (`headers["Host"] = build_host(...)`), donc toute
    valeur passée ici serait écrasée en silence. Le backend recevait ainsi
    l'Origin du navigateur avec le Host du backend, refusait en 403, et le
    navigateur reconnectait sans fin.

    La paire réellement vue par le navigateur est donc **déclarée** dans des
    en-têtes dédiés, et le superviseur prouve son identité par le même jeton
    privé que ``/api/control/*``. La propriété vérifiée côté backend est
    inchangée — origine et hôte doivent correspondre — seule leur source
    diffère.
    """
    headers: dict[str, str] = {}
    for name in _WS_FORWARDED_HEADERS:
        value = incoming.get(name)
        if value:
            headers[name.title()] = value

    origin = incoming.get("origin")
    host = incoming.get("host")
    if origin and host:
        headers["X-Forwarded-Origin"] = origin
        headers["X-Forwarded-Host"] = host
        headers.update(supervisor_control_headers())
    return headers

@app.websocket("/ws")
async def ws_passthrough(client_ws: WebSocket):
    import ssl

    import websockets as _wslib

    await client_ws.accept()

    scheme = "wss" if config.WEB_USE_HTTPS else "ws"
    backend_ws_url = f"{scheme}://127.0.0.1:{BACKEND_PORT}/ws"
    ssl_ctx: ssl.SSLContext | None = None
    if config.WEB_USE_HTTPS:
        ssl_ctx = ssl.create_default_context(cafile=str(CERT_PATH))
        ssl_ctx.check_hostname = False

    extra_headers = _build_ws_proxy_headers(client_ws.headers)

    try:
        async with _wslib.connect(
            backend_ws_url,
            ssl=ssl_ctx,
            additional_headers=extra_headers,
            max_size=64 * 1024 * 1024,
        ) as backend_ws:
            async def client_to_backend():
                while True:
                    msg = await client_ws.receive()
                    if msg.get("type") == "websocket.disconnect":
                        break
                    if msg.get("bytes") is not None:
                        await backend_ws.send(msg["bytes"])
                    elif msg.get("text") is not None:
                        await backend_ws.send(msg["text"])

            async def backend_to_client():
                async for payload in backend_ws:
                    if isinstance(payload, bytes):
                        await client_ws.send_bytes(payload)
                    else:
                        await client_ws.send_text(payload)

            tasks = [
                asyncio.create_task(client_to_backend()),
                asyncio.create_task(backend_to_client()),
            ]
            _done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.debug("Passthrough /ws termine: %s", exc)
    finally:
        try:
            await client_ws.close()
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════
# FRONTEND — export Next.js canonique uniquement
# ══════════════════════════════════════════════════════════════════════════

register_desktop_frontend_routes(app, FRONTEND_RESOLUTION)


# ══════════════════════════════════════════════════════════════════════════
# HEALTH CHECK — surveillance automatique du backend
# ══════════════════════════════════════════════════════════════════════════


def _screen_watcher_running_for_guard() -> bool:
    """True si SW tourne, ou si l'état est inconnu (backend down).

    Conservateur : on ne stoppe Ollama que lorsqu'on *sait* que le screen
    watcher est arrêté — un backend injoignable ne doit pas déclencher un
    stop_ollama parasite.
    """
    try:
        resp = httpx.get(
            f"{BACKEND_URL}/api/control/screen_watcher/detail",
            headers=supervisor_control_headers(),
            timeout=3.0,
            verify=_backend_http_verify(),
        )
        if resp.status_code != 200:
            return True
        data = resp.json()
        if "running" in data:
            return bool(data["running"])
        status = str(data.get("status") or data.get("state") or "").lower()
        if status in {"running", "active", "started"}:
            return True
        if status in {"stopped", "idle", "disabled", "error"}:
            return False
        return True
    except Exception:
        return True


def _get_resource_guard():
    """Singleton lazy du garde-fou (évite import circulaire au chargement)."""
    global _resource_guard
    if _resource_guard is not None:
        return _resource_guard
    from jarvis.resource_guard import ResourceGuard, config_from_settings

    def _stop_ollama_for_guard() -> dict:
        try:
            httpx.post(
                f"{BACKEND_URL}/api/control/screen_watcher/stop",
                headers=supervisor_control_headers(),
                timeout=5.0,
                verify=_backend_http_verify(),
            )
        except Exception as exc:
            log.debug("resource_guard: stop SW avant ollama ignoré : %s", exc)
        return _stop_sync("ollama")

    _resource_guard = ResourceGuard(
        config_from_settings(config, project_dir=PROJECT_DIR),
        is_screen_watcher_running=_screen_watcher_running_for_guard,
        managed_pids=_managed_pids,
        kill_process_tree=_kill_process_tree,
        stop_ollama=_stop_ollama_for_guard,
    )
    return _resource_guard


def _run_resource_guard_tick() -> dict | None:
    """Exécute un tick si l'intervalle est écoulé ; None sinon."""
    if not getattr(config, "RESOURCE_GUARD_ENABLED", True):
        return None
    guard = _get_resource_guard()
    interval = float(getattr(config, "RESOURCE_GUARD_INTERVAL_S", 30))
    if not guard.should_tick(interval):
        return None
    from jarvis.resource_guard import config_from_settings

    guard.config = config_from_settings(config, project_dir=PROJECT_DIR)
    report = guard.tick()
    return report.to_public_dict()


async def _health_check_loop() -> None:
    """Boucle de fond : verifie que le backend est vivant et le redemarre si mort.

    Inclut maintenant un historique des 5 dernieres lignes du log backend
    au moment du crash, pour identifier la cause racine sans avoir a reproduire.
    """
    global _backend_restart_count
    _consecutive_failures = 0
    MAX_CONSECUTIVE_FAILURES = 3
    _last_crash_tail = ""
    _healing_triggered = False  # une seule tentative self-healing par episode de crash-loop

    while True:
        await asyncio.sleep(_health_check_interval)
        try:
            managed_proc = _managed.get("backend")
            proc_alive = managed_proc is not None and managed_proc.poll() is None
            port_open = _port_open(BACKEND_PORT)

            if not proc_alive and not port_open:
                _backend_restart_count += 1
                _consecutive_failures += 1
                crash_tail = _tail_log("backend.log", 5)
                _last_crash_tail = crash_tail
                log.warning(
                    "Backend detecte mort (restart #%d, echec #%d) — "
                    "dernieres lignes du log :\n%s",
                    _backend_restart_count, _consecutive_failures, crash_tail,
                )
                await asyncio.to_thread(_start_sync, "backend")
                await _broadcast({
                    "type": "service_update",
                    "service": "backend",
                    "action": "auto_restart",
                    "restart_count": _backend_restart_count,
                    "ok": True,
                })

            elif not proc_alive and port_open:
                # Port occupe mais pas par notre processus → orphelin resistant
                log.warning("Backend orphelin detecte sur port %d — force kill + restart", BACKEND_PORT)
                _consecutive_failures += 1
                await asyncio.to_thread(_force_kill_port, BACKEND_PORT)
                await asyncio.sleep(1)
                # Si le port est toujours occupe apres kill -9 → abandon temporaire
                if _port_open(BACKEND_PORT):
                    log.error(
                        "Port %d toujours occupe apres force kill — abandon pour ce cycle. "
                        "PIDs restants : %s",
                        BACKEND_PORT, _pids_on_port(BACKEND_PORT),
                    )
                else:
                    _backend_restart_count += 1
                    await asyncio.to_thread(_start_sync, "backend")
                    await _broadcast({
                        "type": "service_update",
                        "service": "backend",
                        "action": "orphan_cleanup",
                        "restart_count": _backend_restart_count,
                        "ok": True,
                    })

            elif proc_alive and not port_open:
                log.debug("Backend en cours de demarrage (PID %d) — port pas encore pret", managed_proc.pid)

            else:
                # Backend vivant et port ouvert → tout va bien
                _consecutive_failures = 0
                _healing_triggered = False  # nouvel episode de crash-loop possible

            # Si trop d'echecs consecutifs → alerte critique + self-healing (opt-in)
            if _consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                log.critical(
                    "ALERTE : %d echecs consecutifs de redemarrage du backend. "
                    "Verifier backend.log et supervisor.log.",
                    _consecutive_failures,
                )
                if not _healing_triggered:
                    _healing_triggered = True
                    try:
                        from scripts.self_healing import handle_crash_loop

                        asyncio.create_task(handle_crash_loop(_last_crash_tail), name="self_healing")
                    except Exception:
                        log.exception("Erreur au declenchement self-healing (ignoree, jamais bloquant)")

            # Garde-fou RAM / orphelins JARVIS (jamais Codex/IDE)
            try:
                rg = await asyncio.to_thread(_run_resource_guard_tick)
                if rg and rg.get("actions"):
                    await _broadcast({"type": "resource_guard", **rg})
            except Exception:
                log.exception("resource_guard tick échoué — ignoré")

        except Exception:
            log.exception("Erreur dans la boucle health-check — sera reessayee")


# ══════════════════════════════════════════════════════════════════════════
# LIFECYCLE
# ══════════════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _health_check_task, _caffeinate_proc
    # Startup
    _validate_supervisor_startup_security()
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log.info("Superviseur JARVIS demarre sur port %d", SUPERVISOR_PORT)
    for line in log_lines_for_resolution(FRONTEND_RESOLUTION):
        log.info("%s", line)
    _log_backend_tls_plan()
    log.info("Backend proxy -> %s", _backend_url())

    # ── Caffeinate : empeche la veille macOS (configurable) ──
    if os.getenv("JARVIS_CAFFEINATE", "false").lower() == "true":
        try:
            _caffeinate_proc = subprocess.Popen(
                ["caffeinate", "-dims", "-t", "0"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            log.info("Caffeinate actif — veille systeme desactivee (affichage, idle, disque, sleep)")
        except Exception as e:
            log.warning("Caffeinate indisponible : %s", e)

    # Ollama d'abord (health) pour que le Screen Watcher puisse s'autostarter
    ollama_autostart = getattr(config, "OLLAMA_AUTOSTART", True)
    if os.getenv("OLLAMA_AUTOSTART", "true" if ollama_autostart else "false").lower() == "true":
        try:
            log.info("Auto-start Ollama...")
            ollama_result = _start_sync("ollama")
            if not ollama_result.get("ok") and not ollama_result.get("healthy"):
                log.warning(
                    "Ollama non healthy au boot — JARVIS continue sans Screen Watcher (%s)",
                    ollama_result.get("error") or ollama_result.get("message"),
                )
        except Exception as exc:
            log.warning("Auto-start Ollama échoué : %s — suite sans vision", exc)

    if os.getenv("SUPERVISOR_AUTO_START_BACKEND", "true").lower() == "true":
        if not _port_open(BACKEND_PORT):
            log.info("Auto-start du backend...")
            _start_sync("backend")
        else:
            managed_proc = _managed.get("backend")
            if managed_proc is None or managed_proc.poll() is not None:
                log.warning("Port %d occupe au demarrage — nettoyage orphelin", BACKEND_PORT)
                _force_kill_port(BACKEND_PORT)
                time.sleep(1)
                _start_sync("backend")

    # Claw3D après le backend : connecteur lecture seule vers l'origine locale
    if getattr(config, "CLAW3D_MANAGED_BY_SUPERVISOR", True):
        try:
            log.info("Auto-start Claw3D...")
            claw_result = _start_sync("claw3d")
            if not claw_result.get("ok"):
                log.warning(
                    "Claw3D non démarré — JARVIS continue (%s)",
                    claw_result.get("message") or claw_result.get("code"),
                )
            elif claw_result.get("skipped"):
                log.info("Claw3D ignoré : %s", claw_result.get("message"))
        except Exception as exc:
            log.warning("Auto-start Claw3D échoué : %s — suite sans UI visuelle", exc)

    # Demarrer la boucle de health-check en background
    _health_check_task = asyncio.create_task(_health_check_loop(), name="health_check")

    yield
    # Shutdown — tuer TOUS les processus enfants dans l'ordre inverse
    log.info("Superviseur arrete — nettoyage des processus enfants...")

    # Arreter caffeinate
    if _caffeinate_proc and _caffeinate_proc.poll() is None:
        try:
            _caffeinate_proc.terminate()
            _caffeinate_proc.wait(timeout=5)
        except Exception:
            _caffeinate_proc.kill()
        _caffeinate_proc = None
        log.info("Caffeinate arrete")

    # Arreter le health-check
    if _health_check_task is not None and not _health_check_task.done():
        _health_check_task.cancel()
        try:
            await _health_check_task
        except asyncio.CancelledError:
            pass

    # Arreter les services geres (ordre : dependants d'abord, backend en dernier)
    for sid in ("claw3d", "tv_dashboard", "ollama", "backend"):
        try:
            _stop_sync(sid)
        except Exception:
            log.exception("Erreur lors de l'arret du service %s", sid)

    await _http.aclose()
    _release_singleton_lock()
    log.info("Superviseur proprement arrete")


app.router.lifespan_context = lifespan


# ══════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    try:
        _validate_supervisor_startup_security()
    except RuntimeError as exc:
        log.error("%s", exc)
        sys.exit(1)
    if config.WEB_HTTPS and not config.WEB_SSL_AVAILABLE:
        log.error(
            "WEB_HTTPS=true mais certificats introuvables — attendu : %s et %s",
            CERT_PATH,
            KEY_PATH,
        )
        sys.exit(1)
    _acquire_singleton_lock()
    _uvicorn_kwargs: dict[str, Any] = {
        "host": config.WEB_HOST,
        "port": SUPERVISOR_PORT,
        "log_level": "warning",
    }
    if config.WEB_USE_HTTPS:
        _uvicorn_kwargs["ssl_certfile"] = str(CERT_PATH)
        _uvicorn_kwargs["ssl_keyfile"] = str(KEY_PATH)
    uvicorn.run(app, **_uvicorn_kwargs)
