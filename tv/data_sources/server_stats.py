"""Stats serveur — CPU, RAM, disque, services launchd, Ollama status.

Utilise psutil pour les métriques système et subprocess pour
vérifier les services launchd et Ollama.
"""

from __future__ import annotations

import logging
import subprocess
from typing import Any

logger = logging.getLogger(__name__)

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    logger.warning("psutil non installé — métriques CPU/RAM/DISQUE indisponibles")
    PSUTIL_AVAILABLE = False


# Fichier DB à vérifier
DB_FILE = "../data/jarvis.db"


async def get_server_stats() -> dict[str, Any]:
    """Retourne les statistiques système complètes."""
    cpu_pct = 0.0
    ram_pct = 0.0
    ram_total_gb = 0.0
    ram_used_gb = 0.0
    disk_pct = 0.0
    disk_total_gb = 0.0
    disk_used_gb = 0.0

    if PSUTIL_AVAILABLE:
        try:
            cpu_pct = psutil.cpu_percent(interval=0.5)
            mem = psutil.virtual_memory()
            ram_pct = mem.percent
            ram_total_gb = round(mem.total / (1024 ** 3), 1)
            ram_used_gb = round(mem.used / (1024 ** 3), 1)
            disk = psutil.disk_usage("/")
            disk_pct = disk.percent
            disk_total_gb = round(disk.total / (1024 ** 3), 1)
            disk_used_gb = round(disk.used / (1024 ** 3), 1)
        except Exception as exc:
            logger.warning("Erreur psutil: %s", exc)

    services = _check_launchd_services()
    ollama_status = _check_ollama()
    db_status = _check_db()

    return {
        "ok": True,
        "cpu": {"percent": cpu_pct},
        "ram": {"percent": ram_pct, "total_gb": ram_total_gb, "used_gb": ram_used_gb},
        "disk": {"percent": disk_pct, "total_gb": disk_total_gb, "used_gb": disk_used_gb},
        "services": services,
        "ollama": ollama_status,
        "database": db_status,
    }


def _check_launchd_services() -> dict[str, bool]:
    """Vérifie l'état des services launchd JARVIS."""
    services = {
        "com.jarvis.pipeline": False,
        "com.jarvis.proactive": False,
        "com.jarvis.listeners": False,
        "com.jarvis.tv": False,
    }
    try:
        out = subprocess.run(
            ["launchctl", "list"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        for line in out.stdout.split("\n"):
            line_stripped = line.strip()
            for svc in services:
                if svc in line_stripped and "PID" not in line_stripped:
                    # Format launchctl: PID Status Label
                    # Si la ligne contient un PID numérique, le service tourne
                    parts = line_stripped.split()
                    if len(parts) >= 2 and parts[0].isdigit():
                        services[svc] = True
    except Exception as exc:
        logger.warning("Erreur launchctl: %s", exc)
    return services


def _check_ollama() -> bool:
    """Vérifie si Ollama est accessible."""
    try:
        import urllib.request
        req = urllib.request.Request("http://localhost:11434/api/tags", method="GET")
        resp = urllib.request.urlopen(req, timeout=5)
        return resp.status == 200
    except Exception:
        return False


def _check_db() -> bool:
    """Vérifie si le fichier SQLite JARVIS est accessible."""
    import os
    db_path = os.path.join(os.path.dirname(__file__), "..", "data", "jarvis.db")
    # Chemin absolu depuis la racine du projet
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent.parent
    db_full = root / "data" / "jarvis.db"
    return db_full.exists() and db_full.is_file()
