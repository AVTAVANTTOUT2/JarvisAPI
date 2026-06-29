#!/usr/bin/env python3
"""Diagnostic standalone des permissions macOS pour JARVIS.

Teste les 3 intégrations natives sans charger FastAPI :
  1. Apple Mail (Automation)
  2. Apple Calendar (Automation)
  3. iMessage / chat.db (Full Disk Access)

Usage :
    python scripts/test_macos_permissions.py
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time

BOLD = "\033[1m"
RESET = "\033[0m"
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
DIM = "\033[2m"


def ok(label: str, detail: str = "") -> None:
    print(f"  {GREEN}OK{RESET}  {label}{f'  {DIM}{detail}{RESET}' if detail else ''}")


def fail(label: str, detail: str = "") -> None:
    print(f"  {RED}ECHEC{RESET}  {label}")
    if detail:
        print(f"        {YELLOW}{detail}{RESET}")


def warn(label: str, detail: str = "") -> None:
    print(f"  {YELLOW}WARN{RESET}  {label}")
    if detail:
        print(f"        {DIM}{detail}{RESET}")


def run_osascript(script: str, timeout: float = 12.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def section(title: str) -> None:
    print(f"\n{BOLD}--- {title} ---{RESET}")


def test_mail() -> bool:
    section("1. Apple Mail (Automation)")
    try:
        r = run_osascript('tell application "Mail" to return name of every account', timeout=15.0)
    except subprocess.TimeoutExpired:
        fail("Mail.app", "TIMEOUT (15s) — Mail.app ne repond pas. Prompt Automation en attente ?")
        print(f"        Verifiez : Reglages Systeme > Confidentialite > Automatisation")
        return False
    except FileNotFoundError:
        fail("osascript introuvable", "Ce script doit tourner sur macOS.")
        return False

    if r.returncode == 0:
        accounts = (r.stdout or "").strip()
        ok("Mail.app", f"Comptes : {accounts}")
        return True

    stderr = (r.stderr or "").strip()
    if "Not authorized to send Apple events" in stderr:
        fail("Mail.app — PERMISSION REFUSEE")
        print(f"        {YELLOW}Reglages Systeme > Confidentialite et securite > Automatisation{RESET}")
        print(f"        {YELLOW}> Cochez Terminal/Cursor pour Mail.{RESET}")
    elif "-600" in stderr:
        fail("Mail.app — app non ouverte (erreur -600)")
        print(f"        Lancez Mail.app manuellement puis relancez ce test.")
    else:
        fail(f"Mail.app (rc={r.returncode})", stderr[:300])
    return False


def test_calendar() -> bool:
    section("2. Apple Calendar (Automation)")

    # D'abord essayer de lancer Calendar en background
    try:
        subprocess.run(
            ["open", "-gj", "-b", "com.apple.iCal"],
            capture_output=True, text=True, timeout=3.0,
        )
        time.sleep(0.5)
    except Exception:
        pass

    try:
        r = run_osascript(
            'tell application id "com.apple.iCal" to return name of every calendar',
            timeout=10.0,
        )
    except subprocess.TimeoutExpired:
        fail("Calendar.app", "TIMEOUT (10s) — prompt Automation en attente ?")
        print(f"        Verifiez : Reglages Systeme > Confidentialite > Automatisation")
        return False
    except FileNotFoundError:
        fail("osascript introuvable", "Ce script doit tourner sur macOS.")
        return False

    if r.returncode == 0:
        calendars = (r.stdout or "").strip()
        ok("Calendar.app", f"Calendriers : {calendars}")
        return True

    stderr = (r.stderr or "").strip()
    if "Not authorized to send Apple events" in stderr:
        fail("Calendar.app — PERMISSION REFUSEE")
        print(f"        {YELLOW}Reglages Systeme > Confidentialite et securite > Automatisation{RESET}")
        print(f"        {YELLOW}> Cochez Terminal/Cursor pour Calendrier.{RESET}")
    elif "-600" in stderr:
        fail("Calendar.app — app non ouverte (erreur -600)")
        print(f"        Lancez Calendar.app manuellement puis relancez ce test.")
    else:
        fail(f"Calendar.app (rc={r.returncode})", stderr[:300])
    return False


def test_imessage() -> bool:
    section("3. iMessage / chat.db (Full Disk Access)")
    db_path = os.path.expanduser("~/Library/Messages/chat.db")

    if not os.path.exists(db_path):
        fail("chat.db introuvable", f"Chemin : {db_path}")
        print(f"        Messages.app n'a probablement jamais ete ouvert sur ce Mac.")
        return False

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=3.0)
        row = conn.execute("SELECT COUNT(*) FROM message").fetchone()
        count = row[0] if row else 0
        conn.close()
        ok("chat.db", f"{count} messages accessibles en lecture")
        return True
    except sqlite3.OperationalError as e:
        err = str(e).lower()
        if "unable to open" in err or "authorization" in err or "permission" in err:
            fail("chat.db — ACCES REFUSE", str(e))
            print(f"        {YELLOW}Reglages Systeme > Confidentialite et securite > Acces complet au disque{RESET}")
            print(f"        {YELLOW}> Ajoutez Terminal / Cursor / l'app qui lance JARVIS.{RESET}")
        else:
            fail(f"OperationalError : {e}")
        return False
    except sqlite3.DatabaseError as e:
        fail(f"DatabaseError : {e}")
        return False
    except Exception as e:
        fail(f"Erreur inattendue : {type(e).__name__}: {e}")
        return False


def test_messages_send() -> bool:
    section("4. Messages.app / envoi (Automation)")
    try:
        r = run_osascript(
            'tell application "Messages" to return name of every account',
            timeout=10.0,
        )
    except subprocess.TimeoutExpired:
        fail("Messages.app", "TIMEOUT — prompt Automation en attente ?")
        return False
    except FileNotFoundError:
        fail("osascript introuvable")
        return False

    if r.returncode == 0:
        ok("Messages.app", (r.stdout or "").strip()[:100])
        return True

    stderr = (r.stderr or "").strip()
    if "Not authorized to send Apple events" in stderr:
        fail("Messages.app — PERMISSION REFUSEE")
        print(f"        {YELLOW}Reglages Systeme > Confidentialite et securite > Automatisation{RESET}")
        print(f"        {YELLOW}> Cochez Terminal/Cursor pour Messages.{RESET}")
    else:
        fail(f"Messages.app (rc={r.returncode})", stderr[:300])
    return False


def main() -> int:
    print(f"\n{BOLD}=== JARVIS — Diagnostic permissions macOS ==={RESET}")
    print(f"{DIM}Ce script teste l'acces aux apps natives sans charger FastAPI.{RESET}")

    results = {
        "Mail": test_mail(),
        "Calendar": test_calendar(),
        "iMessage (chat.db)": test_imessage(),
        "Messages (envoi)": test_messages_send(),
    }

    section("RESUME")
    all_ok = True
    for name, passed in results.items():
        status = f"{GREEN}ACTIF{RESET}" if passed else f"{RED}INACTIF{RESET}"
        print(f"  {name:30s} {status}")
        if not passed:
            all_ok = False

    if all_ok:
        print(f"\n{GREEN}Toutes les integrations sont fonctionnelles.{RESET}")
    else:
        print(f"\n{YELLOW}Certaines integrations sont inactives.{RESET}")
        print(f"{DIM}Corrigez les permissions listees ci-dessus puis relancez ce script.{RESET}")
        print(f"{DIM}Apres correction, redemarrez JARVIS pour que les integrations se reinitialisent.{RESET}")

    print()
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
