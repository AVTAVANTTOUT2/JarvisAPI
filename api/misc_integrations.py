"""Handlers des intégrations, réglages, notifications et mission control."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

import config
from agents.orchestrator import orchestrator
from agents.productivity import productivity_agent
from api.daemon_support import _audio_daemon_status_payload
from api.misc_status import _code_executor_status_payload, _computer_status_payload
from api.people_support import _decode_person_path, _resolve_handle_with_contacts
from database import (
    clear_llm_logs,
    get_llm_logs,
    get_person,
)
from integrations import calendar_client, imessage_bridge, mail_client, weather
from jarvis.event_bus import JarvisEvent, event_bus
from jarvis.notification_service import notification_service
from scripts.email_watcher import email_watcher

logger = logging.getLogger("jarvis")



# ── Productivité : intégrations + tâches + briefings ────────


async def api_debug_resolve(name: str):
    """Debug : résolution du handle iMessage pour un contact."""
    from database import get_db
    decoded = _decode_person_path(name)
    person = get_person(decoded) or get_person(name.strip())
    handle = _resolve_handle_with_contacts(decoded)
    steps: dict[str, Any] = {}
    if person:
        pid = person.get("id")
        with get_db() as conn:
            rp = conn.execute(
                "SELECT handle FROM relationship_profiles WHERE person_id=? AND handle IS NOT NULL LIMIT 1",
                (pid,)
            ).fetchone()
            steps["relationship_profile_handle"] = rp[0] if rp else None
    return {
        "name": decoded,
        "person_found": person is not None,
        "resolved_handle": handle,
        "steps": steps,
    }


async def api_integrations():
    """État de chaque intégration externe.

    Les checks osascript (Mail, Calendar) sont exécutés dans un thread séparé
    avec un timeout court pour ne jamais bloquer l'event loop.
    """
    async def _check(fn, fallback, timeout: float = 2.0):
        try:
            return await asyncio.wait_for(asyncio.to_thread(fn), timeout=timeout)
        except (asyncio.TimeoutError, Exception):
            return fallback

    mail_ok, cal_status, weather_ok = await asyncio.gather(
        _check(lambda: mail_client.is_available() if mail_client else False, False),
        _check(
            lambda: calendar_client.get_status() if calendar_client else {"available": False, "error": "Non initialisé"},
            {"available": False, "error": "Timeout"},
        ),
        _check(lambda: weather.is_available() if weather else False, False),
    )
    return {
        "mail": mail_ok,
        "calendar": cal_status,
        "weather": weather_ok,
        "imessage": imessage_bridge is not None and imessage_bridge.is_available(),
        "imessage_sourcing": config.IMESSAGE_SOURCING_ENABLED,
        "imessage_send": config.IMESSAGE_SEND_ENABLED,
        "email_watcher": email_watcher.running,
        "computer": _computer_status_payload(),
        "code_executor": _code_executor_status_payload(),
        "location_tracking": getattr(config, "LOCATION_TRACKING", True),
        "audio_daemon": _audio_daemon_status_payload(),
    }



# ── Mission Control ──────────────────────────────────────────


async def events_stream():
    """SSE — flux temps réel de tous les événements JARVIS.

    Le frontend MissionControl.tsx consomme ce flux pour afficher
    l'activité en temps réel (pipeline vocal, orchestration, agents, TTS).
    """
    queue: asyncio.Queue[JarvisEvent] = event_bus.subscribe()

    async def generate():
        try:
            # Envoyer l'historique récent au connect
            for evt in event_bus.get_history(30):
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
            while True:
                event = await queue.get()
                yield event.to_sse()
        except asyncio.CancelledError:
            pass
        finally:
            event_bus.unsubscribe(queue)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def mission_prompt(payload: dict[str, Any]):
    """Prompt depuis Mission Control — passe par l'orchestrateur normal.

    Body: {"message": "...", "conversation_id": "..."}
    """
    message = payload.get("message", "")
    if not message or not message.strip():
        raise HTTPException(status_code=400, detail="Message requis")

    conversation_id = payload.get("conversation_id", "mission-control")
    conv_id_int: int | None = None

    if isinstance(conversation_id, str) and conversation_id != "mission-control":
        try:
            conv_id_int = int(conversation_id)
        except (ValueError, TypeError):
            pass
    elif isinstance(conversation_id, (int, float)):
        conv_id_int = int(conversation_id)

    if conv_id_int is None and conversation_id == "mission-control":
        from database import create_conversation
        try:
            conv_id_int = create_conversation(agent="mission_control")
        except Exception as e:
            logger.warning("[mission] create_conversation: %s", e)
            conv_id_int = None

    result = await orchestrator.handle(message, conv_id_int)
    return result


async def api_email_watcher_catchup():
    """Force un cycle de rattrapage (réhydratation DB + analyse des non-lus absents de ``email_summaries``).

    Réinitialise aussi le cache de disponibilité Mail (contourne le cooldown 120s après timeout).
    Ouvre Mail.app avant d'appeler si le dernier test a expiré.
    """
    try:
        result = await email_watcher.run_catchup_cycle()
        return result
    except Exception as e:
        logger.exception("api_email_watcher_catchup : %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


# ── Réglages dynamiques (sans redémarrage) ──────────────────

_VALID_TTS_ENGINES = {"edge", "macos", "kokoro", "ttskit"}


async def api_get_tts_setting():
    """Retourne le moteur TTS actif (DB ou fallback .env)."""
    from database import get_setting as _gs
    engine = _gs("tts_engine", getattr(config, "TTS_ENGINE", config.DEFAULT_TTS_ENGINE) or config.DEFAULT_TTS_ENGINE)
    return {"engine": engine}


async def api_set_tts_setting(body: dict):
    """Change le moteur TTS à la volée (sans redémarrage).

    Payload : ``{"engine": "edge" | "macos" | "kokoro" | "ttskit"}``
    """
    from database import set_setting as _ss
    from audio.tts import get_tts_by_name

    engine = (body.get("engine") or "").lower().strip()
    if engine not in _VALID_TTS_ENGINES:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=422,
            detail=f"Moteur invalide : {engine!r}. Valeurs acceptées : {sorted(_VALID_TTS_ENGINES)}",
        )

    # Vérifie la disponibilité du moteur demandé
    target = get_tts_by_name(engine)
    resolved_engine = getattr(target, "get_backend_name", lambda: "none")()
    if not getattr(target, "available", False) or resolved_engine != engine:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=422,
            detail=f"Le moteur '{engine}' n'est pas disponible sur ce système. "
                   f"Vérifiez edge-tts (edge), les modèles locaux (kokoro/ttskit) "
                   f"ou les commandes say/afconvert (macos).",
        )

    _ss("tts_engine", engine)
    # Aligne config runtime pour le process courant (mobile_voice / cache)
    config.TTS_ENGINE = engine
    logger.info("[TTS] Moteur changé → %s", engine)
    return {"engine": engine, "ok": True}


# ── Notifications (email watcher + alertes patterns) ────────


async def api_notifications_unread():
    """Liste des notifications non lues, triées par priorité."""
    try:
        return {"notifications": notification_service.get_unread()}
    except Exception as e:
        logger.error("Erreur get_unread_notifications : %s", e)
        return {"notifications": []}


async def api_push_vapid_public_key():
    """Clé publique VAPID — à passer en `applicationServerKey` de `PushManager.subscribe`."""
    import push

    return {"key": push.get_vapid_public_key_b64url()}


async def api_push_subscribe(body: dict, request: Request):
    """Enregistre un abonnement Web Push (format `PushSubscription.toJSON()`)."""
    from database import upsert_push_subscription

    endpoint = (body.get("endpoint") or "").strip()
    keys = body.get("keys") or {}
    if not endpoint or not keys.get("p256dh") or not keys.get("auth"):
        raise HTTPException(400, "`endpoint` et `keys.{p256dh,auth}` requis")

    upsert_push_subscription(
        endpoint, keys["p256dh"], keys["auth"], request.headers.get("user-agent", "")
    )
    return {"ok": True}


async def api_push_unsubscribe(body: dict):
    from database import delete_push_subscription

    endpoint = (body.get("endpoint") or "").strip()
    if not endpoint:
        raise HTTPException(400, "`endpoint` requis")
    delete_push_subscription(endpoint)
    return {"ok": True}


async def api_logs(type: str | None = None, limit: int = 100):
    """Logs d'actions rédigés (récent -> ancien). Inclut DevAgent sans filtre."""
    try:
        logs = get_llm_logs(limit=limit, action_type=type)
        return {"logs": logs, "count": len(logs)}
    except Exception as e:
        logger.error("Erreur get_llm_logs : %s", e)
        return {"logs": [], "count": 0}


async def api_logs_clear():
    """Efface explicitement tous les journaux affichés dans l'écran Logs."""
    deleted = clear_llm_logs()
    return {
        "ok": True,
        "deleted": deleted,
        "deleted_count": sum(deleted.values()),
    }


# ── DevAgent autonome (interview -> spec -> boucle dev isolee) ─────────────


async def api_notifications_all(limit: int = 50):
    """Toutes les notifications récentes (lues + non lues), pour historique UI."""
    try:
        return {"notifications": notification_service.get_recent(limit=limit)}
    except Exception as e:
        logger.error("Erreur get_recent_notifications : %s", e)
        return {"notifications": []}


async def api_notifications_mark_read(notif_id: int):
    if not notification_service.mark_read(notif_id):
        raise HTTPException(404, "Notification introuvable")
    return {"ok": True}


async def api_notifications_mark_all_read():
    count = notification_service.mark_all_read()
    return {"ok": True, "marked": count}


async def api_briefing(kind: str = "morning"):
    """Génère un briefing à la demande. `kind` = 'morning' ou 'evening'."""
    try:
        if kind == "evening":
            text = await productivity_agent.evening_summary()
        else:
            text = await productivity_agent.morning_briefing()
        return {"kind": kind, "content": text}
    except Exception as e:
        logger.exception("Erreur briefing")
        raise HTTPException(500, f"Briefing impossible : {type(e).__name__}: {e}")


async def api_emails(limit: int = 20):
    """Resumes emails recents (email_summaries)."""
    from database import get_recent_email_summaries
    summaries = get_recent_email_summaries(limit=limit)
    return {"emails": summaries, "count": len(summaries)}


async def api_mood():
    """Dernier mood enregistre."""
    from database import get_recent_moods
    moods = get_recent_moods(limit=1)
    if moods:
        return {"mood": moods[0].get("mood_score"), "energy": moods[0].get("energy_level"), "context": moods[0].get("context", "")}
    return {"mood": None, "energy": None}
