#!/usr/bin/env python3
"""Daemon iMessage — seul processus autorise a ouvrir chat.db.

Execute sous launchd (herite des permissions TCC de la session login).
Expose une API HTTP locale (127.0.0.1 uniquement) pour tous les composants
JARVIS (Cursor, PWA, API, scripts CLI).

Endpoints :
  GET  /health           — health check (+ verification chat.db)
  GET  /status           — etat du curseur + stats DB
  POST /import/start     — lancer import initial (async)
  POST /sync/start       — lancer sync incrementale (async)
  GET  /import/progress  — progression de l'import en cours
  GET  /stats            — statistiques (messages, chats, handles)
  POST /reconcile        — lancer reconciliation
  POST /doctor           — diagnostic complet

Usage :
  python scripts/imessage_daemon.py              # demarrage direct (dev)
  python scripts/imessage_daemon.py --port 8193  # port personnalise
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import config as cfg
from database import init_db, get_db
from integrations.apple_data import apple_data

logger = logging.getLogger("imessage_daemon")

DEFAULT_PORT = 8193
BIND_ADDRESS = "127.0.0.1"


class DaemonState:
    def __init__(self):
        self.started_at = datetime.now(timezone.utc)
        self.last_health_check: datetime | None = None
        self.health_ok: bool = False
        self.health_error: str = ""
        self.import_running: bool = False
        self.import_progress: str = "idle"
        self.import_result: dict[str, Any] | None = None
        self.import_error: str | None = None
        self.total_imports: int = 0
        self.total_syncs: int = 0
        self.last_watchdog_ok: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(),
            "uptime_seconds": (datetime.now(timezone.utc) - self.started_at).total_seconds(),
            "last_health_check": self.last_health_check.isoformat() if self.last_health_check else None,
            "health_ok": self.health_ok,
            "health_error": self.health_error,
            "import_running": self.import_running,
            "import_progress": self.import_progress,
            "total_imports": self.total_imports,
            "total_syncs": self.total_syncs,
            "last_watchdog_ok": self.last_watchdog_ok.isoformat() if self.last_watchdog_ok else None,
        }


state = DaemonState()
_importer = None


def _get_importer():
    global _importer
    if _importer is None:
        from integrations.imessage_import import IMessageImporter
        _importer = IMessageImporter(batch_size=getattr(cfg, "IIMPORT_BATCH_SIZE", 5000))
    return _importer


def _check_access() -> tuple[bool, str]:
    try:
        if _get_importer().is_available():
            return True, ""
        return False, "chat.db inaccessible"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _watchdog(interval: int = 60) -> None:
    logger.info("[watchdog] Demarrage interval=%ds", interval)
    while True:
        try:
            ok, err = _check_access()
            state.last_health_check = datetime.now(timezone.utc)
            state.health_ok = ok
            state.health_error = err if not ok else ""
            if ok:
                state.last_watchdog_ok = datetime.now(timezone.utc)
            else:
                logger.error("[watchdog] ALERTE: %s", err)
        except Exception as e:
            logger.error("[watchdog] %s", e, exc_info=True)
        time.sleep(interval)


def _import_bg() -> None:
    state.import_running = True
    state.import_progress = "Import en cours..."
    state.import_error = None
    try:
        imp = _get_importer()
        logger.info("[daemon] Import initial demarre")
        r = imp.import_all()
        state.import_result = {
            "mode": r.mode, "total_messages": r.total_messages,
            "total_skipped": r.total_skipped, "total_failed": r.total_failed,
            "total_handles": r.total_handles, "total_chats": r.total_chats,
            "total_attachments": r.total_attachments, "total_reactions": r.total_reactions,
            "duration_seconds": r.duration_seconds, "completed_at": r.completed_at,
            "errors": r.errors[:10], "reconciliation": r.reconciliation,
        }
        state.total_imports += 1
        state.import_progress = f"Import termine: {r.total_messages} msg ({r.total_skipped} skip, {r.total_failed} erreurs)"
    except Exception as e:
        state.import_error = f"{type(e).__name__}: {e}"
        state.import_progress = f"Echec: {e}"
        logger.exception("[daemon] Echec import")
    finally:
        state.import_running = False


def _sync_bg() -> None:
    state.import_running = True
    state.import_progress = "Sync incrementale en cours..."
    state.import_error = None
    try:
        imp = _get_importer()
        logger.info("[daemon] Sync incrementale demarree")
        r = imp.sync_incremental()
        state.total_syncs += 1
        state.import_progress = f"Sync terminee: {r.total_messages} nouveaux, {r.total_skipped} skip"
    except Exception as e:
        state.import_error = f"{type(e).__name__}: {e}"
        state.import_progress = f"Echec sync: {e}"
        logger.exception("[daemon] Echec sync")
    finally:
        state.import_running = False


class Handler(BaseHTTPRequestHandler):
    server_version = "JarvisImessageDaemon/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.debug("[http] %s", fmt % args)

    def _json(self, data: dict[str, Any], code: int = 200) -> None:
        b = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(b)

    def _err(self, code: int, msg: str) -> None:
        self._json({"error": msg}, code)

    def do_GET(self) -> None:
        p = self.path.split("?")[0].rstrip("/")
        if p == "/health": return self._health()
        if p == "/status": return self._status()
        if p == "/import/progress": return self._progress()
        if p == "/stats": return self._stats()
        self._err(404, p)

    def do_POST(self) -> None:
        p = self.path.split("?")[0].rstrip("/")
        cl = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(cl) if cl else b""
        body = json.loads(raw) if raw else {}
        if p == "/import/start": return self._import_start(body)
        if p == "/sync/start": return self._sync_start(body)
        if p == "/reconcile": return self._reconcile()
        if p == "/doctor": return self._doctor()
        self._err(404, p)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _health(self) -> None:
        ok, err = _check_access()
        state.last_health_check = datetime.now(timezone.utc)
        state.health_ok = ok
        state.health_error = err if not ok else ""
        d = {"ok": ok, "error": state.health_error, "daemon": state.to_dict()}
        if ok:
            d["cursor"] = _get_importer().get_status()
            with get_db() as conn:
                d["messages_in_db"] = conn.execute("SELECT COUNT(*) c FROM imessage_messages").fetchone()["c"]
        self._json(d, 200 if ok else 503)

    def _status(self) -> None:
        self._json({"daemon": state.to_dict(), "cursor": _get_importer().get_status()})

    def _progress(self) -> None:
        self._json({
            "running": state.import_running, "progress": state.import_progress,
            "result": state.import_result, "error": state.import_error,
            "total_imports": state.total_imports, "total_syncs": state.total_syncs,
        })

    def _stats(self) -> None:
        cur = _get_importer().get_status()
        with get_db() as conn:
            self._json({
                "cursor": cur,
                "messages": conn.execute("SELECT COUNT(*) c FROM imessage_messages").fetchone()["c"],
                "chats": conn.execute("SELECT COUNT(*) c FROM imessage_chats").fetchone()["c"],
                "handles": conn.execute("SELECT COUNT(*) c FROM imessage_handles").fetchone()["c"],
                "attachments": conn.execute("SELECT COUNT(*) c FROM imessage_attachments").fetchone()["c"],
                "reactions": conn.execute("SELECT COUNT(*) c FROM imessage_reactions").fetchone()["c"],
                "uptime": (datetime.now(timezone.utc) - state.started_at).total_seconds(),
            })

    def _import_start(self, body: dict) -> None:
        if state.import_running:
            self._json({"error": "Import deja en cours", "progress": state.import_progress}, 409)
            return
        if not state.health_ok:
            self._err(503, f"chat.db inaccessible: {state.health_error}")
            return
        threading.Thread(target=_import_bg, daemon=True, name="import").start()
        self._json({"status": "started", "mode": "initial"})

    def _sync_start(self, body: dict) -> None:
        if state.import_running:
            self._json({"error": "Operation deja en cours", "progress": state.import_progress}, 409)
            return
        if not state.health_ok:
            self._err(503, f"chat.db inaccessible: {state.health_error}")
            return
        threading.Thread(target=_sync_bg, daemon=True, name="sync").start()
        self._json({"status": "started", "mode": "incremental"})

    def _reconcile(self) -> None:
        try:
            r = _get_importer().reconcile()
            self._json({
                "ok": r.ok, "chat_db": r.chat_db_messages, "jarvis_db": r.jarvis_db_messages,
                "orphans": r.orphan_messages, "orphan_fixed": r.orphan_fixed,
                "duplicates": r.duplicates_found, "duplicates_removed": r.duplicates_removed,
            })
        except Exception as e:
            self._err(500, str(e))

    def _doctor(self) -> None:
        ok, err = _check_access()
        cur = _get_importer().get_status()
        chat = apple_data.db_path
        with get_db() as conn:
            self._json({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "chat_db": {
                    "path": str(chat), "exists": chat.exists(),
                    "size_mb": round(chat.stat().st_size / 1048576, 1) if chat.exists() else 0,
                    "accessible": ok, "error": err if not ok else "",
                },
                "jarvis_db": {
                    "messages": conn.execute("SELECT COUNT(*) c FROM imessage_messages").fetchone()["c"],
                    "chats": conn.execute("SELECT COUNT(*) c FROM imessage_chats").fetchone()["c"],
                    "handles": conn.execute("SELECT COUNT(*) c FROM imessage_handles").fetchone()["c"],
                },
                "cursor": cur, "daemon": state.to_dict(),
                "verdict": "OK" if ok else "FDA manquant",
            })


class DaemonServer(HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def run_daemon(port: int = DEFAULT_PORT) -> None:
    init_db()
    ok, err = _check_access()
    if not ok:
        logger.warning("[daemon] chat.db inaccessible au demarrage: %s", err)
    else:
        logger.info("[daemon] chat.db accessible")

    state.health_ok = ok
    state.health_error = err if not ok else ""
    state.last_health_check = datetime.now(timezone.utc)

    threading.Thread(target=_watchdog, args=(60,), daemon=True, name="watchdog").start()

    server = DaemonServer((BIND_ADDRESS, port), Handler)
    logger.info("[daemon] %s:%s PID=%d", BIND_ADDRESS, port, os.getpid())

    def _shutdown(sig, frame):
        logger.info("[daemon] Signal %s - arret", sig)
        server.shutdown()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    logger.info("[daemon] Arrete")


if __name__ == "__main__":
    pa = argparse.ArgumentParser(description="JARVIS iMessage Daemon")
    pa.add_argument("--port", type=int, default=DEFAULT_PORT)
    pa.add_argument("--log-level", default="INFO", choices=["DEBUG","INFO","WARNING","ERROR"])
    a = pa.parse_args()
    logging.basicConfig(level=getattr(logging, a.log_level), format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    run_daemon(port=a.port)
