#!/usr/bin/env python3
"""Diagnostic du pipeline OpenCode JARVIS. Sortie PASS/WARN/FAIL, aucun secret."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _assert_no_secrets(payload: Mapping[str, Any]) -> None:
    dumped = json.dumps(payload)
    if "sk-" in dumped:
        raise RuntimeError("diagnostic OpenCode a tenté d'exposer une clé")


def _status_rank(status: str) -> int:
    return {"PASS": 0, "WARN": 1, "FAIL": 2}.get(status, 2)


def _worst(statuses: list[str]) -> str:
    return max(statuses, key=_status_rank) if statuses else "FAIL"


def _check(name: str, status: str, *, code: str, detail: str) -> dict[str, str]:
    return {"name": name, "status": status, "code": code, "detail": detail}


def _key_configured() -> bool:
    try:
        import config

        raw = str(getattr(config, "DEEPSEEK_API_KEY", "") or "").strip()
    except Exception:
        raw = str(os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if not raw or raw in {"sk-...", "sk-dev-..."} or raw.lower().startswith("sk-your"):
        return False
    return True


def _agentic_runtime() -> str:
    try:
        import config

        return str(getattr(config, "AGENTIC_RUNTIME", "auto")).strip().lower() or "auto"
    except Exception:
        return str(os.environ.get("AGENTIC_RUNTIME") or "auto").strip().lower() or "auto"


def _plan_approval_required() -> bool:
    try:
        import config

        return bool(getattr(config, "AGENTIC_REQUIRE_PLAN_APPROVAL", True))
    except Exception:
        return True


def probe_binary(
    binary: Path, *, timeout_seconds: float = 5.0
) -> dict[str, Any]:
    """Lance ``opencode --version`` hors TTY, avec timeout, sans secret."""

    if not binary.exists() or not binary.is_file():
        return _check(
            "binary_probe",
            "FAIL",
            code="binary_absent",
            detail="binaire OpenCode absent",
        )
    try:
        completed = subprocess.run(
            [str(binary), "--version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            close_fds=True,
        )
    except subprocess.TimeoutExpired:
        return _check(
            "binary_probe",
            "FAIL",
            code="timeout",
            detail=f"opencode --version a dépassé {timeout_seconds}s",
        )
    except OSError as exc:
        return _check(
            "binary_probe",
            "FAIL",
            code="binary_exec_error",
            detail=type(exc).__name__,
        )
    version = (completed.stdout or completed.stderr or "").strip().splitlines()
    version_text = version[0] if version else ""
    if completed.returncode != 0 or not version_text:
        return _check(
            "binary_probe",
            "FAIL",
            code="binary_exec_failed",
            detail=f"code {completed.returncode}",
        )
    return {
        **_check(
            "binary_probe",
            "PASS",
            code="ok",
            detail=f"version {version_text}",
        ),
        "version": version_text,
    }


def diagnose(
    *,
    layout: Any | None = None,
    timeout_seconds: float = 5.0,
    model_key_present: bool | None = None,
) -> dict[str, Any]:
    """Inspecte plugin, binaire, flags et logs. N'écrit aucun secret."""

    from integrations.opencode.config import RuntimeLayout
    from integrations.opencode.lifecycle import (
        OpenCodeProcessManager,
        ReleaseManifest,
    )
    from jarvis.agentic.registry import discover_runtime_plugins

    resolved = layout if layout is not None else RuntimeLayout.default()
    plugin_path = ROOT / "integrations" / "opencode" / "plugin.json"
    checks: list[dict[str, Any]] = []

    plugin_enabled = False
    try:
        plugin = json.loads(plugin_path.read_text(encoding="utf-8"))
        plugin_enabled = bool(plugin.get("runtime", {}).get("enabled"))
        checks.append(
            _check(
                "plugin",
                "PASS" if plugin_enabled else "FAIL",
                code="ok" if plugin_enabled else "plugin_disabled",
                detail="plugin.json enabled" if plugin_enabled else "plugin.json disabled",
            )
        )
    except (OSError, json.JSONDecodeError) as exc:
        checks.append(
            _check("plugin", "FAIL", code="plugin_unreadable", detail=type(exc).__name__)
        )

    runtime_setting = _agentic_runtime()
    if runtime_setting == "disabled":
        checks.append(
            _check(
                "agentic_runtime",
                "FAIL",
                code="runtime_disabled",
                detail="AGENTIC_RUNTIME=disabled",
            )
        )
    else:
        checks.append(
            _check(
                "agentic_runtime",
                "PASS",
                code="ok",
                detail=f"AGENTIC_RUNTIME={runtime_setting}",
            )
        )

    path_hit = shutil.which("opencode")
    checks.append(
        _check(
            "path",
            "WARN" if not path_hit else "PASS",
            code="path_missing" if not path_hit else "ok",
            detail=(
                "opencode absent du PATH (JARVIS utilise le binaire empaqueté)"
                if not path_hit
                else "opencode présent dans le PATH"
            ),
        )
    )

    binary = resolved.binary_path
    probe = probe_binary(binary, timeout_seconds=timeout_seconds)
    checks.append(probe)

    try:
        manifest = ReleaseManifest.load()
        probed_version = str(probe.get("version") or "")
        if probe["status"] == "PASS" and probed_version == manifest.version:
            checks.append(
                _check("install", "PASS", code="ok", detail=probed_version)
            )
        elif probe["status"] == "PASS":
            checks.append(
                _check(
                    "install",
                    "WARN",
                    code="version_mismatch",
                    detail=f"binaire {probed_version}, manifest {manifest.version}",
                )
            )
        else:
            checks.append(
                _check(
                    "install",
                    "FAIL",
                    code="install_invalid",
                    detail="binaire absent ou inutilisable",
                )
            )
        process = OpenCodeProcessManager(layout=resolved, manifest=manifest)
        status = process.status()
        health = process.health()
        if status.error_code == "not_started" or health.error_code == "not_started":
            checks.append(
                _check(
                    "serve",
                    "WARN",
                    code="serve_idle",
                    detail="serveur partagé inactif (normal : un serve par run)",
                )
            )
        elif status.running and (status.healthy or health.healthy):
            checks.append(
                _check("serve", "PASS", code="ok", detail=f"pid {status.pid}")
            )
        else:
            checks.append(
                _check(
                    "serve",
                    "FAIL",
                    code=status.error_code or health.error_code or "serve_unhealthy",
                    detail="processus OpenCode non sain",
                )
            )
    except Exception as exc:
        checks.append(
            _check(
                "install",
                "FAIL",
                code="install_error",
                detail=type(exc).__name__,
            )
        )

    logs_dir = resolved.logs_dir
    stdout_log = logs_dir / "server.stdout.log"
    stderr_log = logs_dir / "server.stderr.log"
    if logs_dir.is_dir() and os.access(logs_dir, os.W_OK):
        log_status = "PASS" if stdout_log.exists() or stderr_log.exists() else "WARN"
        checks.append(
            _check(
                "logs",
                log_status,
                code="ok" if log_status == "PASS" else "logs_empty",
                detail=(
                    "journaux serveur présents"
                    if log_status == "PASS"
                    else "répertoire de logs writable, aucun run encore journalisé"
                ),
            )
        )
    else:
        checks.append(
            _check(
                "logs",
                "WARN",
                code="logs_missing",
                detail="répertoire de logs absent (créé au premier serve)",
            )
        )

    discovered = {
        item.runtime_id for item in discover_runtime_plugins(ROOT / "integrations")
    }
    checks.append(
        _check(
            "discovery",
            "PASS" if "opencode" in discovered else "FAIL",
            code="ok" if "opencode" in discovered else "plugin_not_discovered",
            detail=(
                "plugin opencode découvert"
                if "opencode" in discovered
                else "plugin opencode absent du registre"
            ),
        )
    )

    key_present = _key_configured() if model_key_present is None else model_key_present
    checks.append(
        _check(
            "model_key",
            "PASS" if key_present else "FAIL",
            code="ok" if key_present else "deepseek_key_missing",
            detail=(
                "DEEPSEEK_API_KEY configurée"
                if key_present
                else "DEEPSEEK_API_KEY absente ; un run coding échouera explicitement"
            ),
        )
    )

    if _plan_approval_required():
        checks.append(
            _check(
                "plan_gate",
                "WARN",
                code="plan_approval_required",
                detail="AGENTIC_REQUIRE_PLAN_APPROVAL=true : OpenCode attend la validation du plan",
            )
        )
    else:
        checks.append(
            _check("plan_gate", "PASS", code="ok", detail="démarrage immédiat autorisé")
        )

    report = {
        "status": _worst([item["status"] for item in checks]),
        "binary": str(binary),
        "plugin_enabled": plugin_enabled,
        "checks": checks,
    }
    _assert_no_secrets(report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args(argv)
    report = diagnose(timeout_seconds=args.timeout)
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n" + report["status"] + "\n")
    return 0 if report["status"] != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
