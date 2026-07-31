#!/usr/bin/env python3
"""Installe ou désinstalle le service launchd JARVIS 24/7.

Le LaunchAgent exécute le supervisor avec le Python du venv réel. Un wrapper
JARVIS.app est aussi installé pour déclencher visiblement les demandes de
permissions micro/AppleEvents lors de la configuration initiale.

Usage:
    python scripts/jarvis_launchd.py install        # installe .app + launchd
    python scripts/jarvis_launchd.py uninstall      # desinstalle tout
    python scripts/jarvis_launchd.py status         # verifie l'etat
    python scripts/jarvis_launchd.py restart|maj    # redemarre le stack (prises en compte code)
    python scripts/jarvis_launchd.py open           # ouvre l'app (declenche les prompts permissions)

Raccourci terminal (apres install du CLI ~/.local/bin/jarvis) :
    jarvis maj
"""

from __future__ import annotations

import os
import shlex
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

    if not _bootstrap():
        print("Erreur : launchctl bootstrap a echoue.")
        return 1

    print()
    print("JARVIS installe en 24/7.")
    print()
    print("  Demarrage auto au boot     : oui")
    print("  Relance auto apres crash   : oui (KeepAlive)")
    print(f"  Logs                       : {SUPERVISOR_LOG}")
    print()
    print("  Lance manuellement pour les permissions :")
    print(f"    open {APP_DIR}")
    return 0


def cmd_uninstall() -> int:
    uid = os.getuid()
    subprocess.run(
        ["launchctl", "bootout", f"gui/{uid}/com.jarvis.supervisor"],
        capture_output=True,
    )
    if LAUNCHD_DEST.exists():
        LAUNCHD_DEST.unlink()
    print("Service launchd desinstalle.")
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

    ps = subprocess.run(["pgrep", "-f", "supervisor.py"], capture_output=True, text=True)
    if ps.stdout.strip():
        print(f"Supervisor actif : PID {ps.stdout.strip()}")
    else:
        print("Supervisor NON ACTIF")

    if APP_BIN.exists():
        print(f"App      : {APP_DIR}")

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


def _wait_healthy(*, timeout_s: float = 60.0) -> bool:
    """Attend supervisor (:9000) + backend (:8081)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _port_listening(9000) and _port_listening(8081):
            return True
        time.sleep(1.0)
    return False


def cmd_restart() -> int:
    """Redémarre tout le stack via launchd (prise en compte du code).

    Alias CLI : ``jarvis maj``.
    """
    if not LAUNCHD_DEST.exists() or not APP_BIN.exists():
        print("Service non installé — installation…")
        if cmd_install() != 0:
            return 1

    uid = os.getuid()
    label = f"gui/{uid}/com.jarvis.supervisor"

    if not _service_loaded(uid):
        print("LaunchAgent inactif — bootstrap…")
        if not _bootstrap():
            print("Erreur : launchctl bootstrap a échoué.")
            return 1

    print("Redémarrage JARVIS (launchctl kickstart -k)…")
    result = subprocess.run(
        ["launchctl", "kickstart", "-k", label],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        print(f"Erreur kickstart : {err or f'exit {result.returncode}'}")
        return 1

    print("Attente santé (supervisor :9000, backend :8081)…")
    if not _wait_healthy(timeout_s=90.0):
        print("Timeout : le stack n'est pas revenu sain.")
        print(f"Logs : {SUPERVISOR_LOG}")
        cmd_status()
        return 1

    print("OK — changements pris en compte, stack relancé.")
    return cmd_status()


if __name__ == "__main__":
    cmds = {
        "install": cmd_install,
        "uninstall": cmd_uninstall,
        "status": cmd_status,
        "open": cmd_open,
        "restart": cmd_restart,
        "maj": cmd_restart,
    }
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    if action not in cmds:
        print(f"Usage: python {sys.argv[0]} {{{'|'.join(cmds)}}}")
        sys.exit(1)
    sys.exit(cmds[action]())
