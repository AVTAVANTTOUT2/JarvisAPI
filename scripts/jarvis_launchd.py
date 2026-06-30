#!/usr/bin/env python3
"""Installe ou desinstalle le service launchd JARVIS 24/7.

Le service lance JARVIS.app (wrapper natif macOS) via launchd,
ce qui garantit que les permissions micro/AppleEvents sont persistantes
apres reboot et veille.

Usage:
    python scripts/jarvis_launchd.py install        # installe .app + launchd
    python scripts/jarvis_launchd.py uninstall      # desinstalle tout
    python scripts/jarvis_launchd.py status         # verifie l'etat
    python scripts/jarvis_launchd.py open           # ouvre l'app (declenche les prompts permissions)
"""

from __future__ import annotations

import os
import sys
import subprocess
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
LOGS_DIR = PROJECT_DIR / "data" / "logs"
VENV_PYTHON = str(PROJECT_DIR / "venv" / "bin" / "python")
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
        f"cd {PROJECT_DIR}\n"
        f"exec {VENV_PYTHON} supervisor.py\n",
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
    """Ecrit le plist launchd qui pointe vers JARVIS.app."""
    LAUNCHD_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    plist = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        "    <key>Label</key>\n"
        "    <string>com.jarvis.supervisor</string>\n"
        "    <key>ProgramArguments</key>\n"
        "    <array>\n"
        f"        <string>{APP_BIN}</string>\n"
        "    </array>\n"
        f"    <key>WorkingDirectory</key>\n"
        f"    <string>{PROJECT_DIR}</string>\n"
        "    <key>RunAtLoad</key>\n"
        "    <true/>\n"
        "    <key>KeepAlive</key>\n"
        "    <true/>\n"
        "    <key>ThrottleInterval</key>\n"
        "    <integer>5</integer>\n"
        f"    <key>StandardOutPath</key>\n"
        f"    <string>{SUPERVISOR_LOG}</string>\n"
        f"    <key>StandardErrorPath</key>\n"
        f"    <string>{SUPERVISOR_LOG}</string>\n"
        "    <key>EnvironmentVariables</key>\n"
        "    <dict>\n"
        "        <key>PATH</key>\n"
        "        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>\n"
        "        <key>PYTHONUNBUFFERED</key>\n"
        "        <string>1</string>\n"
        f"        <key>HOME</key>\n"
        f"        <string>{HOME}</string>\n"
        "    </dict>\n"
        "</dict>\n"
        "</plist>\n"
    )
    LAUNCHD_DEST.write_text(plist, encoding="utf-8")
    print(f"Plist launchd installe : {LAUNCHD_DEST}")


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
    _install_app()
    _install_launchd_plist()

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


if __name__ == "__main__":
    cmds = {
        "install": cmd_install,
        "uninstall": cmd_uninstall,
        "status": cmd_status,
        "open": cmd_open,
    }
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    if action not in cmds:
        print(f"Usage: python {sys.argv[0]} {{{'|'.join(cmds)}}}")
        sys.exit(1)
    sys.exit(cmds[action]())
