#!/usr/bin/env python3
"""Check santé sync Contacts+iMessage.

Sortie JSON pour audit rapide:
- backend unique (port + process main.py)
- accès chat.db
- statut bridge iMessage via API
- état DB JARVIS (doublons handles/profils orphelins)
- erreurs critiques récentes (backend log)
"""

from __future__ import annotations

import json
import ssl
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database import get_connection, profile_database_path  # noqa: E402
from integrations.apple_data import apple_data  # noqa: E402


DB_PATH = profile_database_path()
BACKEND_LOG = ROOT / "data" / ".jarvis_restart" / "backend.log"
STATUS_URL = "https://127.0.0.1:8081/health/ready"


def _run(cmd: list[str]) -> tuple[int, str, str]:
    res = subprocess.run(cmd, capture_output=True, text=True)
    return res.returncode, (res.stdout or "").strip(), (res.stderr or "").strip()


def check_backend() -> dict:
    out: dict = {}
    code, stdout, stderr = _run(["lsof", "-nP", "-iTCP:8081", "-sTCP:LISTEN"])
    out["port_8081_listen_ok"] = code == 0 and bool(stdout)
    out["port_8081_lsof"] = stdout
    out["port_8081_lsof_error"] = stderr

    code2, stdout2, _ = _run(["pgrep", "-fl", "main.py"])
    lines = [ln for ln in stdout2.splitlines() if ln.strip()] if code2 == 0 else []
    out["main_py_process_count"] = len(lines)
    out["main_py_processes"] = lines
    out["single_instance_ok"] = len(lines) == 1 and out["port_8081_listen_ok"]
    return out


def check_chat_db() -> dict:
    """Diagnostique chat.db via le point d'accès Apple centralisé."""
    report = apple_data.health()
    if not report.get("readable"):
        report["permission_state"] = "full_disk_access_required"
        report["remediation"] = (
            "Réglages Système > Confidentialité et sécurité > Accès complet au disque : "
            "autoriser l'application ou le terminal qui lance com.jarvis.ingestion, "
            "puis relancer ce LaunchAgent."
        )
    else:
        report["permission_state"] = "granted"
    return report


def check_status_api() -> dict:
    out: dict = {"url": STATUS_URL}
    try:
        context = ssl._create_unverified_context()  # noqa: S323 -- loopback only
        with urlopen(STATUS_URL, timeout=3.0, context=context) as response:  # noqa: S310
            status_code = int(response.status)
            payload = json.loads(response.read(64 * 1024).decode("utf-8"))
        out["reachable"] = True
        out["http_status"] = status_code
        out["ready"] = status_code == 200 and payload.get("status") == "ready"
        out["status"] = payload.get("status")
    except HTTPError as exc:
        out["reachable"] = True
        out["http_status"] = exc.code
        out["ready"] = False
        out["error"] = "unauthorized" if exc.code in {401, 403} else f"http_{exc.code}"
    except URLError as exc:
        out["reachable"] = False
        out["ready"] = False
        out["error"] = f"connection_error: {exc.reason}"
    except Exception as exc:
        out["reachable"] = False
        out["ready"] = False
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def check_jarvis_db() -> dict:
    out: dict = {"path": str(DB_PATH), "exists": DB_PATH.exists()}
    if not DB_PATH.exists():
        out["error"] = "jarvis.db absent"
        return out

    conn = get_connection()
    cur = conn.cursor()
    out["people_count"] = cur.execute("SELECT COUNT(*) c FROM people").fetchone()["c"]
    out["relationship_profiles_count"] = cur.execute(
        "SELECT COUNT(*) c FROM relationship_profiles"
    ).fetchone()["c"]
    out["analysis_cache_count"] = cur.execute(
        "SELECT COUNT(*) c FROM imessage_analysis_cache"
    ).fetchone()["c"]
    out["analysis_cache_max_rowid"] = cur.execute(
        "SELECT COALESCE(MAX(last_analyzed_rowid),0) m FROM imessage_analysis_cache"
    ).fetchone()["m"]
    out["duplicate_handle_groups_count"] = cur.execute(
        """
        SELECT COUNT(*) c FROM (
          SELECT LOWER(TRIM(handle)) h
          FROM relationship_profiles
          WHERE handle IS NOT NULL AND TRIM(handle) <> ''
          GROUP BY LOWER(TRIM(handle))
          HAVING COUNT(*) > 1
        )
        """
    ).fetchone()["c"]
    out["people_without_profile_count"] = cur.execute(
        """
        SELECT COUNT(*) c
        FROM people p
        LEFT JOIN relationship_profiles rp ON rp.person_id = p.id
        WHERE rp.person_id IS NULL
        """
    ).fetchone()["c"]
    conn.close()
    return out


def check_recent_critical_errors() -> dict:
    out: dict = {
        "log_path": str(BACKEND_LOG),
        "exists": BACKEND_LOG.exists(),
        "recent_errors": [],
    }
    if not BACKEND_LOG.exists():
        return out
    lines = BACKEND_LOG.read_text(encoding="utf-8", errors="ignore").splitlines()
    interesting = [
        ln
        for ln in lines
        if (
            "[CRITICAL]" in ln
            or "unable to open database file" in ln
            or "TIMEOUT" in ln
        )
    ]
    out["recent_errors"] = interesting[-20:]
    out["recent_errors_count"] = len(out["recent_errors"])
    return out


def main() -> None:
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "backend": check_backend(),
        "chat_db": check_chat_db(),
        "status_api": check_status_api(),
        "jarvis_db": check_jarvis_db(),
        "recent_errors": check_recent_critical_errors(),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
