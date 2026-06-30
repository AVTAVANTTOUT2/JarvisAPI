#!/usr/bin/env python3
"""
JARVIS Supervisor — processus permanent qui controle tous les services.
Port 9000 — toujours actif, sert le frontend, proxy vers le backend.

Ce processus ne s'arrete JAMAIS depuis l'UI.
"""

from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import signal
import socket
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request
from starlette.responses import Response

# ── Configuration ───────────────────────────────────────────────────────
PROJECT_DIR = Path(__file__).parent.resolve()
VENV_PYTHON = str(PROJECT_DIR / "venv" / "bin" / "python")
SUPERVISOR_PORT = int(os.getenv("SUPERVISOR_PORT", "9000"))
BACKEND_PORT = int(os.getenv("WEB_PORT", "8081"))
BACKEND_URL = f"http://127.0.0.1:{BACKEND_PORT}"
DIST_DIR = PROJECT_DIR / "web" / "dist"
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
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
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
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Etat global ─────────────────────────────────────────────────────────
_start_time = time.time()
_managed: dict[str, subprocess.Popen | None] = {
    "backend": None, "tv_dashboard": None, "ollama": None, "vite_dev": None,
}
_caffeinate_proc: subprocess.Popen | None = None
_ws_clients: set[WebSocket] = set()
_health_check_task: asyncio.Task | None = None
_backend_restart_count: int = 0
_health_check_interval: int = int(os.getenv("SUPERVISOR_HEALTH_CHECK_S", "10"))


# ── HTTP client partage (connection pooling) ────────────────────────────
_http = httpx.AsyncClient(
    timeout=httpx.Timeout(30.0, connect=5.0),
    limits=httpx.Limits(max_keepalive_connections=5, max_connections=20),
)

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
    {"id": "vite_dev", "name": "Vite Dev Server", "description": "Frontend dev HMR (port 5173)", "category": "dev", "port": 5173, "can_control": True},
]


async def _svc_status(svc: dict) -> dict:
    sid, port = svc["id"], svc["port"]
    running = _managed_alive(sid) or _port_open(port)
    result = {**svc, "running": running}
    if sid == "backend" and running:
        try:
            resp = await _http.get(f"{BACKEND_URL}/api/control/services", timeout=5)
            if resp.status_code == 200:
                result["sub_services"] = resp.json().get("services", [])
        except Exception:
            pass
    return result


# ══════════════════════════════════════════════════════════════════════════
# CONTROLE SERVICES
# ══════════════════════════════════════════════════════════════════════════

def _start_sync(sid: str) -> dict:
    if sid == "backend":
        if _port_open(BACKEND_PORT):
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
                    return {"ok": False, "error": f"Impossible de liberer le port {BACKEND_PORT} (processus resistant)"}
        else:
            # Port libre mais on nettoie par precaution
            _kill_port(BACKEND_PORT)
        (LOGS_DIR / "backend.log").parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen(
            [VENV_PYTHON, "main.py"],
            cwd=str(PROJECT_DIR),
            stdout=open(str(LOGS_DIR / "backend.log"), "a"),
            stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        _managed["backend"] = proc
        log.info("Backend demarre (PID %d)", proc.pid)
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
        if _port_open(11434):
            return {"ok": True, "message": "Ollama deja actif"}
        proc = subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _managed["ollama"] = proc
        log.info("Ollama demarre (PID %d)", proc.pid)
        return {"ok": True, "message": f"Ollama demarre (PID {proc.pid})"}

    if sid == "vite_dev":
        if _port_open(5173):
            return {"ok": True, "message": "Vite deja actif"}
        _kill_port(5173)
        proc = subprocess.Popen(
            ["pnpm", "dev"],
            cwd=str(PROJECT_DIR / "web"),
            stdout=open(str(LOGS_DIR / "vite.log"), "a"),
            stderr=subprocess.STDOUT,
        )
        _managed["vite_dev"] = proc
        log.info("Vite demarre (PID %d)", proc.pid)
        return {"ok": True, "message": f"Vite demarre (PID {proc.pid})"}

    return {"ok": False, "error": f"Service inconnu : {sid}"}


def _stop_sync(sid: str) -> dict:
    if sid == "backend":
        proc = _managed.get("backend")
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        _managed["backend"] = None
        _kill_port(BACKEND_PORT)
        log.info("Backend arrete")
        return {"ok": True, "message": "Backend arrete"}

    if sid == "tv_dashboard":
        proc = _managed.get("tv_dashboard")
        if proc and proc.poll() is None:
            proc.terminate()
            try: proc.wait(timeout=3)
            except subprocess.TimeoutExpired: proc.kill()
        _managed["tv_dashboard"] = None
        _kill_port(5174)
        log.info("TV dashboard arrete")
        return {"ok": True, "message": "TV dashboard arrete"}

    if sid == "ollama":
        proc = _managed.get("ollama")
        if proc and proc.poll() is None:
            proc.terminate()
        _managed["ollama"] = None
        subprocess.run(["pkill", "-f", "ollama"], capture_output=True)
        log.info("Ollama arrete")
        return {"ok": True, "message": "Ollama arrete"}

    if sid == "vite_dev":
        proc = _managed.get("vite_dev")
        if proc and proc.poll() is None:
            proc.terminate()
        _managed["vite_dev"] = None
        _kill_port(5173)
        log.info("Vite arrete")
        return {"ok": True, "message": "Vite arrete"}

    return {"ok": False, "error": f"Service inconnu : {sid}"}


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
        "services": svcs,
    }


@app.post("/api/supervisor/{sid}/start")
async def api_start(sid: str):
    result = await asyncio.to_thread(_start_sync, sid)
    await _broadcast({"type": "service_update", "service": sid, "action": "start", **result})
    return result


@app.post("/api/supervisor/{sid}/stop")
async def api_stop(sid: str):
    result = await asyncio.to_thread(_stop_sync, sid)
    await _broadcast({"type": "service_update", "service": sid, "action": "stop", **result})
    return result


@app.post("/api/supervisor/{sid}/restart")
async def api_restart(sid: str):
    await asyncio.to_thread(_stop_sync, sid)
    await asyncio.sleep(2)
    result = await asyncio.to_thread(_start_sync, sid)
    await _broadcast({"type": "service_update", "service": sid, "action": "restart", **result})
    return result


@app.post("/api/supervisor/start-all")
async def api_start_all():
    results = {}
    for sid in ["backend", "tv_dashboard", "ollama"]:
        results[sid] = await asyncio.to_thread(_start_sync, sid)
        if sid == "backend":
            await asyncio.sleep(3)
    await _broadcast({"type": "bulk_update", "action": "start-all", "results": results})
    return {"results": results}


@app.post("/api/supervisor/stop-all")
async def api_stop_all():
    results = {}
    for sid in ["tv_dashboard", "ollama", "vite_dev", "backend"]:
        results[sid] = await asyncio.to_thread(_stop_sync, sid)
    await _broadcast({"type": "bulk_update", "action": "stop-all", "results": results})
    return {"results": results}


@app.post("/api/supervisor/restart-all")
async def api_restart_all():
    for sid in ["tv_dashboard", "ollama", "vite_dev", "backend"]:
        await asyncio.to_thread(_stop_sync, sid)
    await asyncio.sleep(2)
    results = {}
    for sid in ["backend", "tv_dashboard", "ollama"]:
        results[sid] = await asyncio.to_thread(_start_sync, sid)
        if sid == "backend":
            await asyncio.sleep(3)
    await _broadcast({"type": "bulk_update", "action": "restart-all", "results": results})
    return {"results": results}


@app.get("/api/supervisor/{sid}/logs")
async def api_logs(sid: str, lines: int = 50):
    log_map = {"backend": LOGS_DIR / "backend.log", "tv_dashboard": LOGS_DIR / "tv.log", "vite_dev": LOGS_DIR / "vite.log"}
    f = log_map.get(sid)
    if not f or not f.exists():
        return {"logs": [], "message": "Pas de logs disponibles"}
    content = f.read_text(errors="replace")
    all_lines = content.splitlines()
    return {"logs": all_lines[-lines:]}


# ── Sous-services ────────────────────────────────────────────────────────

@app.get("/api/supervisor/sub-services")
async def api_sub_services():
    if not _port_open(BACKEND_PORT):
        return {"available": False, "services": [], "message": "Backend arrete"}
    try:
        resp = await _http.get(f"{BACKEND_URL}/api/control/services")
        return {"available": True, **resp.json()}
    except Exception as exc:
        return {"available": False, "services": [], "error": str(exc)}


@app.post("/api/supervisor/sub/{sid}/{action}")
async def api_sub_action(sid: str, action: str):
    if not _port_open(BACKEND_PORT):
        return {"ok": False, "error": "Backend arrete"}
    if action not in ("start", "stop", "restart"):
        return {"ok": False, "error": f"Action invalide : {action}"}
    try:
        resp = await _http.post(f"{BACKEND_URL}/api/control/{sid}/{action}", timeout=15)
        return resp.json()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ══════════════════════════════════════════════════════════════════════════
# WEBSOCKET — etat temps reel
# ══════════════════════════════════════════════════════════════════════════

@app.websocket("/ws/supervisor")
async def ws_supervisor(ws: WebSocket):
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

@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_to_backend(request: Request, path: str):
    if not _port_open(BACKEND_PORT):
        return JSONResponse(status_code=503, content={"error": "Backend arrete", "hint": "POST /api/supervisor/backend/start"})

    body = None
    try:
        body = await request.body()
    except Exception:
        pass

    headers = {}
    for k, v in request.headers.items():
        if k.lower() in ("host", "content-length", "transfer-encoding"):
            continue
        headers[k] = v

    url = f"{BACKEND_URL}/api/{path}"
    if request.url.query:
        url += f"?{request.url.query.decode() if isinstance(request.url.query, bytes) else request.url.query}"

    try:
        resp = await _http.request(method=request.method, url=url, headers=headers, content=body, follow_redirects=False)
        resp_headers = {}
        for k, v in resp.headers.items():
            if k.lower() in ("transfer-encoding", "content-encoding", "connection"):
                continue
            resp_headers[k] = v
        return Response(content=resp.content, status_code=resp.status_code, headers=resp_headers)
    except Exception:
        return JSONResponse(status_code=502, content={"error": "Backend inaccessible"})


# ══════════════════════════════════════════════════════════════════════════
# FRONTEND — servir web/dist/
# ══════════════════════════════════════════════════════════════════════════

if DIST_DIR.exists():
    assets = DIST_DIR / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/{path:path}", response_model=None)
    async def serve_spa(path: str):
        f = DIST_DIR / path
        if f.is_file():
            return FileResponse(f)
        idx = DIST_DIR / "index.html"
        if idx.is_file():
            return FileResponse(idx)
        return JSONResponse(status_code=404, content={"error": "Page introuvable"})
else:
    @app.get("/")
    async def no_frontend():
        return JSONResponse({"message": "Frontend non builde", "hint": "cd web && pnpm run build"})


# ══════════════════════════════════════════════════════════════════════════
# HEALTH CHECK — surveillance automatique du backend
# ══════════════════════════════════════════════════════════════════════════

async def _health_check_loop() -> None:
    """Boucle de fond : verifie que le backend est vivant et le redemarre si mort.

    Inclut maintenant un historique des 5 dernieres lignes du log backend
    au moment du crash, pour identifier la cause racine sans avoir a reproduire.
    """
    global _backend_restart_count
    _consecutive_failures = 0
    MAX_CONSECUTIVE_FAILURES = 3

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

            # Si trop d'echecs consecutifs → alerte critique
            if _consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                log.critical(
                    "ALERTE : %d echecs consecutifs de redemarrage du backend. "
                    "Verifier backend.log et supervisor.log.",
                    _consecutive_failures,
                )

        except Exception:
            log.exception("Erreur dans la boucle health-check — sera reessayee")


# ══════════════════════════════════════════════════════════════════════════
# LIFECYCLE
# ══════════════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _health_check_task, _caffeinate_proc
    # Startup
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log.info("Superviseur JARVIS demarre sur port %d", SUPERVISOR_PORT)
    log.info("Frontend : %s", "web/dist/" if DIST_DIR.exists() else "NON BUILDE")
    log.info("Backend proxy -> %s", BACKEND_URL)

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
    for sid in ("vite_dev", "tv_dashboard", "ollama", "backend"):
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
    _acquire_singleton_lock()
    uvicorn.run(app, host="0.0.0.0", port=SUPERVISOR_PORT, log_level="warning")
