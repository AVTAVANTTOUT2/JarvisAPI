"""Handlers des intégrations, réglages, notifications et mission control."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

import config
from agents.orchestrator import orchestrator
from agents.productivity import productivity_agent
from api.daemon_support import _audio_daemon_status_payload
from api.errors import internal_error
from api.misc_status import _computer_status_payload
from api.people_support import _decode_person_path, _resolve_handle_with_contacts
from database import (
    clear_llm_logs,
    get_event_replay_window,
    get_llm_logs,
    get_person,
)
from integrations import calendar_client, imessage_bridge, mail_client, weather
from jarvis.notification_service import notification_service
from scripts.email_watcher import email_watcher

logger = logging.getLogger("jarvis")


class WebPushKeysRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    p256dh: str = Field(min_length=1, max_length=512)
    auth: str = Field(min_length=1, max_length=256)


class WebPushSubscriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str = Field(min_length=1, max_length=2048)
    keys: WebPushKeysRequest


class WebPushUnsubscribeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str = Field(min_length=1, max_length=2048)



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
        "location_tracking": getattr(config, "LOCATION_TRACKING", True),
        "audio_daemon": _audio_daemon_status_payload(),
    }



# ── Mission Control ──────────────────────────────────────────


SSE_INITIAL_HISTORY = 30
SSE_REPLAY_LIMIT = 1000
SSE_POLL_INTERVAL_SECONDS = 0.5
SSE_HEARTBEAT_SECONDS = 15.0


def _parse_last_event_id(raw: str | None) -> tuple[int | None, str | None]:
    if raw is None or not raw.strip():
        return None, None
    try:
        value = int(raw.strip())
    except ValueError:
        return None, "invalid_last_event_id"
    if value < 0:
        return None, "invalid_last_event_id"
    return value, None


def _format_sse_event(event: dict[str, Any]) -> str:
    event_id = int(event["sse_id"])
    return (
        f"id: {event_id}\n"
        f"data: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


def _format_stream_reset(
    *,
    reason: str,
    requested_after: int | None,
    resume_after: int,
    skipped: int,
) -> str:
    payload = {
        "type": "stream.reset",
        "event_type": "stream.reset",
        "reason": reason,
        "requested_after": requested_after,
        "resume_after": resume_after,
        "skipped": skipped,
    }
    return (
        "event: stream.reset\n"
        f"id: {resume_after}\n"
        f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


async def _durable_event_stream(
    last_event_id: int | None,
    invalid_reason: str | None = None,
) -> AsyncIterator[str]:
    """Lit le journal durable sans file mémoire propre au client.

    La contre-pression du transport ralentit uniquement ce générateur. À la
    prochaine lecture, son curseur SQLite reprend les événements manquants.
    Si le retard excède ``SSE_REPLAY_LIMIT``, un ``stream.reset`` annonce le
    saut vers la fenêtre récente au lieu de faire croître la mémoire.
    """
    cursor = last_event_id
    initial = True
    pending_reset_reason = invalid_reason
    last_heartbeat = time.monotonic()

    while True:
        window = await asyncio.to_thread(
            get_event_replay_window,
            cursor if not initial or cursor is not None else None,
            initial_limit=SSE_INITIAL_HISTORY,
            replay_limit=SSE_REPLAY_LIMIT,
        )
        reset_reason = pending_reset_reason or window.reset_reason
        if reset_reason:
            yield _format_stream_reset(
                reason=reset_reason,
                requested_after=window.requested_after,
                resume_after=window.resume_after,
                skipped=window.skipped,
            )
            cursor = window.resume_after
            pending_reset_reason = None

        for event in window.events:
            event_id = int(event["sse_id"])
            if cursor is not None and event_id <= cursor:
                continue
            yield _format_sse_event(event)
            cursor = event_id

        initial = False
        now = time.monotonic()
        if now - last_heartbeat >= SSE_HEARTBEAT_SECONDS:
            yield ": keepalive\n\n"
            last_heartbeat = now
        await asyncio.sleep(SSE_POLL_INTERVAL_SECONDS)


async def events_stream(request: Request) -> StreamingResponse:
    """SSE — flux temps réel de tous les événements JARVIS.

    Le frontend MissionControl.tsx consomme ce flux pour afficher
    l'activité en temps réel (pipeline vocal, orchestration, agents, TTS).
    """
    last_event_id, invalid_reason = _parse_last_event_id(
        request.headers.get("last-event-id")
    )

    return StreamingResponse(
        _durable_event_stream(last_event_id, invalid_reason),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
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
        raise internal_error("email_catchup_failed", "Rattrapage des emails impossible") from e


# ── Réglages dynamiques (sans redémarrage) ──────────────────


async def api_get_tts_setting():
    """Décrit le moteur vocal local actif.

    Il n'y a plus de catalogue de moteurs : un seul fournisseur local, une
    seule voix. Cette route reste une lecture d'état.
    """
    from jarvis.audio.tts import get_local_tts_provider, load_tts_settings

    settings = load_tts_settings()
    try:
        info = get_local_tts_provider(settings).info()
    except Exception as exc:  # noqa: BLE001 - configuration invalide reste lisible
        logger.warning("[tts/settings] configuration invalide : %s", exc)
        return {
            "engine": settings.provider,
            "available": False,
            "error": "tts_configuration_invalid",
        }
    return {"engine": info.provider, "available": True, **info.as_log_fields()}


async def api_set_tts_setting(body: dict):
    """Refuse tout changement de moteur à chaud.

    Le fournisseur est choisi par ``TTS_PROVIDER`` et chargé une fois, au
    démarrage — plusieurs gigaoctets de poids ne se remplacent pas en cours
    de conversation. Cette route ne subsiste que pour répondre clairement aux
    clients historiques qui l'appellent encore.
    """
    from fastapi import HTTPException

    from jarvis.audio.tts import load_tts_settings

    requested = (body.get("engine") or "").lower().strip()
    active = load_tts_settings().provider
    if requested and requested != active:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Le moteur vocal ne se change pas à chaud. Fournisseur actif : "
                f"{active!r}. Modifiez TTS_PROVIDER dans .env puis redémarrez."
            ),
        )
    return {"engine": active, "ok": True}


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


async def api_push_subscribe(body: WebPushSubscriptionRequest, request: Request):
    """Enregistre un abonnement Web Push (format `PushSubscription.toJSON()`)."""
    from database import upsert_push_subscription
    from core.outbound_security import OutboundURLRejected
    from push import validate_web_push_endpoint

    endpoint = body.endpoint.strip()
    try:
        await asyncio.to_thread(validate_web_push_endpoint, endpoint)
    except OutboundURLRejected as exc:
        raise HTTPException(
            422,
            {"code": exc.code, "message": "Endpoint Web Push refusé"},
        ) from exc

    upsert_push_subscription(
        endpoint,
        body.keys.p256dh,
        body.keys.auth,
        request.headers.get("user-agent", ""),
    )
    return {"ok": True}


async def api_push_unsubscribe(body: WebPushUnsubscribeRequest):
    from database import delete_push_subscription

    endpoint = body.endpoint.strip()
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
        raise internal_error("briefing_failed", "Génération du briefing impossible") from e


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
