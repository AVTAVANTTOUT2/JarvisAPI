"""Contrôle et health-check Ollama locaux (API HTTP, pas pgrep seul)."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

import config

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 11434
HEALTH_TIMEOUT_S = 2.0
START_WAIT_S = 45.0
STOP_WAIT_S = 10.0
PIDFILE = Path(os.getenv("OLLAMA_PIDFILE", str(Path(config.DB_PATH).resolve().parent / "ollama.pid")))

# Préférences modèles vision (du plus léger au plus lourd) — noms Ollama courants.
VISION_MODEL_CANDIDATES: tuple[str, ...] = (
    "qwen2.5vl:3b",
    "qwen2.5-vl:3b",
    "qwen2.5vl:7b",
    "qwen2.5-vl:7b",
    "qwen3-vl:4b",
    "llava:7b",
    "llava:13b",
    "minicpm-v",
)


def ollama_base_url() -> str:
    raw = str(getattr(config, "OLLAMA_URL", f"http://{DEFAULT_HOST}:{DEFAULT_PORT}") or "")
    return raw.rstrip("/") or f"http://{DEFAULT_HOST}:{DEFAULT_PORT}"


def ollama_port() -> int:
    url = ollama_base_url()
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        if parsed.port:
            return int(parsed.port)
    except Exception:
        pass
    return DEFAULT_PORT


def configured_vision_model() -> str:
    return str(
        getattr(config, "SCREEN_VISION_MODEL", None)
        or getattr(config, "SCREEN_WATCHER_VISION_MODEL", None)
        or "qwen2.5-vl:7b"
    )


def _normalize_model_name(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def _model_matches(installed: str, wanted: str) -> bool:
    a = _normalize_model_name(installed)
    b = _normalize_model_name(wanted)
    if a == b:
        return True
    # Compat qwen2.5vl ↔ qwen2.5-vl
    a2 = a.replace("qwen2.5vl", "qwen2.5-vl")
    b2 = b.replace("qwen2.5vl", "qwen2.5-vl")
    if a2 == b2:
        return True
    # Même famille + tag (qwen2.5-vl:7b ↔ qwen2.5-vl:latest) — pas un préfixe flou
    a_base, _, a_tag = a2.partition(":")
    b_base, _, b_tag = b2.partition(":")
    if a_base != b_base:
        return False
    if not a_tag or not b_tag:
        return True
    return a_tag == b_tag or a_tag == "latest" or b_tag == "latest"


def is_vision_capable_name(name: str) -> bool:
    n = _normalize_model_name(name)
    markers = ("vl", "llava", "vision", "minicpm-v", "moondream", "bakllava")
    return any(m in n for m in markers)


def pick_vision_model(installed: list[str], preferred: str | None = None) -> str | None:
    """Choisit un modèle vision parmi ceux installés."""
    preferred = preferred or configured_vision_model()
    for name in installed:
        if _model_matches(name, preferred):
            return name
    for candidate in VISION_MODEL_CANDIDATES:
        for name in installed:
            if _model_matches(name, candidate):
                return name
    for name in installed:
        if is_vision_capable_name(name):
            return name
    return None


def check_ollama_health(
    *,
    timeout_s: float = HEALTH_TIMEOUT_S,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Health check réel via GET /api/tags."""
    url = (base_url or ollama_base_url()).rstrip("/")
    preferred = configured_vision_model()
    started = time.perf_counter()
    result: dict[str, Any] = {
        "service": "ollama",
        "status": "stopped",
        "healthy": False,
        "port": ollama_port(),
        "base_url": url,
        "latency_ms": None,
        "models": [],
        "vision_model": preferred,
        "vision_model_available": False,
        "vision_model_resolved": None,
        "error": None,
        "pid": _read_pidfile(),
    }
    try:
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.get(f"{url}/api/tags")
            latency = round((time.perf_counter() - started) * 1000, 1)
            result["latency_ms"] = latency
            if resp.status_code != 200:
                result["status"] = "error"
                result["error"] = f"HTTP {resp.status_code}"
                return result
            payload = resp.json()
            models_raw = payload.get("models") or []
            names: list[str] = []
            model_details: list[dict[str, Any]] = []
            for m in models_raw:
                name = str(m.get("name") or m.get("model") or "")
                if not name:
                    continue
                names.append(name)
                model_details.append(
                    {
                        "name": name,
                        "size": m.get("size"),
                        "parameter_size": (m.get("details") or {}).get("parameter_size"),
                        "family": (m.get("details") or {}).get("family"),
                    }
                )
            resolved = pick_vision_model(names, preferred)
            result["status"] = "running"
            result["healthy"] = True
            result["models"] = model_details
            result["vision_model_resolved"] = resolved
            if resolved is None:
                result["vision_model_available"] = False
                result["error"] = (
                    "Ollama fonctionne, mais aucun modèle vision configuré n'est disponible."
                )
            else:
                result["vision_model_available"] = True
                if not _model_matches(resolved, preferred):
                    result["error"] = (
                        f"Modèle configuré '{preferred}' absent — fallback '{resolved}'."
                    )
            return result
    except Exception as exc:
        result["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
        result["status"] = "stopped"
        result["healthy"] = False
        result["error"] = str(exc)
        return result


def _read_pidfile() -> int | None:
    try:
        if not PIDFILE.exists():
            return None
        pid = int(PIDFILE.read_text().strip())
        if pid > 0 and _pid_alive(pid):
            return pid
    except Exception:
        pass
    return None


def _write_pidfile(pid: int) -> None:
    try:
        PIDFILE.parent.mkdir(parents=True, exist_ok=True)
        PIDFILE.write_text(str(pid))
    except Exception as exc:
        logger.warning("[ollama] impossible d'écrire pidfile %s : %s", PIDFILE, exc)


def _clear_pidfile() -> None:
    try:
        PIDFILE.unlink(missing_ok=True)
    except Exception:
        pass


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _find_ollama_serve_pids() -> list[int]:
    """PIDs du serveur `ollama serve` uniquement (pas un match flou)."""
    pids: list[int] = []
    try:
        proc = subprocess.run(
            ["pgrep", "-f", "ollama serve"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if proc.returncode == 0:
            for line in proc.stdout.split():
                try:
                    pids.append(int(line.strip()))
                except ValueError:
                    continue
    except Exception:
        pass
    # Aussi le binaire serveur homebrew courant
    try:
        proc = subprocess.run(
            ["pgrep", "-x", "ollama"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if proc.returncode == 0:
            for line in proc.stdout.split():
                try:
                    pid = int(line.strip())
                    if pid not in pids:
                        # Vérifier la ligne de commande
                        cmd = Path(f"/proc/{pid}/cmdline")
                        # macOS : ps
                        ps = subprocess.run(
                            ["ps", "-p", str(pid), "-o", "command="],
                            capture_output=True,
                            text=True,
                            timeout=3,
                        )
                        cmd_line = (ps.stdout or "").strip().lower()
                        if "serve" in cmd_line or cmd_line.endswith("ollama"):
                            pids.append(pid)
                except ValueError:
                    continue
    except Exception:
        pass
    return pids


def start_ollama(*, wait_s: float = START_WAIT_S) -> dict[str, Any]:
    """Démarre `ollama serve` si l'API n'est pas saine, puis attend le health check."""
    health = check_ollama_health()
    if health.get("healthy"):
        logger.info("[ollama] déjà healthy (latency=%sms)", health.get("latency_ms"))
        return {**health, "ok": True, "message": "Ollama déjà actif"}

    logger.info("[ollama] start requested")
    try:
        proc = subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        _write_pidfile(proc.pid)
        logger.info("[ollama] processus lancé pid=%s", proc.pid)
    except FileNotFoundError:
        return {
            "ok": False,
            "service": "ollama",
            "status": "error",
            "healthy": False,
            "error": "Binaire 'ollama' introuvable dans le PATH",
            "message": "Binaire ollama introuvable",
        }
    except Exception as exc:
        return {
            "ok": False,
            "service": "ollama",
            "status": "error",
            "healthy": False,
            "error": str(exc),
            "message": f"Échec démarrage Ollama : {exc}",
        }

    deadline = time.time() + wait_s
    last = health
    while time.time() < deadline:
        last = check_ollama_health()
        if last.get("healthy"):
            logger.info("[ollama] healthy")
            return {
                **last,
                "ok": True,
                "message": "Ollama démarré",
                "pid": proc.pid,
            }
        if proc.poll() is not None:
            _clear_pidfile()
            return {
                **last,
                "ok": False,
                "status": "error",
                "message": "Ollama s'est arrêté pendant le démarrage",
                "error": last.get("error") or "process exited",
            }
        time.sleep(0.5)

    logger.warning("[ollama] timeout health après %.0fs", wait_s)
    return {
        **last,
        "ok": False,
        "status": "error",
        "message": f"Timeout health Ollama ({wait_s:.0f}s)",
        "error": last.get("error") or "timeout",
    }


def stop_ollama(*, wait_s: float = STOP_WAIT_S) -> dict[str, Any]:
    """Arrêt propre du serveur Ollama, kill forcé après timeout."""
    logger.info("[ollama] stop requested")
    pids = set(_find_ollama_serve_pids())
    pf = _read_pidfile()
    if pf:
        pids.add(pf)

    if not pids and not check_ollama_health().get("healthy"):
        _clear_pidfile()
        return {
            "ok": True,
            "service": "ollama",
            "status": "stopped",
            "healthy": False,
            "message": "Ollama déjà arrêté",
        }

    for pid in list(pids):
        try:
            os.kill(pid, signal.SIGTERM)
            logger.info("[ollama] SIGTERM pid=%s", pid)
        except ProcessLookupError:
            pids.discard(pid)
        except Exception as exc:
            logger.warning("[ollama] SIGTERM pid=%s échoué : %s", pid, exc)

    deadline = time.time() + wait_s
    while time.time() < deadline:
        alive = [p for p in pids if _pid_alive(p)]
        if not alive and not check_ollama_health(timeout_s=0.8).get("healthy"):
            _clear_pidfile()
            logger.info("[ollama] stopped")
            return {
                "ok": True,
                "service": "ollama",
                "status": "stopped",
                "healthy": False,
                "message": "Ollama arrêté",
            }
        time.sleep(0.3)

    # Force
    for pid in list(pids):
        if _pid_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
                logger.warning("[ollama] SIGKILL pid=%s", pid)
            except Exception:
                pass

    time.sleep(0.3)
    _clear_pidfile()
    still = check_ollama_health(timeout_s=0.8)
    ok = not still.get("healthy")
    return {
        "ok": ok,
        "service": "ollama",
        "status": "stopped" if ok else "error",
        "healthy": bool(still.get("healthy")),
        "message": "Ollama arrêté" if ok else "Ollama encore joignable après kill",
        "error": None if ok else still.get("error"),
    }


def restart_ollama() -> dict[str, Any]:
    stop_ollama()
    time.sleep(1.0)
    return start_ollama()
