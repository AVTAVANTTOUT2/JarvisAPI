"""Client HTTP pour le daemon iMessage.

Tous les composants JARVIS (Cursor, PWA, API, scripts) utilisent ce client
pour communiquer avec le daemon iMessage au lieu d'ouvrir chat.db directement.

Usage:
    from integrations.imessage_daemon_client import daemon_client
    health = daemon_client.health()
    daemon_client.start_import()
    daemon_client.start_sync()
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

import config

logger = logging.getLogger(__name__)

DEFAULT_DAEMON_URL = "http://127.0.0.1:8193"


@dataclass
class DaemonResponse:
    ok: bool
    status_code: int
    data: dict[str, Any]
    error: str = ""


class IMessageDaemonClient:
    """Client HTTP vers le daemon iMessage.

    Gere les timeouts, retries, et le fallback si le daemon est indisponible.
    """

    def __init__(self, base_url: str = ""):
        self.base_url = (base_url or getattr(config, "IMESSAGE_DAEMON_URL", DEFAULT_DAEMON_URL)).rstrip("/")
        self.timeout = 10

    def _request(self, method: str, path: str, body: dict | None = None) -> DaemonResponse:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8") if body else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                return DaemonResponse(
                    ok=True,
                    status_code=resp.status,
                    data=json.loads(raw) if raw else {},
                )
        except urllib.error.HTTPError as e:
            try:
                err_data = json.loads(e.read())
            except Exception:
                err_data = {}
            return DaemonResponse(
                ok=False,
                status_code=e.code,
                data=err_data,
                error=err_data.get("error", str(e)),
            )
        except urllib.error.URLError as e:
            return DaemonResponse(
                ok=False,
                status_code=0,
                data={},
                error=f"Daemon inaccessible ({e.reason}). Verifier que le daemon tourne sur {self.base_url}",
            )
        except Exception as e:
            return DaemonResponse(ok=False, status_code=0, data={}, error=str(e))

    def _get(self, path: str) -> DaemonResponse:
        return self._request("GET", path)

    def _post(self, path: str, body: dict | None = None) -> DaemonResponse:
        return self._request("POST", path, body)

    # ── API publique ──────────────────────────────────────────

    def health(self) -> DaemonResponse:
        """GET /health — verifie l'acces chat.db et l'etat du daemon."""
        return self._get("/health")

    def is_healthy(self) -> bool:
        """True si le daemon repond et chat.db est accessible."""
        resp = self.health()
        return resp.ok and resp.data.get("ok", False)

    def status(self) -> DaemonResponse:
        """GET /status — etat du curseur et du daemon."""
        return self._get("/status")

    def stats(self) -> DaemonResponse:
        """GET /stats — statistiques completes."""
        return self._get("/stats")

    def progress(self) -> DaemonResponse:
        """GET /import/progress — progression de l'import en cours."""
        return self._get("/import/progress")

    def start_import(self) -> DaemonResponse:
        """POST /import/start — lance un import initial en arriere-plan."""
        return self._post("/import/start")

    def start_sync(self) -> DaemonResponse:
        """POST /sync/start — lance une sync incrementale en arriere-plan."""
        return self._post("/sync/start")

    def reconcile(self) -> DaemonResponse:
        """POST /reconcile — lance une reconciliation."""
        return self._post("/reconcile")

    def doctor(self) -> DaemonResponse:
        """POST /doctor — diagnostic complet."""
        return self._post("/doctor")

    # ── Methodes de haut niveau ───────────────────────────────

    def ensure_imported(self, timeout_s: int = 600) -> tuple[bool, str]:
        """Garantit qu'un import a ete effectue. Attend si un import est en cours.

        Returns:
            (True, message) si import OK
            (False, message) si echec
        """
        # Verifier l'etat actuel
        resp = self.stats()
        if not resp.ok:
            return False, f"Daemon inaccessible: {resp.error}"

        cursor = resp.data.get("cursor", {})
        if cursor.get("total_imported", 0) > 0:
            return True, f"Import deja effectue ({cursor['total_imported']} messages)"

        # Verifier health
        health = self.health()
        if not health.ok or not health.data.get("ok"):
            return False, f"chat.db inaccessible: {health.data.get('error', health.error)}"

        # Lancer l'import
        resp = self.start_import()
        if not resp.ok:
            return False, f"Echec lancement import: {resp.error}"

        # Attendre la fin
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            time.sleep(2)
            prog = self.progress()
            if not prog.data.get("running"):
                if prog.data.get("error"):
                    return False, f"Echec import: {prog.data['error']}"
                return True, f"Import termine ({prog.data.get('progress', '')})"

        return False, "Timeout import"

    def ensure_synced(self, timeout_s: int = 120) -> tuple[bool, str]:
        """Garantit une sync incrementale."""
        resp = self.start_sync()
        if not resp.ok:
            return False, f"Echec lancement sync: {resp.error}"

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            time.sleep(2)
            prog = self.progress()
            if not prog.data.get("running"):
                return True, f"Sync terminee ({prog.data.get('progress', '')})"

        return False, "Timeout sync"


daemon_client = IMessageDaemonClient()
