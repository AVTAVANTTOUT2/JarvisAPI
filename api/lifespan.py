"""Cycle de vie de l'application FastAPI, sans dépendance à main.py."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI

import config
from agents import register_agent
from agents.coach import coach_agent
from agents.devops import devops_agent
from agents.info import info_agent
from agents.journal import journal_agent
from agents.memory import memory_agent
from agents.productivity import productivity_agent
from agents.school import school_agent
from database import get_active_device, init_db, register_device, set_active_device
from integrations import imessage_bridge
from integrations.apple_data import apple_data
from jarvis.event_bus import event_bus
from scripts.email_watcher import email_watcher
from websocket_registry import broadcast_ws

BASE_DIR = Path(__file__).resolve().parent.parent
logger = logging.getLogger("jarvis")


async def _auto_pull_ollama(model: str) -> None:
    """Pull un modele Ollama en background (ne bloque pas le demarrage)."""
    try:
        async with httpx.AsyncClient(timeout=600) as client:
            resp = await client.post(
                "http://localhost:11434/api/pull",
                json={"name": model, "stream": False},
            )
            if resp.status_code == 200:
                logger.info("[startup] Ollama : %s pulle avec succes", model)
            else:
                logger.warning("[startup] Ollama pull %s : HTTP %s", model, resp.status_code)
    except Exception as e:
        logger.warning("[startup] Ollama pull erreur : %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Démarrage : init DB + enregistrement des agents disponibles."""
    logger.info("Démarrage JARVIS…")
    init_db()
    event_bus.bind_loop(asyncio.get_running_loop())

    try:
        from scripts.db_migrations import run_startup_migrations

        run_startup_migrations()
    except Exception as e:
        logger.critical("Erreur migrations au démarrage : %s", e)

    # Cache Contacts.app (résolution numéro / email → nom affiché)
    # build_cache() est synchrone et peut bloquer >20s : lancé en background
    # task pour ne pas retarder le démarrage FastAPI.
    async def _build_contacts_cache():
        try:
            from integrations.contacts import contacts_reader

            if contacts_reader.is_available():
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, contacts_reader.build_cache)
                logger.info("[contacts] Cache : %d entrées", len(contacts_reader._cache))
                for handle, cn in list(contacts_reader._cache.items())[:5]:
                    logger.info("[contacts]   %s → %s", handle, cn)
        except Exception as e:
            logger.warning("[contacts] init cache : %s", e)

    asyncio.create_task(_build_contacts_cache())

    # Diagnostic iMessage : lecture de chat.db (nécessite Full Disk Access pour le terminal / Cursor).
    try:
        _health = apple_data.health()
        if _health.get("readable"):
            logger.info(
                "[imessage] chat.db accessible — %s messages dans la table message",
                _health.get("message_count", 0),
            )
        elif _health.get("exists"):
            logger.error("[imessage] chat.db illisible : %s", _health.get("error", "erreur inconnue"))
        else:
            logger.warning("[imessage] chat.db absent à %s", apple_data.db_path)
    except Exception as _e:
        logger.error("[imessage] Impossible de lire chat.db : %s", _e)
        logger.error(
            "[imessage] → Réglages Système > Confidentialité et sécurité > Accès complet au disque : "
            "ajoute Terminal, iTerm ou Cursor selon l’app qui lance JARVIS."
        )

    # Enregistrement des agents
    register_agent(info_agent)
    register_agent(school_agent)
    register_agent(productivity_agent)
    register_agent(coach_agent)
    register_agent(journal_agent)
    register_agent(memory_agent)
    register_agent(devops_agent)
    logger.info("Agents enregistrés : devops, info, school, productivity, coach, journal, memory")

    # Création des dossiers de sortie
    Path(config.SCHOOL_OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    Path(config.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)

    # Calendar.app : réveil arrière-plan uniquement (-g/-j) pour éviter les -600
    # AppleScript SANS voler le focus (open -a Calendar ramenait Calendrier au 1er plan).
    try:
        subprocess.Popen(
            ["open", "-gj", "-b", "com.apple.iCal"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("[startup] Calendar.app lancé en arrière-plan (sans focus)")
    except Exception as e:
        logger.warning("[startup] Impossible de lancer Calendar.app en arrière-plan : %s", e)

    # ── Daemon iMessage ──
    _imessage_daemon_process = None
    if config.IMESSAGE_DAEMON_ENABLED:
        try:
            import signal as _sig
            daemon_script = str(BASE_DIR / "scripts" / "imessage_daemon.py")
            if Path(daemon_script).exists():
                _imessage_daemon_process = subprocess.Popen(
                    [sys.executable, daemon_script, "--port", str(config.IMESSAGE_DAEMON_PORT)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    preexec_fn=lambda: _sig.signal(_sig.SIGINT, _sig.SIG_IGN),
                )
                logger.info(
                    "[startup] Daemon iMessage lance (PID=%d, port=%d)",
                    _imessage_daemon_process.pid,
                    config.IMESSAGE_DAEMON_PORT,
                )
        except Exception as e:
            logger.warning("[startup] Echec lancement daemon iMessage: %s", e)

    # ── Diagnostic iMessage ──
    try:
        from integrations.imessage_daemon_client import daemon_client
        health = daemon_client.health()
        if health.ok and health.data.get("ok"):
            logger.info("[imessage] Daemon OK — %s msg dans jarvis.db", health.data.get("messages_in_db", "?"))
        else:
            logger.warning("[imessage] Daemon: chat.db inaccessible — %s", health.data.get("error", health.error))
    except Exception:
        pass

    if not config.DEEPSEEK_API_KEY:
        logger.warning("⚠️  DEEPSEEK_API_KEY manquant — copie .env.example en .env et ajoute ta clé")

    # ── Helper : scan initial de l'analyse relationnelle ──
    async def _initial_relationship_scan(analyzer, reader) -> None:
        try:

            logger.info("[analyzer] Lancement du scan initial iMessage…")
            stats = await analyzer.run_initial_scan()
            logger.info("[analyzer] Scan initial terminé : %s", stats)
        except Exception as e:
            logger.error("[analyzer] Scan initial échoué : %s", e)

    # ── iMessage sourcing (lecture seule, chat.db) ──
    _imessage_scan_task = None
    _imessage_relationship_task = None
    try:
        if config.IMESSAGE_SOURCING_ENABLED:
            from integrations.imessage_reader import imessage_reader

            if imessage_reader.is_available():
                from scripts.relationship_analyzer import analyzer

                _imessage_relationship_task = asyncio.create_task(
                    _initial_relationship_scan(analyzer, imessage_reader),
                    name="imessage_relationship_scan",
                )
                _imessage_scan_task = asyncio.create_task(
                    imessage_reader.periodic_scan(config.IMESSAGE_SCAN_INTERVAL),
                    name="imessage_sourcing_scan",
                )
                logger.info(
                    "iMessage sourcing activé (lecture seule, scan %ss)",
                    config.IMESSAGE_SCAN_INTERVAL,
                )
            else:
                logger.warning(
                    "[startup] imessage_reader indisponible "
                    "(Full Disk Access manquant ?)"
                )
        else:
            logger.info(
                "iMessage sourcing désactivé (IMESSAGE_SOURCING_ENABLED=false)"
            )
    except ImportError:
        logger.warning(
            "[startup] modules iMessage reader / analyzer non importables"
        )
    except Exception as e:
        logger.warning("[startup] iMessage sourcing erreur : %s", e)

    # ── iMessage bridge (envoi) — VOLONTAIREMENT NON DÉMARRÉ ──
    # Le bridge n'est pas lancé au startup. L'envoi reste bloqué
    # au niveau de integrations/imessage.py tant que
    # IMESSAGE_SEND_ENABLED=false (défaut .env).

    # Email watcher — surveillance proactive des mails non lus.
    email_task = asyncio.create_task(email_watcher.start())
    logger.info(
        "Email watcher lancé — scan toutes les %.0fs",
        config.EMAIL_CHECK_INTERVAL,
    )

    try:
        from scripts.sync_contacts import sync_people_names

        asyncio.create_task(sync_people_names())
        logger.info("[startup] sync people ↔ Contacts.app programmée (background)")
    except Exception as e:
        logger.warning("[startup] sync contacts indisponible : %s", e)

    # Enregistrement de la machine locale (Mac Mini par défaut) + activation
    daemon_task = None
    try:
        local_device_id = config.DEVICE_ID or "mac_mini"
        register_device(
            device_id=local_device_id,
            device_name=config.DEVICE_NAME or f"Mac Mini ({local_device_id})",
            device_type="desktop",
        )
        if get_active_device() is None:
            set_active_device(local_device_id)
        logger.info("[startup] machine locale enregistrée : %s", local_device_id)
    except Exception as e:
        logger.warning("[startup] register_device locale : %s", e)

    # Daemon JARVIS — sentinelle permanente (screen watcher, notif proactives, wake word)
    if getattr(config, "DAEMON_ENABLED", True):
        try:
            from scripts.jarvis_daemon import daemon

            daemon_task = asyncio.create_task(daemon.start(), name="jarvis_daemon")
            logger.info("[startup] daemon JARVIS démarré (mode: veille)")
        except Exception as e:
            logger.warning("[startup] daemon JARVIS non démarré : %s", e)
    else:
        logger.info("[startup] daemon désactivé (DAEMON_ENABLED=false)")

    # Auto-pull du modèle vision Ollama si dispo mais modèle manquant
    try:
        import httpx as _httpx
        resp = _httpx.get("http://localhost:11434/api/tags", timeout=3)
        if resp.status_code == 200:
            models = [m["name"] for m in resp.json().get("models", [])]
            vision_model = getattr(config, "SCREEN_VISION_MODEL", "qwen2.5vl:7b")
            if not any(vision_model.split(":")[0] in m for m in models):
                logger.info("[startup] Ollama : pull %s en background...", vision_model)
                asyncio.create_task(_auto_pull_ollama(vision_model))
    except Exception:
        pass

    # Audio Daemon — micro natif Mac Mini (wake word + conversation mains libres)
    audio_daemon_task = None
    if getattr(config, "AUDIO_DAEMON_ENABLED", False):
        try:
            from scripts.audio_daemon import audio_daemon

            audio_daemon.set_broadcast(broadcast_ws)
            audio_daemon_task = asyncio.create_task(audio_daemon.start(), name="audio_daemon")
            logger.info("[startup] Audio daemon démarré (wake word + micro natif)")
        except Exception as e:
            logger.warning("[startup] Audio daemon non démarré : %s", e)

    # Connexion ADB automatique à la TV au démarrage (prépare le terrain,
    # évite la latence de adb connect au premier "allume la télé")
    try:
        import asyncio as _asyncio
        tv_ip = getattr(config, "TV_IP", "")
        tv_port = int(getattr(config, "TV_ADB_PORT", "5555") or "5555")
        if tv_ip:
            proc = await _asyncio.create_subprocess_exec(
                "adb", "connect", f"{tv_ip}:{tv_port}",
                stdout=_asyncio.subprocess.PIPE,
                stderr=_asyncio.subprocess.PIPE,
            )
            stdout, stderr = await _asyncio.wait_for(proc.communicate(), timeout=5.0)
            output = (stdout + stderr).decode(errors="replace").strip()
            if "connected" in output.lower() or "already" in output.lower():
                logger.info("[startup] ADB connecté à la TV (%s:%s) — %s", tv_ip, tv_port, output.split("\n")[0][:80])
            else:
                logger.debug("[startup] ADB TV non joignable (%s) : %s", tv_ip, output[:100])
    except Exception as e:
        logger.debug("[startup] ADB TV skip : %s", e)

    logger.info(f"JARVIS prêt → http://localhost:{config.WEB_PORT}")

    # ── Délégation Cursor : reprise des jobs persistants après restart ──
    if getattr(config, "CURSOR_DELEGATION_ENABLED", True):
        try:
            from integrations.cursor_delegation import cursor_delegation

            resumed = cursor_delegation.resume_pending_jobs()
            if resumed.get("requeued") or resumed.get("orphaned"):
                logger.info("[startup] jobs Cursor : %s", resumed)
        except Exception as e:
            logger.warning("[startup] reprise jobs Cursor : %s", e)

    from scripts.scheduler import start_scheduler

    start_scheduler()
    logger.info("Scheduler APScheduler démarré (briefing matin, tâches en retard)")

    yield

    from scripts.scheduler import shutdown_scheduler

    shutdown_scheduler()
    if imessage_bridge is not None:
        imessage_bridge.stop()
    # Annulation des tâches de sourcing iMessage
    for _task, _label in [
        (_imessage_scan_task, "imessage_scan"),
        (_imessage_relationship_task, "imessage_relationship"),
    ]:
        if _task is not None:
            _task.cancel()
            try:
                await _task
            except (asyncio.CancelledError, Exception):
                pass

    email_watcher.stop()
    if email_task is not None:
        email_task.cancel()
        try:
            await email_task
        except (asyncio.CancelledError, Exception):
            pass

    if daemon_task is not None:
        try:
            from scripts.jarvis_daemon import daemon as _daemon

            _daemon.stop()
        except Exception:
            pass
        daemon_task.cancel()
        try:
            await daemon_task
        except (asyncio.CancelledError, Exception):
            pass

    if audio_daemon_task is not None:
        try:
            from scripts.audio_daemon import audio_daemon as _audio_daemon

            await _audio_daemon.stop()
        except Exception:
            pass
        audio_daemon_task.cancel()
        try:
            await audio_daemon_task
        except (asyncio.CancelledError, Exception):
            pass

    await event_bus.wait_until_idle()
    event_bus.unbind_loop()
    logger.info("Arrêt JARVIS.")
