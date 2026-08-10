"""Construction du contexte conversationnel, titres et TTS streaming."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from fastapi import WebSocket

import config
from api.llm_logging import _schedule_llm_log
from database import (
    get_all_people,
    get_app_usage,
    get_conversation_documents,
    get_conversations,
    get_current_screen_context,
    get_recordings,
    get_school_documents,
    get_tasks,
)
from integrations import calendar_client, mail_client, weather
from jarvis.document_privacy import document_strict_local_enabled
from jarvis.pii.boundary import DataBoundary
from jarvis.security.llm_data_boundary import wrap_untrusted_data

logger = logging.getLogger("jarvis")


async def _send_tts_streaming(
    ws: WebSocket,
    text: str,
    emotion: str,
    *,
    turn_id: str | None = None,
    cancel_event: Any | None = None,
) -> str:
    """Envoie `speaking`, chunks audio, puis `speech_done` (boucle cliente).

    Annulable via ``cancel_event`` (asyncio.Event) : dès qu'il est set, on
    arrête d'envoyer des chunks et on signale ``speech_cancelled`` pour que
    le client jette l'audio du ``turn_id`` courant.

    Le navigateur reçoit **un seul blob WAV** : contrairement au MP3, des
    fragments WAV concaténés ne forment pas un fichier valide, et le client
    assemble avant de lire. Le chemin qui compte pour la latence — le tour de
    parole local — passe, lui, par la diffusion fragment par fragment
    (``jarvis.audio.tts.playback``).

    Retourne ``"completed"`` | ``"cancelled"`` | ``"skipped"``.
    """
    from audio.audio_format import DEFAULT_TTS_MIME
    from audio.tts_cache import last_tts, speculative_tts
    from jarvis.audio.tts import get_local_tts_provider
    from jarvis.audio.tts.errors import TTSError
    from jarvis.audio.tts.wav import pcm_to_wav

    audio_mime = DEFAULT_TTS_MIME
    payload: dict[str, Any] = {"type": "speaking", "emotion": emotion, "audio_mime": audio_mime}
    if turn_id:
        payload["turn_id"] = turn_id
    await ws.send_json(payload)

    def _cancelled() -> bool:
        return bool(cancel_event is not None and cancel_event.is_set())

    if _cancelled():
        await ws.send_json({"type": "speech_cancelled", "turn_id": turn_id})
        return "cancelled"

    if not (text and text.strip()):
        await ws.send_json({"type": "speech_done", "turn_id": turn_id})
        return "skipped"

    # TTS spéculatif : la réponse correspond à un audio déjà pré-généré
    cached = speculative_tts.get(text, emotion)
    if cached:
        if _cancelled():
            await ws.send_json({"type": "speech_cancelled", "turn_id": turn_id})
            return "cancelled"
        try:
            await ws.send_bytes(cached)
            last_tts.store(text, emotion, cached, audio_mime)
        except asyncio.CancelledError:
            await ws.send_json({"type": "speech_cancelled", "turn_id": turn_id})
            raise
        except Exception as e:
            logger.error("[TTS] envoi cache spéculatif : %s", e)
        if _cancelled():
            await ws.send_json({"type": "speech_cancelled", "turn_id": turn_id})
            return "cancelled"
        await ws.send_json({"type": "speech_done", "turn_id": turn_id})
        return "completed"

    request_id = turn_id or uuid.uuid4().hex
    audio = b""
    provider = None
    try:
        provider = get_local_tts_provider()
        # On consomme les fragments nous-mêmes plutôt que d'appeler la
        # synthèse complète : une annulation en cours de route doit arrêter la
        # génération, pas seulement jeter le résultat à la fin.
        pcm: list[bytes] = []
        sample_rate = provider.info().sample_rate
        channels = provider.info().channels
        async for chunk in provider.stream(
            text, request_id=request_id, utterance_id=request_id,
        ):
            if _cancelled():
                await provider.cancel(request_id)
                break
            if chunk.data:
                pcm.append(chunk.data)
                sample_rate = chunk.sample_rate
                channels = chunk.channels
        if pcm and not _cancelled():
            audio = pcm_to_wav(
                b"".join(pcm), sample_rate=sample_rate, channels=channels,
            )
    except asyncio.CancelledError:
        await ws.send_json({"type": "speech_cancelled", "turn_id": turn_id})
        raise
    except TTSError as e:
        # Pas de repli vers un autre moteur ni vers un service distant : le
        # client garde la réponse texte et sait que la voix est indisponible.
        logger.error("[TTS] synthèse locale indisponible : %s", e)
    except Exception as e:
        logger.error("[TTS] Erreur de synthèse : %s", e)

    if _cancelled():
        await ws.send_json({"type": "speech_cancelled", "turn_id": turn_id})
        return "cancelled"

    if audio:
        try:
            await ws.send_bytes(audio)
            last_tts.store(text, emotion, audio, audio_mime)
        except Exception as e:
            logger.error("[TTS] envoi audio : %s", e)

    if _cancelled():
        await ws.send_json({"type": "speech_cancelled", "turn_id": turn_id})
        return "cancelled"
    await ws.send_json({"type": "speech_done", "turn_id": turn_id})
    return "completed"


async def _build_enriched_context(text: str, conversation_id: int) -> dict:
    """Construit le contexte enrichi à partir de toutes les sources de données.

    Appelé par _process_message (WS) ET _process_message_internal (REST).
    Contexte permanent : documents de la conversation.
    Contexte conditionnel : mails, calendar, météo, tâches, localisation, fichiers,
    enregistrements, conversations passées — détectés par mots-clés dans le texte.
    """
    context: dict = {}
    lower = text.lower()

    # ─── CONTEXTE PERMANENT ───────────────────────────────────────────────────
    # Documents attachés : jamais de contenu brut vers l'orchestrateur cloud.
    # Le consentement est propre à chaque upload et le mode strict local reste
    # prioritaire même pour un document précédemment autorisé.
    try:
        conv_docs = get_conversation_documents(conversation_id)
        eligible_docs = [
            doc
            for doc in conv_docs
            if doc.get("extracted_text") and bool(doc.get("cloud_consent"))
        ]
        if eligible_docs and not document_strict_local_enabled():
            limit = max(1, int(config.DOCUMENT_CLOUD_MAX_CHARS))
            docs_parts: list[str] = []
            remaining = limit
            for doc in eligible_docs:
                prefix = f"[DOCUMENT_{doc['id']}]\n"
                available = max(0, remaining - len(prefix))
                if available <= 0:
                    break
                fragment = str(doc.get("extracted_text") or "")[:available]
                docs_parts.append(prefix + fragment)
                remaining -= len(prefix) + len(fragment)

            raw_context = "\n\n".join(docs_parts)[:limit]
            if raw_context:
                safe_context = wrap_untrusted_data(
                    "ATTACHED_DOCUMENTS",
                    raw_context,
                    max_chars=limit,
                )
                DataBoundary().check(safe_context)
                context["documents_context"] = safe_context
    except Exception as e:
        logger.warning("[ctx] document cloud bloqué par la frontière de données : %s", e)

    # ─── CONTEXTE CONDITIONNEL ────────────────────────────────────────────────

    # Mails — mention explicite ou nom d'une personne connue
    mail_triggers = ["mail", "email", "courrier", "boîte", "inbox", "reçu", "envoyé",
                     "message de", "écrit", "mails", "messagerie"]
    people_names: list[str] = []
    try:
        people_names = [p["name"].lower() for p in get_all_people() if p.get("name")]
    except Exception:
        pass

    if any(t in lower for t in mail_triggers) or any(n in lower for n in people_names):
        try:
            if mail_client and mail_client.is_available():
                emails = await mail_client.get_unread(10)
                if emails:
                    email_lines = "\n".join([
                        f"- De: {e.get('from', '')} | Objet: {e.get('subject', '')} | "
                        f"{str(e.get('preview', '') or e.get('snippet', ''))[:300]}"
                        for e in emails
                    ])
                    context["emails_context"] = wrap_untrusted_data(
                        "MAIL_APP",
                        email_lines,
                        max_chars=5_000,
                    )
        except Exception as ex:
            logger.warning("[ctx] mail : %s", ex)

    # Calendar — planning, agenda, dates
    calendar_triggers = ["planning", "agenda", "rdv", "rendez-vous", "calendrier",
                         "emploi du temps", "semaine", "demain", "aujourd'hui",
                         "ce soir", "ce matin", "cours", "quand", "horaire", "programme"]
    if any(t in lower for t in calendar_triggers):
        try:
            if calendar_client and calendar_client.is_available():
                events = await calendar_client.get_today_events()
                if events:
                    context["calendar_context"] = "\n".join([
                        f"- {e.get('start', '?')} → {e.get('end', '?')} : {e.get('summary', '?')}"
                        for e in events
                    ])
        except Exception as ex:
            logger.warning("[ctx] calendar : %s", ex)

    # Météo — conditions climatiques
    weather_triggers = ["météo", "meteo", "temps", "pluie", "soleil", "parapluie",
                        "température", "chaud", "froid", "dehors", "sortir"]
    if any(t in lower for t in weather_triggers):
        try:
            if weather and weather.is_available():
                w = await weather.get_current()
                if w:
                    context["weather_context"] = (
                        f"{w.get('city', '?')} : {w.get('temp', '?')}°C, "
                        f"{w.get('description', '?')}"
                    )
        except Exception as ex:
            logger.warning("[ctx] weather : %s", ex)

    # Tâches — todo, deadlines
    task_triggers = ["tâche", "tache", "todo", "faire", "à faire", "en retard",
                     "priorité", "rappel", "deadline", "échéance", "tâches"]
    if any(t in lower for t in task_triggers):
        try:
            tasks = get_tasks()
            if tasks:
                context["tasks_context"] = "\n".join([
                    f"- [{t['priority']}] {t['title']} ({t['status']})" +
                    (f" — échéance {t['due_date']}" if t.get("due_date") else "")
                    for t in tasks[:10]
                ])
        except Exception as ex:
            logger.warning("[ctx] tasks : %s", ex)

    # Localisation — lieu actuel, position GPS
    location_triggers = ["où", "position", "lieu", "ici", "maison", "bureau", "salle",
                         "adresse", "localisation", "trajet", "déplacement"]
    if any(t in lower for t in location_triggers):
        try:
            from integrations.location import location_manager
            status = await location_manager.get_status()
            if status:
                loc_text = ""
                if status.get("current_visit"):
                    loc_text = f"Actuellement à : {status['current_visit'].get('place_name', '?')}"
                elif status.get("current_location"):
                    loc = status["current_location"]
                    loc_text = f"Position : {loc.get('latitude', '?')}, {loc.get('longitude', '?')}"
                if loc_text:
                    context["location_context"] = loc_text
        except Exception:
            pass

    # Fichiers / documents scolaires
    file_triggers = ["fichier", "document", "cours", "pdf", "rapport", "devoir",
                     "dissertation", "fiche", "upload", "télécharger", "documents"]
    if any(t in lower for t in file_triggers):
        try:
            docs = get_school_documents(limit=10)
            if docs:
                context["school_docs_context"] = "\n".join([
                    f"- {d['title']} ({d.get('doc_type', '?')})"
                    for d in docs
                ])
        except Exception:
            pass
        try:
            recs = get_recordings(limit=5)
            if recs:
                recording_lines = "\n".join([
                    f"- {r.get('title', r.get('label', '?'))} ({r.get('duration_seconds', 0)}s)"
                    for r in recs
                ])
                context["recordings_context"] = wrap_untrusted_data(
                    "TRANSCRIPTION_METADATA",
                    recording_lines,
                    max_chars=2_000,
                )
        except Exception:
            pass

    # Conversations passées — référence au passé
    memory_triggers = ["on avait", "la dernière fois", "tu te souviens", "on a parlé",
                       "rappelle", "avant", "hier on", "la semaine dernière", "souviens-toi"]
    if any(t in lower for t in memory_triggers):
        try:
            recent_convs = get_conversations(limit=5)
            if recent_convs:
                recent_lines = "\n".join([
                    f"- [{c.get('title', 'Sans titre')}] {str(c.get('last_message', ''))[:100]}"
                    for c in recent_convs
                ])
                context["recent_conversations"] = wrap_untrusted_data(
                    "CONVERSATION_HISTORY_SEARCH",
                    recent_lines,
                    max_chars=2_000,
                )
        except Exception:
            pass

    # Contexte écran (toujours injecté si disponible — c'est gratuit côté tokens cachés)
    try:
        screen_ctx = get_current_screen_context()
        if screen_ctx:
            context["screen_context"] = (
                f"Écran : {screen_ctx.get('app', '?')} — "
                f"{screen_ctx.get('activity', '?')} (mood: {screen_ctx.get('mood', '?')})"
            )
    except Exception:
        pass

    # Temps par app aujourd'hui — uniquement si la question concerne la productivité
    screen_triggers = [
        "temps", "productivité", "productif", "travaillé", "passé combien",
        "app", "application", "écran", "screen time", "distrait", "procrastin",
    ]
    if any(t in lower for t in screen_triggers):
        try:
            usage = get_app_usage()
            if usage:
                top = sorted(usage, key=lambda x: x.get("duration_seconds", 0), reverse=True)[:10]
                context["screen_time_context"] = "\n".join([
                    f"- {u['app']} : {int(u.get('duration_seconds', 0)) // 60} min"
                    for u in top
                ])
        except Exception:
            pass

    # ── ContextPlanner : budget + traçabilité (« pourquoi cette donnée ? ») ──
    try:
        from jarvis.cognitive import plan_context, route_request

        intent = route_request(text, interaction_mode="chat")
        planner_input = {
            "calendar": context.get("calendar_context"),
            "tasks": context.get("tasks_context"),
            "emails": context.get("emails_context"),
            "weather": context.get("weather_context"),
            "location": context.get("location_context"),
            "memory_hits": context.get("recent_conversations"),
            "screen_context": context.get("screen_context"),
        }
        planned = plan_context(intent, planner_input)
        # Budget réel : tronque les tranches conditionnelles selon l'intent.
        slice_to_ctx_key = {
            "CALENDAR": "calendar_context",
            "TASKS": "tasks_context",
            "EMAILS": "emails_context",
            "WEATHER": "weather_context",
            "LOCATION": "location_context",
            "MEMORY": "recent_conversations",
            "SCREEN": "screen_context",
        }
        budget = planned.char_budget()
        used = 0
        for s in sorted(planned.slices, key=lambda x: -x.relevance):
            ctx_key = slice_to_ctx_key.get(s.key)
            if not ctx_key or ctx_key not in context:
                continue
            remaining = max(0, budget - used)
            value = str(context[ctx_key])
            if len(value) > remaining:
                if remaining < 200:
                    context.pop(ctx_key, None)
                    continue
                context[ctx_key] = value[:remaining]
            used += len(str(context.get(ctx_key, "")))
        context["__routing"] = intent.to_diagnostic()
        context["__context_trace"] = planned.to_diagnostic()
    except Exception as e:
        logger.debug("[ctx] context planner : %s", e)

    _schedule_llm_log(
        agent="system",
        action_type="context_enrichment",
        payload={
            "conversation_id": conversation_id,
            "keys": sorted(k for k in context.keys() if not k.startswith("__")),
            "key_count": len(context),
            "routing": context.get("__routing"),
            "context_trace": context.get("__context_trace"),
        },
        status="success",
    )
    return context
