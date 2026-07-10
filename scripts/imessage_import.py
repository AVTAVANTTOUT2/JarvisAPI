#!/usr/bin/env python3
"""CLI d'import iMessage — importe chat.db dans jarvis.db.

Usage :
    python scripts/imessage_import.py              # import initial complet
    python scripts/imessage_import.py --sync       # sync incrementale
    python scripts/imessage_import.py --reset      # reset le curseur
    python scripts/imessage_import.py --check      # audit sans import
    python scripts/imessage_import.py --status     # etat du curseur
    python scripts/imessage_import.py --reconcile  # reconciliation seule
    python scripts/imessage_import.py --doctor     # diagnostic complet de l'environnement
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from database import init_db
from integrations.imessage_import import IMessageImporter, imessage_importer


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(level=level, format=fmt)


# ═══════════════════════════════════════════════════════════
# Mode Doctor — diagnostic complet
# ═══════════════════════════════════════════════════════════

def cmd_doctor(importer: IMessageImporter, args: argparse.Namespace) -> int:
    """Diagnostic complet de l'acces a chat.db et de l'environnement TCC."""
    report: dict[str, any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sections": {},
    }

    _doctor_system_info(report)
    _doctor_python_info(report)
    _doctor_process_chain(report)
    _doctor_cursor_info(report)
    _doctor_launchagent(report)
    _doctor_tcc(report)
    _doctor_chat_db(report)
    _doctor_concurrent_access(report)
    _doctor_jarvis_running(report)
    _doctor_sqlite_open(report)
    _doctor_summary(report)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        _print_doctor_report(report)

    overall = report.get("sections", {}).get("sqlite_open", {}).get("success", False)
    return 0 if overall else 1


def _add_section(report: dict, key: str, title: str, data: dict) -> None:
    data["_title"] = title
    report["sections"][key] = data


def _doctor_system_info(report: dict) -> None:
    data = {
        "hostname": platform.node(),
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "user": os.environ.get("USER", ""),
        "uid": os.getuid(),
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "shell": os.environ.get("SHELL", ""),
        "home": os.environ.get("HOME", ""),
        "cwd": os.getcwd(),
        "virtual_env": os.environ.get("VIRTUAL_ENV", "(not set)"),
    }
    _add_section(report, "system", "Systeme", data)


def _doctor_python_info(report: dict) -> None:
    data = {
        "sys_executable": sys.executable,
        "sys_prefix": sys.prefix,
        "sys_base_prefix": sys.base_prefix,
        "platform_python_version": platform.python_version(),
        "realpath": os.path.realpath(sys.executable),
        "which_python3": shutil.which("python3"),
        "which_python": shutil.which("python"),
        "which_uv": shutil.which("uv"),
        "path": os.environ.get("PATH", ""),
    }
    if os.path.exists(sys.executable):
        st = os.stat(sys.executable)
        data["executable_inode"] = st.st_ino
        data["executable_size"] = st.st_size
        with open(sys.executable, "rb") as f:
            data["executable_sha256"] = hashlib.sha256(f.read()).hexdigest()
    _add_section(report, "python", "Python", data)


def _doctor_process_chain(report: dict) -> None:
    """Remonte la chaine de processus parents jusqu'a init/launchd."""
    chain = []
    pid = os.getpid()
    for _ in range(10):
        try:
            result = subprocess.run(
                ["ps", "-o", "pid,ppid,comm,args", "-p", str(pid)],
                capture_output=True, text=True, timeout=3,
            )
            lines = result.stdout.strip().split("\n")
            if len(lines) < 2:
                break
            parts = lines[1].split(None, 3)
            if len(parts) < 3:
                break
            ppid = int(parts[1])
            comm = parts[2]
            args_str = parts[3] if len(parts) > 3 else comm
            chain.append({"pid": pid, "ppid": ppid, "comm": comm, "args": args_str[:200]})
            if ppid <= 1:
                break
            pid = ppid
        except Exception:
            break

    # Detecter SSH / cursor-server dans la chaine
    chain_str = " → ".join(
        f"{c['comm']}" for c in chain
    )
    is_under_cursor_server = any(
        "cursor-server" in c["comm"] or "node" in c["comm"]
        for c in chain
    )
    is_under_sshd = any("sshd" in c["comm"] for c in chain)

    data = {
        "chain": chain,
        "chain_summary": chain_str,
        "under_cursor_server": is_under_cursor_server,
        "under_sshd": is_under_sshd,
        "under_remote": is_under_cursor_server or is_under_sshd,
        "tcc_warning": (
            "Ce processus est lance via cursor-server/SSH. "
            "TCC evalue l'identite du processus parent (node/sshd), "
            "pas Cursor.app. Full Disk Access doit etre accorde a "
            "sshd OU au binaire cursor-server, pas a Cursor."
            if is_under_cursor_server or is_under_sshd
            else None
        ),
    }
    _add_section(report, "process_chain", "Chaine de processus", data)


def _doctor_cursor_info(report: dict) -> None:
    cursor_bins = []
    try:
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.split("\n"):
            if "cursor-server" in line and "grep" not in line:
                parts = line.split(None, 10)
                if len(parts) >= 11:
                    cursor_bins.append({
                        "pid": parts[1],
                        "cpu": parts[2],
                        "mem": parts[3],
                        "args": parts[10][:200],
                    })
    except Exception:
        pass

    cursor_app = None
    for loc in ["/Applications/Cursor.app", "/Applications/Cursor/Cursor.app"]:
        if os.path.exists(loc):
            cursor_app = loc
            try:
                r = subprocess.run(
                    ["codesign", "-dvvv", loc],
                    capture_output=True, text=True, timeout=5,
                )
                if r.returncode == 0:
                    for line in r.stderr.split("\n"):
                        if "Identifier" in line:
                            cursor_app += f" [{line.strip()}]"
                            break
            except Exception:
                pass
            break

    data = {
        "cursor_server_processes": len(cursor_bins),
        "cursor_server_details": cursor_bins[:3],
        "cursor_app_path": cursor_app,
        "remote_mode": len(cursor_bins) > 0,
    }
    _add_section(report, "cursor", "Cursor IDE", data)


def _doctor_launchagent(report: dict) -> None:
    agents = {}
    la_dir = Path.home() / "Library" / "LaunchAgents"
    if la_dir.exists():
        for f in la_dir.glob("*jarvis*"):
            try:
                r = subprocess.run(
                    ["plutil", "-p", str(f)],
                    capture_output=True, text=True, timeout=5,
                )
                agents[str(f.name)] = r.stdout.strip() if r.returncode == 0 else f"ERR: {r.stderr}"
            except Exception as e:
                agents[str(f.name)] = str(e)

    # Verifier si le supervisor tourne
    supervisor_running = False
    supervisor_python = ""
    try:
        r = subprocess.run(
            ["pgrep", "-fl", "supervisor.py"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            supervisor_running = True
            supervisor_python = r.stdout.strip()
    except Exception:
        pass

    # Verifier main.py
    main_running = False
    main_python = ""
    try:
        r = subprocess.run(
            ["pgrep", "-fl", "main.py"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            main_running = True
            main_python = r.stdout.strip()
    except Exception:
        pass

    data = {
        "launch_agents": agents,
        "supervisor_running": supervisor_running,
        "supervisor_cmd": supervisor_python,
        "main_running": main_running,
        "main_cmd": main_python,
        "supervisor_has_fda_likely": supervisor_running,
    }
    _add_section(report, "launchagent", "LaunchAgent", data)


def _doctor_tcc(report: dict) -> None:
    # Lister ce qui a acces au dossier Messages
    tcc_status = {
        "chat_db_accessible": _test_chat_db_open(),
        "os_access_read": os.access(
            str(Path.home() / "Library" / "Messages" / "chat.db"), os.R_OK,
        ),
        "os_access_write": os.access(
            str(Path.home() / "Library" / "Messages" / "chat.db"), os.W_OK,
        ),
    }

    # Tenter d'identifier le processus responsable via lsof
    try:
        r = subprocess.run(
            ["lsof", str(Path.home() / "Library" / "Messages" / "chat.db")],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            tcc_status["lsof_output"] = r.stdout.strip()
            tcc_status["lsof_available"] = True
        else:
            tcc_status["lsof_available"] = False
            tcc_status["lsof_error"] = r.stderr.strip()
    except Exception:
        tcc_status["lsof_available"] = False

    # Quelle application a FDA ?
    python_real = os.path.realpath(sys.executable)
    tcc_status["current_python_needs_fda"] = python_real
    tcc_status["tcc_interpretation"] = (
        "TCC (Transparency, Consent, and Control) evalue l'identite du "
        "processus qui ouvre le fichier. Sous macOS Sequoia, le TCC "
        "utilise le code-signing identity de l'executable. "
        "Si lance depuis Cursor remote (ssh/cursor-server), le processus "
        "vu par TCC est cursor-server (node) ou sshd, PAS Cursor.app."
    )

    _add_section(report, "tcc", "TCC / Full Disk Access", tcc_status)


def _test_chat_db_open() -> bool:
    chat_db = Path.home() / "Library" / "Messages" / "chat.db"
    if not chat_db.exists():
        return False
    try:
        conn = sqlite3.connect(f"file:{chat_db}?mode=ro", uri=True, timeout=2.0)
        conn.execute("SELECT COUNT(*) FROM message LIMIT 1")
        conn.close()
        return True
    except (sqlite3.OperationalError, PermissionError, OSError):
        return False


def _doctor_chat_db(report: dict) -> None:
    chat_db = Path.home() / "Library" / "Messages" / "chat.db"
    exists = chat_db.exists()
    data = {
        "path": str(chat_db),
        "exists": exists,
    }
    if exists:
        st = chat_db.stat()
        data["size_mb"] = round(st.st_size / (1024 * 1024), 1)
        data["size_bytes"] = st.st_size
        data["inode"] = st.st_ino
        data["modified_at"] = datetime.fromtimestamp(st.st_mtime).isoformat()
        data["readable"] = os.access(str(chat_db), os.R_OK)
        data["writable"] = os.access(str(chat_db), os.W_OK)

    # Verifier WAL et SHM
    for suffix, label in [("-wal", "WAL"), ("-shm", "SHM")]:
        p = Path(str(chat_db) + suffix)
        data[f"has_{label.lower()}"] = p.exists()
        if p.exists():
            data[f"{label.lower()}_size_bytes"] = p.stat().st_size

    _add_section(report, "chat_db", "chat.db", data)


def _doctor_concurrent_access(report: dict) -> None:
    chat_db = str(Path.home() / "Library" / "Messages" / "chat.db")
    processes = []
    try:
        r = subprocess.run(
            ["lsof", chat_db],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            for line in r.stdout.strip().split("\n")[1:]:
                parts = line.split()
                if len(parts) >= 10:
                    processes.append({
                        "command": parts[0],
                        "pid": parts[1],
                        "user": parts[2],
                        "fd": parts[3],
                        "size": parts[6] if len(parts) > 6 else "",
                        "node": parts[-1] if len(parts) > 7 else "",
                    })
    except Exception:
        pass

    data = {
        "open_processes": processes,
        "open_count": len(processes),
        "sqlite_warning": (
            "SQLite en mode WAL accepte plusieurs lecteurs simultanes. "
            "Aucun conflit attendu."
            if len(processes) > 0
            else None
        ),
    }
    _add_section(report, "concurrent", "Acces concurrents", data)


def _doctor_jarvis_running(report: dict) -> None:
    processes = {}
    for proc_name in ["main.py", "supervisor.py", "screen_watcher", "audio_daemon"]:
        try:
            r = subprocess.run(
                ["pgrep", "-fla", proc_name],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip():
                processes[proc_name] = r.stdout.strip().split("\n")
            else:
                processes[proc_name] = []
        except Exception:
            processes[proc_name] = []

    can_use_existing = bool(processes.get("main.py"))

    data = {
        "running": processes,
        "can_relay_through_supervisor": can_use_existing,
        "relay_advice": (
            "Le supervisor (PID={}) tourne deja avec FDA. "
            "L'import peut etre declenche via son API REST "
            "plutot que directement depuis Cursor."
            if can_use_existing
            else "Aucun processus JARVIS existant. Lancement direct requis."
        ),
    }
    _add_section(report, "jarvis_process", "Processus JARVIS", data)


def _doctor_sqlite_open(report: dict) -> None:
    chat_db = str(Path.home() / "Library" / "Messages" / "chat.db")
    success = False
    error_full = ""
    error_type = ""
    attempts = []

    # Tentative 1 : URI mode=ro
    try:
        conn = sqlite3.connect(f"file:{chat_db}?mode=ro", uri=True, timeout=2.0)
        c = conn.execute("SELECT COUNT(*) FROM message").fetchone()
        count = c[0] if c else 0
        conn.close()
        success = True
        attempts.append({"method": "URI mode=ro", "result": f"SUCCESS ({count} messages)"})
    except sqlite3.OperationalError as e:
        error_full = str(e)
        error_type = type(e).__name__
        attempts.append({"method": "URI mode=ro", "result": f"FAILED — {e}"})
    except PermissionError as e:
        error_full = f"PermissionError(errno={e.errno}): {e}"
        error_type = "PermissionError"
        attempts.append({"method": "URI mode=ro", "result": f"FAILED — {e}"})
    except OSError as e:
        error_full = f"OSError(errno={e.errno}): {e}"
        error_type = "OSError"
        attempts.append({"method": "URI mode=ro", "result": f"FAILED — {e}"})

    # Tentative 2 : path direct
    if not success:
        try:
            conn = sqlite3.connect(chat_db, uri=False, timeout=2.0)
            conn.execute("SELECT COUNT(*) FROM message LIMIT 1")
            conn.close()
            success = True
            attempts.append({"method": "Direct path", "result": "SUCCESS"})
        except Exception as e:
            attempts.append({"method": "Direct path", "result": f"FAILED — {e}"})

    data = {
        "success": success,
        "attempts": attempts,
        "error_type": error_type,
        "error_message": error_full,
    }
    _add_section(report, "sqlite_open", "Ouverture SQLite", data)


def _doctor_summary(report: dict) -> None:
    sections = report.get("sections", {})
    sqlite_ok = sections.get("sqlite_open", {}).get("success", False)
    tcc_accessible = sections.get("tcc", {}).get("chat_db_accessible", False)
    under_remote = sections.get("process_chain", {}).get("under_remote", False)
    cursor_remote = sections.get("cursor", {}).get("remote_mode", False)
    jarvis_running = any(
        sections.get("jarvis_process", {}).get("running", {}).get(k, [])
        for k in ["main.py", "supervisor.py"]
    )

    findings = []
    fix_commands = []

    if not sqlite_ok:
        findings.append("chat.db est INACCESSIBLE depuis ce processus.")
        if under_remote or cursor_remote:
            findings.append(
                "CAUSE RACINE : Vous utilisez Cursor en mode SSH remote. "
                "Le processus qui tente d'ouvrir chat.db est cursor-server (node), "
                "qui N'est PAS Cursor.app. TCC refuse car cursor-server n'a "
                "pas Full Disk Access."
            )
            fix_commands.append(
                "SOLUTION 1 (manuelle) : Ajouter /usr/sbin/sshd (ou cursor-server) "
                "dans Reglages Systeme > Confidentialite > Acces complet au disque."
            )
            fix_commands.append(
                "SOLUTION 2 (recommandee) : Lancer l'import via le supervisor "
                "qui tourne deja avec FDA : "
                "curl -X POST http://127.0.0.1:8081/api/imessage-import"
            )
            fix_commands.append(
                "SOLUTION 3 : Ouvrir un Terminal.app standard (pas Cursor), "
                "cd ~/JarvisAPI && venv/bin/python scripts/imessage_import.py"
            )
        else:
            findings.append(
                "CAUSE RACINE : Le processus Python n'a pas Full Disk Access. "
                "Verifier que Terminal.app (ou Cursor.app si lance en local) "
                "est dans Reglages Systeme > Confidentialite > Acces complet au disque."
            )
            fix_commands.append(
                "Ajouter l'application qui lance JARVIS dans "
                "Reglages Systeme > Confidentialite > Acces complet au disque"
            )

    if jarvis_running:
        fix_commands.append(
            "TRIGGER VIA API : Le supervisor tourne. "
            "Lancer l'import via l'API : "
            "curl -X POST http://127.0.0.1:8081/api/imessage-import/run"
        )

    if sqlite_ok:
        findings.append("chat.db est accessible. L'import peut demarrer.")
        fix_commands.append("python scripts/imessage_import.py")

    data = {
        "chat_db_accessible": sqlite_ok,
        "cursor_remote_mode": cursor_remote,
        "under_remote_process": under_remote,
        "jarvis_already_running": jarvis_running,
        "diagnosis": findings,
        "fix_commands": fix_commands,
        "conclusion": (
            "IMPOSSIBLE depuis Cursor remote — utiliser le supervisor ou Terminal.app"
            if not sqlite_ok and (under_remote or cursor_remote)
            else "FDA manquant — ajouter l'app dans Reglages Systeme"
            if not sqlite_ok
            else "OK — pret pour l'import"
        ),
    }
    _add_section(report, "summary", "RESUME ET CORRECTIONS", data)


def _print_doctor_report(report: dict) -> None:
    """Affichage formate du rapport doctor."""
    print()
    print("=" * 70)
    print("  DOCTOR — Diagnostic complet iMessage / TCC / SQLite")
    print("=" * 70)

    sections = report.get("sections", {})
    order = [
        "system", "python", "process_chain", "cursor",
        "launchagent", "tcc", "chat_db", "concurrent",
        "jarvis_process", "sqlite_open", "summary",
    ]

    for key in order:
        sec = sections.get(key, {})
        title = sec.pop("_title", key)
        print(f"\n── {title}")

        if key == "process_chain":
            chain = sec.get("chain", [])
            for c in chain:
                marker = " ◀── ICI" if c["pid"] == os.getpid() else ""
                print(f"  {c['pid']:>6} → {c['ppid']:<6}  {c['comm']:<25}{marker}")
            if sec.get("tcc_warning"):
                print(f"\n  ⚠  {sec['tcc_warning']}")

        elif key == "summary":
            print()
            print("  ┌─ DIAGNOSTIC")
            for f in sec.get("diagnosis", []):
                print(f"  │  {f}")
            print("  └")
            print()
            print("  ┌─ CORRECTIONS")
            for cmd in sec.get("fix_commands", []):
                print(f"  │  → {cmd}")
            print("  └")
            print(f"\n  ► CONCLUSION : {sec.get('conclusion', '')}")

        elif key == "launchagent":
            for k, v in sec.items():
                if k == "launch_agents":
                    for name, plist in v.items():
                        print(f"  {name}: {plist[:200]}")
                else:
                    print(f"  {k}: {v}")

        elif key == "cursor":
            for k, v in sec.items():
                if isinstance(v, list):
                    for item in v:
                        print(f"  {item}")
                else:
                    print(f"  {k}: {v}")

        elif key == "sqlite_open":
            for attempt in sec.get("attempts", []):
                status = "✓" if "SUCCESS" in str(attempt["result"]) else "✗"
                print(f"  {status} {attempt['method']}")
                if "FAILED" in str(attempt["result"]):
                    print(f"    → {attempt['result']}")

        else:
            for k, v in sec.items():
                if isinstance(v, list):
                    if len(v) > 0:
                        print(f"  {k}:")
                        for item in v[:5]:
                            print(f"    - {item}")
                        if len(v) > 5:
                            print(f"    ... et {len(v) - 5} autres")
                elif isinstance(v, dict):
                    print(f"  {k}:")
                    for sk, sv in v.items():
                        print(f"    {sk}: {sv}")
                elif isinstance(v, bool):
                    print(f"  {k}: {'✓ OUI' if v else '✗ NON'}")
                else:
                    print(f"  {k}: {v}")

    print()
    print("=" * 70)
    print(f"  Rapport genere le {report.get('generated_at', '?')}")
    print("=" * 70)


# ═══════════════════════════════════════════════════════════
# Commandes existantes
# ═══════════════════════════════════════════════════════════

def cmd_import(importer: IMessageImporter, args: argparse.Namespace) -> int:
    """Import initial complet de chat.db.

    Utilise le daemon iMessage si disponible, sinon acces direct.
    """
    # Essayer le daemon d'abord
    try:
        from integrations.imessage_daemon_client import daemon_client

        health = daemon_client.health()
        if health.ok and health.data.get("ok"):
            print("Daemon iMessage detecte — import via daemon...")
            cursor = daemon_client.status()
            if cursor.data.get("cursor", {}).get("total_imported", 0) > 0 and not args.force:
                print(f"Un import precedent existe ({cursor.data['cursor']['total_imported']} messages).")
                print("Utilisez --force pour reimporter ou --sync pour la sync incrementale.")
                return 1

            ok, msg = daemon_client.ensure_imported(timeout_s=900)
            if ok:
                print(f"Import termine via daemon: {msg}")
                return 0
            print(f"Echec import via daemon: {msg}")
            return 1
        else:
            logger.info("[cli] Daemon indisponible — fallback acces direct")
    except Exception as e:
        logger.debug("[cli] Daemon client error: %s — fallback direct", e)

    # Fallback: acces direct
    return _cmd_import_direct(importer, args)


def _cmd_import_direct(importer: IMessageImporter, args: argparse.Namespace) -> int:
    """Import direct (fallback si daemon indisponible)."""
    if not importer.is_available():
        print("ERREUR : chat.db inaccessible.")
        print("  → Verifier : Reglages Systeme > Confidentialite > Acces complet au disque")
        print("  → Lancez --doctor pour un diagnostic complet")
        return 1

    cursor = importer.get_status()
    if cursor.get("total_imported", 0) > 0 and not args.force:
        print(f"Un import precedent existe ({cursor['total_imported']} messages deja importes).")
        print("Utilisez --force pour reimporter completement, ou --sync pour la sync incrementale.")
        return 1

    print("Demarrage de l'import complet...")
    print(f"  Chat DB   : ~/Library/Messages/chat.db")
    print(f"  JARVIS DB : jarvis.db")
    print(f"  Batch     : {importer.batch_size}")
    print()

    t0 = time.time()
    result = importer.import_all()
    elapsed = time.time() - t0

    _print_import_result(result, elapsed)
    return 0 if result.total_failed == 0 else 1


def cmd_sync(importer: IMessageImporter, args: argparse.Namespace) -> int:
    """Sync incrementale — daemon prioritaire."""
    try:
        from integrations.imessage_daemon_client import daemon_client

        ok, msg = daemon_client.ensure_synced(timeout_s=120)
        if ok:
            print(f"Sync via daemon: {msg}")
            return 0
        logger.info("[cli] Daemon sync failed: %s — fallback direct", msg)
    except Exception:
        pass

    return _cmd_sync_direct(importer, args)


def _cmd_sync_direct(importer: IMessageImporter, args: argparse.Namespace) -> int:
    """Sync direct (fallback)."""
    if not importer.is_available():
        print("ERREUR : chat.db inaccessible.")
        print("  → Lancez --doctor pour un diagnostic complet")
        return 1

    cursor = importer.get_status()
    last_rowid = cursor.get("last_apple_rowid", 0)
    print(f"Sync incrementale depuis ROWID {last_rowid}...")

    t0 = time.time()
    result = importer.sync_incremental()
    elapsed = time.time() - t0

    if result.total_messages == 0 and result.total_skipped == 0:
        print("Aucun nouveau message.")
    else:
        print(f"Sync terminee en {elapsed:.1f}s :")
        print(f"  Nouveaux messages : {result.total_messages}")
        print(f"  Skipes            : {result.total_skipped}")
        print(f"  Echoues           : {result.total_failed}")
        if result.errors:
            for err in result.errors[:5]:
                print(f"    - {err}")

    return 0


def cmd_check(importer: IMessageImporter, args: argparse.Namespace) -> int:
    """Audit sans import — reconciliation seule."""
    if not importer.is_available():
        print("ERREUR : chat.db inaccessible.")
        print("  → Lancez --doctor pour un diagnostic complet")
        return 1

    print("Lancement de l'audit (reconciliation)...")
    report = importer.reconcile()

    print()
    print("=" * 60)
    print("AUDIT iMessage")
    print("=" * 60)
    print(f"  Messages chat.db      : {report.chat_db_messages}")
    print(f"  Messages jarvis.db    : {report.jarvis_db_messages}")
    print(f"  Chats chat.db          : {report.chat_db_chats}")
    print(f"  Chats jarvis.db        : {report.jarvis_db_chats}")
    print(f"  Handles chat.db        : {report.chat_db_handles}")
    print(f"  Handles jarvis.db      : {report.jarvis_db_handles}")
    print(f"  Orphelins              : {report.orphan_messages}")
    print(f"  Corriges               : {report.orphan_fixed}")
    print(f"  Doublons trouves       : {report.duplicates_found}")
    print(f"  Doublons supprimes     : {report.duplicates_removed}")
    print(f"  OK                     : {report.ok}")

    if report.chat_db_messages > 0:
        pct = report.jarvis_db_messages / report.chat_db_messages * 100
        print(f"  Taux de couverture     : {pct:.1f}%")

    return 0 if report.ok else 1


def cmd_status(importer: IMessageImporter, args: argparse.Namespace) -> int:
    """Affiche l'etat du curseur de synchronisation."""
    status = importer.get_status()
    print("=" * 60)
    print("STATUT IMPORT iMessage")
    print("=" * 60)
    for key in [
        "status", "last_apple_rowid", "last_date", "total_imported",
        "total_failed", "last_sync_at", "completed_at", "started_at",
        "error_message", "jarvis_db_messages", "jarvis_db_chats", "jarvis_db_handles",
    ]:
        val = status.get(key, "—")
        print(f"  {key}: {val}")
    return 0


def cmd_reset(importer: IMessageImporter, args: argparse.Namespace) -> int:
    """Reinitialise le curseur pour un reimport complet."""
    confirm = input(
        "ATTENTION : reinitialiser le curseur forcera un reimport complet. "
        "Continuer ? (oui/NON) : "
    )
    if confirm.strip().lower() != "oui":
        print("Abandonne.")
        return 0

    importer.reset_cursor()
    print("Curseur reinitialise. Lancez `python scripts/imessage_import.py` pour re-importer.")
    return 0


def cmd_reconcile(importer: IMessageImporter, args: argparse.Namespace) -> int:
    """Reconciliation seule."""
    return cmd_check(importer, args)


def _print_import_result(result, elapsed: float) -> None:
    print()
    print("=" * 60)
    print("IMPORT TERMINE")
    print("=" * 60)
    print(f"  Duree               : {elapsed:.1f}s")
    print(f"  Mode                : {result.mode}")
    print(f"  Handles             : {result.total_handles}")
    print(f"  Chats               : {result.total_chats}")
    print(f"  Messages importes   : {result.total_messages}")
    print(f"  Messages skippes    : {result.total_skipped}")
    print(f"  Messages echoues    : {result.total_failed}")
    print(f"  Attachments         : {result.total_attachments}")
    print(f"  Reactions           : {result.total_reactions}")

    if result.errors:
        print(f"\n  Erreurs ({len(result.errors)}) :")
        for err in result.errors[:10]:
            print(f"    - {err}")
        if len(result.errors) > 10:
            print(f"    ... et {len(result.errors) - 10} autres")

    rec = result.reconciliation
    if rec:
        print()
        print("  RECONCILIATION :")
        print(f"    chat.db → {rec.get('chat_db_messages', '?')} messages")
        print(f"    jarvis.db → {rec.get('jarvis_db_messages', '?')} messages")
        print(f"    Orphelins corriges : {rec.get('orphan_fixed', 0)}")
        print(f"    Doublons supprimes : {rec.get('duplicates_removed', 0)}")
        print(f"    OK : {rec.get('ok', '?')}")


# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import iMessage — chat.db → jarvis.db",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  python scripts/imessage_import.py              # import initial complet
  python scripts/imessage_import.py --sync       # sync incrementale
  python scripts/imessage_import.py --force      # import force (ignore curseur)
  python scripts/imessage_import.py --reset      # reset le curseur
  python scripts/imessage_import.py --check      # audit sans import
  python scripts/imessage_import.py --status     # etat du curseur
  python scripts/imessage_import.py --doctor     # diagnostic complet TCC/SQLite/env
        """,
    )
    parser.add_argument("--sync", action="store_true", help="Sync incrementale (nouveaux messages)")
    parser.add_argument("--force", action="store_true", help="Forcer l'import initial meme si un precedent existe")
    parser.add_argument("--reset", action="store_true", help="Reinitialiser le curseur de sync")
    parser.add_argument("--check", action="store_true", help="Audit de reconciliation sans import")
    parser.add_argument("--status", action="store_true", help="Afficher l'etat du curseur")
    parser.add_argument("--reconcile", action="store_true", help="Reconciliation seule")
    parser.add_argument("--doctor", action="store_true", help="Diagnostic complet TCC/SQLite/environnement")
    parser.add_argument("--json", action="store_true", help="Sortie JSON (avec --doctor)")
    parser.add_argument("--batch", type=int, default=5000, help="Taille de batch (defaut: 5000)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Logs detailles (DEBUG)")

    args = parser.parse_args()

    _setup_logging(verbose=args.verbose)
    init_db()

    importer = IMessageImporter(batch_size=args.batch)

    if args.doctor:
        return cmd_doctor(importer, args)

    if not importer.is_available():
        print("AVERTISSEMENT : chat.db inaccessible.")
        print("  → Lancez --doctor pour un diagnostic complet.")
        if args.status:
            return cmd_status(importer, args)
        if args.reset:
            return cmd_reset(importer, args)
        return 1

    if args.reset:
        return cmd_reset(importer, args)
    elif args.check:
        return cmd_check(importer, args)
    elif args.status:
        return cmd_status(importer, args)
    elif args.sync:
        return cmd_sync(importer, args)
    elif args.reconcile:
        return cmd_reconcile(importer, args)
    else:
        return cmd_import(importer, args)


if __name__ == "__main__":
    sys.exit(main())
