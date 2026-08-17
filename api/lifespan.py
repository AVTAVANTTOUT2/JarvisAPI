"""Cycle de vie de l'application FastAPI, sans dépendance à main.py."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI

import config
from agents import register_agent
from agents.coach import coach_agent
from agents.devops import devops_agent
from agents.food import food_agent
from agents.info import info_agent
from agents.journal import journal_agent
from agents.memory import memory_agent
from agents.productivity import productivity_agent
from agents.school import school_agent
from api.lifespan_helpers import connect_tv_adb
from database import (
    get_active_device,
    init_db,
    register_local_device,
    set_active_device,
)
from jarvis.event_bus import event_bus
from jarvis.tv_events import publish_audio_daemon_state
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
                logger.warning(
                    "[startup] Ollama pull %s : HTTP %s", model, resp.status_code
                )
    except Exception as e:
        logger.warning("[startup] Ollama pull erreur : %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Démarrage : init DB + enregistrement des agents disponibles."""
    logger.info("Démarrage JARVIS…")
    try:
        config.validate_required_runtime_config()
    except config.ConfigurationError as exc:
        logger.critical("[startup] Configuration invalide : %s", exc)
        raise
    init_db()
    from scripts.db_migrations import run_startup_migrations

    # Une migration partiellement appliquée rendrait le schéma ambigu. Le
    # processus doit donc échouer avant de lier les consommateurs runtime.
    run_startup_migrations()
    event_bus.bind_loop(asyncio.get_running_loop())

    # Cache Contacts.app (résolution numéro / email → nom affiché)
    # build_cache() est synchrone et peut bloquer >20s : lancé en background
    # task pour ne pas retarder le démarrage FastAPI.
    async def _build_contacts_cache():
        try:
            from integrations.contacts import contacts_reader

            if contacts_reader.is_available():
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, contacts_reader.build_cache)
                logger.info(
                    "[contacts] Cache : %d entrées", len(contacts_reader._cache)
                )
                for handle, cn in list(contacts_reader._cache.items())[:5]:
                    logger.info("[contacts]   %s → %s", handle, cn)
        except Exception as e:
            logger.warning("[contacts] init cache : %s", e)

    asyncio.create_task(_build_contacts_cache())

    # Enregistrement des agents
    register_agent(info_agent)
    register_agent(school_agent)
    register_agent(productivity_agent)
    register_agent(coach_agent)
    register_agent(journal_agent)
    register_agent(memory_agent)
    register_agent(devops_agent)
    register_agent(food_agent)
    logger.info(
        "Agents enregistrés : devops, food, info, school, productivity, coach, journal, memory"
    )

    # Création des dossiers de sortie
    Path(config.SCHOOL_OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    from jarvis.uploads import harden_upload_tree_permissions
    from scripts.db_maintenance import harden_backup_permissions

    harden_upload_tree_permissions()
    harden_backup_permissions()

    # L'ingestion Apple est un service launchd indépendant. Le backend ne
    # doit jamais forker après le chargement de Torch/uvloop : cela a déjà
    # laissé des processus bloqués dans les handlers OpenMP de ``fork``.
    # La readiness et /api/data-health exposent l'état du service externe.
    if getattr(config, "INGESTION_SERVICE_ENABLED", True):
        logger.info("[startup] ingestion gérée par com.jarvis.ingestion")

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
        register_local_device(
            device_id=local_device_id,
            device_name=config.DEVICE_NAME or f"Mac Mini ({local_device_id})",
            device_type="desktop",
        )
        if get_active_device() is None:
            set_active_device(local_device_id)
        logger.info("[startup] machine locale enregistrée : %s", local_device_id)
    except Exception as e:
        logger.warning("[startup] register_local_device : %s", e)

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

            async def _broadcast_daemon_state(event: dict) -> None:
                """Diffuse l'état du daemon aux clients chat, puis au canal TV.

                Le miroir TV est un flux distinct et borné : il ne peut ni
                retarder ni faire échouer la diffusion du chat.
                """
                await broadcast_ws(event)
                publish_audio_daemon_state(event)

            audio_daemon.set_broadcast(_broadcast_daemon_state)
            audio_daemon_task = asyncio.create_task(
                audio_daemon.start(), name="audio_daemon"
            )
            logger.info("[startup] Audio daemon démarré (wake word + micro natif)")
        except Exception as e:
            logger.warning("[startup] Audio daemon non démarré : %s", e)

    # Prépare ADB avant la première commande TV, sans rendre le démarrage fatal.
    await connect_tv_adb()

    logger.info(f"JARVIS prêt → http://localhost:{config.WEB_PORT}")

    # Le core réconcilie les runs via le registre dynamique. L'absence de plugin
    # reste un état fonctionnel et ne doit jamais empêcher JARVIS de démarrer.
    agentic_service = None
    agentic_finalizer_stop = asyncio.Event()
    agentic_finalizer_task = None
    try:
        from jarvis.agentic import get_agentic_service

        agentic_service = get_agentic_service()
        agentic_service.start_maintenance()
        reconciled = await agentic_service.reconcile_nonterminal()
        if reconciled:
            logger.info("[startup] runs agentiques réconciliés : %d", len(reconciled))
    except Exception as exc:
        logger.warning("[startup] réconciliation agentique indisponible : %s", exc)

    # Pilotage de tâches : traduit les événements runtime en activité lisible.
    # L'abonnement est posé une seule fois et reste sans effet tant qu'aucune
    # tâche n'est liée à un run — un événement orphelin est simplement ignoré.
    try:
        from jarvis.task_control.service import get_task_control_service

        get_task_control_service().bind_runtime_events()
    except Exception as exc:
        logger.warning("[startup] pilotage de tâches indisponible : %s", exc)

    try:
        from agents.devagent.finalizer import run_engineering_finalizer_worker

        agentic_finalizer_task = asyncio.create_task(
            run_engineering_finalizer_worker(agentic_finalizer_stop),
            name="agentic-engineering-finalizer",
        )
    except Exception as exc:
        logger.warning("[startup] finaliseur agentique indisponible : %s", exc)

    # Délégation historique, uniquement comme fallback explicitement configuré.
    if str(
        getattr(config, "AGENTIC_RUNTIME_FALLBACK", "disabled")
    ).lower() == "legacy" and getattr(config, "CURSOR_DELEGATION_ENABLED", True):
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
    agentic_finalizer_stop.set()
    if agentic_finalizer_task is not None:
        try:
            await asyncio.wait_for(agentic_finalizer_task, timeout=5.0)
        except (TimeoutError, asyncio.CancelledError):
            agentic_finalizer_task.cancel()
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

    if agentic_service is not None:
        try:
            await agentic_service.dispose()
        except Exception as exc:
            logger.warning("[shutdown] arrêt du runtime agentique : %s", exc)

    await event_bus.wait_until_idle()
    event_bus.unbind_loop()
    logger.info("Arrêt JARVIS.")
