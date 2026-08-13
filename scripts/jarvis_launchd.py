#!/usr/bin/env python3
"""CLI de cycle de vie JARVIS 24/7 (launchd + stack).

Le LaunchAgent exécute le supervisor avec le Python du venv réel. Un wrapper
JARVIS.app déclenche les demandes de permissions micro/AppleEvents.

Usage:
    python scripts/jarvis_launchd.py stop           # arrête tout le stack
    python scripts/jarvis_launchd.py start          # charge launchd et attend la santé
    python scripts/jarvis_launchd.py restart|maj    # stop puis start
    python scripts/jarvis_launchd.py status         # LaunchAgent + processus
    python scripts/jarvis_launchd.py install        # .app + launchd + CLI ~/.local/bin/jarvis
    python scripts/jarvis_launchd.py uninstall      # arrête puis retire le LaunchAgent
    python scripts/jarvis_launchd.py open           # permissions micro / AppleEvents

Raccourci : ``jarvis stop`` / ``jarvis start`` / ``jarvis maj``.
"""

from __future__ import annotations

import os
import shlex
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

if __package__:
    from scripts.launchagents import write_launch_agents
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts.launchagents import write_launch_agents

PROJECT_DIR = Path(__file__).resolve().parent.parent
LOGS_DIR = PROJECT_DIR / "data" / "logs"
VENV_DIR = PROJECT_DIR / "venv"
VENV_PYTHON = VENV_DIR / "bin" / "python"
HOME = os.path.expanduser("~")
APP_DIR = Path(HOME) / "Applications" / "JARVIS.app"
APP_BIN = APP_DIR / "Contents" / "MacOS" / "JARVIS"
APP_PLIST = APP_DIR / "Contents" / "Info.plist"
LAUNCHD_DIR = Path(HOME) / "Library" / "LaunchAgents"
LAUNCHD_DEST = LAUNCHD_DIR / "com.jarvis.supervisor.plist"
BUNDLE_ID = "fr.avity.jarvis"
SUPERVISOR_LOG = str(LOGS_DIR / "supervisor.log")
CLI_SRC = PROJECT_DIR / "scripts" / "jarvis"
CLI_DEST = Path(HOME) / ".local" / "bin" / "jarvis"


def _install_app() -> None:
    """Cree le wrapper JARVIS.app que macOS reconnait comme une vraie application.
    Necessaire pour que les permissions micro/AppleEvents survivent aux reboot/veille.
    """
    APP_BIN.parent.mkdir(parents=True, exist_ok=True)

    APP_BIN.write_text(
        "#!/bin/bash\n"
        'export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"\n'
        "export PYTHONUNBUFFERED=1\n"
        f"cd {shlex.quote(str(PROJECT_DIR))}\n"
        f"exec {shlex.quote(str(VENV_PYTHON))} supervisor.py\n",
        encoding="utf-8",
    )
    APP_BIN.chmod(0o755)

    APP_PLIST.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        "    <key>CFBundleName</key>\n"
        "    <string>JARVIS</string>\n"
        f"    <key>CFBundleIdentifier</key>\n"
        f"    <string>{BUNDLE_ID}</string>\n"
        "    <key>CFBundleExecutable</key>\n"
        "    <string>JARVIS</string>\n"
        "    <key>CFBundleVersion</key>\n"
        "    <string>1.0</string>\n"
        "    <key>NSMicrophoneUsageDescription</key>\n"
        "    <string>JARVIS utilise le microphone pour la conversation vocale.</string>\n"
        "    <key>NSAppleEventsUsageDescription</key>\n"
        "    <string>JARVIS controle Mail, Calendar et Messages via AppleScript.</string>\n"
        "</dict>\n"
        "</plist>\n",
        encoding="utf-8",
    )

    print(f"JARVIS.app installe : {APP_DIR}")


def _install_launchd_plist() -> None:
    """Génère le plist supervisor depuis le checkout et le venv réels."""
    written = write_launch_agents(
        output_dir=LAUNCHD_DIR,
        repo_root=PROJECT_DIR,
        venv_dir=VENV_DIR,
        services=("supervisor",),
    )
    print(f"Plist launchd installe : {written['supervisor']}")


def _install_cli() -> None:
    """Installe le raccourci ``jarvis`` dans ~/.local/bin."""
    if not CLI_SRC.is_file():
        raise FileNotFoundError(f"CLI introuvable : {CLI_SRC}")
    CLI_DEST.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(CLI_SRC, CLI_DEST)
    mode = CLI_DEST.stat().st_mode
    CLI_DEST.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"CLI installe : {CLI_DEST}")


def _bootstrap() -> bool:
    """Charge le service dans launchd. Retourne True si succes."""
    uid = os.getuid()
    # Decharger d'abord si existant
    subprocess.run(
        ["launchctl", "bootout", f"gui/{uid}/{LAUNCHD_DEST.name}"],
        capture_output=True,
    )
    result = subprocess.run(
        ["launchctl", "bootstrap", f"gui/{uid}", str(LAUNCHD_DEST)],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def cmd_install() -> int:
    try:
        _install_launchd_plist()
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Erreur : installation LaunchAgent impossible : {exc}")
        return 1
    _install_app()
    _install_cli()

    if not _bootstrap():
        print("Erreur : launchctl bootstrap a echoue.")
        return 1

    print()
    print("JARVIS installe en 24/7.")
    print()
    print("  Demarrage auto au boot     : oui")
    print("  Relance auto apres crash   : oui (KeepAlive)")
    print(f"  Logs                       : {SUPERVISOR_LOG}")
    print("  CLI                        : jarvis stop | start | restart | maj")
    print()
    print("  Lance manuellement pour les permissions :")
    print(f"    open {APP_DIR}")
    return 0


def cmd_uninstall() -> int:
    cmd_stop()
    uid = os.getuid()
    subprocess.run(
        ["launchctl", "bootout", f"gui/{uid}/com.jarvis.supervisor"],
        capture_output=True,
    )
    if LAUNCHD_DEST.exists():
        LAUNCHD_DEST.unlink()
    print("Service launchd desinstalle. CLI conserve : jarvis install pour revenir.")
    return 0


def cmd_status() -> int:
    if not LAUNCHD_DEST.exists():
        print("Service NON INSTALLE")
        return 1

    uid = os.getuid()
    result = subprocess.run(
        ["launchctl", "print", f"gui/{uid}/com.jarvis.supervisor"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print("Service INSTALLE")
        for line in result.stdout.splitlines():
            line_s = line.strip()
            for kw in ("state", "last exit", "pid", "running"):
                if line_s.startswith(kw):
                    print(f"  {line_s}")
                    break
    else:
        print("Service INSTALLE mais INACTIF")

    from scripts.jarvis_stack import default_list_snapshots, select_owned

    owned = select_owned(default_list_snapshots(PROJECT_DIR), PROJECT_DIR)
    if owned:
        print("Processus JARVIS :")
        for item in owned:
            print(f"  {item.service:16} pid={item.pid}")
    else:
        print("Processus JARVIS : aucun")

    if APP_BIN.exists():
        print(f"App      : {APP_DIR}")
    if CLI_DEST.is_file():
        print(f"CLI      : {CLI_DEST}")

    return 0


def cmd_open() -> int:
    """Ouvre JARVIS.app — declenche les prompts de permission macOS."""
    result = subprocess.run(["open", str(APP_DIR)], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Erreur open : {result.stderr}")
        return 1
    print("JARVIS.app ouvert.")
    print("macOS va demander les permissions : Microphone, Apple Events, Mail, Calendar, Messages.")
    print("Verifier dans Reglages > Confidentialite apres accord.")
    return 0


def _service_loaded(uid: int) -> bool:
    result = subprocess.run(
        ["launchctl", "print", f"gui/{uid}/com.jarvis.supervisor"],
        capture_output=True,
    )
    return result.returncode == 0


def _port_listening(port: int) -> bool:
    result = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
        capture_output=True,
    )
    return result.returncode == 0


def _backend_port() -> int:
    try:
        import config as jarvis_config

        return int(jarvis_config.WEB_PORT)
    except (ImportError, AttributeError, TypeError, ValueError):
        return 8081


def _wait_healthy(*, timeout_s: float = 60.0) -> bool:
    """Attend supervisor (:9000) + backend (WEB_PORT)."""
    backend_port = _backend_port()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _port_listening(9000) and _port_listening(backend_port):
            return True
        time.sleep(1.0)
    return False


def cmd_stop() -> int:
    """Arrête launchd, Ollama, Claw3D, le runtime agentique et tous les PID JARVIS."""
    from scripts.jarvis_stack import stop_stack

    print("Arrêt du stack JARVIS…")
    report = stop_stack(root=PROJECT_DIR)
    for label in report.bootout:
        print(f"  launchd bootout {label}")
    for name, result in report.managers.items():
        print(f"  manager {name}: {result}")
    for item in report.stopped:
        print(f"  stop {item.service} pid={item.pid}")
    if report.still_alive:
        print(f"Encore vivant : {report.still_alive}")
        print(f"Logs : {SUPERVISOR_LOG}")
        return 1
    print("OK — stack arrêté. Plist conservé ; relance : jarvis start")
    return 0


def cmd_start() -> int:
    """Charge le LaunchAgent et attend supervisor + backend."""
    if not LAUNCHD_DEST.exists() or not APP_BIN.exists():
        print("Service non installé — installation…")
        return cmd_install()

    uid = os.getuid()
    if not _service_loaded(uid):
        print("Chargement LaunchAgent…")
        if not _bootstrap():
            print("Erreur : launchctl bootstrap a échoué.")
            return 1
    elif not (_port_listening(9000) and _port_listening(_backend_port())):
        label = f"gui/{uid}/com.jarvis.supervisor"
        print("LaunchAgent chargé mais stack down — kickstart…")
        result = subprocess.run(
            ["launchctl", "kickstart", "-k", label],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip()
            print(f"Erreur kickstart : {err or f'exit {result.returncode}'}")
            return 1

    print("Attente santé (supervisor :9000, backend)…")
    if not _wait_healthy(timeout_s=90.0):
        print("Timeout : le stack n'est pas revenu sain.")
        print(f"Logs : {SUPERVISOR_LOG}")
        cmd_status()
        return 1

    print("OK — stack démarré.")
    return cmd_status()


def cmd_restart() -> int:
    """Arrêt complet, attente bornée, puis relance. Alias CLI : ``jarvis maj``."""
    from scripts.jarvis_stack import RestartBlocked, cli_restart

    try:
        return cli_restart(
            root=PROJECT_DIR,
            stop=cmd_stop,
            start=cmd_start,
            ports=(9000, _backend_port()),
        )
    except RestartBlocked as exc:
        print(f"Relance refusée : {exc}")
        return 1


COMMANDS = {
    "stop": cmd_stop,
    "start": cmd_start,
    "restart": cmd_restart,
    "maj": cmd_restart,
    "status": cmd_status,
    "install": cmd_install,
    "uninstall": cmd_uninstall,
    "open": cmd_open,
}


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    if action not in COMMANDS:
        print(f"Usage: python {sys.argv[0]} {{{'|'.join(COMMANDS)}}}")
        sys.exit(1)
    sys.exit(COMMANDS[action]())
