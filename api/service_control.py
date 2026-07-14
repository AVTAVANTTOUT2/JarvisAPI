"""Contrôle des services internes et externes exposés par l'API."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


# ── Service Control ──────────────────────────────────────────

# Services internes contrôlables via /api/control/
INTERNAL_SERVICES = [
    "audio_daemon",
    "email_watcher",
    "jarvis_daemon",
    "screen_watcher",
    "scheduler",
    "relationship_analyzer",
]

# Tasks asyncio lancées dynamiquement — stockées pour pouvoir les annuler
_service_tasks: dict[str, asyncio.Task] = {}


def _get_all_services_status() -> list[dict[str, object]]:
    """Retourne l'état de chaque service (interne + externe)."""
    services: list[dict[str, object]] = []

    # ── Audio Daemon ──
    try:
        from scripts.audio_daemon import audio_daemon
        services.append({
            "id": "audio_daemon",
            "name": "Audio Daemon",
            "description": "Micro natif + wake word + TTS",
            "category": "audio",
            "running": audio_daemon.enabled,
            "state": audio_daemon.state,
            "can_control": True,
        })
    except Exception:
        services.append({"id": "audio_daemon", "name": "Audio Daemon", "running": False, "can_control": True, "category": "audio", "description": "Micro natif + wake word + TTS"})

    # ── Email Watcher ──
    try:
        from scripts.email_watcher import email_watcher as _ew
        running = getattr(_ew, '_running', False) or getattr(_ew, 'running', False)
        services.append({
            "id": "email_watcher",
            "name": "Email Watcher",
            "description": "Surveillance Mail.app (analyse Haiku)",
            "category": "integrations",
            "running": running,
            "can_control": True,
        })
    except Exception:
        services.append({"id": "email_watcher", "name": "Email Watcher", "running": False, "can_control": True, "category": "integrations", "description": "Surveillance Mail.app"})

    # ── JARVIS Daemon ──
    try:
        from scripts.jarvis_daemon import daemon as _jd
        running = getattr(_jd, '_running', False) or getattr(_jd, 'running', False)
        services.append({
            "id": "jarvis_daemon",
            "name": "JARVIS Daemon",
            "description": "Sentinelle permanente (triage notifications)",
            "category": "core",
            "running": running,
            "can_control": True,
        })
    except Exception:
        services.append({"id": "jarvis_daemon", "name": "JARVIS Daemon", "running": False, "can_control": True, "category": "core", "description": "Sentinelle permanente"})

    # ── Screen Watcher ──
    try:
        from scripts.screen_watcher import screen_watcher as _sw
        running = getattr(_sw, '_running', False) or getattr(_sw, 'running', False)
        services.append({
            "id": "screen_watcher",
            "name": "Screen Watcher",
            "description": "Analyse ecran Ollama vision",
            "category": "monitoring",
            "running": running,
            "can_control": True,
        })
    except Exception:
        services.append({"id": "screen_watcher", "name": "Screen Watcher", "running": False, "can_control": True, "category": "monitoring", "description": "Analyse ecran Ollama"})

    # ── Scheduler ──
    try:
        from scripts.scheduler import scheduler as _sched
        sched_running = _sched.running if hasattr(_sched, 'running') else getattr(_sched, 'state', 0) == 1
        jobs_count = len(_sched.get_jobs()) if sched_running else 0
        services.append({
            "id": "scheduler",
            "name": "Scheduler",
            "description": f"APScheduler ({jobs_count} jobs)",
            "category": "core",
            "running": sched_running,
            "can_control": True,
        })
    except Exception:
        services.append({"id": "scheduler", "name": "Scheduler", "running": False, "can_control": True, "category": "core", "description": "APScheduler"})

    # ── Relationship Analyzer ──
    try:
        from scripts.relationship_analyzer import analyzer as _rel
        running_rel = getattr(_rel, '_running', False)
        services.append({
            "id": "relationship_analyzer",
            "name": "Relationship Analyzer",
            "description": "Analyse iMessage -> profils relationnels",
            "category": "analysis",
            "running": running_rel,
            "can_control": True,
        })
    except Exception:
        services.append({"id": "relationship_analyzer", "name": "Relationship Analyzer", "running": False, "can_control": True, "category": "analysis", "description": "Analyse iMessage"})

    # ── Processus externes (lecture seule — check via subprocess) ──

    # Ollama
    import subprocess as _sp
    try:
        r = _sp.run(["pgrep", "-f", "ollama"], capture_output=True, timeout=3)
        ollama_running = r.returncode == 0
    except Exception:
        ollama_running = False
    services.append({
        "id": "ollama",
        "name": "Ollama",
        "description": "LLM local (qwen2.5-vl, triage)",
        "category": "external",
        "running": ollama_running,
        "can_control": True,
    })

    # TV Dashboard (port 5174)
    import socket as _sock
    tv_running = False
    try:
        with _sock.create_connection(("127.0.0.1", 5174), timeout=1):
            tv_running = True
    except Exception:
        pass
    services.append({
        "id": "tv_dashboard",
        "name": "TV Dashboard",
        "description": "Dashboard War Room (port 5174)",
        "category": "external",
        "running": tv_running,
        "can_control": True,
    })

    # Vite Dev Server (port 5173)
    vite_running = False
    try:
        with _sock.create_connection(("127.0.0.1", 5173), timeout=1):
            vite_running = True
    except Exception:
        pass
    services.append({
        "id": "vite_dev",
        "name": "Vite Dev Server",
        "description": "Frontend dev (port 5173)",
        "category": "external",
        "running": vite_running,
        "can_control": False,
    })

    return services


async def _start_service(service: str) -> dict[str, object]:
    """Demarre un service par son id."""
    svc = service.strip().lower()

    if svc == "audio_daemon":
        from scripts.audio_daemon import audio_daemon as _ad
        if _ad.enabled and _ad._running:
            return {"ok": True, "message": "Deja actif"}
        _service_tasks["audio_daemon"] = asyncio.create_task(_ad.start(), name="audio_daemon_ctrl")
        return {"ok": True, "message": "Audio daemon demarre"}

    if svc == "email_watcher":
        from scripts.email_watcher import email_watcher as _ew
        if getattr(_ew, '_running', False) or getattr(_ew, 'running', False):
            return {"ok": True, "message": "Deja actif"}
        _service_tasks["email_watcher"] = asyncio.create_task(_ew.start(), name="email_watcher_ctrl")
        return {"ok": True, "message": "Email watcher demarre"}

    if svc == "jarvis_daemon":
        from scripts.jarvis_daemon import daemon as _jd
        if getattr(_jd, '_running', False) or getattr(_jd, 'running', False):
            return {"ok": True, "message": "Deja actif"}
        _service_tasks["jarvis_daemon"] = asyncio.create_task(_jd.start(), name="jarvis_daemon_ctrl")
        return {"ok": True, "message": "JARVIS daemon demarre"}

    if svc == "screen_watcher":
        from scripts.screen_watcher import screen_watcher as _sw
        if getattr(_sw, "running", False):
            return {"ok": True, "message": "Deja actif"}
        try:
            from scripts.jarvis_daemon import daemon as _jd
            if getattr(_jd, "running", False):
                return {"ok": True, "message": "Deja actif via jarvis_daemon"}
        except Exception:
            pass
        _service_tasks["screen_watcher"] = asyncio.create_task(_sw.start(), name="screen_watcher_ctrl")
        return {"ok": True, "message": "Screen watcher demarre"}

    if svc == "scheduler":
        from scripts.scheduler import start_scheduler as _start_sched
        _start_sched()
        return {"ok": True, "message": "Scheduler demarre"}

    if svc == "relationship_analyzer":
        from scripts.relationship_analyzer import analyzer as _rel
        _service_tasks["relationship_analyzer"] = asyncio.create_task(
            _rel.run_initial_scan(), name="relationship_analyzer_ctrl"
        )
        return {"ok": True, "message": "Analyzer lance"}

    if svc == "ollama":
        subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"ok": True, "message": "Ollama lance"}

    if svc == "tv_dashboard":
        tv_dir = BASE_DIR / "tv"
        subprocess.Popen(
            ["python3", str(tv_dir / "server.py")],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return {"ok": True, "message": "TV dashboard lance"}

    return {"ok": False, "error": f"Service inconnu : {service}"}


async def _stop_service(service: str) -> dict[str, object]:
    """Arrete un service par son id."""
    svc = service.strip().lower()

    if svc == "audio_daemon":
        from scripts.audio_daemon import audio_daemon as _ad
        await _ad.stop()
        task = _service_tasks.pop("audio_daemon", None)
        if task and not task.done():
            task.cancel()
        return {"ok": True, "message": "Audio daemon arrete"}

    if svc == "email_watcher":
        from scripts.email_watcher import email_watcher as _ew
        _ew.stop()
        task = _service_tasks.pop("email_watcher", None)
        if task and not task.done():
            task.cancel()
        return {"ok": True, "message": "Email watcher arrete"}

    if svc == "jarvis_daemon":
        from scripts.jarvis_daemon import daemon as _jd
        _jd.stop()
        task = _service_tasks.pop("jarvis_daemon", None)
        if task and not task.done():
            task.cancel()
        return {"ok": True, "message": "JARVIS daemon arrete"}

    if svc == "screen_watcher":
        from scripts.screen_watcher import screen_watcher as _sw
        _sw.stop()
        task = _service_tasks.pop("screen_watcher", None)
        if task and not task.done():
            task.cancel()
        return {"ok": True, "message": "Screen watcher arrete"}

    if svc == "scheduler":
        from scripts.scheduler import shutdown_scheduler as _stop_sched
        _stop_sched()
        return {"ok": True, "message": "Scheduler arrete"}

    if svc == "relationship_analyzer":
        task = _service_tasks.pop("relationship_analyzer", None)
        if task and not task.done():
            task.cancel()
        return {"ok": True, "message": "Analyzer arrete"}

    if svc == "ollama":
        subprocess.run(["pkill", "-f", "ollama"], capture_output=True)
        return {"ok": True, "message": "Ollama arrete"}

    if svc == "tv_dashboard":
        result = subprocess.run(
            ["lsof", "-nP", "-iTCP:5174", "-sTCP:LISTEN", "-t"],
            capture_output=True, text=True, timeout=5,
        )
        pids = result.stdout.strip().split()
        for pid in pids:
            if pid:
                subprocess.run(["kill", "-TERM", pid], capture_output=True)
        return {"ok": True, "message": "TV dashboard arrete"}

    return {"ok": False, "error": f"Service inconnu : {service}"}


# TAG_MAP pour les logs : un tag par service pour filtrer backend.log
_SERVICE_LOG_TAGS: dict[str, str] = {
    "audio_daemon": "audio_daemon",
    "email_watcher": "email_watcher",
    "jarvis_daemon": "daemon",
    "screen_watcher": "screen",
    "scheduler": "scheduler",
    "relationship_analyzer": "analyzer",
    "ollama": "ollama",
    "tv_dashboard": "tv",
}
