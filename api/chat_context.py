"""Construction du contexte conversationnel, titres et TTS streaming."""

from __future__ import annotations

import logging

from fastapi import WebSocket

import config
import llm
from api.llm_logging import _schedule_llm_log
from database import (
    get_all_people,
    get_app_usage,
    get_conversation_detail,
    get_conversation_documents,
    get_conversations,
    get_current_screen_context,
    get_recordings,
    get_school_documents,
    get_tasks,
    update_conversation,
)
from integrations import calendar_client, mail_client, weather

logger = logging.getLogger("jarvis")


async def _maybe_title_conversation(conv_id: int) -> None:
    """Génère un titre court si la conversation n'en a pas encore et a au moins 1 user + 1 assistant."""
    try:
        conv = get_conversation_detail(conv_id)
        if not conv or conv.get("title"):
            return
        msgs = conv.get("messages", [])
        has_user = any(m.get("role") == "user" for m in msgs)
        has_assistant = any(m.get("role") == "assistant" for m in msgs)
        if not (has_user and has_assistant):
            return
        first_msgs = msgs[:4]
        context = "\n".join([f"{m['role']}: {m['content'][:100]}" for m in first_msgs])
        result = await llm.chat(
            messages=[{"role": "user", "content": context}],
            model=config.DEEPSEEK_FAST_MODEL,
            system="Génère un titre court (3-6 mots) pour cette conversation. Pas de guillemets, pas de ponctuation finale. Juste le titre. Exemples : 'Révision exam droit', 'Analyse relation Bertille', 'Planning semaine', 'Problème code Python'.",
            max_tokens=20,
            temperature=0.3,
            use_cache=False,
        )
        title = (result.get("content") or "").strip().strip('"').strip("'")
        if title:
            update_conversation(conv_id, title=title)
            _schedule_llm_log(
                agent="system",
                action_type="auto_title",
                payload={"conversation_id": conv_id, "title": title},
                status="success",
            )
            logger.info("[conv] Titre auto : #%d → %s", conv_id, title)
    except Exception as e:
        _schedule_llm_log(
            agent="system",
            action_type="auto_title",
            payload={"conversation_id": conv_id, "error": str(e)},
            status="error",
        )
        logger.debug("[conv] _maybe_title_conversation : %s", e)


async def _send_tts_streaming(ws: WebSocket, text: str, emotion: str) -> None:
    """Envoie `speaking`, chunks audio, puis `speech_done` (boucle cliente).

    Le moteur TTS est lu dynamiquement depuis `app_settings.tts_engine` à chaque
    appel — pas besoin de redémarrer le serveur pour changer de backend.
    """
    from audio.tts import get_tts_by_name
    from audio.audio_format import tts_audio_mime
    from database import get_setting as _get_setting

    from audio.tts_cache import last_tts, speculative_tts

    engine_name = (
        __import__("audio.tts", fromlist=["resolve_tts_engine_name"]).resolve_tts_engine_name()
    )
    active_engine = get_tts_by_name(engine_name)

    audio_mime = tts_audio_mime(engine_name)
    await ws.send_json({"type": "speaking", "emotion": emotion, "audio_mime": audio_mime})
    if not (text and text.strip()) or active_engine is None or not getattr(active_engine, "available", False):
        await ws.send_json({"type": "speech_done"})
        return

    # TTS spéculatif : la réponse correspond à un audio déjà pré-généré
    cached = speculative_tts.get(text, emotion)
    if cached:
        try:
            await ws.send_bytes(cached)
            last_tts.store(text, emotion, cached, audio_mime)
        except Exception as e:
            logger.error("[TTS] envoi cache spéculatif : %s", e)
        finally:
            await ws.send_json({"type": "speech_done"})
        return

    collected: list[bytes] = []
    try:
        async for chunk in active_engine.synthesize_stream(text, emotion=emotion):
            if chunk:
                collected.append(chunk)
                await ws.send_bytes(chunk)
    except Exception as e:
        logger.error("[TTS] Erreur streaming (%s) : %s", engine_name, e)
    finally:
        if collected:
            # « répète » rejouera exactement cet audio, sans re-génération
            last_tts.store(text, emotion, b"".join(collected), audio_mime)
        await ws.send_json({"type": "speech_done"})


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
    # Documents attachés à la conversation
    try:
        conv_docs = get_conversation_documents(conversation_id)
        if conv_docs:
            docs_parts = [
                f"[DOCUMENT: {d['original_name']}]\n{str(d.get('extracted_text') or '')[:3000]}"
                for d in conv_docs
                if d.get("extracted_text")
            ]
            if docs_parts:
                context["documents_context"] = (
                    "[DOCUMENTS ATTACHÉS]\n" + "\n\n".join(docs_parts)
                )
    except Exception as e:
        logger.debug("[ctx] conv_docs : %s", e)

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
                    context["emails_context"] = "\n".join([
                        f"- De: {e.get('from', '')} | Objet: {e.get('subject', '')} | "
                        f"{str(e.get('preview', '') or e.get('snippet', ''))[:300]}"
                        for e in emails
                    ])
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
                context["recordings_context"] = "\n".join([
                    f"- {r.get('title', r.get('label', '?'))} ({r.get('duration_seconds', 0)}s)"
                    for r in recs
                ])
        except Exception:
            pass

    # Conversations passées — référence au passé
    memory_triggers = ["on avait", "la dernière fois", "tu te souviens", "on a parlé",
                       "rappelle", "avant", "hier on", "la semaine dernière", "souviens-toi"]
    if any(t in lower for t in memory_triggers):
        try:
            recent_convs = get_conversations(limit=5)
            if recent_convs:
                context["recent_conversations"] = "\n".join([
                    f"- [{c.get('title', 'Sans titre')}] {str(c.get('last_message', ''))[:100]}"
                    for c in recent_convs
                ])
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

    _schedule_llm_log(
        agent="system",
        action_type="context_enrichment",
        payload={"conversation_id": conversation_id, "keys": sorted(list(context.keys())), "key_count": len(context)},
        status="success",
    )
    return context
